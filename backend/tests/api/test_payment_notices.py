"""Payment notices: a customer says they paid, optionally with a receipt; staff accept or decline.

REQ-060, REQ-061; domain rule BR-14, invariant INV-15; removal of receipts BR-32; file rules of ADR-020.
"""

import asyncio
import hashlib
import re
import threading
import uuid
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.auth import AuthService
from qarz.application.chat_texts import money, say
from qarz.application.files import FileService, FileStoreUnavailable
from qarz.application.payment_notices import PaymentNoticeService
from qarz.application.ports import FileStoreError
from qarz.application.reminders import ReminderService
from qarz.application.scheduler import Scheduler
from qarz.application.shop_deletion import ShopDeletionService
from qarz.domain.files import MAX_FILE_BYTES
from qarz.infrastructure.db import Database, PgTenantSession
from qarz.infrastructure.file_store import FilesystemFileStore
from qarz.interface.http import create_app
from qarz.interface.payment_notices_api import MAX_BODY_BYTES

from ..receipt_samples import COMMENT, EXIF, HTML, JPEG, PDF, PNG, WEBP, jpeg
from .conftest import TEST_BOT_TOKEN, TEST_SECRETS_KEY, HeaderAuthenticator, World, as_user, stored_objects
from .test_customer_account import ME, attach_waiter, customer_row, link_of
from .test_customers_ledger import _subscription, another_client, key, record, seed_entry, shop, today
from .test_disputes import staff_notices, tg

pytestmark = pytest.mark.db


def send(
    client: TestClient,
    user: uuid.UUID,
    link: Any,
    amount: Any,
    receipt: bytes | None = None,
    *,
    name: str = "chek.jpg",
    declared: str = "image/jpeg",
    headers: dict[str, str] | None = None,
) -> Any:
    path = f"{ME}/{link}/payment-notices"
    sent = {**as_user(user), **(headers or {})}
    if receipt is None:
        return client.post(path, json={"amount": amount}, headers=sent)
    return client.post(path, data={"amount": str(amount)}, files={"receipt": (name, receipt, declared)}, headers=sent)


def receipt(client: TestClient, path: str, headers: dict[str, str] | None = None) -> Any:
    """Open a receipt the way a front end does: ask for a link as a staff member, then follow it.

    The link is followed with no credentials at all: it is the authorization. When no link is given,
    the refusal is returned instead.
    """
    link = client.get(path, headers=headers or {})
    if link.status_code != 200:
        return link
    assert set(link.json()) == {"url", "expires_at"}
    assert link.json()["url"].startswith("/files/")
    return client.get(link.json()["url"])


def accept(client: TestClient, world: World, user: uuid.UUID, notice: Any, amount: Any = None, **headers: str) -> Any:
    body = None if amount is None else {"amount": amount}
    sent = {**as_user(user), **(headers or key())}
    return client.post(f"{shop(world)}/payment-notices/{notice}/accept", json=body, headers=sent)


def decline(client: TestClient, world: World, user: uuid.UUID, notice: Any, reason: Any = "Pul kelib tushmagan") -> Any:
    return client.post(
        f"{shop(world)}/payment-notices/{notice}/decline", json={"reason": reason}, headers={**as_user(user), **key()}
    )


def notices(owner: psycopg.Connection, shop_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT customer_id, amount, status, file_id IS NOT NULL, payment_entry, decline_reason, decided_by, "
        "closed_at IS NOT NULL FROM payment_notice WHERE shop_id = %s ORDER BY created_at, id",
        (shop_id,),
    ).fetchall()


def files(owner: psycopg.Connection, shop_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return [
        (row[0], row[1], row[2], bytes(row[3]), row[4], row[5], row[6])
        for row in owner.execute(
            "SELECT id, purpose, object_key, sha256, size_bytes, mime, delete_after FROM stored_file "
            "WHERE shop_id = %s ORDER BY created_at, id",
            (shop_id,),
        ).fetchall()
    ]


def payments(owner: psycopg.Connection, customer: uuid.UUID) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT id, amount, author_id FROM ledger_entry WHERE customer_id = %s AND kind = 'payment' ORDER BY seq",
        (customer,),
    ).fetchall()


def balance(client: TestClient, world: World, customer: uuid.UUID) -> int:
    seen = client.get(f"{shop(world)}/customers/{customer}", headers=as_user(world.seller_a))
    assert seen.status_code == 200, seen.text
    return int(seen.json()["balance"])


def seed_notice(
    owner: psycopg.Connection,
    world: World,
    amount: int = 20000,
    *,
    days_ago: float = 0,
    customer: uuid.UUID | None = None,
    shop_id: uuid.UUID | None = None,
    file_id: uuid.UUID | None = None,
) -> uuid.UUID:
    notice_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO payment_notice (id, shop_id, customer_id, amount, file_id, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            notice_id,
            shop_id or world.shop_a,
            customer or world.customer_a,
            amount,
            file_id,
            datetime.now(UTC) - timedelta(days=days_ago),
        ),
    )
    return notice_id


def about(moment: datetime, expected: datetime) -> bool:
    return abs(moment - expected) < timedelta(minutes=2)


def told(owner: psycopg.Connection, dedupe_key: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (str(row[0]), dict(row[1]))
        for row in owner.execute(
            "SELECT recipient, payload FROM outbox_message WHERE dedupe_key = %s", (dedupe_key,)
        ).fetchall()
    ]


def sent_to_staff(owner: psycopg.Connection, shop_id: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    """What the staff of one shop were told about new notices."""
    return [
        (str(row[0]), dict(row[1]))
        for row in owner.execute(
            "SELECT recipient, payload FROM outbox_message WHERE shop_id = %s AND dedupe_key LIKE 'notice:%%:sent:%%' "
            "ORDER BY recipient, dedupe_key",
            (shop_id,),
        ).fetchall()
    ]


def with_services(
    worker_database_url: str, file_root: Path, action: Callable[[PaymentNoticeService, FileService], Awaitable[Any]]
) -> Any:
    """Call the functions the worker will call, on services wired as the application wires them."""

    async def run() -> Any:
        database = Database(worker_database_url)
        try:
            file_service = FileService(database, FilesystemFileStore(file_root))
            return await action(PaymentNoticeService(database, file_service), file_service)
        finally:
            await database.dispose()

    return asyncio.run(run())


# --- sending (REQ-060) ---------------------------------------------------------------------------------


def test_a_customer_sends_a_notice_and_every_staff_member_is_told(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    link = link_of(owner, world.customer_a)
    entries_before = owner.execute("SELECT count(*) FROM ledger_entry WHERE shop_id = %s", (world.shop_a,)).fetchone()

    response = send(client, world.customer_of_a, link, 20000)
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["status"], body["amount"], body["has_receipt"]) == ("sent", 20000, False)
    assert (body["recorded_amount"], body["payment_entry_id"], body["decline_reason"], body["closed_at"]) == (
        None,
        None,
        None,
        None,
    )
    assert notices(owner, world.shop_a) == [(world.customer_a, 20000, "sent", False, None, None, None, False)]
    assert files(owner, world.shop_a) == [] and stored_objects(file_root) == []

    # A notice never changes the balance by itself (REQ-060, INV-15).
    assert balance(client, world, world.customer_a) == 50000
    assert (
        owner.execute("SELECT count(*) FROM ledger_entry WHERE shop_id = %s", (world.shop_a,)).fetchone()
        == entries_before
    )

    messages = staff_notices(owner, f"notice:{body['id']}:sent")
    assert sorted(recipient for recipient, _ in messages) == sorted(
        tg(owner, user) for user in (world.owner_a, world.manager_a, world.seller_a)
    )
    text = say("uz", "s_notice", shop="Shop A", name="Ali", amount=money("uz", 20000), balance=money("uz", 50000))
    assert text == "💵 Shop A\nAli 20\xa0000\xa0so'm to'laganini bildirdi.\nHozirgi qarzi: 50\xa0000\xa0so'm"
    notice_hex = uuid.UUID(body["id"]).hex
    for _, payload in messages:
        assert payload["text"] == text
        buttons = {b["text"]: b["callback_data"] for b in payload["reply_markup"]["inline_keyboard"][0]}
        assert buttons == {"✅ Qabul qilish": f"v2:pna:{notice_hex}", "Rad etish": f"v2:pnd:{notice_hex}"}
    logged = owner.execute(
        "SELECT actor_kind FROM activity WHERE shop_id = %s AND action = 'payment_notice.sent'", (world.shop_a,)
    ).fetchall()
    assert logged == [("customer",)]

    # The customer sees their notice; staff see it on the customer and in the shop's list.
    mine = client.get(f"{ME}/{link}", headers=as_user(world.customer_of_a)).json()
    assert [(n["id"], n["status"], n["amount"]) for n in mine["payment_notices"]] == [(body["id"], "sent", 20000)]
    detail = client.get(f"{shop(world)}/customers/{world.customer_a}", headers=as_user(world.seller_a)).json()
    assert [n["id"] for n in detail["payment_notices"]] == [body["id"]]
    other = client.get(f"{shop(world)}/customers/{world.settled_customer_a}", headers=as_user(world.seller_a)).json()
    assert other["payment_notices"] == []
    listed = client.get(f"{shop(world)}/payment-notices", headers=as_user(world.seller_a)).json()["items"]
    assert [(n["id"], n["customer_id"], n["customer_name"], n["customer_balance"], n["amount"]) for n in listed] == [
        (body["id"], str(world.customer_a), "Ali", 50000, 20000)
    ]


def test_a_receipt_is_kept_under_a_key_that_names_nobody(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    link = link_of(owner, world.customer_a)
    response = send(client, world.customer_of_a, link, 20000, JPEG, name="Ali Valiyev karta 8600.jpg")
    assert response.status_code == 201, response.text
    assert response.json()["has_receipt"] is True

    stored = files(owner, world.shop_a)
    assert len(stored) == 1
    file_id, purpose, object_key, sha256, size, mime, delete_after = stored[0]
    assert (purpose, size, mime, sha256) == ("payment_notice", len(JPEG), "image/jpeg", hashlib.sha256(JPEG).digest())
    # Kept at most until the notice has closed (14 days) and the retention has run (90 days).
    assert about(delete_after, datetime.now(UTC) + timedelta(days=104))
    assert re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{64}", object_key)
    for personal in ("Ali", "8600", "jpg", world.shop_a.hex, world.customer_a.hex, str(file_id), file_id.hex):
        assert personal not in object_key
    # Exactly the bytes that were sent, CR and LF included, and nothing else in the store.
    assert [path.read_bytes() for path in stored_objects(file_root)] == [JPEG]
    assert stored_objects(file_root)[0] == file_root / object_key
    assert owner.execute("SELECT file_id FROM payment_notice WHERE shop_id = %s", (world.shop_a,)).fetchall() == [
        (file_id,)
    ]

    messages = staff_notices(owner, f"notice:{response.json()['id']}:sent")
    assert {payload["text"] for _, payload in messages} == {
        say("uz", "s_notice_receipt", shop="Shop A", name="Ali", amount=money("uz", 20000), balance=money("uz", 50000))
    }


@pytest.mark.parametrize(
    ("content", "mime"),
    [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp"), (PDF, "application/pdf")],
    ids=["jpeg", "png", "webp", "pdf"],
)
def test_the_type_is_read_from_the_content_whatever_the_sender_declares(
    client: TestClient, world: World, owner: psycopg.Connection, content: bytes, mime: str
) -> None:
    sent = send(
        client, world.customer_of_a, link_of(owner, world.customer_a), 1000, content, name="x.exe", declared="text/html"
    )
    assert sent.status_code == 201, sent.text
    assert [row[5] for row in files(owner, world.shop_a)] == [mime]


@pytest.mark.parametrize(
    ("content", "why"),
    [
        (HTML, "type"),  # a file that lies about its type: named and declared as a JPEG
        (b"MZ\x90\x00" + bytes(64), "type"),
        (b"GIF89a" + bytes(32), "type"),
        (b"RIFF\x24\x00\x00\x00WAVEfmt " + bytes(32), "type"),  # RIFF, but not WebP
        (b"\xff\xd8", "type"),  # too short to be a JPEG
        (b" " + JPEG, "type"),  # the signature must be the very first bytes
        (b"\xff\xd8\xff" + HTML, "malformed"),  # starts like a JPEG and is something else (P36-1)
        (JPEG[:-2], "malformed"),  # a JPEG cut off before its end
        (PNG[:-4] + b"\x00\x00\x00\x00", "malformed"),  # a PNG whose last checksum is wrong
        (WEBP[:-2], "malformed"),
        (PDF.replace(b"%%EOF", b"%%EOG"), "malformed"),
        (b"", "empty"),
        (JPEG + bytes(MAX_FILE_BYTES + 1 - len(JPEG)), "too_large"),
    ],
    ids=[
        "html",
        "exe",
        "gif",
        "wav",
        "short",
        "shifted",
        "disguised",
        "cut-jpeg",
        "bad-png",
        "cut-webp",
        "bad-pdf",
        "empty",
        "one-byte-too-large",
    ],
)
def test_a_receipt_that_is_not_an_image_or_pdf_of_allowed_size_is_refused_and_nothing_is_kept(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, content: bytes, why: str
) -> None:
    refused = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, content)
    assert (refused.status_code, refused.json()["error"]["code"]) == (422, "VALIDATION"), refused.text
    assert refused.json()["error"]["fields"] == {"receipt": why}
    assert notices(owner, world.shop_a) == [] and files(owner, world.shop_a) == []
    assert stored_objects(file_root) == []
    assert sent_to_staff(owner, world.shop_a) == []


def test_a_receipt_of_exactly_the_limit_is_accepted(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    # A PDF is kept as it is, so its size in the store is the size that was sent.
    content = PDF[:-6] + b"%" + bytes(MAX_FILE_BYTES - len(PDF) - 1) + b"%%EOF\n"
    assert len(content) == MAX_FILE_BYTES
    sent = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, content)
    assert sent.status_code == 201, sent.text
    assert [row[4] for row in files(owner, world.shop_a)] == [MAX_FILE_BYTES]
    assert [path.stat().st_size for path in stored_objects(file_root)] == [MAX_FILE_BYTES]


def test_a_body_longer_than_any_receipt_is_refused_before_it_is_parsed(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    link = link_of(owner, world.customer_a)
    refused = send(client, world.customer_of_a, link, 20000, JPEG + bytes(MAX_BODY_BYTES))
    assert (refused.status_code, refused.json()["error"]["code"]) == (413, "BODY_TOO_LARGE")

    # The same without a Content-Length to announce it: the bytes are counted as they arrive.
    def chunks() -> Any:
        for _ in range(MAX_BODY_BYTES // 65536 + 2):
            yield bytes(65536)

    streamed = client.post(
        f"{ME}/{link}/payment-notices",
        content=chunks(),
        headers={**as_user(world.customer_of_a), "Content-Type": "multipart/form-data; boundary=x"},
    )
    assert (streamed.status_code, streamed.json()["error"]["code"]) == (413, "BODY_TOO_LARGE")
    assert notices(owner, world.shop_a) == [] and stored_objects(file_root) == []


@pytest.mark.parametrize("amount", [0, 99, 100_000_001, -20000, "20000", 20000.5, True, None, [20000]])
def test_the_amount_is_a_whole_number_of_sums_within_the_entry_bounds(
    client: TestClient, world: World, owner: psycopg.Connection, amount: Any
) -> None:
    refused = send(client, world.customer_of_a, link_of(owner, world.customer_a), amount)
    assert (refused.status_code, refused.json()["error"]["code"]) == (422, "VALIDATION"), refused.text
    assert "amount" in refused.json()["error"]["fields"]
    assert notices(owner, world.shop_a) == []


@pytest.mark.parametrize("amount", ["0", "99", "100000001", "-20000", "20 000", "20000.0", "2e4", "", "ming"])
def test_the_amount_of_a_form_is_checked_the_same_way(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, amount: str
) -> None:
    refused = send(client, world.customer_of_a, link_of(owner, world.customer_a), amount, JPEG)
    assert (refused.status_code, refused.json()["error"]["code"]) == (422, "VALIDATION"), refused.text
    assert notices(owner, world.shop_a) == [] and stored_objects(file_root) == []


def test_the_smallest_and_the_largest_amount_are_accepted(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    seed_entry(owner, world, world.customer_a, 2, "credit", 99_950_000, promised=today())
    assert send(client, world.customer_of_a, link, 100).status_code == 201
    assert send(client, world.customer_of_a, link, 100_000_000).status_code == 201


def test_a_notice_cannot_be_for_more_than_is_owed(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    link = link_of(owner, world.customer_a)
    refused = send(client, world.customer_of_a, link, 50001, JPEG)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "EXCEEDS_BALANCE"), refused.text
    assert notices(owner, world.shop_a) == [] and files(owner, world.shop_a) == []
    assert stored_objects(file_root) == [], "the receipt of a refused notice is not kept"
    assert send(client, world.customer_of_a, link, 50000).status_code == 201

    # Someone who owes nothing has nothing to report.
    attach_waiter(client, world, world.settled_customer_a)
    nothing_owed = send(client, world.waiter, link_of(owner, world.settled_customer_a), 100)
    assert (nothing_owed.status_code, nothing_owed.json()["error"]["code"]) == (409, "EXCEEDS_BALANCE")
    assert len(notices(owner, world.shop_a)) == 1


def test_at_most_three_notices_of_a_customer_wait_at_once(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    link = link_of(owner, world.customer_a)
    sent = [send(client, world.customer_of_a, link, 1000 + n) for n in range(3)]
    assert [response.status_code for response in sent] == [201, 201, 201]

    refused = send(client, world.customer_of_a, link, 5000, JPEG)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "PAYMENT_NOTICE_NOT_ALLOWED")
    assert refused.json()["error"]["fields"] == {"reason": "too_many_open"}
    assert len(notices(owner, world.shop_a)) == 3 and stored_objects(file_root) == []
    assert len(sent_to_staff(owner, world.shop_a)) == 9, (
        "three notices to three staff members, none for the refused one"
    )

    # A decided notice no longer counts.
    assert decline(client, world, world.seller_a, sent[0].json()["id"]).status_code == 200
    assert send(client, world.customer_of_a, link, 5000).status_code == 201
    assert send(client, world.customer_of_a, link, 6000).status_code == 409


def test_nobody_can_send_a_notice_for_an_account_that_is_not_theirs(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    link = link_of(owner, world.customer_a)
    attach_waiter(client, world, world.settled_customer_a)
    for user, used_link in (
        (world.stranger, link),
        (world.owner_a, link),  # staff of the same shop are not the customer
        (world.waiter, link),  # another customer of the same shop
        (world.owner_b, link),
        (world.customer_of_a, uuid.uuid4()),
        (world.customer_of_a, link_of(owner, world.settled_customer_a)),
    ):
        for receipt in (None, JPEG):
            response = send(client, user, used_link, 20000, receipt)
            assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND"), response.text
        # Even what would otherwise be a malformed request is answered as if nothing were there.
        garbage = client.post(
            f"{ME}/{used_link}/payment-notices",
            content=b"not json",
            headers={**as_user(user), "Content-Type": "text/plain"},
        )
        assert garbage.status_code == 404
    assert client.post(f"{ME}/{link}/payment-notices", json={"amount": 20000}).status_code == 401
    assert send(client, world.customer_of_a, "not-a-uuid", 20000).status_code == 404
    assert notices(owner, world.shop_a) == [] and files(owner, world.shop_a) == []
    assert stored_objects(file_root) == []
    assert sent_to_staff(owner, world.shop_a) == []


def test_a_request_that_is_not_a_notice_is_refused(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    path = f"{ME}/{link_of(owner, world.customer_a)}/payment-notices"
    me = as_user(world.customer_of_a)
    attempts = [
        client.post(path, content=b"amount=20000", headers={**me, "Content-Type": "text/plain"}),
        client.post(path, content=b"amount=20000", headers={**me, "Content-Type": "application/x-www-form-urlencoded"}),
        client.post(path, content=b"{", headers={**me, "Content-Type": "application/json"}),
        client.post(path, json=[20000], headers=me),
        client.post(path, json={"amount": 20000, "status": "accepted"}, headers=me),
        client.post(path, json={}, headers=me),
        client.post(path, data={"note": "x"}, files={"receipt": ("a.jpg", JPEG, "image/jpeg")}, headers=me),
        client.post(
            path, data={"amount": "20000", "status": "accepted"}, files={"receipt": ("a.jpg", JPEG)}, headers=me
        ),
        client.post(
            path,
            data={"amount": "20000"},
            files=[("receipt", ("a.jpg", JPEG, "image/jpeg")), ("receipt", ("b.jpg", JPEG, "image/jpeg"))],
            headers=me,
        ),
        client.post(path, content=b"no boundaries here", headers={**me, "Content-Type": "multipart/form-data"}),
        client.post(path, content=b"--x\r\n", headers={**me, "Content-Type": "multipart/form-data; boundary=x"}),
    ]
    for response in attempts:
        assert (response.status_code, response.json()["error"]["code"]) == (422, "VALIDATION"), response.text
    assert notices(owner, world.shop_a) == [] and stored_objects(file_root) == []


def test_a_form_without_a_receipt_is_a_notice_without_one(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    path = f"{ME}/{link_of(owner, world.customer_a)}/payment-notices"
    # httpx builds a multipart body only when there is a file part, so this form is written out by hand.
    body = b'--b\r\nContent-Disposition: form-data; name="amount"\r\n\r\n20000\r\n--b--\r\n'
    sent = client.post(
        path, content=body, headers={**as_user(world.customer_of_a), "Content-Type": "multipart/form-data; boundary=b"}
    )
    assert sent.status_code == 201, sent.text
    assert notices(owner, world.shop_a) == [(world.customer_a, 20000, "sent", False, None, None, None, False)]


def test_a_repeated_send_with_the_same_key_makes_one_notice_and_keeps_one_file(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    link = link_of(owner, world.customer_a)
    request_key = key()
    first = send(client, world.customer_of_a, link, 20000, JPEG, headers=request_key)
    again = send(client, world.customer_of_a, link, 20000, JPEG, headers=request_key)
    assert (first.status_code, again.status_code) == (201, 201)
    assert first.json() == again.json()
    assert len(notices(owner, world.shop_a)) == 1 and len(files(owner, world.shop_a)) == 1
    assert [path.read_bytes() for path in stored_objects(file_root)] == [JPEG]
    assert len(sent_to_staff(owner, world.shop_a)) == 3

    # The same key for something else is refused, and its file is not kept either.
    other = send(client, world.customer_of_a, link, 30000, PNG, headers=request_key)
    assert (other.status_code, other.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert len(stored_objects(file_root)) == 1
    bad_key = send(client, world.customer_of_a, link, 20000, headers={"Idempotency-Key": "short"})
    assert bad_key.status_code == 422 and "Idempotency-Key" in bad_key.json()["error"]["fields"]
    # Without a key every request is its own notice.
    assert send(client, world.customer_of_a, link, 20000).status_code == 201
    assert len(notices(owner, world.shop_a)) == 2


def test_without_a_file_store_a_receipt_is_refused_and_a_plain_notice_still_works(
    app_database_url: str, world: World, owner: psycopg.Connection
) -> None:
    database = Database(app_database_url)
    app = create_app(
        database.reachable, database, auth=AuthService(database, TEST_BOT_TOKEN), authenticator=HeaderAuthenticator()
    )
    link = link_of(owner, world.customer_a)
    with TestClient(app) as bare:
        refused = send(bare, world.customer_of_a, link, 20000, JPEG)
        assert (refused.status_code, refused.json()["error"]["code"]) == (503, "FILE_STORE_UNAVAILABLE")
        assert notices(owner, world.shop_a) == []
        # What the file is, is still checked first: the store is not the reason an invalid file is refused.
        assert send(bare, world.customer_of_a, link, 20000, HTML).status_code == 422
        assert send(bare, world.customer_of_a, link, 20000).status_code == 201
        bare.portal.call(database.dispose)  # type: ignore[union-attr]


def test_a_link_that_ended_while_the_request_was_on_its_way_sends_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The link is resolved before the shop's transaction opens; inside it, it is looked at again."""
    link = link_of(owner, world.customer_a)
    assert client.post(f"{ME}/{link}/disconnect", headers=as_user(world.customer_of_a)).status_code == 200

    async def resolved_a_moment_ago(*_: Any) -> tuple[uuid.UUID, uuid.UUID]:
        return world.shop_a, world.customer_a

    monkeypatch.setattr("qarz.application.payment_notices.resolve_link", resolved_a_moment_ago)
    response = send(client, world.customer_of_a, link, 20000, JPEG)
    assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert notices(owner, world.shop_a) == [] and stored_objects(file_root) == []


def test_the_storage_itself_never_decides_a_notice_twice(
    app_database_url: str, world: World, owner: psycopg.Connection
) -> None:
    notice = seed_notice(owner, world, 20000)
    owner.execute(
        "UPDATE payment_notice SET status = 'declined', decline_reason = 'Birinchi qaror', closed_at = now() "
        "WHERE id = %s",
        (notice,),
    )

    async def close_again() -> Any:
        database = Database(app_database_url)
        try:
            async with database.tenant(world.shop_a) as session:
                return await session.close_payment_notice(
                    notice,
                    status="expired",
                    payment_entry=None,
                    decline_reason=None,
                    decided_by=None,
                    now=datetime.now(UTC),
                )
        finally:
            await database.dispose()

    record = asyncio.run(close_again())
    assert (record.status, record.decline_reason) == ("declined", "Birinchi qaror")


# --- accepting (REQ-061, BR-14) ------------------------------------------------------------------------


def test_a_seller_accepts_and_a_payment_is_recorded_in_their_name(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    notice = send(client, world.customer_of_a, link, 20000, JPEG).json()["id"]

    response = accept(client, world, world.seller_a, notice)
    assert response.status_code == 200, response.text
    body = response.json()
    paid = payments(owner, world.customer_a)
    assert [(amount, author) for _, amount, author in paid] == [(20000, world.seller_a_membership)]
    entry_id = paid[0][0]
    assert (body["entry"]["id"], body["entry"]["kind"], body["entry"]["amount"]) == (str(entry_id), "payment", 20000)
    assert body["customer"]["balance"] == 30000
    assert (body["notice"]["status"], body["notice"]["recorded_amount"], body["notice"]["payment_entry_id"]) == (
        "accepted",
        20000,
        str(entry_id),
    )
    assert notices(owner, world.shop_a) == [
        (world.customer_a, 20000, "accepted", True, entry_id, None, world.seller_a_membership, True)
    ]
    assert balance(client, world, world.customer_a) == 30000
    # The receipt is now kept for 90 days from the decision, no longer.
    assert about(files(owner, world.shop_a)[0][6], datetime.now(UTC) + timedelta(days=90))

    # The customer hears of the payment like of any other, and of what became of their notice.
    customer_chat = tg(owner, world.customer_of_a)
    assert told(owner, f"entry:{entry_id}:notify") == [
        (
            customer_chat,
            {
                "text": say(
                    "uz", "n_payment", shop="Shop A", name="Ali", amount=money("uz", 20000), balance=money("uz", 30000)
                )
            },
        )
    ]
    outcome = say("uz", "n_notice_accepted", shop="Shop A", amount=money("uz", 20000))
    assert outcome == "Shop A\n20\xa0000\xa0so'm to'lov haqidagi xabaringiz qabul qilindi."
    assert told(owner, f"notice:{notice}:accepted") == [(customer_chat, {"text": outcome})]
    logged = owner.execute(
        "SELECT actor_id FROM activity WHERE shop_id = %s AND action = 'payment_notice.accepted'", (world.shop_a,)
    ).fetchall()
    assert logged == [(world.seller_a_membership,)]

    mine = client.get(f"{ME}/{link}", headers=as_user(world.customer_of_a)).json()
    assert [(n["status"], n["recorded_amount"]) for n in mine["payment_notices"]] == [("accepted", 20000)]
    assert client.get(f"{shop(world)}/payment-notices", headers=as_user(world.seller_a)).json() == {"items": []}
    detail = client.get(f"{shop(world)}/customers/{world.customer_a}", headers=as_user(world.seller_a)).json()
    assert detail["payment_notices"] == []


def test_the_outcome_reaches_the_customer_in_their_own_language(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.customer_of_a,))
    link = link_of(owner, world.customer_a)
    first = send(client, world.customer_of_a, link, 20000).json()["id"]
    second = send(client, world.customer_of_a, link, 5000).json()["id"]
    assert accept(client, world, world.owner_a, first).status_code == 200
    assert decline(client, world, world.owner_a, second, "Не поступило").status_code == 200
    assert (
        told(owner, f"notice:{first}:accepted")[0][1]["text"]
        == "Shop A\nВаше сообщение об оплате 20\xa0000\xa0сум принято."
    )
    assert told(owner, f"notice:{second}:declined")[0][1]["text"] == (
        "Shop A\nВаше сообщение об оплате 5\xa0000\xa0сум отклонено.\nПричина: Не поступило"
    )


def test_staff_may_correct_the_amount_and_the_customer_is_told_both(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    notice = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000).json()["id"]
    for bad in (99, 100_000_001, 0, "15000", 15000.5, True):
        refused = accept(client, world, world.manager_a, notice, bad)
        assert refused.status_code == 422, (bad, refused.text)
    assert notices(owner, world.shop_a)[0][2] == "sent" and payments(owner, world.customer_a) == []

    response = accept(client, world, world.manager_a, notice, 15000)
    assert response.status_code == 200, response.text
    assert [(amount, author) for _, amount, author in payments(owner, world.customer_a)] == [
        (15000, world.manager_a_membership)
    ]
    assert (response.json()["notice"]["amount"], response.json()["notice"]["recorded_amount"]) == (20000, 15000)
    assert balance(client, world, world.customer_a) == 35000
    text = told(owner, f"notice:{notice}:accepted")[0][1]["text"]
    assert text == say(
        "uz", "n_notice_corrected", shop="Shop A", amount=money("uz", 20000), recorded=money("uz", 15000)
    )
    assert text == (
        "Shop A\n20\xa0000\xa0so'm to'lov haqidagi xabaringiz qabul qilindi, "
        "lekin to'lov 15\xa0000\xa0so'm deb yozildi."
    )


def test_an_amount_above_the_balance_is_refused_and_the_notice_stays_open(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    notice = send(client, world.customer_of_a, link_of(owner, world.customer_a), 50000).json()["id"]
    assert record(client, world, world.customer_a, "payment", 20000).status_code == 201  # paid at the counter meanwhile
    before = (notices(owner, world.shop_a), payments(owner, world.customer_a))

    for amount in (None, 30001):
        refused = accept(client, world, world.seller_a, notice, amount)
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "EXCEEDS_BALANCE"), refused.text
    assert (notices(owner, world.shop_a), payments(owner, world.customer_a)) == before
    assert before[0][0][2] == "sent"
    assert told(owner, f"notice:{notice}:accepted") == []
    listed = client.get(f"{shop(world)}/payment-notices", headers=as_user(world.seller_a)).json()["items"]
    assert [(n["id"], n["customer_balance"]) for n in listed] == [(notice, 30000)]

    assert accept(client, world, world.seller_a, notice, 30000).status_code == 200
    assert balance(client, world, world.customer_a) == 0


def test_a_repeated_accept_records_one_payment(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    notice = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000).json()["id"]
    request_key = key()
    first = accept(client, world, world.seller_a, notice, **request_key)
    again = accept(client, world, world.seller_a, notice, **request_key)
    assert (first.status_code, again.status_code) == (200, 200)
    assert first.json() == again.json()
    assert len(payments(owner, world.customer_a)) == 1

    # A new request for a notice that is decided is refused, by anyone and either way.
    for user in (world.seller_a, world.owner_a):
        late = accept(client, world, user, notice)
        assert (late.status_code, late.json()["error"]["code"]) == (409, "PAYMENT_NOTICE_NOT_OPEN")
        assert late.json()["error"]["fields"] == {"reason": "accepted"}
    assert decline(client, world, world.owner_a, notice).status_code == 409
    assert len(payments(owner, world.customer_a)) == 1
    assert notices(owner, world.shop_a)[0][2] == "accepted"


def test_two_staff_accepting_at_once_record_exactly_one_payment(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    link = link_of(owner, world.customer_a)
    with another_client(app_database_url) as second:
        for attempt in range(5):
            notice = send(client, world.customer_of_a, link, 1000).json()["id"]
            barrier = threading.Barrier(2)

            def decide(pair: tuple[TestClient, uuid.UUID], notice: str = notice, wait: Any = barrier) -> int:
                wait.wait(timeout=10)
                return int(accept(pair[0], world, pair[1], notice).status_code)

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = sorted(pool.map(decide, ((client, world.seller_a), (second, world.manager_a))))
            assert statuses == [200, 409], attempt
            assert len(payments(owner, world.customer_a)) == attempt + 1
    assert balance(client, world, world.customer_a) == 45000


# --- declining -------------------------------------------------------------------------------------------


def test_declining_changes_nothing_but_the_notice_and_tells_the_customer_why(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    notice = send(client, world.customer_of_a, link, 20000, PDF).json()["id"]

    response = decline(client, world, world.seller_a, notice, "  Kartaga   pul tushmagan ")
    assert response.status_code == 200, response.text
    assert (response.json()["status"], response.json()["decline_reason"]) == ("declined", "Kartaga pul tushmagan")
    assert notices(owner, world.shop_a) == [
        (world.customer_a, 20000, "declined", True, None, "Kartaga pul tushmagan", world.seller_a_membership, True)
    ]
    assert payments(owner, world.customer_a) == [] and balance(client, world, world.customer_a) == 50000
    assert about(files(owner, world.shop_a)[0][6], datetime.now(UTC) + timedelta(days=90))

    text = say("uz", "n_notice_declined", shop="Shop A", amount=money("uz", 20000), reason="Kartaga pul tushmagan")
    assert text == "Shop A\n20\xa0000\xa0so'm to'lov haqidagi xabaringiz rad etildi.\nSabab: Kartaga pul tushmagan"
    assert told(owner, f"notice:{notice}:declined") == [(tg(owner, world.customer_of_a), {"text": text})]
    mine = client.get(f"{ME}/{link}", headers=as_user(world.customer_of_a)).json()
    assert [(n["status"], n["decline_reason"]) for n in mine["payment_notices"]] == [
        ("declined", "Kartaga pul tushmagan")
    ]

    for late in (decline(client, world, world.owner_a, notice), accept(client, world, world.owner_a, notice)):
        assert (late.status_code, late.json()["error"]["code"]) == (409, "PAYMENT_NOTICE_NOT_OPEN")
        assert late.json()["error"]["fields"] == {"reason": "declined"}
    assert payments(owner, world.customer_a) == []


@pytest.mark.parametrize("reason", ["", "  ", "ab", "x" * 301, None, 5])
def test_declining_needs_a_reason(client: TestClient, world: World, owner: psycopg.Connection, reason: Any) -> None:
    notice = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000).json()["id"]
    assert decline(client, world, world.owner_a, notice, reason).status_code == 422
    assert notices(owner, world.shop_a)[0][2] == "sent"
    assert told(owner, f"notice:{notice}:declined") == []


# --- the shop's list, and other shops ------------------------------------------------------------------


def test_staff_list_the_open_notices_of_their_own_shop_only(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    first = send(client, world.customer_of_a, link, 20000).json()["id"]
    second = send(client, world.customer_of_a, link, 5000, JPEG).json()["id"]
    for user in (world.seller_a, world.manager_a, world.owner_a):
        listed = client.get(f"{shop(world)}/payment-notices", headers=as_user(user)).json()["items"]
        assert [(n["id"], n["amount"], n["has_receipt"]) for n in listed] == [
            (first, 20000, False),
            (second, 5000, True),
        ]
    assert client.get(f"/api/v1/shops/{world.shop_b}/payment-notices", headers=as_user(world.owner_b)).json() == {
        "items": []
    }

    # Shop A's notice does not exist through shop B, for shop B's own owner.
    for response in (
        accept(client, world, world.owner_b, first),
        client.post(
            f"/api/v1/shops/{world.shop_b}/payment-notices/{first}/accept", headers={**as_user(world.owner_b), **key()}
        ),
        client.post(
            f"/api/v1/shops/{world.shop_b}/payment-notices/{first}/decline",
            json={"reason": "Begona do'kon"},
            headers={**as_user(world.owner_b), **key()},
        ),
        receipt(
            client, f"/api/v1/shops/{world.shop_b}/payment-notices/{second}/receipt", headers=as_user(world.owner_b)
        ),
        accept(client, world, world.owner_a, uuid.uuid4()),
        decline(client, world, world.owner_a, uuid.uuid4()),
    ):
        assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND"), response.text
    assert [row[2] for row in notices(owner, world.shop_a)] == ["sent", "sent"]

    assert decline(client, world, world.manager_a, first).status_code == 200
    remaining = client.get(f"{shop(world)}/payment-notices", headers=as_user(world.seller_a)).json()["items"]
    assert [n["id"] for n in remaining] == [second]


# --- the receipt ---------------------------------------------------------------------------------------


def test_staff_download_the_receipt_as_an_attachment(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    notice = send(client, world.customer_of_a, link, 20000, JPEG, name="evil.html", declared="text/html").json()["id"]
    for user in (world.seller_a, world.manager_a, world.owner_a):
        response = receipt(client, f"{shop(world)}/payment-notices/{notice}/receipt", headers=as_user(user))
        assert response.status_code == 200, response.text
        assert response.content == JPEG
        assert response.headers["content-type"] == "image/jpeg"
        name = f"receipt-{files(owner, world.shop_a)[0][0].hex[:8]}.jpg"
        assert response.headers["content-disposition"] == f'attachment; filename="{name}"'
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "private, no-store"

    plain = send(client, world.customer_of_a, link, 5000).json()["id"]
    missing = receipt(client, f"{shop(world)}/payment-notices/{plain}/receipt", headers=as_user(world.owner_a))
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "NOT_FOUND")
    # Not for the customer through the staff route, not for anyone without a session, and at no other address.
    for headers in (as_user(world.customer_of_a), as_user(world.stranger), as_user(world.owner_b)):
        assert receipt(client, f"{shop(world)}/payment-notices/{notice}/receipt", headers=headers).status_code == 404
    assert receipt(client, f"{shop(world)}/payment-notices/{notice}/receipt").status_code == 401
    object_key = files(owner, world.shop_a)[0][2]
    for path in (f"/{object_key}", f"/files/{object_key}", f"/api/v1/files/{object_key}"):
        assert client.get(path, headers=as_user(world.owner_a)).status_code == 404


def test_a_file_of_one_shop_cannot_be_read_through_another(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, JPEG)
    file_of_a = files(owner, world.shop_a)[0][0]
    # Shop B somehow holds a notice that points at shop A's file: row-level security still hides the file.
    customer_b = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Begona', 'begona')",
        (customer_b, world.shop_b),
    )
    forged = uuid.uuid4()
    owner.execute(
        "INSERT INTO payment_notice (id, shop_id, customer_id, amount) VALUES (%s, %s, %s, 1000)",
        (forged, world.shop_b, customer_b),
    )
    owner.execute("UPDATE payment_notice SET file_id = NULL WHERE shop_id = %s", (world.shop_a,))
    owner.execute("UPDATE payment_notice SET file_id = %s WHERE id = %s", (file_of_a, forged))

    response = receipt(
        client, f"/api/v1/shops/{world.shop_b}/payment-notices/{forged}/receipt", headers=as_user(world.owner_b)
    )
    assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert JPEG not in response.content


def test_a_receipt_whose_object_is_gone_or_changed_is_not_served(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    notice = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, JPEG).json()["id"]
    path = f"{shop(world)}/payment-notices/{notice}/receipt"
    stored_objects(file_root)[0].write_bytes(HTML)  # something replaced the object behind the service's back
    assert receipt(client, path, headers=as_user(world.owner_a)).status_code == 404
    stored_objects(file_root)[0].unlink()
    assert receipt(client, path, headers=as_user(world.owner_a)).status_code == 404


# --- expiry (domain model: "Sent -> Expired after 14 days") --------------------------------------------


def test_a_notice_nobody_decided_in_fourteen_days_is_no_longer_listed_or_acceptable(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    in_time = seed_notice(owner, world, 1000, days_ago=13.99)
    stale = seed_notice(owner, world, 2000, days_ago=14.01)

    listed = client.get(f"{shop(world)}/payment-notices", headers=as_user(world.seller_a)).json()["items"]
    assert [n["id"] for n in listed] == [str(in_time)]
    detail = client.get(f"{shop(world)}/customers/{world.customer_a}", headers=as_user(world.seller_a)).json()
    assert [n["id"] for n in detail["payment_notices"]] == [str(in_time)]
    mine = client.get(f"{ME}/{link}", headers=as_user(world.customer_of_a)).json()["payment_notices"]
    assert {n["id"]: n["status"] for n in mine} == {str(in_time): "sent", str(stale): "expired"}

    for response in (accept(client, world, world.owner_a, stale), decline(client, world, world.owner_a, stale)):
        assert (response.status_code, response.json()["error"]["code"]) == (409, "PAYMENT_NOTICE_NOT_OPEN")
        assert response.json()["error"]["fields"] == {"reason": "expired"}
    assert payments(owner, world.customer_a) == []
    assert accept(client, world, world.owner_a, in_time).status_code == 200


def test_a_stale_notice_does_not_count_against_the_limit_and_is_marked_when_the_customer_sends_again(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    file_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime, delete_after) "
        "VALUES (%s, %s, 'payment_notice', 'ab/abcd', %s, 3, 'image/png', %s)",
        (file_id, world.shop_a, b"\x00" * 32, datetime.now(UTC) + timedelta(days=80)),
    )
    stale = [seed_notice(owner, world, 1000 + n, days_ago=15 + n) for n in range(2)]
    stale.append(seed_notice(owner, world, 3000, days_ago=20, file_id=file_id))
    other = seed_notice(owner, world, 100, days_ago=30, customer=world.settled_customer_a)

    assert send(client, world.customer_of_a, link, 20000).status_code == 201
    rows = owner.execute(
        "SELECT id, status, closed_at FROM payment_notice WHERE id = ANY(%s)", ([*stale, other],)
    ).fetchall()
    by_id = {row[0]: (row[1], row[2]) for row in rows}
    for notice_id in stale:
        assert by_id[notice_id][0] == "expired" and about(by_id[notice_id][1], datetime.now(UTC))
    assert by_id[other] == ("sent", None), "only what the request touched is marked"
    # The receipt of an expired notice is due 90 days after it closed; the date only ever moves forward.
    assert files(owner, world.shop_a)[0][6] < datetime.now(UTC) + timedelta(days=80, minutes=1)


def test_the_hourly_job_can_mark_a_shops_stale_notices(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    file_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime, delete_after) "
        "VALUES (%s, %s, 'payment_notice', 'ab/abce', %s, 3, 'image/png', %s)",
        (file_id, world.shop_a, b"\x00" * 32, datetime.now(UTC) + timedelta(days=200)),
    )
    decided = send(client, world.customer_of_a, link_of(owner, world.customer_a), 3000).json()["id"]
    assert decline(client, world, world.owner_a, decided).status_code == 200
    stale = seed_notice(owner, world, 1000, days_ago=14.01, file_id=file_id)
    stale_other = seed_notice(owner, world, 100, days_ago=40, customer=world.settled_customer_a)
    fresh = seed_notice(owner, world, 2000, days_ago=13.99)

    marked = with_services(worker_database_url, file_root, lambda service, _: service.expire_due(world.shop_a))
    assert marked == 2
    statuses = dict(
        owner.execute("SELECT id, status FROM payment_notice WHERE shop_id = %s", (world.shop_a,)).fetchall()
    )
    assert statuses == {stale: "expired", stale_other: "expired", fresh: "sent", uuid.UUID(decided): "declined"}
    assert about(files(owner, world.shop_a)[0][6], datetime.now(UTC) + timedelta(days=90))
    assert with_services(worker_database_url, file_root, lambda service, _: service.expire_due(world.shop_a)) == 0
    assert with_services(worker_database_url, file_root, lambda service, _: service.expire_due(world.shop_b)) == 0


# --- subscription state (BR-29, BR-30) -----------------------------------------------------------------


def test_in_limited_mode_notices_are_still_handled(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    _subscription(owner, world, "state = 'limited'")
    link = link_of(owner, world.customer_a)
    first = send(client, world.customer_of_a, link, 20000)
    assert first.status_code == 201
    second = send(client, world.customer_of_a, link, 5000).json()["id"]
    assert accept(client, world, world.seller_a, first.json()["id"]).status_code == 200
    assert decline(client, world, world.seller_a, second).status_code == 200
    assert balance(client, world, world.customer_a) == 30000


def test_in_a_suspended_shop_nothing_is_decided_and_only_the_owner_may_look(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    notice = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, JPEG).json()["id"]
    _subscription(owner, world, "state = 'suspended'")
    for user in (world.owner_a, world.seller_a):
        for response in (accept(client, world, user, notice), decline(client, world, user, notice)):
            assert (response.status_code, response.json()["error"]["code"]) == (403, "SHOP_SUSPENDED")
    assert notices(owner, world.shop_a)[0][2] == "sent" and payments(owner, world.customer_a) == []

    for path in (f"{shop(world)}/payment-notices", f"{shop(world)}/payment-notices/{notice}/receipt"):
        assert client.get(path, headers=as_user(world.owner_a)).status_code == 200
        for user in (world.manager_a, world.seller_a):
            response = client.get(path, headers=as_user(user))
            assert (response.status_code, response.json()["error"]["code"]) == (403, "SHOP_SUSPENDED")


# --- removal of the customer's data (BR-32) ------------------------------------------------------------


def test_removal_makes_the_customers_receipts_unreachable_and_the_cleanup_deletes_them(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    customer = world.settled_customer_a
    attach_waiter(client, world, customer)
    link = link_of(owner, customer)
    assert record(client, world, customer, "credit", 30000).status_code == 201
    declined = send(client, world.waiter, link, 10000, JPEG).json()["id"]
    assert decline(client, world, world.seller_a, declined).status_code == 200
    accepted = send(client, world.waiter, link, 30000, PDF).json()["id"]
    # Someone else's receipt, which must survive.
    kept = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, PNG).json()["id"]
    assert len(stored_objects(file_root)) == 3
    assert accept(client, world, world.seller_a, accepted).status_code == 200  # the debt is settled

    done = client.post(f"{ME}/{link}/removal", headers=as_user(world.waiter))
    assert (done.status_code, done.json()["removed"]) == (200, True)
    assert customer_row(owner, customer)[3] == "anonymized"

    # At once: no notice of theirs points at a file any more, and both files are due for deletion now.
    assert owner.execute(
        "SELECT count(*) FROM payment_notice WHERE customer_id = %s AND file_id IS NOT NULL", (customer,)
    ).fetchone() == (0,)
    due = [row for row in files(owner, world.shop_a) if row[6] <= datetime.now(UTC)]
    assert sorted(row[5] for row in due) == ["application/pdf", "image/jpeg"]
    for notice in (declined, accepted):
        response = receipt(client, f"{shop(world)}/payment-notices/{notice}/receipt", headers=as_user(world.owner_a))
        assert response.status_code == 404
    # The amounts and the decisions stay, without the person.
    assert [row[1:3] for row in notices(owner, world.shop_a) if row[0] == customer] == [
        (10000, "declined"),
        (30000, "accepted"),
    ]

    # The cleanup removes the objects and their rows, and nothing that is not due.
    removed = with_services(
        worker_database_url,
        file_root,
        lambda _, kept_files: kept_files.purge_due_receipts(world.shop_a, datetime.now(UTC)),
    )
    assert removed == 2
    assert [path.read_bytes() for path in stored_objects(file_root)] == [PNG]
    assert [row[5] for row in files(owner, world.shop_a)] == ["image/png"]
    assert (
        receipt(client, f"{shop(world)}/payment-notices/{kept}/receipt", headers=as_user(world.owner_a)).content == PNG
    )
    again = with_services(
        worker_database_url,
        file_root,
        lambda _, kept_files: kept_files.purge_due_receipts(world.shop_a, datetime.now(UTC)),
    )
    assert again == 0


def test_the_payment_that_settles_the_debt_of_someone_who_asked_for_removal_takes_their_receipt_with_it(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    link = link_of(owner, world.customer_a)
    asked = client.post(f"{ME}/{link}/removal", headers=as_user(world.customer_of_a))
    assert asked.json() == {"removed": False, "waiting_for_balance": 50000}
    notice = send(client, world.customer_of_a, link, 50000, JPEG).json()["id"]
    customer_chat = tg(owner, world.customer_of_a)

    response = accept(client, world, world.owner_a, notice)
    assert response.status_code == 200, response.text
    assert customer_row(owner, world.customer_a)[3] == "anonymized"
    entry_id = payments(owner, world.customer_a)[0][0]
    assert notices(owner, world.shop_a) == [
        (world.customer_a, 50000, "accepted", False, entry_id, None, world.owner_a_membership, True)
    ]
    # Due at once, not 90 days from now: the removal outranks the retention period.
    assert files(owner, world.shop_a)[0][6] <= datetime.now(UTC)
    assert (
        receipt(client, f"{shop(world)}/payment-notices/{notice}/receipt", headers=as_user(world.owner_a)).status_code
        == 404
    )
    # They were still told what became of their notice.
    assert [recipient for recipient, _ in told(owner, f"notice:{notice}:accepted")] == [customer_chat]
    assert client.get(f"{shop(world)}/payment-notices", headers=as_user(world.owner_a)).json() == {"items": []}


def test_an_open_notice_of_a_removed_customer_is_not_offered_to_staff(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    notice = send(client, world.customer_of_a, link, 20000).json()["id"]
    assert record(client, world, world.customer_a, "payment", 50000).status_code == 201
    assert client.post(f"{ME}/{link}/removal", headers=as_user(world.customer_of_a)).json()["removed"] is True

    assert client.get(f"{shop(world)}/payment-notices", headers=as_user(world.owner_a)).json() == {"items": []}
    refused = accept(client, world, world.owner_a, notice)
    assert (refused.status_code, refused.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert len(payments(owner, world.customer_a)) == 1


def test_the_cleanup_leaves_what_is_not_due_and_other_shops_alone(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    notice = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, JPEG).json()["id"]
    now = datetime.now(UTC)

    def purge(shop_id: uuid.UUID, moment: datetime) -> int:
        return int(
            with_services(worker_database_url, file_root, lambda _, kept: kept.purge_due_receipts(shop_id, moment))
        )

    assert purge(world.shop_a, now + timedelta(days=103)) == 0
    assert purge(world.shop_b, now + timedelta(days=200)) == 0, "another shop's cleanup cannot see the file"
    assert len(stored_objects(file_root)) == 1 and len(files(owner, world.shop_a)) == 1

    assert purge(world.shop_a, now + timedelta(days=105)) == 1
    assert stored_objects(file_root) == [] and files(owner, world.shop_a) == []
    assert notices(owner, world.shop_a)[0][3] is False, "the notice stays, without its receipt"
    assert (
        receipt(client, f"{shop(world)}/payment-notices/{notice}/receipt", headers=as_user(world.owner_a)).status_code
        == 404
    )


# --- when the file store fails ---------------------------------------------------------------------------


class FailingStore:
    """A store in which chosen operations fail the way an unreachable S3 service would."""

    def __init__(self, inner: FilesystemFileStore, *failing: str) -> None:
        self.inner, self.failing = inner, set(failing)

    async def put(self, key: str, data: bytes, mime: str) -> None:
        if "put" in self.failing:
            raise FileStoreError("ConnectionRefusedError")
        await self.inner.put(key, data, mime)

    async def get(self, key: str) -> bytes:
        if "get" in self.failing:
            raise FileStoreError("TimeoutError")
        return await self.inner.get(key)

    async def delete(self, key: str) -> None:
        if "delete" in self.failing:
            raise FileStoreError("TimeoutError")
        await self.inner.delete(key)


@contextmanager
def client_with_store(app_database_url: str, store: Any) -> Iterator[TestClient]:
    database = Database(app_database_url)
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        authenticator=HeaderAuthenticator(),
        file_store=store,
        secrets_key=TEST_SECRETS_KEY,
    )
    with TestClient(app) as test_client:
        yield test_client
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]


def test_a_store_that_cannot_be_written_to_refuses_the_notice_and_one_that_cannot_be_read_the_download(
    app_database_url: str, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    link = link_of(owner, world.customer_a)
    with client_with_store(app_database_url, FailingStore(FilesystemFileStore(file_root), "put")) as down:
        refused = send(down, world.customer_of_a, link, 20000, JPEG)
        assert (refused.status_code, refused.json()["error"]["code"]) == (503, "FILE_STORE_UNAVAILABLE")
        assert (
            refused.json()["error"]["message"]
            == "Fayllarni saqlash hozir ishlamayapti. Birozdan keyin qayta urinib ko'ring."
        )
    assert notices(owner, world.shop_a) == [] and files(owner, world.shop_a) == []
    assert sent_to_staff(owner, world.shop_a) == []

    with client_with_store(app_database_url, FailingStore(FilesystemFileStore(file_root), "get")) as unreadable:
        notice = send(unreadable, world.customer_of_a, link, 20000, JPEG).json()["id"]
        response = receipt(
            unreadable, f"{shop(world)}/payment-notices/{notice}/receipt", headers=as_user(world.owner_a)
        )
        assert (response.status_code, response.json()["error"]["code"]) == (503, "FILE_STORE_UNAVAILABLE")
        assert JPEG not in response.content


def test_a_receipt_is_not_left_in_the_store_when_the_notice_could_not_be_recorded(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store is not transactional: what was staged for a transaction that failed is removed again."""
    staged_then: list[int] = []

    async def fail(self: Any, **_: Any) -> Any:
        staged_then.append(len(stored_objects(file_root)))
        raise RuntimeError("the database went away")

    monkeypatch.setattr(PgTenantSession, "add_payment_notice", fail)
    # The failure is not the caller's to see: it is answered as a server error and logged.
    response = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, JPEG)
    assert response.status_code == 500
    assert "went away" not in response.text
    assert staged_then == [1], "the receipt had been stored before the transaction failed"
    assert stored_objects(file_root) == []
    assert notices(owner, world.shop_a) == [] and files(owner, world.shop_a) == []


def test_a_receipt_the_cleanup_could_not_delete_is_found_again_by_the_next_run(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, JPEG)
    later = datetime.now(UTC) + timedelta(days=200)

    async def purge(store: Any) -> int:
        database = Database(worker_database_url)
        try:
            return await FileService(database, store).purge_due_receipts(world.shop_a, later)
        finally:
            await database.dispose()

    assert asyncio.run(purge(FailingStore(FilesystemFileStore(file_root), "delete"))) == 0
    assert len(files(owner, world.shop_a)) == 1 and len(stored_objects(file_root)) == 1
    assert notices(owner, world.shop_a)[0][3] is True, "the row stays while its object does"
    assert asyncio.run(purge(FilesystemFileStore(file_root))) == 1
    assert files(owner, world.shop_a) == [] and stored_objects(file_root) == []
    with pytest.raises(FileStoreUnavailable):
        asyncio.run(purge(None))


# --- erasing a shop (REQ-048, BR-25) -----------------------------------------------------------------------


def test_erasing_a_shop_deletes_its_receipts_from_the_store_and_waits_when_it_cannot(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, JPEG)
    send(client, world.customer_of_a, link_of(owner, world.customer_a), 5000, PDF)
    # A receipt of another shop, which must survive.
    kept = FilesystemFileStore(file_root)
    asyncio.run(kept.put("zz/other-shop", PNG, "image/png"))
    owner.execute(
        "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime) "
        "VALUES (gen_random_uuid(), %s, 'payment_notice', 'zz/other-shop', %s, %s, 'image/png')",
        (world.shop_b, hashlib.sha256(PNG).digest(), len(PNG)),
    )
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() - interval '1 second' WHERE id = %s",
        (world.shop_a,),
    )

    def erase(store: Any) -> int:
        async def run() -> int:
            database = Database(worker_database_url)
            try:
                return await ShopDeletionService(database, files=store).erase_due()
            finally:
                await database.dispose()

        return asyncio.run(run())

    def status() -> Any:
        return owner.execute("SELECT status FROM shop WHERE id = %s", (world.shop_a,)).fetchone()

    # No store, or a store that fails: the shop is not erased, so that the objects are not orphaned.
    for store in (None, FailingStore(kept, "delete")):
        erase(store)
        assert status() == ("deletion_pending",)
        assert len(files(owner, world.shop_a)) == 2 and len(stored_objects(file_root)) == 3

    erase(kept)
    assert status() == ("erased",)
    assert files(owner, world.shop_a) == [] and notices(owner, world.shop_a) == []
    assert [path.read_bytes() for path in stored_objects(file_root)] == [PNG], "only the other shop's receipt is left"
    assert len(files(owner, world.shop_b)) == 1


def test_a_shop_without_files_is_erased_without_a_file_store(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str
) -> None:
    send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000)
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() - interval '1 second' WHERE id = %s",
        (world.shop_a,),
    )

    async def run() -> int:
        database = Database(worker_database_url)
        try:
            return await ShopDeletionService(database).erase_due()
        finally:
            await database.dispose()

    asyncio.run(run())
    assert owner.execute("SELECT status FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == ("erased",)
    assert notices(owner, world.shop_a) == []


# --- security review: P36-1, P36-2, P36-3 -------------------------------------------------------------------


def test_what_is_kept_of_a_photo_has_no_metadata_and_nothing_after_its_end(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    """P36-1: the whole file is read through and put together again from the parts an image needs."""
    sent = jpeg(EXIF, COMMENT, trailing=b"<script>alert(1)</script>PK\x03\x04 another file")
    assert b"GPSLatitude" in sent and b"8600 1234" in sent
    notice = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, sent).json()["id"]

    assert [path.read_bytes() for path in stored_objects(file_root)] == [JPEG]
    stored = files(owner, world.shop_a)[0]
    assert (stored[3], stored[4], stored[5]) == (hashlib.sha256(JPEG).digest(), len(JPEG), "image/jpeg")
    served = receipt(client, f"{shop(world)}/payment-notices/{notice}/receipt", headers=as_user(world.owner_a)).content
    assert served == JPEG
    for removed in (b"GPSLatitude", b"8600 1234", b"script", b"PK\x03\x04"):
        assert removed not in served


class WatchedStore:
    """A file store that counts its calls and notes how many shop transactions were open during each."""

    def __init__(self, inner: FilesystemFileStore, open_transactions: Callable[[], int]) -> None:
        self.inner, self.open_transactions = inner, open_transactions
        self.calls: list[tuple[str, int]] = []

    async def put(self, key: str, data: bytes, mime: str) -> None:
        self.calls.append(("put", self.open_transactions()))
        await self.inner.put(key, data, mime)

    async def get(self, key: str) -> bytes:
        self.calls.append(("get", self.open_transactions()))
        return await self.inner.get(key)

    async def delete(self, key: str) -> None:
        self.calls.append(("delete", self.open_transactions()))
        await self.inner.delete(key)


class WatchedDatabase:
    """The real database, counting the shop transactions that are open at any moment."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self.open = 0

    def tenant(self, shop_id: uuid.UUID) -> Any:
        watched, inner = self, self._database.tenant(shop_id)

        class Counted:
            async def __aenter__(self) -> Any:
                session = await inner.__aenter__()
                watched.open += 1
                return session

            async def __aexit__(self, *error: Any) -> Any:
                watched.open -= 1
                return await inner.__aexit__(*error)

        return Counted()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._database, name)


@contextmanager
def watched_client(app_database_url: str, file_root: Path) -> Iterator[tuple[TestClient, WatchedStore]]:
    database = WatchedDatabase(Database(app_database_url))
    store = WatchedStore(FilesystemFileStore(file_root), lambda: database.open)
    app = create_app(
        database.reachable,
        database,  # type: ignore[arg-type]
        auth=AuthService(database, TEST_BOT_TOKEN),  # type: ignore[arg-type]
        authenticator=HeaderAuthenticator(),
        file_store=store,
        secrets_key=TEST_SECRETS_KEY,
    )
    with TestClient(app) as test_client:
        yield test_client, store
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]


def test_the_file_store_is_never_called_while_a_shop_transaction_is_open(
    app_database_url: str, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    """P36-2: a slow store must not hold database connections. Reading, writing and discarding alike."""
    link = link_of(owner, world.customer_a)
    with watched_client(app_database_url, file_root) as (watched, store):
        notice = send(watched, world.customer_of_a, link, 20000, JPEG).json()["id"]
        path = f"{shop(world)}/payment-notices/{notice}/receipt"
        assert receipt(watched, path, headers=as_user(world.seller_a)).content == JPEG
        # A notice refused under the lock after its receipt was stored: the key is reused for another request.
        request_key = key()
        assert send(watched, world.customer_of_a, link, 1000, PNG, headers=request_key).status_code == 201
        assert send(watched, world.customer_of_a, link, 2000, PDF, headers=request_key).status_code == 409
        assert [name for name, _ in store.calls] == ["put", "get", "put", "put", "delete"]
        assert [during for _, during in store.calls] == [0, 0, 0, 0, 0]


def test_a_notice_that_will_be_refused_never_reaches_the_file_store(
    app_database_url: str, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    """P36-3: the limits are checked before the upload, not only after it."""
    link = link_of(owner, world.customer_a)
    with watched_client(app_database_url, file_root) as (watched, store):
        above = send(watched, world.customer_of_a, link, 50001, JPEG)
        assert (above.status_code, above.json()["error"]["code"]) == (409, "EXCEEDS_BALANCE")
        for amount in (1000, 2000, 3000):
            assert send(watched, world.customer_of_a, link, amount).status_code == 201
        for _ in range(5):
            too_many = send(watched, world.customer_of_a, link, 4000, JPEG)
            assert (too_many.status_code, too_many.json()["error"]["code"]) == (409, "PAYMENT_NOTICE_NOT_ALLOWED")
        assert send(watched, world.customer_of_a, link, 4000, HTML).status_code == 422
        assert send(watched, world.stranger, link, 4000, JPEG).status_code == 404
        assert store.calls == [], "nothing was uploaded, so nothing had to be deleted either"
    assert len(notices(owner, world.shop_a)) == 3 and files(owner, world.shop_a) == []


# --- the body limit: one route may carry a receipt ---------------------------------------------------------


def test_only_the_notice_route_may_carry_more_than_the_general_limit(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    link = link_of(owner, world.customer_a)
    two_megabytes = PDF[:-6] + b"%" + bytes(2 * 1024 * 1024) + b"%%EOF\n"
    sent = send(client, world.customer_of_a, link, 20000, two_megabytes)
    assert sent.status_code == 201, sent.text
    assert [path.stat().st_size for path in stored_objects(file_root)] == [len(two_megabytes)]

    # The same bytes to any other route are refused before anything is read, whoever sends them.
    notice = sent.json()["id"]
    for method, path in (
        ("POST", f"{shop(world)}/payment-notices/{notice}/accept"),
        ("POST", f"{shop(world)}/payment-notices/{notice}/decline"),
        ("POST", f"{ME}/{link}/disputes"),
        ("POST", f"{ME}/{link}/payment-notices/"),
        ("POST", f"{ME}/{link}/payment-notices/extra"),
        ("POST", f"{ME}/not-a-link/payment-notices"),
        ("PUT", f"{ME}/{link}/payment-notices"),
        ("POST", "/tg/webhook"),
    ):
        refused = client.request(
            method,
            path,
            content=two_megabytes,
            headers={**as_user(world.owner_a), **key(), "Content-Type": "text/plain"},
        )
        assert (refused.status_code, refused.json()["error"]["code"]) == (413, "BODY_TOO_LARGE"), path
    assert notices(owner, world.shop_a)[0][2] == "sent"


# --- the hourly job ----------------------------------------------------------------------------------------


def test_the_worker_expires_stale_notices_and_deletes_due_receipts_once_an_hour(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    link = link_of(owner, world.customer_a)
    # A receipt whose customer's data was removed: due for deletion at once (BR-32).
    attach_waiter(client, world, world.settled_customer_a)
    other_link = link_of(owner, world.settled_customer_a)
    assert record(client, world, world.settled_customer_a, "credit", 1000).status_code == 201
    removed_notice = send(client, world.waiter, other_link, 1000, PNG).json()["id"]
    assert accept(client, world, world.owner_a, removed_notice).status_code == 200
    assert client.post(f"{ME}/{other_link}/removal", headers=as_user(world.waiter)).json()["removed"] is True
    # A receipt that is not due, and a notice that waited too long.
    send(client, world.customer_of_a, link, 20000, JPEG)
    stale = seed_notice(owner, world, 1000, days_ago=14.01)
    assert len(stored_objects(file_root)) == 2

    # Far in the future and at night in Tashkent: no other job of the scheduler has work then, and no
    # earlier test has used the hour. The receipt that must survive is given a deadline beyond it.
    moment = datetime(2083, 3, 3, 21, 30, tzinfo=UTC)
    owner.execute(
        "UPDATE stored_file SET delete_after = '2200-01-01' WHERE shop_id = %s AND mime = 'image/jpeg'", (world.shop_a,)
    )

    def tick(minutes: int) -> None:
        clock = lambda: moment + timedelta(minutes=minutes)  # noqa: E731

        async def run() -> None:
            database = Database(worker_database_url)
            try:
                service = PaymentNoticeService(database, FileService(database, FilesystemFileStore(file_root)), clock)
                await Scheduler(database, ReminderService(database, clock), clock, notices=service).tick()
            finally:
                await database.dispose()

        asyncio.run(run())

    def status(notice: uuid.UUID) -> Any:
        return owner.execute("SELECT status FROM payment_notice WHERE id = %s", (notice,)).fetchone()

    tick(0)
    assert status(stale) == ("expired",)
    assert [path.read_bytes() for path in stored_objects(file_root)] == [JPEG], "only the receipt that was due is gone"
    assert [row[5] for row in files(owner, world.shop_a)] == ["image/jpeg"]

    # Later in the same hour nothing is done again; in the next hour it is.
    later = seed_notice(owner, world, 2000, days_ago=14.01)
    tick(2)
    assert status(later) == ("sent",)
    tick(61)
    assert status(later) == ("expired",)
    assert len(stored_objects(file_root)) == 1


def test_the_hourly_job_looks_only_at_shops_that_have_work(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str
) -> None:
    now = datetime.now(UTC)

    def shops() -> set[uuid.UUID]:
        async def run() -> list[uuid.UUID]:
            database = Database(worker_database_url)
            try:
                async with database.platform() as session:
                    return await session.shops_with_receipt_work(now - timedelta(days=14), now)
            finally:
                await database.dispose()

        return set(asyncio.run(run())) & {world.shop_a, world.shop_b}

    send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, JPEG)
    seed_notice(owner, world, 1000, days_ago=13.9)
    assert shops() == set(), "a fresh notice and a receipt that is not due are no work"
    stale = seed_notice(owner, world, 1000, days_ago=14.1)
    assert shops() == {world.shop_a}
    owner.execute("UPDATE payment_notice SET status = 'declined', closed_at = now() WHERE id = %s", (stale,))
    assert shops() == set()
    owner.execute("UPDATE stored_file SET delete_after = %s WHERE shop_id = %s", (now, world.shop_a))
    assert shops() == {world.shop_a}


def test_a_repeat_of_a_notice_already_sent_is_answered_from_the_record_even_when_a_new_one_would_be_refused(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    link = link_of(owner, world.customer_a)
    request_key = key()
    for amount in (1000, 2000):
        assert send(client, world.customer_of_a, link, amount).status_code == 201
    third = send(client, world.customer_of_a, link, 3000, JPEG, headers=request_key)
    assert third.status_code == 201
    # Three are waiting now, so a new notice would be refused; this is not a new one.
    again = send(client, world.customer_of_a, link, 3000, JPEG, headers=request_key)
    assert (again.status_code, again.json()) == (201, third.json())
    assert len(notices(owner, world.shop_a)) == 3 and len(stored_objects(file_root)) == 1
