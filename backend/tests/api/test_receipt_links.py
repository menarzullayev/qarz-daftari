"""Receipts are served through signed links valid five minutes (ADR-020), and a receipt the shop has seen
before is pointed out to staff (specification, "Security": "duplicate detection by hash")."""

import asyncio
import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.auth import AuthService
from qarz.application.chat_texts import money, say
from qarz.application.files import FileService
from qarz.domain.file_links import derive_key, expiry, sign
from qarz.infrastructure.db import Database
from qarz.infrastructure.file_store import FilesystemFileStore
from qarz.interface.http import create_app

from ..receipt_samples import EXIF, JPEG, PDF, PNG, jpeg
from .conftest import TEST_BOT_TOKEN, TEST_SECRETS_KEY, HeaderAuthenticator, World, as_user
from .test_customer_account import ME, link_of
from .test_customers_ledger import shop
from .test_payment_notices import files, receipt, send, sent_to_staff

pytestmark = pytest.mark.db

KEY = derive_key(TEST_SECRETS_KEY)


def link_to(client: TestClient, world: World, notice: Any, user: uuid.UUID | None = None) -> Any:
    return client.get(f"{shop(world)}/payment-notices/{notice}/receipt", headers=as_user(user or world.seller_a))


class StillClock:
    """A clock that moves only when the test moves it, so that a boundary can be met to the second."""

    def __init__(self) -> None:
        self.start = datetime.now(UTC)
        self.offset = timedelta(0)

    def now(self) -> datetime:
        return self.start + self.offset


@contextmanager
def clocked_client(
    app_database_url: str, file_root: Path, secret: str | None = TEST_SECRETS_KEY
) -> Iterator[tuple[TestClient, StillClock]]:
    database = Database(app_database_url)
    clock = StillClock()
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        authenticator=HeaderAuthenticator(),
        now=clock.now,
        file_store=FilesystemFileStore(file_root),
        secrets_key=secret,
    )
    with TestClient(app) as test_client:
        yield test_client, clock
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]


# --- the link ------------------------------------------------------------------------------------------


def test_staff_are_given_a_link_and_the_link_serves_the_file_to_whoever_holds_it(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    notice = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, JPEG).json()["id"]
    file_id = files(owner, world.shop_a)[0][0]

    given = link_to(client, world, notice)
    assert given.status_code == 200, given.text
    body = given.json()
    assert set(body) == {"url", "expires_at"}
    assert JPEG not in given.content, "the operation answers with a link, never with the file"
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert timedelta(minutes=4, seconds=58) < expires_at - datetime.now(UTC) <= timedelta(minutes=5)
    # Exactly the link an independent signing of the same file and expiry gives.
    assert body["url"] == "/files/" + sign(KEY, world.shop_a, file_id, expires_at)

    # No session, no header: the link is the authorization.
    served = client.get(body["url"])
    assert served.status_code == 200 and served.content == JPEG
    assert served.headers["content-type"] == "image/jpeg"
    assert served.headers["content-disposition"] == f'attachment; filename="receipt-{file_id.hex[:8]}.jpg"'
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.headers["cache-control"] == "private, no-store"
    # The link is outside the API and is not an operation of it.
    assert client.get("/api/v1" + body["url"]).status_code == 404
    assert client.post(body["url"]).status_code == 404


def test_a_link_works_for_five_minutes_and_not_a_second_longer(
    app_database_url: str, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    with clocked_client(app_database_url, file_root) as (client, clock):
        notice = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, PNG).json()["id"]
        url = link_to(client, world, notice).json()["url"]
        for seconds, status in ((0, 200), (299, 200), (300, 200), (301, 404), (3600, 404)):
            clock.offset = timedelta(seconds=seconds)
            response = client.get(url)
            assert response.status_code == status, seconds
            assert (PNG in response.content) is (status == 200)
        # A new link is given at any time, and it is another one.
        fresh = link_to(client, world, notice).json()["url"]
        assert fresh != url and client.get(fresh).content == PNG


def test_a_link_that_was_changed_or_signed_with_another_key_serves_nothing(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    first = send(client, world.customer_of_a, link, 20000, JPEG).json()["id"]
    second = send(client, world.customer_of_a, link, 5000, PNG).json()["id"]
    url, other = link_to(client, world, first).json()["url"], link_to(client, world, second).json()["url"]
    token, other_token = url.removeprefix("/files/"), other.removeprefix("/files/")
    file_id = files(owner, world.shop_a)[0][0]
    later = expiry(datetime.now(UTC))

    def flipped(index: int) -> str:
        return token[:index] + ("A" if token[index] != "A" else "B") + token[index + 1 :]

    attempts = {
        "shop changed": flipped(3),
        "file changed": flipped(25),
        "expiry changed": flipped(50),
        "signature changed": flipped(70),
        "signature of another file's link": token[:54] + other_token[54:],
        "another file with this signature": other_token[:54] + token[54:],
        "signed with another key": sign(derive_key("another-server-secret-0123456789"), world.shop_a, file_id, later),
        "signed with the secret itself": sign(TEST_SECRETS_KEY.encode(), world.shop_a, file_id, later),
        "cut": token[:-1],
        "no signature": token[:54],
        "empty signature": token[:55],
        "not a link": "x" * 98,
        "this shop's file under another shop": sign(KEY, world.shop_b, file_id, later),
        "a file that does not exist": sign(KEY, world.shop_a, uuid.uuid4(), later),
    }
    for what, attempt in attempts.items():
        response = client.get(f"/files/{attempt}")
        assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND"), what
        assert JPEG not in response.content and PNG not in response.content
    # The counterpart: a link signed here with the right key for the right file is served.
    assert client.get("/files/" + sign(KEY, world.shop_a, file_id, later)).content == JPEG
    assert client.get(url).content == JPEG and client.get(other).content == PNG


def test_without_the_server_secret_no_link_is_given_and_nothing_is_served(
    app_database_url: str, world: World, owner: psycopg.Connection, file_root: Path
) -> None:
    with clocked_client(app_database_url, file_root, secret=None) as (client, _):
        notice = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, JPEG).json()["id"]
        refused = link_to(client, world, notice)
        assert (refused.status_code, refused.json()["error"]["code"]) == (503, "FILE_STORE_UNAVAILABLE")
        file_id = files(owner, world.shop_a)[0][0]
        later = expiry(datetime.now(UTC))
        for key in (KEY, b"", bytes(32), derive_key("x" * 16)):
            assert client.get("/files/" + sign(key, world.shop_a, file_id, later)).status_code == 404
        # Whoever may not see the receipt learns nothing from this either.
        assert link_to(client, world, notice, world.owner_b).status_code == 404


def test_a_secret_too_short_to_sign_with_stops_the_start(app_database_url: str) -> None:
    database = Database(app_database_url)
    with pytest.raises(ValueError, match="too short"):
        create_app(database.reachable, database, auth=AuthService(database, TEST_BOT_TOKEN), secrets_key="short")
    asyncio.run(database.dispose())


def test_a_file_deleted_or_marked_for_deletion_is_not_served_even_with_a_valid_link(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str, file_root: Path
) -> None:
    link = link_of(owner, world.customer_a)
    marked = send(client, world.customer_of_a, link, 20000, JPEG).json()["id"]
    deleted = send(client, world.customer_of_a, link, 5000, PDF).json()["id"]
    marked_url, deleted_url = (
        link_to(client, world, marked).json()["url"],
        link_to(client, world, deleted).json()["url"],
    )
    assert client.get(marked_url).status_code == 200 and client.get(deleted_url).status_code == 200
    by_mime = {row[5]: row[0] for row in files(owner, world.shop_a)}

    # Marked for deletion, as a customer's removal marks it: the object is still in the store.
    owner.execute("UPDATE stored_file SET delete_after = %s WHERE id = %s", (datetime.now(UTC), by_mime["image/jpeg"]))
    assert client.get(marked_url).status_code == 404
    assert link_to(client, world, marked).status_code == 404, "and no new link is given for it"
    one_second_left = datetime.now(UTC) + timedelta(seconds=30)
    owner.execute("UPDATE stored_file SET delete_after = %s WHERE id = %s", (one_second_left, by_mime["image/jpeg"]))
    assert client.get(marked_url).status_code == 200, "a file whose deletion is still ahead is served"

    # Deleted by the cleanup: the row and the object are gone.
    owner.execute(
        "UPDATE stored_file SET delete_after = %s WHERE id = %s",
        (datetime.now(UTC) - timedelta(days=1), by_mime["application/pdf"]),
    )

    async def purge() -> int:
        database = Database(app_database_url)
        try:
            service = FileService(database, FilesystemFileStore(file_root))
            return await service.purge_due_receipts(world.shop_a, datetime.now(UTC))
        finally:
            await database.dispose()

    assert asyncio.run(purge()) == 1
    assert client.get(deleted_url).status_code == 404
    assert link_to(client, world, deleted).status_code == 404


def test_a_link_is_given_only_to_staff_of_the_shop(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    notice = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, JPEG).json()["id"]
    for user in (world.seller_a, world.manager_a, world.owner_a):
        assert link_to(client, world, notice, user).status_code == 200
    for user in (world.customer_of_a, world.stranger, world.owner_b, world.suspended_a):
        assert link_to(client, world, notice, user).status_code == 404
    assert client.get(f"{shop(world)}/payment-notices/{notice}/receipt").status_code == 401
    through_b = client.get(
        f"/api/v1/shops/{world.shop_b}/payment-notices/{notice}/receipt", headers=as_user(world.owner_b)
    )
    assert through_b.status_code == 404


# --- a receipt seen before -----------------------------------------------------------------------------


def staff_list(client: TestClient, world: World) -> dict[str, bool]:
    listed = client.get(f"{shop(world)}/payment-notices", headers=as_user(world.seller_a)).json()["items"]
    return {item["id"]: item["receipt_seen_before"] for item in listed}


def test_a_receipt_the_shop_has_seen_before_is_pointed_out_to_staff_and_never_to_the_customer(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    first = send(client, world.customer_of_a, link, 20000, JPEG)
    # The same photo again, this time with the camera's metadata still in it: the same receipt.
    again = send(client, world.customer_of_a, link, 10000, jpeg(EXIF))
    different = send(client, world.customer_of_a, link, 5000, PNG)
    ids = [response.json()["id"] for response in (first, again, different)]

    assert staff_list(client, world) == {ids[0]: False, ids[1]: True, ids[2]: False}
    detail = client.get(f"{shop(world)}/customers/{world.customer_a}", headers=as_user(world.seller_a)).json()
    assert {n["id"]: n["receipt_seen_before"] for n in detail["payment_notices"]} == staff_list(client, world)

    told = {payload["text"] for _, payload in sent_to_staff(owner, world.shop_a)}
    plain = say("uz", "s_notice_receipt", shop="Shop A", name="Ali", amount="{amount}", balance=money("uz", 50000))
    warning = "\n" + say("uz", "s_receipt_seen_before")
    assert warning == "\n⚠️ Aynan shu chek bu do'konga avval ham yuborilgan."
    assert told == {
        plain.format(amount=money("uz", 20000)),
        plain.format(amount=money("uz", 10000)) + warning,
        plain.format(amount=money("uz", 5000)),
    }

    # Nothing of it reaches the customer: not in the answer to sending, not on their page.
    for response in (first, again, different):
        assert "receipt_seen_before" not in response.json()
    mine = client.get(f"{ME}/{link}", headers=as_user(world.customer_of_a))
    assert "seen_before" not in mine.text and len(mine.json()["payment_notices"]) == 3
    # A notice without a receipt is never one seen before.
    plain_notice = client.post(
        f"{shop(world)}/payment-notices/{ids[0]}/decline",
        json={"reason": "Tekshirildi"},
        headers={**as_user(world.seller_a), "Idempotency-Key": f"test-{uuid.uuid4().hex}"},
    )
    assert "receipt_seen_before" not in plain_notice.json()


def test_the_same_receipt_in_another_shop_is_not_a_receipt_seen_before(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    # Shop B already holds exactly this file.
    owner.execute(
        "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime, created_at) "
        "VALUES (gen_random_uuid(), %s, 'payment_notice', 'bb/other', %s, %s, 'image/jpeg', now() - interval '1 day')",
        (world.shop_b, hashlib.sha256(JPEG).digest(), len(JPEG)),
    )
    notice = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000, JPEG).json()["id"]
    assert staff_list(client, world) == {notice: False}
    assert "⚠️" not in "".join(payload["text"] for _, payload in sent_to_staff(owner, world.shop_a))

    # And once shop A has it, a notice in shop B is judged by shop B's own files only.
    customer_b, notice_b = uuid.uuid4(), uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Begona', 'begona')",
        (customer_b, world.shop_b),
    )
    owner.execute(
        "INSERT INTO payment_notice (id, shop_id, customer_id, amount, file_id) "
        "SELECT %s, %s, %s, 1000, id FROM stored_file WHERE shop_id = %s",
        (notice_b, world.shop_b, customer_b, world.shop_b),
    )
    listed = client.get(f"/api/v1/shops/{world.shop_b}/payment-notices", headers=as_user(world.owner_b)).json()
    assert [(item["id"], item["receipt_seen_before"]) for item in listed["items"]] == [(str(notice_b), False)]


def test_a_receipt_is_seen_before_only_when_an_earlier_file_has_the_same_content(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    first = send(client, world.customer_of_a, link, 1000, PDF).json()["id"]
    assert receipt(client, f"{shop(world)}/payment-notices/{first}/receipt", as_user(world.owner_a)).content == PDF
    second = send(client, world.customer_of_a, link, 2000, PDF + b"\n").json()["id"]  # one byte more: another file
    third = send(client, world.customer_of_a, link, 3000, PDF).json()["id"]
    assert staff_list(client, world) == {first: False, second: False, third: True}


def test_without_a_file_store_no_link_is_given_either(
    app_database_url: str, world: World, owner: psycopg.Connection
) -> None:
    """A link nobody could follow is not handed out."""
    file_id, notice = uuid.uuid4(), uuid.uuid4()
    owner.execute(
        "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime) "
        "VALUES (%s, %s, 'payment_notice', 'cc/kept-elsewhere', %s, %s, 'image/jpeg')",
        (file_id, world.shop_a, hashlib.sha256(JPEG).digest(), len(JPEG)),
    )
    owner.execute(
        "INSERT INTO payment_notice (id, shop_id, customer_id, amount, file_id) VALUES (%s, %s, %s, 1000, %s)",
        (notice, world.shop_a, world.customer_a, file_id),
    )
    database = Database(app_database_url)
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        authenticator=HeaderAuthenticator(),
        secrets_key=TEST_SECRETS_KEY,
    )
    with TestClient(app) as storeless:
        refused = link_to(storeless, world, notice)
        assert (refused.status_code, refused.json()["error"]["code"]) == (503, "FILE_STORE_UNAVAILABLE")
        signed = sign(KEY, world.shop_a, file_id, expiry(datetime.now(UTC)))
        assert storeless.get(f"/files/{signed}").status_code == 503
        storeless.portal.call(database.dispose)  # type: ignore[union-attr]
