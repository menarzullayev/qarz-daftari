"""Paying the subscription by card transfer: the owner sends a receipt, administrators and the review
group are told, an administrator approves or rejects (story S17.2; REQ-054, REQ-055; BR-27; ADR-019).

No real payment is involved anywhere: the shops, the receipts and the card number are test data.
"""

import asyncio
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from qarz.application.admin_access import AdminAccess
from qarz.application.auth import AuthService
from qarz.application.chat_texts import day, money, say
from qarz.application.files import FileService
from qarz.application.payment_notices import PaymentNoticeService
from qarz.application.shop_deletion import ShopDeletionService
from qarz.domain.files import MAX_FILE_BYTES
from qarz.domain.subscription import add_months
from qarz.domain.subscription_receipts import MAX_AMOUNT, MIN_AMOUNT
from qarz.infrastructure.db import _MEASURE_NAMESPACE, Database
from qarz.infrastructure.file_store import FilesystemFileStore
from qarz.interface.http import create_app

from ..receipt_samples import EXIF, HTML, JPEG, PDF, PNG, jpeg, segment
from .conftest import (
    ADMIN_API,
    TEST_BOT_TOKEN,
    TEST_SECRETS_KEY,
    AdminEnv,
    HeaderAuthenticator,
    World,
    as_user,
    elevate,
    make_admin,
    stored_objects,
)
from .test_disputes import tg

pytestmark = pytest.mark.db

TASHKENT = ZoneInfo("Asia/Tashkent")
RECEIPTS = f"{ADMIN_API}/receipts"
CARD = "8600123412341234"


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"receipt-{uuid.uuid4().hex}"}


def path(shop_id: uuid.UUID) -> str:
    return f"/api/v1/shops/{shop_id}/subscription/receipts"


def submit(
    client: TestClient,
    user: uuid.UUID,
    shop_id: uuid.UUID,
    amount: Any = 100_000,
    months: Any = 1,
    content: bytes | None = JPEG,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    files = None if content is None else {"receipt": ("chek.jpg", content, "image/jpeg")}
    data = {name: str(value) for name, value in (("amount", amount), ("months", months)) if value is not None}
    sent = {**as_user(user), **(key() if headers is None else headers)}
    if files is None:
        # A form without a file part still has to be a multipart form.
        return client.post(path(shop_id), data=data, files={"x": ("", b"")}, headers=sent)
    return client.post(path(shop_id), data=data, files=files, headers=sent)


def sent_ok(client: TestClient, user: uuid.UUID, shop_id: uuid.UUID, *args: Any, **kwargs: Any) -> str:
    response = submit(client, user, shop_id, *args, **kwargs)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def rows(owner: psycopg.Connection, shop_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT stated_amount, stated_months, status, months, reject_reason, decided_by, file_id IS NOT NULL "
        "FROM subscription_receipt WHERE shop_id = %s ORDER BY created_at, id",
        (shop_id,),
    ).fetchall()


def files_of(owner: psycopg.Connection, shop_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT purpose, mime, delete_after FROM stored_file WHERE shop_id = %s ORDER BY created_at, id", (shop_id,)
    ).fetchall()


def subscription(owner: psycopg.Connection, shop_id: uuid.UUID) -> Any:
    return owner.execute(
        "SELECT state, trial_ends, paid_through, prior_state FROM subscription WHERE shop_id = %s", (shop_id,)
    ).fetchone()


def set_subscription(owner: psycopg.Connection, shop_id: uuid.UUID, state: str, **dates: Any) -> None:
    owner.execute(
        "UPDATE subscription SET state = %s, trial_ends = %s, paid_through = %s, prior_state = %s WHERE shop_id = %s",
        (state, dates.get("trial_ends"), dates.get("paid_through"), dates.get("prior_state"), shop_id),
    )


def announced(owner: psycopg.Connection, receipt: str) -> dict[str, str]:
    """Who was told that the receipt waits, and in what words."""
    return {
        str(row[0]): str(row[1]["text"])
        for row in owner.execute(
            "SELECT recipient, payload FROM outbox_message WHERE dedupe_key LIKE %s", (f"subreceipt:{receipt}:new:%",)
        ).fetchall()
    }


def decided_notice(owner: psycopg.Connection, receipt: str) -> list[tuple[str, str]]:
    return [
        (str(row[0]), str(row[1]["text"]))
        for row in owner.execute(
            "SELECT recipient, payload FROM outbox_message WHERE dedupe_key = %s", (f"subreceipt:{receipt}:decided",)
        ).fetchall()
    ]


def setting(owner: psycopg.Connection, admin: uuid.UUID, name: str, value: Any) -> None:
    """A platform setting as an administrator would have stored it; removed again after the test."""
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES (%s, %s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by",
        (name, Jsonb(value), str(admin)),
    )


def unique_image(*metadata: bytes) -> tuple[bytes, bytes]:
    """An image no other test has sent, and the same image with other metadata around it."""
    mark = segment(0xE2, b"ICC_PROFILE\x00\x01\x01" + uuid.uuid4().bytes + bytes(8))
    return jpeg(keep=mark), jpeg(EXIF, keep=mark)


def today(env: AdminEnv) -> date:
    return env.clock.now().astimezone(TASHKENT).date()


def measures(owner: psycopg.Connection, receipt: str) -> list[tuple[Any, ...]]:
    """The measurement rows about one receipt, which they name only by a value derived from its identifier."""
    return owner.execute(
        "SELECT kind, amount FROM measure.event WHERE entry_ref = %s ORDER BY kind",
        (uuid.uuid5(_MEASURE_NAMESPACE, receipt),),
    ).fetchall()


def counts(owner: psycopg.Connection, shop_id: uuid.UUID) -> tuple[Any, ...]:
    return (
        owner.execute("SELECT count(*) FROM activity WHERE shop_id = %s", (shop_id,)).fetchone(),
        owner.execute("SELECT count(*) FROM measure.event").fetchone(),
    )


def approve(
    client: TestClient, admin: dict[str, str], receipt: str, body: dict[str, Any] | None = None, **kw: Any
) -> Any:
    return client.post(f"{RECEIPTS}/{receipt}/approve", json=body, headers={**admin, **kw.get("headers", key())})


def reject(client: TestClient, admin: dict[str, str], receipt: str, reason: Any = "Pul kelib tushmagan") -> Any:
    return client.post(f"{RECEIPTS}/{receipt}/reject", json={"reason": reason}, headers={**admin, **key()})


def audit(owner: psycopg.Connection, receipt: str) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT action, admin_id, target_type, target_shop, reason, detail FROM admin_audit "
        "WHERE target_id = %s ORDER BY at, id",
        (receipt,),
    ).fetchall()


@pytest.fixture
def admin(client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv) -> dict[str, str]:
    """Headers of an administrator who passed the second factor."""
    return elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))


# --- the owner sends a receipt (REQ-054) -----------------------------------------------------------------


def test_the_owner_sends_a_receipt_and_nothing_changes_until_it_is_decided(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, file_root: Path
) -> None:
    before, was = counts(owner, world.shop_a), subscription(owner, world.shop_a)
    response = submit(client, world.owner_a, world.shop_a, 300_000, 3, jpeg(EXIF))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body == {
        "id": body["id"],
        "stated_amount": 300_000,
        "stated_months": 3,
        "status": "submitted",
        "months": None,
        "reject_reason": None,
        "created_at": body["created_at"],
        "decided_at": None,
    }
    assert abs(datetime.fromisoformat(body["created_at"]) - admin_env.clock.now()) < timedelta(minutes=1)
    assert rows(owner, world.shop_a) == [(300_000, 3, "submitted", None, None, None, True)]
    assert subscription(owner, world.shop_a) == was, "a receipt is a claim, not a payment (INV-16)"

    # The file: sanitized like every receipt, kept three years, under a key that names nobody.
    ((purpose, mime, delete_after),) = files_of(owner, world.shop_a)
    assert (purpose, mime) == ("subscription_receipt", "image/jpeg")
    assert abs(delete_after - (admin_env.clock.now() + timedelta(days=1095))) < timedelta(minutes=1)
    (stored,) = stored_objects(file_root)
    assert stored.read_bytes() == JPEG, "the metadata is gone"
    assert str(world.shop_a) not in str(stored) and str(world.owner_a) not in str(stored)

    after = counts(owner, world.shop_a)
    assert (after[0][0], after[1][0]) == (before[0][0] + 1, before[1][0] + 1)
    activity = owner.execute(
        "SELECT actor_kind, actor_id, subject_type, subject_id FROM activity "
        "WHERE shop_id = %s AND action = 'subscription.receipt_sent'",
        (world.shop_a,),
    ).fetchall()
    assert activity == [("staff", world.owner_a_membership, "subscription_receipt", uuid.UUID(body["id"]))]
    assert measures(owner, body["id"]) == [("subscription_receipt_sent", 300_000)]

    history = client.get(path(world.shop_a), headers=as_user(world.owner_a))
    assert (history.status_code, history.json()) == (200, {"items": [body]})


def test_administrators_on_the_allow_list_and_the_review_group_are_told_without_card_details(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    make_admin(owner, admin_env, world.admin)
    russian = world.stranger
    make_admin(owner, admin_env, russian)
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (russian,))
    # Each of these lacks one thing that makes a reviewer.
    off_the_list = world.owner_b
    make_admin(owner, admin_env, off_the_list)
    admin_env.allowed.discard(int(tg(owner, off_the_list)))
    never_confirmed = world.seller_a
    make_admin(owner, admin_env, never_confirmed, confirmed=False)
    disabled = world.manager_a
    make_admin(owner, admin_env, disabled)
    owner.execute("UPDATE admin_account SET status = 'disabled' WHERE user_id = %s", (disabled,))
    group = -1_000_000_000_000 - uuid.uuid4().int % 10**9
    setting(owner, world.admin, "review_group", group)
    setting(owner, world.admin, "card_number", CARD)

    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    told = announced(owner, receipt)
    uzbek = say("uz", "a_receipt_new", shop="Shop A", amount=money("uz", 300_000), months=3)
    assert told == {
        tg(owner, world.admin): uzbek,
        tg(owner, russian): say("ru", "a_receipt_new", shop="Shop A", amount=money("ru", 300_000), months=3),
        str(group): uzbek,
    }
    assert uzbek == "Yangi obuna cheki: «Shop A», 300\xa0000\xa0so'm, 3 oy. Admin panelda ko'rib chiqing."
    for text in told.values():
        assert CARD not in text and CARD[-4:] not in text
    # Text, and for an administrator the two buttons: the image is not forwarded anywhere, and the group
    # gets no buttons (tests/api/test_subscription_receipts_admin_chat.py).
    payloads = owner.execute(
        "SELECT recipient, payload FROM outbox_message WHERE dedupe_key LIKE %s", (f"subreceipt:{receipt}:new:%",)
    ).fetchall()
    assert {recipient: set(payload) for recipient, payload in payloads} == {
        tg(owner, world.admin): {"text", "reply_markup"},
        tg(owner, russian): {"text", "reply_markup"},
        str(group): {"text"},
    }
    # The shop's own people are told nothing new by this.
    assert tg(owner, world.owner_a) not in told


def test_without_a_review_group_and_without_reviewers_the_receipt_still_waits(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a)
    assert announced(owner, receipt) == {}
    assert rows(owner, world.shop_a) == [(100_000, 1, "submitted", None, None, None, True)]


def test_the_same_file_sent_again_is_flagged_to_the_reviewers_across_shops_and_to_no_shop(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    unique, same_with_metadata = unique_image()
    first = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1, unique)
    assert say("uz", "a_receipt_copies", count=1) not in announced(owner, first)[tg(owner, world.admin)]

    # Another shop sends the very same image, with different metadata around it.
    second = submit(client, world.owner_b, world.shop_b, 200_000, 2, same_with_metadata)
    assert second.status_code == 201, second.text
    assert set(second.json()) == {
        "id",
        "stated_amount",
        "stated_months",
        "status",
        "months",
        "reject_reason",
        "created_at",
        "decided_at",
    }, "the shop is told nothing about another shop's receipt"
    warned = announced(owner, second.json()["id"])[tg(owner, world.admin)]
    assert warned.endswith("\n" + say("uz", "a_receipt_copies", count=1))
    # And the first shop once more: now two others carry it.
    third = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1, unique)
    assert announced(owner, third)[tg(owner, world.admin)].endswith("\n" + say("uz", "a_receipt_copies", count=2))

    listed = {item["id"]: item for item in all_waiting(client, admin)}
    assert [listed[receipt]["copies"] for receipt in (first, second.json()["id"], third)] == [2, 2, 2]
    seen = client.get(f"{RECEIPTS}/{second.json()['id']}", headers=admin).json()
    assert [(copy["id"], copy["shop_id"], copy["shop_name"], copy["stated_amount"]) for copy in seen["copies"]] == [
        (first, str(world.shop_a), "Shop A", 100_000),
        (third, str(world.shop_a), "Shop A", 100_000),
    ]
    # Each shop's history holds its own receipts only.
    mine = client.get(path(world.shop_b), headers=as_user(world.owner_b)).json()["items"]
    assert [item["id"] for item in mine] == [second.json()["id"]]
    assert "Shop A" not in str(mine) and first not in str(mine)

    # A different image is no copy of anything.
    other = sent_ok(client, world.owner_b, world.shop_b, 100_000, 1, unique_image()[0])
    assert "\n" not in announced(owner, other)[tg(owner, world.admin)]
    assert client.get(f"{RECEIPTS}/{other}", headers=admin).json()["copies"] == []
    assert {item["id"]: item["copies"] for item in all_waiting(client, admin)}[other] == 0


@pytest.mark.parametrize(
    ("amount", "months", "content", "field"),
    [
        (MIN_AMOUNT - 1, 1, JPEG, "amount"),
        (MAX_AMOUNT + 1, 1, JPEG, "amount"),
        (0, 1, JPEG, "amount"),
        ("abc", 1, JPEG, "amount"),
        ("100000.5", 1, JPEG, "amount"),
        (None, 1, JPEG, "amount"),
        (100_000, 0, JPEG, "months"),
        (100_000, 37, JPEG, "months"),
        (100_000, None, JPEG, "months"),
        (100_000, 1, HTML, "receipt"),
        (100_000, 1, b"", "receipt"),
        (100_000, 1, JPEG + b"x" * (MAX_FILE_BYTES - len(JPEG) + 1), "receipt"),
        (100_000, 1, None, "receipt"),
    ],
    ids=[
        "amount below",
        "amount above",
        "amount zero",
        "amount words",
        "amount fraction",
        "amount missing",
        "months zero",
        "months above",
        "months missing",
        "file html",
        "file empty",
        "file too large",
        "file missing",
    ],
)
def test_a_receipt_that_breaks_a_rule_is_refused_and_nothing_is_kept(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    file_root: Path,
    amount: Any,
    months: Any,
    content: bytes | None,
    field: str,
) -> None:
    before = counts(owner, world.shop_a)
    response = submit(client, world.owner_a, world.shop_a, amount, months, content)
    assert response.status_code == 422, response.text
    assert field in response.json()["error"]["fields"] or "_" in response.json()["error"]["fields"]
    assert rows(owner, world.shop_a) == [] and files_of(owner, world.shop_a) == []
    assert stored_objects(file_root) == []
    assert counts(owner, world.shop_a) == before


def test_the_field_at_fault_is_named(client: TestClient, world: World) -> None:
    def fields(*args: Any) -> set[str]:
        return set(submit(client, world.owner_a, world.shop_a, *args).json()["error"]["fields"])

    assert fields(MIN_AMOUNT - 1, 1) == {"amount"}
    assert fields(100_000, 37) == {"months"}
    assert fields(100_000, 1, HTML) == {"receipt"}
    json_body = client.post(
        path(world.shop_a), json={"amount": 100_000, "months": 1}, headers={**as_user(world.owner_a), **key()}
    )
    assert (json_body.status_code, set(json_body.json()["error"]["fields"])) == (422, {"_"})


def test_amounts_months_and_files_at_the_limits_are_accepted(client: TestClient, world: World) -> None:
    assert submit(client, world.owner_a, world.shop_a, MIN_AMOUNT, 1, PDF).status_code == 201
    assert submit(client, world.owner_a, world.shop_a, MAX_AMOUNT, 36, PNG).status_code == 201


def test_a_body_longer_than_any_receipt_is_refused_before_it_is_parsed(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    huge = submit(client, world.owner_a, world.shop_a, 100_000, 1, b"\xff" * (MAX_FILE_BYTES + 64 * 1024))
    assert huge.status_code == 413, huge.text
    assert rows(owner, world.shop_a) == []


def test_at_most_three_receipts_of_a_shop_wait_at_once(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], file_root: Path
) -> None:
    waiting = [sent_ok(client, world.owner_a, world.shop_a, 100_000 + index, 1) for index in range(3)]
    refused = submit(client, world.owner_a, world.shop_a, 100_000, 1, PNG)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "SUBSCRIPTION_RECEIPT_NOT_ALLOWED")
    assert refused.json()["error"]["fields"] == {"reason": "too_many_waiting"}
    assert len(rows(owner, world.shop_a)) == 3 and len(stored_objects(file_root)) == 3, "its file was not kept"
    # Another shop is not held back by this one.
    assert submit(client, world.owner_b, world.shop_b).status_code == 201
    # Once one is decided there is room again.
    assert reject(client, admin, waiting[0]).status_code == 200
    assert submit(client, world.owner_a, world.shop_a, 100_000, 1, PNG).status_code == 201


def test_two_receipts_sent_at_once_cannot_both_take_the_last_place(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str, file_root: Path
) -> None:
    database = Database(app_database_url)
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        authenticator=HeaderAuthenticator(),
        file_store=FilesystemFileStore(file_root),
    )
    with TestClient(app) as second:
        for attempt in range(4):
            owner.execute(
                "UPDATE subscription_receipt SET status = 'rejected', reject_reason = 'x' WHERE shop_id = %s",
                (world.shop_a,),
            )
            for _ in range(2):
                sent_ok(client, world.owner_a, world.shop_a)
            kept = len(stored_objects(file_root))
            barrier = threading.Barrier(2)

            def send(which: TestClient, wait: Any = barrier) -> int:
                wait.wait(timeout=10)
                return int(submit(which, world.owner_a, world.shop_a, 100_000, 1, PNG).status_code)

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = sorted(pool.map(send, (client, second)))
            assert statuses == [201, 409], attempt
            waiting = [row for row in rows(owner, world.shop_a) if row[2] == "submitted"]
            assert len(waiting) == 3
            assert len(stored_objects(file_root)) == kept + 1, "the file of the one refused was removed again"
        second.portal.call(database.dispose)  # type: ignore[union-attr]


def test_a_receipt_larger_than_an_ordinary_request_is_accepted(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    """Every other route stops at one mebibyte; a receipt may be up to five."""
    profile = b"".join(segment(0xE2, b"ICC_PROFILE" + bytes([0, 1, 1]) + bytes(60_000)) for _ in range(30))
    large = jpeg(keep=profile)
    assert 1024 * 1024 < len(large) < MAX_FILE_BYTES
    assert submit(client, world.owner_a, world.shop_a, 100_000, 1, large).status_code == 201
    assert [path.stat().st_size for path in stored_objects(file_root)] == [len(large)]


def test_only_the_owner_sends_and_reads_receipts_and_is_asked_who_they_are_first(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    garbage = {"Content-Type": "multipart/form-data; boundary=x"}
    for staff in (world.manager_a, world.seller_a):
        refused = submit(client, staff, world.shop_a)
        assert (refused.status_code, refused.json()["error"]["code"]) == (403, "FORBIDDEN_ROLE")
        assert refused.json()["error"]["fields"] == {"needed_role": "owner"}
        assert client.get(path(world.shop_a), headers=as_user(staff)).status_code == 403
    for outsider in (world.owner_b, world.stranger, world.suspended_a):
        nothing = client.post(path(world.shop_a), content=b"not a form", headers={**as_user(outsider), **garbage})
        assert (nothing.status_code, nothing.json()["error"]["code"]) == (404, "NOT_FOUND")
        assert client.get(path(world.shop_a), headers=as_user(outsider)).status_code == 404
    # The owner without a key is told so before the body is looked at.
    no_key = client.post(path(world.shop_a), content=b"not a form", headers={**as_user(world.owner_a), **garbage})
    assert (no_key.status_code, set(no_key.json()["error"]["fields"])) == (422, {"Idempotency-Key"})
    assert rows(owner, world.shop_a) == []


def test_a_repeated_request_keeps_one_receipt_and_one_file(
    client: TestClient, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    headers = key()
    first = submit(client, world.owner_a, world.shop_a, 100_000, 1, JPEG, headers=headers)
    again = submit(client, world.owner_a, world.shop_a, 100_000, 1, JPEG, headers=headers)
    assert (first.status_code, again.status_code) == (201, 201)
    assert first.json() == again.json()
    assert len(rows(owner, world.shop_a)) == 1 and len(stored_objects(file_root)) == 1
    assert len(announced(owner, first.json()["id"])) == 0  # nobody to tell here; and nothing was queued twice

    for changed in ((200_000, 1, JPEG), (100_000, 2, JPEG), (100_000, 1, PNG)):
        other = submit(client, world.owner_a, world.shop_a, *changed, headers=headers)
        assert (other.status_code, other.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert len(rows(owner, world.shop_a)) == 1 and len(stored_objects(file_root)) == 1


def test_a_shop_in_any_mode_may_send_a_receipt(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    """Paying is how a shop leaves limited mode, and a suspended shop may pay as it may pay online."""
    for state in ("limited", "suspended", "active", "trial"):
        set_subscription(owner, world.shop_a, state)
        owner.execute(
            "UPDATE subscription_receipt SET status = 'rejected', reject_reason = 'x' WHERE shop_id = %s",
            (world.shop_a,),
        )
        assert submit(client, world.owner_a, world.shop_a).status_code == 201, state
        assert client.get(path(world.shop_a), headers=as_user(world.owner_a)).status_code == 200, state


def test_without_a_file_store_a_receipt_is_refused(
    app_database_url: str, world: World, owner: psycopg.Connection
) -> None:
    database = Database(app_database_url)
    app = create_app(
        database.reachable, database, auth=AuthService(database, TEST_BOT_TOKEN), authenticator=HeaderAuthenticator()
    )
    with TestClient(app) as bare:
        refused = submit(bare, world.owner_a, world.shop_a)
        assert (refused.status_code, refused.json()["error"]["code"]) == (503, "FILE_STORE_UNAVAILABLE")
        # What the file is, is still checked first.
        assert submit(bare, world.owner_a, world.shop_a, 100_000, 1, HTML).status_code == 422
        assert rows(owner, world.shop_a) == []
        bare.portal.call(database.dispose)  # type: ignore[union-attr]


# --- the administrator's queue ---------------------------------------------------------------------------


def all_waiting(client: TestClient, admin: dict[str, str], **params: Any) -> list[dict[str, Any]]:
    """Every receipt of one status, read page by page. The database is shared with other tests' shops."""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        page = client.get(RECEIPTS, params={**params, **({"cursor": cursor} if cursor else {})}, headers=admin)
        assert page.status_code == 200, page.text
        items += page.json()["items"]
        cursor = page.json()["next_cursor"]
        if cursor is None:
            return items


def test_the_queue_lists_waiting_receipts_of_all_shops_oldest_first(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str]
) -> None:
    first = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1)
    second = sent_ok(client, world.owner_b, world.shop_b, 200_000, 2, PNG)
    third = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, PDF)
    owner.execute("UPDATE subscription_receipt SET created_at = now() - interval '2 days' WHERE id = %s", (third,))
    assert reject(client, admin, second).status_code == 200

    waiting = all_waiting(client, admin, limit=2)
    assert len({item["id"] for item in waiting}) == len(waiting), "no receipt twice across pages"
    assert [item["created_at"] for item in waiting] == sorted(item["created_at"] for item in waiting)
    mine = [item for item in waiting if item["shop_id"] in (str(world.shop_a), str(world.shop_b))]
    assert [item["id"] for item in mine] == [third, first]
    assert mine[1] == {
        "id": first,
        "shop_id": str(world.shop_a),
        "shop_name": "Shop A",
        "stated_amount": 100_000,
        "stated_months": 1,
        "status": "submitted",
        "months": None,
        "reject_reason": None,
        "created_at": mine[1]["created_at"],
        "decided_at": None,
        "decided_by": None,
        "has_file": True,
        "copies": mine[1]["copies"],
    }
    # REQ-059: nothing of the shop's customers, and no way to reach the owner from here.
    for hidden in ("Ali", str(world.customer_a), tg(owner, world.owner_a)):
        assert hidden not in str(mine)

    rejected = [item["id"] for item in all_waiting(client, admin, status="rejected")]
    assert second in rejected and first not in rejected
    assert second not in [item["id"] for item in all_waiting(client, admin, status="approved")]


@pytest.mark.parametrize("params", [{"status": "open"}, {"status": ""}, {"limit": 0}, {"limit": 101}, {"cursor": "x"}])
def test_bad_queue_parameters_are_refused(client: TestClient, admin: dict[str, str], params: dict[str, Any]) -> None:
    assert client.get(RECEIPTS, params=params, headers=admin).status_code == 422


def test_an_administrator_opens_a_receipt_through_a_link_and_the_look_is_audited(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, jpeg(EXIF))
    seen = client.get(f"{RECEIPTS}/{receipt}", headers=admin)
    assert seen.status_code == 200, seen.text
    body = seen.json()
    assert set(body) == {
        "id",
        "shop_id",
        "shop_name",
        "stated_amount",
        "stated_months",
        "status",
        "months",
        "reject_reason",
        "created_at",
        "decided_at",
        "decided_by",
        "has_file",
        "file",
        "copies",
    }
    assert (body["shop_name"], body["stated_amount"], body["stated_months"], body["has_file"]) == (
        "Shop A",
        300_000,
        3,
        True,
    )
    assert set(body["file"]) == {"url", "expires_at"} and body["file"]["url"].startswith("/files/")
    expires = datetime.fromisoformat(body["file"]["expires_at"])
    assert abs(expires - (admin_env.clock.now() + timedelta(minutes=5))) < timedelta(seconds=30)

    # The link is the authorization: followed with no credentials, served as a download.
    served = client.get(body["file"]["url"])
    assert (served.status_code, served.content) == (200, JPEG)
    assert served.headers["content-disposition"].startswith("attachment")
    admin_env.clock.offset += timedelta(minutes=5, seconds=1)
    assert client.get(body["file"]["url"]).status_code == 404, "five minutes, not longer"

    assert [(row[0], row[1], row[2], row[3]) for row in audit(owner, receipt)] == [
        ("receipt.viewed", world.admin, "receipt", world.shop_a)
    ]
    unknown = client.get(f"{RECEIPTS}/{uuid.uuid4()}", headers=admin)
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert client.get(f"{RECEIPTS}/not-a-uuid", headers=admin).status_code == 404


def test_the_owner_of_a_shop_cannot_use_the_administrators_side(client: TestClient, world: World) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a)
    for url in (RECEIPTS, f"{RECEIPTS}/{receipt}"):
        assert client.get(url, headers=as_user(world.owner_a)).status_code == 404
    for action in ("approve", "reject"):
        refused = client.post(
            f"{RECEIPTS}/{receipt}/{action}", json={"reason": "o'zim"}, headers={**as_user(world.owner_a), **key()}
        )
        assert refused.status_code == 404


# --- approval (REQ-055, BR-27) ------------------------------------------------------------------------------


def test_approval_extends_the_paid_period_and_tells_the_owner(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    trial_ends = subscription(owner, world.shop_a)[1]
    receipt = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1)
    before = counts(owner, world.shop_a)

    response = approve(client, admin, receipt)
    assert response.status_code == 200, response.text
    # Nothing was paid before: one month from today, today being the first paid day.
    until = add_months(today(admin_env), 1) - timedelta(days=1)
    body = response.json()
    assert body == {
        "id": receipt,
        "shop_id": str(world.shop_a),
        "shop_name": "Shop A",
        "stated_amount": 100_000,
        "stated_months": 1,
        "status": "approved",
        "months": 1,
        "reject_reason": None,
        "created_at": body["created_at"],
        "decided_at": body["decided_at"],
        "decided_by": str(world.admin),
        "has_file": True,
        "subscription": {"state": "active", "paid_through": until.isoformat()},
    }
    assert subscription(owner, world.shop_a) == ("active", trial_ends, until, None)
    assert rows(owner, world.shop_a) == [(100_000, 1, "approved", 1, None, world.admin, True)]

    # The admin audit, the shop's own activity, an identity-free measure, and the owner's notice.
    ((action, who, target_type, shop, reason, detail),) = audit(owner, receipt)
    assert (action, who, target_type, shop, reason) == (
        "subscription.receipt_approved",
        world.admin,
        "receipt",
        world.shop_a,
        None,
    )
    assert detail == {
        "via": "panel",
        "stated_amount": 100_000,
        "stated_months": 1,
        "months": 1,
        "before": {"state": "trial", "paid_through": None},
        "after": {"state": "active", "paid_through": until.isoformat()},
    }
    after = counts(owner, world.shop_a)
    assert (after[0][0], after[1][0]) == (before[0][0] + 1, before[1][0] + 1)
    activity = owner.execute(
        "SELECT actor_kind, actor_id, subject_type, subject_id FROM activity "
        "WHERE shop_id = %s AND action = 'subscription.receipt_approved'",
        (world.shop_a,),
    ).fetchall()
    assert activity == [("admin", None, "subscription_receipt", uuid.UUID(receipt))], "the shop is not told who"
    assert measures(owner, receipt) == [
        ("subscription_receipt_approved", 100_000),
        ("subscription_receipt_sent", 100_000),
    ]
    refs = owner.execute("SELECT shop_ref, entry_ref FROM measure.event ORDER BY at DESC LIMIT 20").fetchall()
    assert all(world.shop_a not in row and uuid.UUID(receipt) not in row for row in refs), "no identifier in a measure"
    assert decided_notice(owner, receipt) == [
        (tg(owner, world.owner_a), say("uz", "sub_receipt_approved", shop="Shop A", months=1, date=day(until)))
    ]
    # The owner sees the outcome, and the shop page of the administrator shows the change.
    mine = client.get(path(world.shop_a), headers=as_user(world.owner_a)).json()["items"]
    assert [(item["status"], item["months"]) for item in mine] == [("approved", 1)]
    state = client.get(f"/api/v1/shops/{world.shop_a}/subscription", headers=as_user(world.owner_a)).json()
    assert (state["state"], state["paid_through"]) == ("active", until.isoformat())
    page = client.get(f"{ADMIN_API}/shops/{world.shop_a}", headers=admin).json()
    assert "subscription.receipt_approved" in [change["action"] for change in page["changes"]]


@pytest.mark.parametrize(
    ("state", "paid_offset", "months", "expected_state", "from_paid_through", "prior"),
    [
        ("active", 0, 1, "active", True, None),  # paid through today: the month is added to that date
        ("active", 20, 3, "active", True, None),  # paying early loses nothing
        ("active", -1, 1, "active", False, None),  # the period ended yesterday: from today
        ("limited", -40, 2, "active", False, None),  # a limited shop works again
        ("limited", None, 1, "active", False, None),
        ("suspended", 5, 1, "suspended", True, "active"),  # paid for, and still suspended (BR-30)
        ("suspended", None, 6, "suspended", False, "active"),
    ],
)
def test_the_period_runs_from_the_later_of_today_and_the_paid_through_date(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    state: str,
    paid_offset: int | None,
    months: int,
    expected_state: str,
    from_paid_through: bool,
    prior: str | None,
) -> None:
    now = today(admin_env)
    paid = None if paid_offset is None else now + timedelta(days=paid_offset)
    set_subscription(
        owner, world.shop_a, state, paid_through=paid, prior_state="trial" if state == "suspended" else None
    )
    receipt = sent_ok(client, world.owner_a, world.shop_a, 100_000 * months, months)

    response = approve(client, admin, receipt)
    assert response.status_code == 200, response.text
    until = (
        add_months(paid, months)
        if from_paid_through and paid is not None
        else add_months(now, months) - timedelta(days=1)
    )
    assert subscription(owner, world.shop_a) == (expected_state, None, until, prior)
    assert response.json()["subscription"] == {"state": expected_state, "paid_through": until.isoformat()}


def test_the_administrator_may_correct_the_months_and_note_why(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3)
    response = approve(client, admin, receipt, {"months": 2, "reason": "  200 000 kelib   tushgan "})
    assert response.status_code == 200, response.text
    until = add_months(today(admin_env), 2) - timedelta(days=1)
    assert (response.json()["months"], response.json()["stated_months"]) == (2, 3)
    assert subscription(owner, world.shop_a)[2] == until
    assert audit(owner, receipt)[0][4] == "200 000 kelib tushgan"
    assert decided_notice(owner, receipt)[0][1] == say(
        "uz", "sub_receipt_approved", shop="Shop A", months=2, date=day(until)
    )


@pytest.mark.parametrize(
    "body", [{"months": 0}, {"months": 37}, {"months": "2"}, {"months": 1.5}, {"reason": "x"}, {"x": 1}]
)
def test_an_approval_that_is_malformed_changes_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], body: dict[str, Any]
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a)
    was = subscription(owner, world.shop_a)
    assert approve(client, admin, receipt, body).status_code == 422
    assert subscription(owner, world.shop_a) == was
    assert rows(owner, world.shop_a)[0][2] == "submitted" and audit(owner, receipt) == []
    assert decided_notice(owner, receipt) == []


# --- rejection ------------------------------------------------------------------------------------------


def test_rejection_tells_the_owner_why_and_changes_no_subscription(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str]
) -> None:
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.owner_a,))
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3)
    was, before = subscription(owner, world.shop_a), counts(owner, world.shop_a)

    response = reject(client, admin, receipt, "  Pul   kelib tushmagan ")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["months"], body["reject_reason"], body["decided_by"]) == (
        "rejected",
        None,
        "Pul kelib tushmagan",
        str(world.admin),
    )
    assert "subscription" not in body
    assert subscription(owner, world.shop_a) == was
    assert rows(owner, world.shop_a) == [(300_000, 3, "rejected", None, "Pul kelib tushmagan", world.admin, True)]
    assert [(row[0], row[4], row[5]) for row in audit(owner, receipt)] == [
        (
            "subscription.receipt_rejected",
            "Pul kelib tushmagan",
            {"stated_amount": 300_000, "stated_months": 3, "via": "panel"},
        )
    ]
    after = counts(owner, world.shop_a)
    assert (after[0][0], after[1][0]) == (before[0][0] + 1, before[1][0] + 1)
    assert decided_notice(owner, receipt) == [
        (
            tg(owner, world.owner_a),
            say("ru", "sub_receipt_rejected", shop="Shop A", amount=money("ru", 300_000), reason="Pul kelib tushmagan"),
        )
    ]
    mine = client.get(path(world.shop_a), headers=as_user(world.owner_a)).json()["items"]
    assert [(item["status"], item["reject_reason"]) for item in mine] == [("rejected", "Pul kelib tushmagan")]


@pytest.mark.parametrize("reason", ["", "  ", "xx", "x" * 501, None, 5])
def test_a_rejection_needs_a_reason(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], reason: Any
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a)
    assert reject(client, admin, receipt, reason).status_code == 422
    assert rows(owner, world.shop_a)[0][2] == "submitted" and audit(owner, receipt) == []
    assert decided_notice(owner, receipt) == []


# --- a receipt is decided once ----------------------------------------------------------------------------


def test_deciding_twice_has_no_second_effect(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str]
) -> None:
    receipt = sent_ok(client, world.owner_a, world.shop_a)
    headers = key()
    first = approve(client, admin, receipt, headers=headers)
    assert first.status_code == 200
    paid = subscription(owner, world.shop_a)

    # The same request again: the stored answer, and nothing done.
    again = approve(client, admin, receipt, headers=headers)
    assert (again.status_code, again.json()) == (200, first.json())
    # A new request, to approve or to reject: refused, saying what it already is.
    for attempt in (
        approve(client, admin, receipt),
        approve(client, admin, receipt, {"months": 6}),
        reject(client, admin, receipt),
    ):
        assert (attempt.status_code, attempt.json()["error"]["code"]) == (409, "RECEIPT_ALREADY_DECIDED")
        assert attempt.json()["error"]["fields"] == {"status": "approved"}
    assert subscription(owner, world.shop_a) == paid
    assert rows(owner, world.shop_a) == [(100_000, 1, "approved", 1, None, world.admin, True)]
    assert len(audit(owner, receipt)) == 1 and len(decided_notice(owner, receipt)) == 1

    declined = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1, PNG)
    assert reject(client, admin, declined).status_code == 200
    late = approve(client, admin, declined)
    assert (late.status_code, late.json()["error"]["fields"]) == (409, {"status": "rejected"})
    assert subscription(owner, world.shop_a) == paid


@contextmanager
def second_admin_app(app_database_url: str, env: AdminEnv, root: Path) -> Iterator[TestClient]:
    """Another instance of the application with the same administrators, as a second server would be."""
    database = Database(app_database_url)
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        admin=AdminAccess(database, allowed_tg_ids=env.allowed, cipher=env.box, now=env.clock.now),
        authenticator=HeaderAuthenticator(),
        now=env.clock.now,
        file_store=FilesystemFileStore(root),
        secrets_key=TEST_SECRETS_KEY,
    )
    with TestClient(app) as other:
        yield other
        other.portal.call(database.dispose)  # type: ignore[union-attr]


def test_two_administrators_deciding_at_once_cannot_both_win(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    app_database_url: str,
    file_root: Path,
) -> None:
    colleague = elevate(client, admin_env, world.stranger, make_admin(owner, admin_env, world.stranger))
    with second_admin_app(app_database_url, admin_env, file_root) as second:
        for attempt in range(4):
            set_subscription(owner, world.shop_a, "limited")
            receipt = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1)
            barrier = threading.Barrier(2)

            def decide(which: int, receipt: str = receipt, wait: Any = barrier, attempt: int = attempt) -> int:
                wait.wait(timeout=10)
                if which == 0:
                    return int(approve(client, admin, receipt).status_code)
                # Two approvals in even rounds; an approval against a rejection in odd ones.
                if attempt % 2 == 0:
                    return int(approve(second, colleague, receipt, {"months": 12}).status_code)
                return int(reject(second, colleague, receipt).status_code)

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = sorted(pool.map(decide, (0, 1)))
            assert statuses == [200, 409], attempt
            state, _, paid_through, _ = subscription(owner, world.shop_a)
            status, months = rows(owner, world.shop_a)[-1][2:4]
            now = today(admin_env)
            if status == "approved":
                # Exactly one approval took effect, for its own number of months.
                assert (state, paid_through) == ("active", add_months(now, months) - timedelta(days=1))
            else:
                assert (state, paid_through) == ("limited", None)
            assert len(audit(owner, receipt)) == 1 and len(decided_notice(owner, receipt)) == 1


def test_the_database_itself_decides_a_receipt_once_and_only_for_an_administrator(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    receipt = uuid.UUID(sent_ok(client, world.owner_a, world.shop_a))
    owner.execute("UPDATE admin_account SET status = 'active' WHERE user_id = %s", (world.admin,))

    async def run() -> list[Any]:
        database = Database(app_database_url)
        now = datetime.now().astimezone()
        try:
            async with database.platform() as session:
                return [
                    # Not an administrator: nothing is seen and nothing is changed.
                    await session.admin_receipt(world.owner_a, receipt, lock=False),
                    await session.admin_receipts(world.owner_a, status="submitted", after=None, limit=100),
                    await session.admin_receipt_copies(world.owner_a, receipt),
                    await session.admin_decide_receipt(
                        world.owner_a, receipt, status="approved", months=1, reason=None, now=now
                    ),
                    await session.admin_shop_activity(
                        world.owner_a, world.shop_a, action="subscription.receipt_approved", subject_id=receipt
                    ),
                    # An administrator: once.
                    await session.admin_decide_receipt(
                        world.admin, receipt, status="rejected", months=None, reason="Soxta", now=now
                    ),
                    await session.admin_decide_receipt(
                        world.admin, receipt, status="approved", months=3, reason=None, now=now
                    ),
                ]
        finally:
            await database.dispose()

    assert asyncio.run(run()) == [None, [], [], False, False, True, False]
    assert rows(owner, world.shop_a) == [(100_000, 1, "rejected", None, "Soxta", world.admin, True)]


# --- retention and erasure ---------------------------------------------------------------------------------


def _services(app_database_url: str, root: Path, action: Any) -> Any:
    async def run() -> Any:
        database = Database(app_database_url)
        try:
            store = FilesystemFileStore(root)
            files = FileService(database, store)
            return await action(
                PaymentNoticeService(database, files), ShopDeletionService(database, files=store), files
            )
        finally:
            await database.dispose()

    return asyncio.run(run())


def test_a_receipt_file_is_deleted_when_its_three_years_are_over_and_the_receipt_stays(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin: dict[str, str],
    app_database_url: str,
    file_root: Path,
) -> None:
    old = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1, JPEG)
    recent = sent_ok(client, world.owner_a, world.shop_a, 200_000, 2, PNG)
    assert approve(client, admin, old).status_code == 200
    owner.execute(
        "UPDATE stored_file SET delete_after = now() - interval '1 minute' "
        "WHERE id = (SELECT file_id FROM subscription_receipt WHERE id = %s)",
        (old,),
    )
    # Due already, before the job: no link is given to it any more.
    assert client.get(f"{RECEIPTS}/{old}", headers=admin).json()["file"] is None

    async def shops_with_work() -> list[uuid.UUID]:
        database = Database(app_database_url)
        try:
            async with database.platform() as session:
                at = datetime.now().astimezone()
                return await session.shops_with_receipt_work(at - timedelta(days=14), at)
        finally:
            await database.dispose()

    # The hourly job finds the shop, and deletes what is due there and nothing else.
    assert world.shop_a in asyncio.run(shops_with_work()) and world.shop_b not in asyncio.run(shops_with_work())
    purge = lambda _, __, files: files.purge_due_receipts(world.shop_a, datetime.now().astimezone())  # noqa: E731
    assert _services(app_database_url, file_root, purge) == 1
    assert [path.read_bytes() for path in stored_objects(file_root)] == [PNG]
    assert [row[0:2] for row in files_of(owner, world.shop_a)] == [("subscription_receipt", "image/png")]
    # The record of the payment outlives its image.
    assert rows(owner, world.shop_a) == [
        (100_000, 1, "approved", 1, None, world.admin, False),
        (200_000, 2, "submitted", None, None, None, True),
    ]
    seen = client.get(f"{RECEIPTS}/{old}", headers=admin).json()
    assert (seen["has_file"], seen["file"], seen["status"]) == (False, None, "approved")
    assert client.get(client.get(f"{RECEIPTS}/{recent}", headers=admin).json()["file"]["url"]).content == PNG
    assert _services(app_database_url, file_root, purge) == 0
    assert world.shop_a not in asyncio.run(shops_with_work())


def test_erasing_a_shop_removes_its_receipts_and_their_files(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin: dict[str, str],
    app_database_url: str,
    file_root: Path,
) -> None:
    gone = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1, JPEG)
    assert approve(client, admin, gone).status_code == 200
    sent_ok(client, world.owner_a, world.shop_a, 100_000, 1, PDF)
    kept = sent_ok(client, world.owner_b, world.shop_b, 100_000, 1, PNG)
    # The waiting period is over. Written as the migration owner: the application role cannot do this.
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() - interval '1 second' WHERE id = %s",
        (world.shop_a,),
    )

    assert _services(app_database_url, file_root, lambda _, deletion, __: deletion.erase_due()) >= 1
    assert rows(owner, world.shop_a) == [] and files_of(owner, world.shop_a) == []
    assert [path.read_bytes() for path in stored_objects(file_root)] == [PNG]
    assert client.get(f"{RECEIPTS}/{gone}", headers=admin).status_code == 404
    assert gone not in [item["id"] for item in all_waiting(client, admin, status="approved")]
    assert kept in [item["id"] for item in all_waiting(client, admin)]
    # The stored answer of the approval, which named the shop, went with it; the audit keeps its row.
    assert owner.execute(
        "SELECT count(*) FROM admin_request_key WHERE about_shop = %s", (world.shop_a,)
    ).fetchone() == (0,)
    assert [row[0] for row in audit(owner, gone)] == ["subscription.receipt_approved"]
