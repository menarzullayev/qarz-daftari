"""Customers, credit sales, payments, reversals and the overview (stories S4.1, S4.3, S4.4, S4.5)."""

import random
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.auth import AuthService
from qarz.domain import ledger
from qarz.domain.ledger import Entry, EntryKind
from qarz.domain.promise import tashkent_date
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app

from .conftest import TEST_BOT_TOKEN, HeaderAuthenticator, World, as_user

pytestmark = pytest.mark.db


def today() -> date:
    return tashkent_date(datetime.now(UTC))


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"test-{uuid.uuid4().hex}"}


def shop(world: World) -> str:
    return f"/api/v1/shops/{world.shop_a}"


def write(client: TestClient, user: uuid.UUID, method: str, path: str, body: Any = None) -> Any:
    return client.request(method, path, json=body, headers={**as_user(user), **key()})


def read(client: TestClient, user: uuid.UUID, path: str, **params: Any) -> Any:
    return client.get(path, params=params, headers=as_user(user))


def new_customer(client: TestClient, world: World, name: str, phone: str | None = None) -> str:
    body: dict[str, Any] = {"display_name": name}
    if phone is not None:
        body["phone"] = phone
    response = write(client, world.seller_a, "POST", f"{shop(world)}/customers", body)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def record(client: TestClient, world: World, customer: Any, kind: str, amount: Any, **extra: Any) -> Any:
    body = {"kind": kind, "amount": amount, **extra}
    return write(client, world.seller_a, "POST", f"{shop(world)}/customers/{customer}/entries", body)


def reverse(client: TestClient, world: World, entry: Any, user: uuid.UUID | None = None) -> Any:
    return write(client, user or world.manager_a, "POST", f"{shop(world)}/entries/{entry}/reversal")


def detail(client: TestClient, world: World, customer: Any) -> dict[str, Any]:
    response = read(client, world.seller_a, f"{shop(world)}/customers/{customer}")
    assert response.status_code == 200, response.text
    return dict(response.json())


def names(client: TestClient, world: World, **params: Any) -> list[str]:
    response = read(client, world.seller_a, f"{shop(world)}/customers", **params)
    assert response.status_code == 200, response.text
    return [item["display_name"] for item in response.json()["items"]]


def count(owner: psycopg.Connection, table: str, shop_id: uuid.UUID) -> int:
    row = owner.execute(f"SELECT count(*) FROM {table} WHERE shop_id = %s", (shop_id,)).fetchone()
    assert row is not None
    return int(row[0])


def seed_customer(owner: psycopg.Connection, shop_id: uuid.UUID, name: str) -> uuid.UUID:
    customer_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, %s, %s)",
        (customer_id, shop_id, name, name.lower()),
    )
    return customer_id


def seed_entry(
    owner: psycopg.Connection,
    world: World,
    customer: uuid.UUID,
    seq: int,
    kind: str,
    amount: int,
    *,
    promised: date | None = None,
    reverses: uuid.UUID | None = None,
    days_ago: float = 0,
    shop_id: uuid.UUID | None = None,
    author: uuid.UUID | None = None,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    created_at = datetime.now(UTC) - timedelta(days=days_ago)
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, reverses_id, author_id, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            entry_id,
            shop_id or world.shop_a,
            customer,
            seq,
            kind,
            amount,
            reverses,
            author or world.seller_a_membership,
            created_at,
        ),
    )
    if promised is not None:
        owner.execute(
            "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor, created_at) "
            "VALUES (%s, %s, %s, %s, 'staff', %s)",
            (uuid.uuid4(), shop_id or world.shop_a, entry_id, promised, created_at),
        )
    return entry_id


# --- customers (REQ-003, REQ-004, REQ-005) ------------------------------------------------------------


def test_a_customer_needs_only_a_name(client: TestClient, world: World) -> None:
    response = write(
        client, world.seller_a, "POST", f"{shop(world)}/customers", {"display_name": "  Alisher   Karimov "}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body == {
        "id": body["id"],
        "display_name": "Alisher Karimov",
        "phone": None,
        "status": "active",
        "reminders_off": False,
        "credit_limit": None,
        "balance": 0,
    }


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("90 123-45-67", "+998901234567"),
        ("998901234567", "+998901234567"),
        ("+998 (90) 123 45 67", "+998901234567"),
        ("+7 912 345 67 89", "+79123456789"),
        ("   ", None),
    ],
)
def test_a_phone_number_is_stored_in_one_format(
    client: TestClient, world: World, typed: str, stored: str | None
) -> None:
    response = write(
        client, world.seller_a, "POST", f"{shop(world)}/customers", {"display_name": "Telefonli", "phone": typed}
    )
    assert response.status_code == 201, response.text
    assert response.json()["phone"] == stored


@pytest.mark.parametrize(
    "body",
    [
        {"display_name": ""},
        {"display_name": "   "},
        {"display_name": "x" * 81},
        {"display_name": 5},
        {"display_name": "Ali", "phone": "12345"},
        {"display_name": "Ali", "phone": "abc"},
        {"display_name": "Ali", "phone": "+0123456789"},
        {"display_name": "Ali", "credit_limit": 1},
        {},
    ],
)
def test_an_invalid_customer_is_refused_and_not_stored(
    client: TestClient, world: World, owner: psycopg.Connection, body: dict[str, Any]
) -> None:
    before = count(owner, "customer", world.shop_a)
    response = write(client, world.seller_a, "POST", f"{shop(world)}/customers", body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION"
    assert count(owner, "customer", world.shop_a) == before


def test_search_ignores_case_and_script_and_matches_part_of_a_name_or_phone(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    new_customer(client, world, "Alisher Karimov", "+998901234567")
    new_customer(client, world, "Яхё Турсунов")
    new_customer(client, world, "Gulnora")
    seed_customer(owner, world.shop_b, "Alisher Boshqa")  # another shop's customer with the same name

    assert names(client, world, q="алишер") == ["Alisher Karimov"]
    assert names(client, world, q="KARIM") == ["Alisher Karimov"]
    assert names(client, world, q="yahyo") == ["Яхё Турсунов"]
    assert names(client, world, q="  Gulno ") == ["Gulnora"]
    assert names(client, world, q="4567") == ["Alisher Karimov"]
    assert names(client, world, q="90 123") == ["Alisher Karimov"]
    assert names(client, world, q="zzz") == []
    # Search text is matched literally: wildcard characters are not wildcards.
    assert names(client, world, q="%") == []
    assert names(client, world, q="_") == []
    assert names(client, world, q="a%r") == []
    # No search text: every active customer of this shop and none of another shop's, in name order.
    assert names(client, world) == ["Ali", "Alisher Karimov", "Gulnora", "Vali", "Яхё Турсунов"]
    assert names(client, world, status="archived") == ["Sobir"]


def test_the_customer_list_is_paged_by_cursor(client: TestClient, world: World) -> None:
    created = [f"Sahifa {letter}" for letter in "ABCDE"]
    for name in reversed(created):
        new_customer(client, world, name)

    seen: list[str] = []
    cursor: str | None = None
    for expected_size in (2, 2, 1):
        params: dict[str, Any] = {"q": "sahifa", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = read(client, world.seller_a, f"{shop(world)}/customers", **params).json()
        assert len(page["items"]) == expected_size
        seen += [item["display_name"] for item in page["items"]]
        cursor = page["next_cursor"]
    assert cursor is None
    assert seen == created


@pytest.mark.parametrize(
    "params",
    [{"cursor": "abc"}, {"cursor": "WyJhIl0"}, {"limit": 0}, {"limit": 101}, {"status": "all"}, {"q": "x" * 81}],
)
def test_bad_list_parameters_are_refused(client: TestClient, world: World, params: dict[str, Any]) -> None:
    for path in ("customers", "overview/debtors"):
        if path == "overview/debtors" and ("status" in params or "q" in params):
            continue
        response = read(client, world.seller_a, f"{shop(world)}/{path}", **params)
        assert response.status_code == 422, response.text


def test_a_manager_can_rename_a_customer_and_change_the_phone(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    path = f"{shop(world)}/customers/{world.customer_a}"
    renamed = write(client, world.manager_a, "PATCH", path, {"display_name": "Алишер", "phone": "901112233"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["display_name"] == "Алишер"
    assert renamed.json()["phone"] == "+998901112233"
    assert renamed.json()["balance"] == 50000
    assert names(client, world, q="alisher") == ["Алишер"]
    assert names(client, world, q="ali") == ["Алишер", "Vali"]  # in the order of the normalized names

    # A field that is not sent keeps its value; null removes the phone.
    kept = write(client, world.manager_a, "PATCH", path, {"reminders_off": True})
    assert (kept.json()["phone"], kept.json()["reminders_off"]) == ("+998901112233", True)
    removed = write(client, world.manager_a, "PATCH", path, {"phone": None})
    assert (removed.json()["phone"], removed.json()["reminders_off"]) == (None, True)

    assert write(client, world.manager_a, "PATCH", path, {}).status_code == 422
    assert write(client, world.manager_a, "PATCH", path, {"display_name": " "}).status_code == 422
    actions = owner.execute(
        "SELECT count(*) FROM activity WHERE shop_id = %s AND action = 'customer.updated' AND subject_id = %s",
        (world.shop_a, world.customer_a),
    ).fetchone()
    assert actions == (3,)


def test_a_customer_who_owes_cannot_be_archived(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    refused = write(client, world.owner_a, "POST", f"{shop(world)}/customers/{world.customer_a}/archive")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "CUSTOMER_HAS_BALANCE"
    assert owner.execute("SELECT status FROM customer WHERE id = %s", (world.customer_a,)).fetchone() == ("active",)


def test_archiving_hides_a_customer_and_blocks_new_entries_until_restored(client: TestClient, world: World) -> None:
    customer = world.settled_customer_a
    archived = write(client, world.manager_a, "POST", f"{shop(world)}/customers/{customer}/archive")
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"
    assert "Vali" not in names(client, world)
    assert names(client, world, status="archived") == ["Sobir", "Vali"]

    refused = record(client, world, customer, "credit", 45000)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "CUSTOMER_ARCHIVED")

    restored = write(client, world.manager_a, "POST", f"{shop(world)}/customers/{customer}/unarchive")
    assert restored.json()["status"] == "active"
    assert record(client, world, customer, "credit", 45000).status_code == 201


def test_a_repeated_create_request_makes_one_customer(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    headers = {**as_user(world.seller_a), **key()}
    path = f"{shop(world)}/customers"
    before = count(owner, "customer", world.shop_a)
    first = client.post(path, json={"display_name": "Bir marta"}, headers=headers)
    second = client.post(path, json={"display_name": "Bir marta"}, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert count(owner, "customer", world.shop_a) == before + 1

    other = client.post(path, json={"display_name": "Boshqa"}, headers=headers)
    assert (other.status_code, other.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert count(owner, "customer", world.shop_a) == before + 1


# --- credit sales and payments (REQ-007, REQ-009, REQ-010) ---------------------------------------------


def test_a_credit_sale_stores_total_time_author_and_default_promise(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    measured = {row[0] for row in owner.execute("SELECT id FROM measure.event").fetchall()}
    response = record(client, world, world.settled_customer_a, "credit", 45000, note="  non   va sut ")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["customer"]["balance"] == 45000  # the author sees the new balance (REQ-010)
    assert body["entry"]["seq"] == 1
    assert body["entry"]["note"] == "non va sut"
    assert body["entry"]["promised_date"] == (today() + timedelta(days=30)).isoformat()  # the shop default

    row = owner.execute(
        "SELECT kind, amount, author_id, now() - created_at < interval '1 minute' FROM ledger_entry WHERE id = %s",
        (body["entry"]["id"],),
    ).fetchone()
    assert row == ("credit", 45000, world.seller_a_membership, True)
    activity = owner.execute(
        "SELECT actor_id, subject_id FROM activity WHERE shop_id = %s AND action = 'ledger.credit_recorded'",
        (world.shop_a,),
    ).fetchall()
    assert activity == [(world.seller_a_membership, world.settled_customer_a)]

    # One measurement row, which names neither the shop nor the entry (ADR-010).
    # The row is found as the one that was not there before, not as the newest: the measurement tests
    # write rows dated years ahead, and those stay.
    added = [
        row[1:]
        for row in owner.execute("SELECT id, shop_ref, entry_ref, kind, amount, promised FROM measure.event").fetchall()
        if row[0] not in measured
    ]
    assert len(added) == 1
    event = added[0]
    assert event[2:] == ("credit", 45000, today() + timedelta(days=30))
    assert event[0] != world.shop_a
    assert str(event[1]) != body["entry"]["id"]


def test_the_shop_default_and_a_chosen_date_set_the_promise(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE shop SET default_promise_days = 10 WHERE id = %s", (world.shop_a,))
    by_default = record(client, world, world.settled_customer_a, "credit", 1000)
    assert by_default.json()["entry"]["promised_date"] == (today() + timedelta(days=10)).isoformat()

    chosen = (today() + timedelta(days=3)).isoformat()
    assert record(client, world, world.settled_customer_a, "credit", 1000, promised_date=chosen).json()["entry"][
        "promised_date"
    ] == (chosen)
    same_day = record(client, world, world.settled_customer_a, "credit", 1000, promised_date=today().isoformat())
    assert same_day.status_code == 201


def test_a_promised_date_outside_the_allowed_range_is_refused(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = count(owner, "ledger_entry", world.shop_a)
    customer = world.settled_customer_a
    for offset, reason in ((-1, "PROMISE_BEFORE_SALE"), (366, "PROMISE_TOO_FAR")):
        chosen = (today() + timedelta(days=offset)).isoformat()
        response = record(client, world, customer, "credit", 1000, promised_date=chosen)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["fields"] == {"promised_date": reason}
    with_date = record(client, world, world.customer_a, "payment", 1000, promised_date=today().isoformat())
    assert with_date.status_code == 422
    assert "promised_date" in with_date.json()["error"]["fields"]
    assert record(client, world, customer, "credit", 1000, promised_date="tomorrow").status_code == 422
    assert count(owner, "ledger_entry", world.shop_a) == before


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "credit", "amount": 0},
        {"kind": "credit", "amount": 99},
        {"kind": "credit", "amount": -5000},
        {"kind": "credit", "amount": 100_000_001},
        {"kind": "credit", "amount": "45000"},
        {"kind": "credit", "amount": 45000.5},
        {"kind": "credit", "amount": 45000.0},
        {"kind": "credit", "amount": True},
        {"kind": "credit", "amount": None},
        {"kind": "credit"},
        {"amount": 45000},
        {"kind": "opening", "amount": 45000},
        {"kind": "reversal", "amount": 45000},
        {"kind": "Credit", "amount": 45000},
        {"kind": "credit", "amount": 45000, "note": "x" * 201},
        {"kind": "credit", "amount": 45000, "author_id": "00000000-0000-4000-8000-000000000000"},
        {"kind": "credit", "amount": 45000, "created_at": "2020-01-01T00:00:00Z"},
    ],
)
def test_an_invalid_entry_is_refused_and_not_stored(
    client: TestClient, world: World, owner: psycopg.Connection, body: dict[str, Any]
) -> None:
    before = (count(owner, "ledger_entry", world.shop_a), count(owner, "promise", world.shop_a))
    response = write(client, world.seller_a, "POST", f"{shop(world)}/customers/{world.customer_a}/entries", body)
    assert response.status_code == 422, response.text
    assert (count(owner, "ledger_entry", world.shop_a), count(owner, "promise", world.shop_a)) == before


def test_amounts_at_the_limits_are_accepted(client: TestClient, world: World) -> None:
    assert record(client, world, world.settled_customer_a, "credit", 100).status_code == 201
    assert record(client, world, world.settled_customer_a, "credit", 100_000_000).status_code == 201


def test_payments_reduce_the_balance_and_cannot_exceed_it(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer = world.settled_customer_a
    assert record(client, world, customer, "credit", 100_000).json()["customer"]["balance"] == 100_000
    partial = record(client, world, customer, "payment", 30_000)
    assert partial.status_code == 201, partial.text
    assert partial.json()["customer"]["balance"] == 70_000
    assert partial.json()["entry"]["promised_date"] is None

    too_much = record(client, world, customer, "payment", 70_100)
    assert (too_much.status_code, too_much.json()["error"]["code"]) == (409, "EXCEEDS_BALANCE")
    assert record(client, world, customer, "payment", 70_000).json()["customer"]["balance"] == 0
    nothing_owed = record(client, world, customer, "payment", 100)
    assert (nothing_owed.status_code, nothing_owed.json()["error"]["code"]) == (409, "EXCEEDS_BALANCE")

    rows = owner.execute(
        "SELECT seq, kind, amount FROM ledger_entry WHERE customer_id = %s ORDER BY seq", (customer,)
    ).fetchall()
    assert rows == [(1, "credit", 100_000), (2, "payment", 30_000), (3, "payment", 70_000)]
    # Only the credit sale carries a promise.
    promises = owner.execute(
        "SELECT count(*) FROM promise p JOIN ledger_entry e ON e.id = p.entry_id WHERE e.customer_id = %s", (customer,)
    ).fetchone()
    assert promises == (1,)


def test_a_repeated_entry_request_writes_one_entry(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    headers = {**as_user(world.seller_a), **key()}
    path = f"{shop(world)}/customers/{world.customer_a}/entries"
    before = count(owner, "ledger_entry", world.shop_a)
    first = client.post(path, json={"kind": "credit", "amount": 45000}, headers=headers)
    second = client.post(path, json={"kind": "credit", "amount": 45000}, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert count(owner, "ledger_entry", world.shop_a) == before + 1

    changed = client.post(path, json={"kind": "credit", "amount": 46000}, headers=headers)
    assert (changed.status_code, changed.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert count(owner, "ledger_entry", world.shop_a) == before + 1


def test_entries_for_a_customer_of_another_shop_or_nobody_are_not_found(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    foreign = seed_customer(owner, world.shop_b, "Begona")
    before = (count(owner, "ledger_entry", world.shop_a), count(owner, "ledger_entry", world.shop_b))
    for customer in (foreign, uuid.uuid4()):
        response = record(client, world, customer, "credit", 45000)
        assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")
        assert read(client, world.owner_a, f"{shop(world)}/customers/{customer}").status_code == 404
        patch = write(client, world.owner_a, "PATCH", f"{shop(world)}/customers/{customer}", {"display_name": "X"})
        assert patch.status_code == 404
        assert write(client, world.owner_a, "POST", f"{shop(world)}/customers/{customer}/archive").status_code == 404
    assert (count(owner, "ledger_entry", world.shop_a), count(owner, "ledger_entry", world.shop_b)) == before
    assert owner.execute("SELECT display_name, status FROM customer WHERE id = %s", (foreign,)).fetchone() == (
        "Begona",
        "active",
    )


@contextmanager
def another_client(app_database_url: str) -> Iterator[TestClient]:
    """A second, independent application instance, as a second server process would be."""
    database = Database(app_database_url)
    app = create_app(
        database.reachable, database, auth=AuthService(database, TEST_BOT_TOKEN), authenticator=HeaderAuthenticator()
    )
    with TestClient(app) as test_client:
        yield test_client
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]


def test_two_payments_at_once_cannot_take_the_balance_below_zero(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    """INV-3 under concurrency: both requests pay the whole balance; exactly one may succeed."""
    with another_client(app_database_url) as second:
        for attempt in range(5):
            customer = new_customer(client, world, f"Poyga {attempt}")
            assert record(client, world, customer, "credit", 80_000).status_code == 201
            barrier = threading.Barrier(2)

            def pay(which: TestClient, customer: str = customer, barrier: threading.Barrier = barrier) -> int:
                barrier.wait(timeout=10)
                return int(record(which, world, customer, "payment", 80_000).status_code)

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = sorted(pool.map(pay, (client, second)))
            assert statuses == [201, 409]
            assert detail(client, world, customer)["balance"] == 0
            rows = owner.execute(
                "SELECT seq, kind FROM ledger_entry WHERE customer_id = %s ORDER BY seq", (customer,)
            ).fetchall()
            assert rows == [(1, "credit"), (2, "payment")]


def test_two_sales_at_once_both_land_in_order(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    with another_client(app_database_url) as second:
        customer = new_customer(client, world, "Ikki savdo")
        barrier = threading.Barrier(2)

        def sell(which: TestClient) -> int:
            barrier.wait(timeout=10)
            return int(record(which, world, customer, "credit", 10_000).status_code)

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(sell, (client, second))) == [201, 201]
        rows = owner.execute("SELECT seq FROM ledger_entry WHERE customer_id = %s ORDER BY seq", (customer,)).fetchall()
        assert rows == [(1,), (2,)]
        assert detail(client, world, customer)["balance"] == 20_000


# --- reversal (REQ-011, REQ-012) ----------------------------------------------------------------------


def test_a_reversal_cancels_an_entry_and_both_stay_visible(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    response = reverse(client, world, world.entry_a)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["customer"]["balance"] == 0
    assert body["entry"]["kind"] == "reversal"
    assert body["entry"]["amount"] == 50000
    assert body["entry"]["reverses_id"] == str(world.entry_a)

    history = detail(client, world, world.customer_a)
    assert history["balance"] == 0
    assert [(e["kind"], e["reversed"], e["reverses_id"]) for e in history["entries"]] == [
        ("reversal", False, str(world.entry_a)),
        ("credit", True, None),
    ]
    # The original row is untouched (REQ-011).
    original = owner.execute("SELECT kind, amount, seq FROM ledger_entry WHERE id = %s", (world.entry_a,)).fetchone()
    assert original == ("credit", 50000, 1)
    logged = owner.execute(
        "SELECT actor_id FROM activity WHERE shop_id = %s AND action = 'ledger.entry_reversed'", (world.shop_a,)
    ).fetchall()
    assert logged == [(world.manager_a_membership,)]

    again = reverse(client, world, world.entry_a)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "ALREADY_REVERSED")
    of_reversal = reverse(client, world, body["entry"]["id"])
    assert (of_reversal.status_code, of_reversal.json()["error"]["code"]) == (409, "CANNOT_REVERSE_REVERSAL")
    assert count(owner, "ledger_entry", world.shop_a) == 2


def test_a_sale_that_has_been_paid_cannot_be_reversed_until_the_payment_is(client: TestClient, world: World) -> None:
    payment = record(client, world, world.customer_a, "payment", 50000).json()["entry"]["id"]
    refused = reverse(client, world, world.entry_a)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "WOULD_GO_NEGATIVE")
    assert detail(client, world, world.customer_a)["balance"] == 0

    undone = reverse(client, world, payment, world.owner_a)
    assert undone.status_code == 201, undone.text
    assert undone.json()["customer"]["balance"] == 50000
    assert reverse(client, world, world.entry_a).json()["customer"]["balance"] == 0


def test_an_entry_of_another_shop_or_no_entry_cannot_be_reversed(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    foreign_customer = seed_customer(owner, world.shop_b, "Begona")
    author = owner.execute("SELECT id FROM membership WHERE shop_id = %s", (world.shop_b,)).fetchone()
    assert author is not None
    foreign_entry = seed_entry(
        owner, world, foreign_customer, 1, "credit", 70000, promised=today(), shop_id=world.shop_b, author=author[0]
    )
    for entry in (foreign_entry, uuid.uuid4()):
        response = reverse(client, world, entry, world.owner_a)
        assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert count(owner, "ledger_entry", world.shop_b) == 1
    assert count(owner, "ledger_entry", world.shop_a) == 1


def test_a_repeated_reversal_request_writes_one_reversal(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    headers = {**as_user(world.manager_a), **key()}
    path = f"{shop(world)}/entries/{world.entry_a}/reversal"
    first, second = client.post(path, headers=headers), client.post(path, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert count(owner, "ledger_entry", world.shop_a) == 2


# --- subscription state (BR-29, BR-30) ----------------------------------------------------------------


def _subscription(owner: psycopg.Connection, world: World, sql: str) -> None:
    owner.execute(f"UPDATE subscription SET {sql} WHERE shop_id = %s", (world.shop_a,))


@pytest.mark.parametrize(
    "state_sql",
    [
        "state = 'trial', trial_ends = current_date - 1",
        "state = 'active', paid_through = current_date - 1",
        "state = 'active', paid_through = NULL",
        "state = 'limited'",
    ],
)
def test_in_limited_mode_only_new_credit_sales_are_refused(
    client: TestClient, world: World, owner: psycopg.Connection, state_sql: str
) -> None:
    _subscription(owner, world, state_sql)
    before = count(owner, "ledger_entry", world.shop_a)
    refused = record(client, world, world.customer_a, "credit", 45000)
    assert (refused.status_code, refused.json()["error"]["code"]) == (402, "SUBSCRIPTION_LIMITED")
    assert count(owner, "ledger_entry", world.shop_a) == before

    assert record(client, world, world.customer_a, "payment", 10000).status_code == 201
    assert reverse(client, world, world.entry_a).status_code == 409  # refused by the ledger rule, not the mode
    new_customer(client, world, "Cheklangan rejimda")
    assert detail(client, world, world.customer_a)["balance"] == 40000
    assert read(client, world.seller_a, f"{shop(world)}/overview").status_code == 200


def test_a_reversal_is_allowed_in_limited_mode(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    _subscription(owner, world, "state = 'limited'")
    assert reverse(client, world, world.entry_a).status_code == 201


def test_a_shop_without_a_subscription_is_limited(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    owner.execute("DELETE FROM subscription WHERE shop_id = %s", (world.shop_a,))
    refused = record(client, world, world.customer_a, "credit", 45000)
    assert (refused.status_code, refused.json()["error"]["code"]) == (402, "SUBSCRIPTION_LIMITED")


@pytest.mark.parametrize(
    ("state_sql", "day_offset", "status"),
    [
        ("state = 'trial', trial_ends = %s", 0, 201),
        ("state = 'active', paid_through = %s, trial_ends = NULL", 0, 201),
        ("state = 'trial', trial_ends = %s", -1, 402),
        ("state = 'active', paid_through = %s, trial_ends = NULL", -1, 402),
    ],
)
def test_a_period_ends_with_its_last_day_in_tashkent(
    client: TestClient, world: World, owner: psycopg.Connection, state_sql: str, day_offset: int, status: int
) -> None:
    """The last day is the Tashkent calendar day, whatever day it is for the database server."""
    owner.execute(
        f"UPDATE subscription SET {state_sql} WHERE shop_id = %s", (today() + timedelta(days=day_offset), world.shop_a)
    )
    assert record(client, world, world.customer_a, "credit", 45000).status_code == status


def test_a_suspended_shop_accepts_no_writes_and_only_the_owner_may_look(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    _subscription(owner, world, "state = 'suspended'")
    before = (count(owner, "ledger_entry", world.shop_a), count(owner, "customer", world.shop_a))
    base = shop(world)
    writes = [
        ("POST", f"{base}/customers", {"display_name": "Yangi"}),
        ("PATCH", f"{base}/customers/{world.customer_a}", {"display_name": "Yangi"}),
        ("POST", f"{base}/customers/{world.settled_customer_a}/archive", None),
        ("POST", f"{base}/customers/{world.archived_customer_a}/unarchive", None),
        ("POST", f"{base}/customers/{world.customer_a}/entries", {"kind": "credit", "amount": 45000}),
        ("POST", f"{base}/customers/{world.customer_a}/entries", {"kind": "payment", "amount": 1000}),
        ("POST", f"{base}/entries/{world.entry_a}/reversal", None),
    ]
    for method, path, body in writes:
        response = write(client, world.owner_a, method, path, body)
        assert (response.status_code, response.json()["error"]["code"]) == (403, "SHOP_SUSPENDED"), path
    assert (count(owner, "ledger_entry", world.shop_a), count(owner, "customer", world.shop_a)) == before
    assert owner.execute("SELECT display_name, status FROM customer WHERE id = %s", (world.customer_a,)).fetchone() == (
        "Ali",
        "active",
    )

    reads = [
        f"{base}/customers",
        f"{base}/customers/{world.customer_a}",
        f"{base}/overview",
        f"{base}/overview/debtors",
    ]
    for path in reads:
        assert read(client, world.owner_a, path).status_code == 200, path
        for staff in (world.manager_a, world.seller_a):
            response = read(client, staff, path)
            assert (response.status_code, response.json()["error"]["code"]) == (403, "SHOP_SUSPENDED"), path


# --- overview and customer detail (REQ-026, REQ-027, REQ-045) ------------------------------------------


def test_the_overview_of_the_seeded_shop(client: TestClient, world: World) -> None:
    assert read(client, world.seller_a, f"{shop(world)}/overview").json() == {
        "outstanding": 50000,
        "debtors": 1,
        "overdue": {"amount": 0, "customers": 0},
        "due_today": 0,
    }
    # Another shop sees nothing of it.
    assert read(client, world.owner_b, f"/api/v1/shops/{world.shop_b}/overview").json()["outstanding"] == 0


def test_overdue_follows_oldest_first_allocation_and_promised_dates(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    late = seed_customer(owner, world.shop_a, "Kechikkan")
    seed_entry(owner, world, late, 1, "credit", 100_000, promised=today() - timedelta(days=10), days_ago=20)
    seed_entry(owner, world, late, 2, "credit", 50_000, promised=today(), days_ago=5)
    seed_entry(owner, world, late, 3, "credit", 20_000, promised=today() + timedelta(days=5), days_ago=1)
    seed_entry(owner, world, late, 4, "payment", 30_000)

    expected_overdue = {
        "amount": 70_000,  # the payment went to the oldest sale
        "since": (today() - timedelta(days=10)).isoformat(),
        "days": 10,
        "due_today": 50_000,
    }
    view = detail(client, world, late)
    assert view["balance"] == 140_000
    assert view["overdue"] == expected_overdue
    assert view["payment_history"] == {
        "on_time_percent": 0,
        "on_time_amount": 0,
        "due_amount": 100_000,
        "longest_delay_days": 10,
    }
    assert [e["seq"] for e in view["entries"]] == [4, 3, 2, 1]
    assert view["entries_total"] == 4
    assert view["entries"][3]["promised_date"] == (today() - timedelta(days=10)).isoformat()

    assert read(client, world.seller_a, f"{shop(world)}/overview").json() == {
        "outstanding": 190_000,
        "debtors": 2,
        "overdue": {"amount": 70_000, "customers": 1},
        "due_today": 50_000,
    }
    everyone = read(client, world.seller_a, f"{shop(world)}/overview/debtors").json()
    assert [(i["display_name"], i["balance"]) for i in everyone["items"]] == [("Kechikkan", 140_000), ("Ali", 50_000)]
    assert everyone["items"][0]["overdue"] == expected_overdue
    assert everyone["items"][1]["overdue"] == {"amount": 0, "since": None, "days": 0, "due_today": 0}
    assert everyone["next_cursor"] is None
    only_late = read(client, world.seller_a, f"{shop(world)}/overview/debtors", overdue=True).json()
    assert [i["display_name"] for i in only_late["items"]] == ["Kechikkan"]


def test_the_newest_promise_is_the_current_one(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    customer = seed_customer(owner, world.shop_a, "Muddat")
    entry = seed_entry(owner, world, customer, 1, "credit", 10_000, promised=today() - timedelta(days=3), days_ago=9)
    assert detail(client, world, customer)["overdue"]["amount"] == 10_000
    owner.execute(
        "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor) VALUES (%s, %s, %s, %s, 'staff')",
        (uuid.uuid4(), world.shop_a, entry, today() + timedelta(days=4)),
    )
    assert detail(client, world, customer)["overdue"] == {"amount": 0, "since": None, "days": 0, "due_today": 0}
    listed = read(client, world.seller_a, f"{shop(world)}/overview/debtors", overdue=True).json()
    assert "Muddat" not in [i["display_name"] for i in listed["items"]]


def test_the_payment_history_indicator_counts_what_was_repaid_in_time(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer = seed_customer(owner, world.shop_a, "Intizomli")
    seed_entry(owner, world, customer, 1, "credit", 100_000, promised=today() - timedelta(days=10), days_ago=20)
    seed_entry(owner, world, customer, 2, "payment", 75_000, days_ago=15)
    seed_entry(owner, world, customer, 3, "payment", 25_000, days_ago=6)
    assert detail(client, world, customer)["payment_history"] == {
        "on_time_percent": 75,
        "on_time_amount": 75_000,
        "due_amount": 100_000,
        "longest_delay_days": 4,
    }
    # Nothing has fallen due yet: no indicator rather than a misleading 0 or 100.
    assert detail(client, world, world.customer_a)["payment_history"] is None


def test_debtors_are_paged_largest_balance_first(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    balances = [90_000, 70_000, 70_000, 60_000]  # with Ali's 50 000 that makes five debtors, two of them equal
    for index, amount in enumerate(balances):
        customer = seed_customer(owner, world.shop_a, f"Qarzdor {index}")
        seed_entry(owner, world, customer, 1, "credit", amount, promised=today() + timedelta(days=1))

    seen: list[tuple[str, int]] = []
    cursor: str | None = None
    for expected_size in (2, 2, 1):
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = read(client, world.seller_a, f"{shop(world)}/overview/debtors", **params).json()
        assert len(page["items"]) == expected_size
        seen += [(item["id"], item["balance"]) for item in page["items"]]
        cursor = page["next_cursor"]
    assert cursor is None
    assert [balance for _, balance in seen] == [90_000, 70_000, 70_000, 60_000, 50_000]
    assert len({customer for customer, _ in seen}) == 5


def _random_account(rng: random.Random, start: date) -> list[Entry]:
    """A valid account history built only from operations the ledger rules allow."""
    entries: list[Entry] = []
    at = datetime.now(UTC) - timedelta(days=60)
    for seq in range(1, rng.randint(2, 14)):
        at += timedelta(hours=rng.randint(1, 100))
        owed = ledger.balance(entries)
        choice = rng.random()
        candidate: Entry | None = None
        if choice < 0.5 or not entries:
            promised = start + timedelta(days=rng.randint(-20, 10))
            candidate = Entry(uuid.uuid4(), seq, EntryKind.CREDIT, rng.randrange(100, 200_000, 100), at, None, promised)
        elif choice < 0.85 and owed > 0:
            candidate = Entry(uuid.uuid4(), seq, EntryKind.PAYMENT, rng.randint(1, owed), at)
        else:
            target = rng.choice(entries)
            candidate = Entry(uuid.uuid4(), seq, EntryKind.REVERSAL, target.amount, at, target.id)
        if ledger.validate_new_entry(entries, candidate.kind, candidate.amount, candidate.reverses_id) is None:
            entries.append(candidate)
    return [
        Entry(e.id, index, e.kind, e.amount, e.created_at, e.reverses_id, e.promised_date)
        for index, e in enumerate(entries, 1)
    ]


def test_the_database_figures_agree_with_the_domain_rules_on_generated_accounts(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """Lists and totals are computed in SQL; the customer detail is computed by `qarz.domain.ledger`."""
    rng = random.Random(20261006)  # noqa: S311 - a fixed seed for repeatable test data, not a secret
    expected: dict[str, tuple[int, dict[str, Any]]] = {}
    for index in range(40):
        customer = seed_customer(owner, world.shop_a, f"Tasodifiy {index:02d}")
        account = _random_account(rng, today())
        for entry in account:
            owner.execute(
                "INSERT INTO ledger_entry"
                " (id, shop_id, customer_id, seq, kind, amount, reverses_id, author_id, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    entry.id,
                    world.shop_a,
                    customer,
                    entry.seq,
                    entry.kind.value,
                    entry.amount,
                    entry.reverses_id,
                    world.seller_a_membership,
                    entry.created_at,
                ),
            )
            if entry.promised_date is not None:
                owner.execute(
                    "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor) "
                    "VALUES (%s, %s, %s, %s, 'staff')",
                    (uuid.uuid4(), world.shop_a, entry.id, entry.promised_date),
                )
        status = ledger.overdue(account, today())
        expected[str(customer)] = (
            ledger.balance(account),
            {
                "amount": status.overdue_amount,
                "since": None
                if status.earliest_unmet_promised_date is None
                else status.earliest_unmet_promised_date.isoformat(),
                "days": status.days_overdue,
                "due_today": status.due_today_amount,
            },
        )
    expected[str(world.customer_a)] = (50_000, {"amount": 0, "since": None, "days": 0, "due_today": 0})

    listed: dict[str, tuple[int, dict[str, Any]]] = {}
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 7}
        if cursor:
            params["cursor"] = cursor
        page = read(client, world.seller_a, f"{shop(world)}/overview/debtors", **params).json()
        listed |= {item["id"]: (item["balance"], item["overdue"]) for item in page["items"]}
        cursor = page["next_cursor"]
        if cursor is None:
            break

    owing = {customer: figures for customer, figures in expected.items() if figures[0] > 0}
    assert len(owing) > 15, "the generator must produce enough debtors to mean something"
    assert sum(1 for _, overdue in owing.values() if overdue["amount"] > 0) > 5
    assert listed == owing

    # The same accounts through the customer detail and the customer list.
    for customer, (balance, overdue) in expected.items():
        view = detail(client, world, customer)
        assert (view["balance"], view["overdue"]) == (balance, overdue)
    in_list = read(client, world.seller_a, f"{shop(world)}/customers", q="tasodifiy", limit=100).json()["items"]
    assert {item["id"]: item["balance"] for item in in_list} == {
        customer: balance for customer, (balance, _) in expected.items() if customer != str(world.customer_a)
    }

    overdue_only = read(client, world.seller_a, f"{shop(world)}/overview/debtors", overdue=True, limit=100).json()
    assert {item["id"] for item in overdue_only["items"]} == {c for c, (_, o) in owing.items() if o["amount"] > 0}
    assert read(client, world.seller_a, f"{shop(world)}/overview").json() == {
        "outstanding": sum(balance for balance, _ in owing.values()),
        "debtors": len(owing),
        "overdue": {
            "amount": sum(o["amount"] for _, o in owing.values()),
            "customers": sum(1 for _, o in owing.values() if o["amount"] > 0),
        },
        "due_today": sum(o["due_today"] for _, o in owing.values()),
    }


# --- the lists read only what they show (S19.1 load test) ---------------------------------------------


def test_every_page_of_the_customer_list_carries_its_own_balances(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """Balances are read for the customers of the page only; each page must still show the right ones."""
    expected: dict[str, int] = {}
    for index in range(7):
        customer = seed_customer(owner, world.shop_a, f"Sahifa {index}")
        if index == 0:
            expected[str(customer)] = 0  # no entries at all
            continue
        seed_entry(owner, world, customer, 1, "credit", 10_000 * index, promised=today(), days_ago=3)
        seed_entry(owner, world, customer, 2, "payment", 1_000 * index, days_ago=2)
        balance = 9_000 * index
        if index % 3 == 0:
            # A second sale, reversed: it and its reversal both drop out of the balance.
            other = seed_entry(owner, world, customer, 3, "credit", 4_000, promised=today(), days_ago=1)
            seed_entry(owner, world, customer, 4, "reversal", 4_000, reverses=other)
        elif index % 3 == 1:
            seed_entry(owner, world, customer, 3, "credit", 500, promised=today(), days_ago=1)
            balance += 500
        expected[str(customer)] = balance

    seen: dict[str, int] = {}
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"q": "sahifa", "limit": 3}
        if cursor:
            params["cursor"] = cursor
        page = read(client, world.seller_a, f"{shop(world)}/customers", **params).json()
        assert not set(seen) & {item["id"] for item in page["items"]}
        seen |= {item["id"]: item["balance"] for item in page["items"]}
        cursor = page["next_cursor"]
        pages += 1
        if cursor is None:
            break
    assert pages == 3
    assert seen == expected
    # A customer of another shop with the same name adds nothing to anyone's balance here.
    stranger = seed_customer(owner, world.shop_b, "Sahifa 1")
    member_b = owner.execute("SELECT id FROM membership WHERE shop_id = %s LIMIT 1", (world.shop_b,)).fetchone()
    assert member_b is not None
    seed_entry(owner, world, stranger, 1, "credit", 777_000, promised=today(), shop_id=world.shop_b, author=member_b[0])
    again = read(client, world.seller_a, f"{shop(world)}/customers", q="sahifa", limit=100).json()["items"]
    assert {item["id"]: item["balance"] for item in again} == expected


def test_a_promise_on_a_covered_sale_changes_no_figure(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The lists look up the promised date of uncovered debts only; a covered one must not be missed for it."""
    customer = seed_customer(owner, world.shop_a, "Qoplangan")
    paid_off = seed_entry(
        owner, world, customer, 1, "credit", 30_000, promised=today() - timedelta(days=30), days_ago=40
    )
    seed_entry(owner, world, customer, 2, "credit", 20_000, promised=today() - timedelta(days=2), days_ago=10)
    seed_entry(owner, world, customer, 3, "credit", 5_000, promised=today(), days_ago=1)
    # Covers the first sale entirely and 4 000 of the second.
    seed_entry(owner, world, customer, 4, "payment", 34_000)

    def listed() -> dict[str, Any]:
        items = read(client, world.seller_a, f"{shop(world)}/overview/debtors", limit=100).json()["items"]
        return next(dict(item) for item in items if item["display_name"] == "Qoplangan")

    since = (today() - timedelta(days=2)).isoformat()
    expected = {"amount": 16_000, "since": since, "days": 2, "due_today": 5_000}
    assert (listed()["balance"], listed()["overdue"]) == (21_000, expected)
    assert detail(client, world, customer)["overdue"] == expected
    before = read(client, world.seller_a, f"{shop(world)}/overview").json()

    # Moving the promise of the covered sale, in either direction, is seen nowhere.
    owner.execute(
        "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor) VALUES (%s, %s, %s, %s, 'staff')",
        (uuid.uuid4(), world.shop_a, paid_off, today() - timedelta(days=300)),
    )
    assert (listed()["balance"], listed()["overdue"]) == (21_000, expected)
    assert detail(client, world, customer)["overdue"] == expected
    assert read(client, world.seller_a, f"{shop(world)}/overview").json() == before
