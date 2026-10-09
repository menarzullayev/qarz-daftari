"""The cash book (expansion module H; BR-44 to BR-51).

Behind the platform switch `cash_book_on`. Each rule here has the case that must work and the case that
must be refused; who may call what, by role and as an outsider, is in the authorization suite, and how
the ledger feeds the book is in test_cash_book_ledger.py.
"""

import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.domain import cash

from .conftest import World, as_user, set_overrides, switch_permissions_on
from .test_customers_ledger import key, read, shop, today, write

pytestmark = pytest.mark.db

NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "Topilmadi.", "fields": {}}}


def switch(owner: psycopg.Connection, value: str = "true") -> None:
    """Store the switch as an administrator's change would (`value` is JSON). The row is signed with a
    user identifier, so the `admin_env` fixture behind `client` removes it after the test."""
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('cash_book_on', %s::jsonb, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by",
        (value, str(uuid.uuid4())),
    )


@pytest.fixture
def on(client: TestClient, owner: psycopg.Connection) -> Iterator[None]:
    switch(owner)
    yield


def base(world: World) -> str:
    return f"{shop(world)}/cash"


def categories(client: TestClient, world: World, user: uuid.UUID | None = None) -> list[dict[str, Any]]:
    response = read(client, user or world.manager_a, f"{base(world)}/categories")
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def category(client: TestClient, world: World, direction: str, name: str) -> str:
    """The identifier of one of the shop's categories, by its name."""
    found = [item for item in categories(client, world) if item["direction"] == direction and item["name"] == name]
    assert len(found) == 1, (direction, name)
    return str(found[0]["id"])


def add(
    client: TestClient,
    world: World,
    direction: str,
    amount: Any,
    *,
    name: str | None = None,
    method: str = "cash",
    user: uuid.UUID | None = None,
    **extra: Any,
) -> Any:
    chosen = name or ("Savdo" if direction == "income" else "Ijara")
    body = {
        "direction": direction,
        "method": method,
        "amount": amount,
        "category_id": extra.pop("category_id", None) or category(client, world, direction, chosen),
        **extra,
    }
    return write(client, user or world.manager_a, "POST", f"{base(world)}/entries", body)


def added(client: TestClient, world: World, direction: str, amount: Any, **extra: Any) -> dict[str, Any]:
    response = add(client, world, direction, amount, **extra)
    assert response.status_code == 201, response.text
    return dict(response.json()["entry"])


def cancel(client: TestClient, world: World, entry: Any, reason: Any = "Xato yozilgan", user: Any = None) -> Any:
    return write(
        client, user or world.manager_a, "POST", f"{base(world)}/entries/{entry}/cancellation", {"reason": reason}
    )


def day(client: TestClient, world: World, user: uuid.UUID | None = None, **params: Any) -> dict[str, Any]:
    response = read(client, user or world.manager_a, f"{base(world)}/day", **params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def line(book: dict[str, Any], method: str, currency: str = "UZS") -> dict[str, Any]:
    found = [item for item in book["balances"] if item["method"] == method and item["currency"] == currency]
    assert len(found) == 1, (method, currency, book["balances"])
    return dict(found[0])


def figures(item: dict[str, Any]) -> tuple[int, int, int, int]:
    return item["opening"], item["income"], item["expense"], item["closing"]


def rows(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT direction, method, currency, amount, note, day, cancelled_at IS NOT NULL, cancel_reason "
        "FROM cash_entry WHERE shop_id = %s ORDER BY created_at, id",
        (world.shop_a,),
    ).fetchall()


def cash_rows_of_the_world(owner: psycopg.Connection, world: World) -> tuple[int, int]:
    row = owner.execute(
        "SELECT (SELECT count(*) FROM cash_entry WHERE shop_id IN (%(a)s, %(b)s)), "
        "(SELECT count(*) FROM cash_category WHERE shop_id IN (%(a)s, %(b)s))",
        {"a": world.shop_a, "b": world.shop_b},
    ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


def actions(owner: psycopg.Connection, world: World) -> list[str]:
    return [
        str(row[0])
        for row in owner.execute(
            "SELECT action FROM activity WHERE shop_id = %s AND action LIKE 'cash.%%' ORDER BY at, id",
            (world.shop_a,),
        ).fetchall()
    ]


def refused(response: Any, status: int, code: str) -> dict[str, str]:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code, response.text
    return dict(response.json()["error"]["fields"])


# --- the switch ------------------------------------------------------------------------------------------


def _every_route(world: World) -> list[tuple[str, str, Any]]:
    some = uuid.uuid4()
    return [
        ("GET", f"{base(world)}/day", None),
        ("GET", f"{base(world)}/summary?from={today()}&to={today()}", None),
        ("GET", f"{base(world)}/categories", None),
        ("POST", f"{base(world)}/categories", {"direction": "expense", "name": "Soliq"}),
        ("PATCH", f"{base(world)}/categories/{some}", {"name": "Soliq"}),
        ("DELETE", f"{base(world)}/categories/{some}", None),
        (
            "POST",
            f"{base(world)}/entries",
            {"direction": "income", "method": "cash", "amount": 5000, "category_id": str(some)},
        ),
        ("POST", f"{base(world)}/entries/{some}/cancellation", {"reason": "xato"}),
        ("POST", f"{base(world)}/backfill", {}),
    ]


@pytest.mark.parametrize("stored", [None, "false", '"true"', "1", "null"])
def test_with_the_switch_off_no_route_of_the_module_exists_for_anyone(
    client: TestClient, world: World, owner: psycopg.Connection, stored: str | None
) -> None:
    """Off is the default (no row), and only the JSON value `true` is on. The owner, a stranger and
    someone who is not signed in all get the answer of a route that was never there, and nothing is
    written: not even the default categories."""
    if stored is not None:
        switch(owner, stored)
    for method, path, body in _every_route(world):
        for caller in (as_user(world.owner_a), as_user(world.stranger), {}):
            response = client.request(method, path, json=body, headers={**caller, **key()})
            unknown = client.request(method, "/api/v1/no-such-route", json=body, headers={**caller, **key()})
            assert response.status_code == unknown.status_code == 404, (method, path, response.text)
            assert response.json() == unknown.json() == NOT_FOUND
    assert cash_rows_of_the_world(owner, world) == (0, 0)
    assert actions(owner, world) == []


def test_the_check_above_would_notice_a_route_that_answered(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    """The counterpart: with the switch on the same requests are answered as themselves."""
    statuses = [
        client.request(method, path, json=body, headers={**as_user(world.owner_a), **key()}).status_code
        for method, path, body in _every_route(world)
    ]
    # The category and the entry named are not the shop's: not found, and a category that is not valid.
    assert statuses == [200, 200, 200, 201, 404, 404, 422, 404, 200]


def test_a_client_learns_that_the_cash_book_is_on_from_a_header_and_only_then(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = client.get("/api/v1/me/shops", headers=as_user(world.manager_a))
    assert "x-qarz-cash-book" not in before.headers
    switch(owner)
    after = client.get("/api/v1/me/shops", headers=as_user(world.manager_a))
    assert after.headers["x-qarz-cash-book"] == "on"
    # The body is the one it always was.
    assert after.json() == before.json()
    switch(owner, "false")
    assert "x-qarz-cash-book" not in client.get("/api/v1/me/shops", headers=as_user(world.manager_a)).headers


# --- categories ------------------------------------------------------------------------------------------


def test_the_first_use_writes_the_default_categories_once_in_the_shops_language(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    listed = categories(client, world)
    assert [(item["direction"], item["name"]) for item in listed] == [
        ("income", "Boshlang'ich qoldiq"),
        ("income", "Boshqa kirim"),
        ("income", "Qarz qaytdi"),
        ("income", "Savdo"),
        ("expense", "Boshqa chiqim"),
        ("expense", "Ijara"),
        ("expense", "Ish haqi"),
        ("expense", "Kommunal to'lovlar"),
        ("expense", "Tovar xaridi"),
        ("expense", "Transport"),
    ]
    assert [item["name"] for item in listed if item["fixed"]] == ["Qarz qaytdi"]
    assert not any(item["archived"] for item in listed)
    # With them, what an entry may be written in: this shop works in so'm alone.
    assert read(client, world.manager_a, f"{base(world)}/categories").json()["currencies"] == ["UZS"]
    # Asked again, by somebody else: the same ten, not ten more.
    assert categories(client, world, world.owner_a) == listed
    assert cash_rows_of_the_world(owner, world) == (0, 10)


def test_a_russian_shop_gets_russian_names_and_another_shop_gets_its_own(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    owner.execute("UPDATE shop SET lang = 'ru' WHERE id = %s", (world.shop_b,))
    response = read(client, world.owner_b, f"/api/v1/shops/{world.shop_b}/cash/categories")
    assert response.status_code == 200, response.text
    names = {item["name"] for item in response.json()["items"]}
    assert {"Продажи", "Возврат долга", "Аренда", "Зарплата"} <= names
    # Shop A has none of them, and nothing of its own yet: nobody used its book.
    assert owner.execute("SELECT count(*) FROM cash_category WHERE shop_id = %s", (world.shop_a,)).fetchone() == (0,)


def new_category(client: TestClient, world: World, direction: str, name: Any, user: Any = None) -> Any:
    return write(
        client, user or world.manager_a, "POST", f"{base(world)}/categories", {"direction": direction, "name": name}
    )


def test_a_category_is_added_and_its_name_is_its_own_within_a_direction(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    made = new_category(client, world, "expense", "  Soliq   to'lovi ")
    assert made.status_code == 201, made.text
    assert made.json() == {
        "id": made.json()["id"],
        "direction": "expense",
        "name": "Soliq to'lovi",
        "archived": False,
        "fixed": False,
    }
    # The same name in another case, with another apostrophe, or in the other alphabet is the same name.
    for again in ("soliq to'lovi", "Soliq to‘lovi", "SOLIQ TO'LOVI", "Солиқ тўлови"):
        refused(new_category(client, world, "expense", again), 409, "CASH_CATEGORY_NAME_TAKEN")
    # Income has a list of its own.
    assert new_category(client, world, "income", "Soliq to'lovi").status_code == 201
    assert actions(owner, world) == ["cash.category_created", "cash.category_created"]


@pytest.mark.parametrize("name", ["", "   ", "ь", "-", "x" * 61])
def test_a_category_needs_a_usable_name(client: TestClient, world: World, on: None, name: str) -> None:
    assert "name" in refused(new_category(client, world, "expense", name), 422, "VALIDATION")


@pytest.mark.parametrize("direction", ["", "both", "INCOME", "kirim"])
def test_a_category_is_of_income_or_of_expense(client: TestClient, world: World, on: None, direction: str) -> None:
    assert "direction" in refused(new_category(client, world, direction, "Soliq"), 422, "VALIDATION")


def test_a_shop_has_a_limited_number_of_categories(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    categories(client, world)
    for number in range(cash.MAX_CATEGORIES - 10):
        owner.execute(
            "INSERT INTO cash_category (id, shop_id, direction, name, name_norm) "
            "VALUES (gen_random_uuid(), %s, 'expense', %s, %s)",
            (world.shop_a, f"Toifa {number}", f"toifa {number}"),
        )
    assert "name" in refused(new_category(client, world, "expense", "Yana bitta"), 422, "VALIDATION")
    # One fewer, and the same request goes through.
    owner.execute("DELETE FROM cash_category WHERE shop_id = %s AND name = 'Toifa 0'", (world.shop_a,))
    assert new_category(client, world, "expense", "Yana bitta").status_code == 201


def patch(client: TestClient, world: World, category_id: Any, user: Any = None, **change: Any) -> Any:
    return write(client, user or world.manager_a, "PATCH", f"{base(world)}/categories/{category_id}", change)


def test_a_category_is_renamed_but_not_to_a_name_that_is_taken(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    rent = category(client, world, "expense", "Ijara")
    renamed = patch(client, world, rent, name="Do'kon ijarasi")
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Do'kon ijarasi"
    refused(patch(client, world, rent, name="transport"), 409, "CASH_CATEGORY_NAME_TAKEN")
    # Its own name again, written differently, is not a clash with itself.
    assert patch(client, world, rent, name="DO'KON IJARASI").json()["name"] == "DO'KON IJARASI"
    # A name of the other direction is free.
    assert patch(client, world, rent, name="Savdo").status_code == 200
    assert "name" in refused(patch(client, world, rent, name="  "), 422, "VALIDATION")
    assert "_" in refused(patch(client, world, rent), 422, "VALIDATION")
    refused(patch(client, world, uuid.uuid4(), name="Yangi"), 404, "NOT_FOUND")


def test_an_archived_category_takes_no_new_entry_and_keeps_its_old_ones(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    rent = category(client, world, "expense", "Ijara")
    before = added(client, world, "expense", 300_000, name="Ijara")
    assert patch(client, world, rent, archived=True).json()["archived"] is True
    refused(add(client, world, "expense", 300_000, category_id=rent), 409, "CASH_CATEGORY_ARCHIVED")
    # The entry written before is still in the book, under its category.
    shown = day(client, world)["entries"]
    assert [(item["id"], item["category"]["name"]) for item in shown] == [(before["id"], "Ijara")]
    # Brought back, it takes entries again.
    assert patch(client, world, rent, archived=False).json()["archived"] is False
    assert add(client, world, "expense", 300_000, category_id=rent).status_code == 201


def test_the_category_of_payments_is_renamed_and_nothing_else(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    fixed = category(client, world, "income", "Qarz qaytdi")
    assert patch(client, world, fixed, name="Qarzdan tushum").json() == {
        "id": fixed,
        "direction": "income",
        "name": "Qarzdan tushum",
        "archived": False,
        "fixed": True,
    }
    refused(patch(client, world, fixed, archived=True), 409, "CASH_CATEGORY_FIXED")
    refused(write(client, world.owner_a, "DELETE", f"{base(world)}/categories/{fixed}"), 409, "CASH_CATEGORY_FIXED")
    # Nobody writes into it by hand: what is in it is what the ledger says customers paid.
    fields = refused(add(client, world, "income", 50_000, category_id=fixed), 422, "VALIDATION")
    assert fields == {"category_id": "written by the ledger only"}
    assert rows(owner, world) == []


def test_a_category_is_deleted_only_while_nothing_was_ever_written_under_it(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    unused = category(client, world, "expense", "Transport")
    assert write(client, world.manager_a, "DELETE", f"{base(world)}/categories/{unused}").json() == {"deleted": True}
    assert "Transport" not in {item["name"] for item in categories(client, world)}
    refused(write(client, world.manager_a, "DELETE", f"{base(world)}/categories/{unused}"), 404, "NOT_FOUND")

    used = category(client, world, "expense", "Ijara")
    entry = added(client, world, "expense", 300_000, name="Ijara")
    refused(write(client, world.manager_a, "DELETE", f"{base(world)}/categories/{used}"), 409, "CASH_CATEGORY_IN_USE")
    # A cancelled entry is still an entry of the book, and still says what it was for.
    assert cancel(client, world, entry["id"]).status_code == 201
    refused(write(client, world.manager_a, "DELETE", f"{base(world)}/categories/{used}"), 409, "CASH_CATEGORY_IN_USE")
    assert "Ijara" in {item["name"] for item in categories(client, world)}
    # The default set is written once: a deleted default does not come back.
    assert len(categories(client, world)) == 9


def test_another_shops_category_is_nobodys_here(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    theirs = read(client, world.owner_b, f"/api/v1/shops/{world.shop_b}/cash/categories").json()["items"]
    rent = next(item["id"] for item in theirs if item["name"] == "Ijara")
    refused(patch(client, world, rent, name="Meniki"), 404, "NOT_FOUND")
    refused(write(client, world.manager_a, "DELETE", f"{base(world)}/categories/{rent}"), 404, "NOT_FOUND")
    fields = refused(add(client, world, "expense", 10_000, category_id=rent), 422, "VALIDATION")
    assert fields == {"category_id": "not a category of this direction"}
    assert cash_rows_of_the_world(owner, world)[0] == 0


# --- entries ---------------------------------------------------------------------------------------------


def test_income_and_expense_are_written_and_the_day_adds_up_by_method(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    sale = added(client, world, "income", 500_000, note="  Kunlik   savdo ")
    assert sale == {
        "id": sale["id"],
        "direction": "income",
        "method": "cash",
        "currency": "UZS",
        "amount": 500_000,
        "category": {"id": category(client, world, "income", "Savdo"), "name": "Savdo"},
        "note": "Kunlik savdo",
        "day": today().isoformat(),
        "created_at": sale["created_at"],
        "author_id": str(world.manager_a_membership),
        "source": "manual",
        "customer": None,
        "cancelled": None,
    }
    added(client, world, "income", 120_000, method="card")
    added(client, world, "expense", 300_000, name="Ijara")
    added(client, world, "expense", 20_000, name="Transport", method="card")
    added(client, world, "income", 80_000, method="transfer")

    book = day(client, world)
    assert book["date"] == today().isoformat()
    assert figures(line(book, "cash")) == (0, 500_000, 300_000, 200_000)
    assert figures(line(book, "card")) == (0, 120_000, 20_000, 100_000)
    assert figures(line(book, "transfer")) == (0, 80_000, 0, 80_000)
    assert [item["count"] for item in book["balances"]] == [2, 2, 1]
    # One line per method of the one currency the shop works in, and one total of that currency.
    assert [(item["currency"], item["method"]) for item in book["balances"]] == [
        ("UZS", "cash"),
        ("UZS", "card"),
        ("UZS", "transfer"),
    ]
    assert book["totals"] == [
        {"currency": "UZS", "opening": 0, "income": 700_000, "expense": 320_000, "closing": 380_000, "count": 5}
    ]
    # Newest first.
    assert [item["amount"] for item in book["entries"]] == [80_000, 20_000, 300_000, 120_000, 500_000]
    assert book["next_cursor"] is None
    assert actions(owner, world) == [
        "cash.income_recorded",
        "cash.income_recorded",
        "cash.expense_recorded",
        "cash.expense_recorded",
        "cash.income_recorded",
    ]


def test_an_empty_book_shows_every_method_at_zero(client: TestClient, world: World, on: None) -> None:
    book = day(client, world)
    assert [figures(item) for item in book["balances"]] == [(0, 0, 0, 0)] * 3
    assert book["entries"] == []


def test_a_day_opens_with_what_the_days_before_left_and_a_balance_may_be_below_zero(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    yesterday, before = today() - timedelta(days=1), today() - timedelta(days=2)
    added(client, world, "income", 400_000, day=before.isoformat())
    added(client, world, "expense", 150_000, name="Ijara", day=yesterday.isoformat())
    added(client, world, "expense", 300_000, name="Ish haqi")
    assert figures(line(day(client, world, date=before.isoformat()), "cash")) == (0, 400_000, 0, 400_000)
    assert figures(line(day(client, world, date=yesterday.isoformat()), "cash")) == (400_000, 0, 150_000, 250_000)
    # More went out than the book holds: it records what was written, and says so.
    assert figures(line(day(client, world), "cash")) == (250_000, 0, 300_000, -50_000)
    # An entry belongs to its day whenever it was written: each day lists its own.
    assert [item["amount"] for item in day(client, world, date=yesterday.isoformat())["entries"]] == [150_000]


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"direction": "both"}, "direction"),
        ({"method": "cheque"}, "method"),
        ({"method": ""}, "method"),
        ({"amount": 99}, "amount"),
        ({"amount": 100_000_001}, "amount"),
        ({"amount": 0}, "amount"),
        ({"amount": -5000}, "amount"),
        ({"amount": True}, "amount"),
        ({"amount": "5000"}, "amount"),
        ({"amount": 5000.5}, "amount"),
        ({"note": "x" * 201}, "note"),
        ({"currency": "EUR"}, "currency"),
        # The shop does not work in dollars: naming them is the same error as naming no currency at all.
        ({"currency": "USD"}, "currency"),
        ({"category_id": str(uuid.uuid4())}, "category_id"),
        ({"category_id": "not-an-identifier"}, "category_id"),
        ({"extra": 1}, "extra"),
    ],
)
def test_an_entry_that_is_not_valid_is_refused_and_writes_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, change: dict[str, Any], field: str
) -> None:
    body = {
        "direction": "income",
        "method": "cash",
        "amount": 5000,
        "category_id": category(client, world, "income", "Savdo"),
        **change,
    }
    response = write(client, world.manager_a, "POST", f"{base(world)}/entries", body)
    assert field in refused(response, 422, "VALIDATION")
    assert rows(owner, world) == []
    assert actions(owner, world) == []


def test_the_smallest_and_the_largest_amount_are_accepted(client: TestClient, world: World, on: None) -> None:
    assert added(client, world, "income", 100)["amount"] == 100
    assert added(client, world, "income", 100_000_000)["amount"] == 100_000_000


def test_an_entry_is_of_a_category_of_its_own_direction(client: TestClient, world: World, on: None) -> None:
    rent = category(client, world, "expense", "Ijara")
    fields = refused(add(client, world, "income", 5000, category_id=rent), 422, "VALIDATION")
    assert fields == {"category_id": "not a category of this direction"}
    assert add(client, world, "expense", 5000, category_id=rent).status_code == 201


def test_an_entry_is_dated_within_the_last_month_and_never_ahead(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    oldest = today() - timedelta(days=cash.BACKDATE_DAYS)
    assert added(client, world, "income", 5000, day=oldest.isoformat())["day"] == oldest.isoformat()
    too_old = add(client, world, "income", 5000, day=(oldest - timedelta(days=1)).isoformat())
    assert refused(too_old, 422, "VALIDATION") == {"day": "TOO_OLD"}
    ahead = add(client, world, "income", 5000, day=(today() + timedelta(days=1)).isoformat())
    assert refused(ahead, 422, "VALIDATION") == {"day": "IN_FUTURE"}
    assert [row[5] for row in rows(owner, world)] == [oldest]


def test_a_repeated_request_writes_one_entry_and_a_reused_key_is_refused(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    body = {
        "direction": "income",
        "method": "cash",
        "amount": 5000,
        "category_id": category(client, world, "income", "Savdo"),
    }
    headers = {**as_user(world.manager_a), **key()}
    first = client.post(f"{base(world)}/entries", json=body, headers=headers)
    again = client.post(f"{base(world)}/entries", json=body, headers=headers)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    assert len(rows(owner, world)) == 1
    other = client.post(f"{base(world)}/entries", json={**body, "amount": 6000}, headers=headers)
    refused(other, 409, "IDEMPOTENCY_KEY_REUSED")
    assert len(rows(owner, world)) == 1
    # Without a key nothing is written at all.
    keyless = client.post(f"{base(world)}/entries", json=body, headers=as_user(world.manager_a))
    assert keyless.status_code == 422, keyless.text
    assert len(rows(owner, world)) == 1


# --- cancelling ------------------------------------------------------------------------------------------


def test_a_cancelled_entry_stays_in_the_book_with_its_reason_and_counts_in_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    kept = added(client, world, "income", 500_000)
    wrong = added(client, world, "expense", 300_000, name="Ijara")
    response = cancel(client, world, wrong["id"], "  Ikki marta   yozilgan ", user=world.owner_a)
    assert response.status_code == 201, response.text
    cancelled = response.json()["entry"]
    assert cancelled == {
        **wrong,
        "cancelled": {
            "at": cancelled["cancelled"]["at"],
            "by": str(world.owner_a_membership),
            "reason": "Ikki marta yozilgan",
        },
    }
    book = day(client, world)
    assert figures(line(book, "cash")) == (0, 500_000, 0, 500_000)
    assert line(book, "cash")["count"] == 1
    # Still listed, as it was written, with what was said about it.
    assert [(item["id"], item["cancelled"] is not None) for item in book["entries"]] == [
        (wrong["id"], True),
        (kept["id"], False),
    ]
    assert rows(owner, world)[1][6:] == (True, "Ikki marta yozilgan")
    assert actions(owner, world)[-1] == "cash.entry_cancelled"


def test_an_entry_is_cancelled_once(client: TestClient, world: World, owner: psycopg.Connection, on: None) -> None:
    wrong = added(client, world, "income", 5000)
    assert cancel(client, world, wrong["id"]).status_code == 201
    refused(cancel(client, world, wrong["id"], "Yana bir bor"), 409, "CASH_ENTRY_CANCELLED")
    assert rows(owner, world)[0][7] == "Xato yozilgan"
    assert actions(owner, world).count("cash.entry_cancelled") == 1


@pytest.mark.parametrize("reason", ["", "   ", "ab", "  a  ", "x" * 301])
def test_a_cancellation_needs_a_reason(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, reason: str
) -> None:
    entry = added(client, world, "income", 5000)
    assert "reason" in refused(cancel(client, world, entry["id"], reason), 422, "VALIDATION")
    assert rows(owner, world)[0][6] is False


def test_only_an_entry_of_this_shop_can_be_cancelled(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    entry = added(client, world, "income", 5000)
    refused(cancel(client, world, uuid.uuid4()), 404, "NOT_FOUND")
    theirs = write(
        client,
        world.owner_b,
        "POST",
        f"/api/v1/shops/{world.shop_b}/cash/entries/{entry['id']}/cancellation",
        {"reason": "Begona"},
    )
    refused(theirs, 404, "NOT_FOUND")
    assert rows(owner, world)[0][6] is False


# --- reading: pages and periods --------------------------------------------------------------------------


def test_a_days_entries_come_in_pages(client: TestClient, world: World, on: None) -> None:
    for amount in (1000, 2000, 3000, 4000, 5000):
        added(client, world, "income", amount)
    first = day(client, world, limit=2)
    assert [item["amount"] for item in first["entries"]] == [5000, 4000]
    second = day(client, world, limit=2, cursor=first["next_cursor"])
    assert [item["amount"] for item in second["entries"]] == [3000, 2000]
    last = day(client, world, limit=2, cursor=second["next_cursor"])
    assert [item["amount"] for item in last["entries"]] == [1000]
    assert last["next_cursor"] is None
    # The balances are of the whole day on every page.
    assert figures(line(last, "cash")) == (0, 15_000, 0, 15_000)


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"cursor": "nonsense"}, "cursor"),
        ({"cursor": "WyJhIiwiYiJd"}, "cursor"),  # two texts that are not a time and an identifier
        ({"date": "yesterday"}, "date"),
        ({"date": "2026-02-30"}, "date"),
        ({"date": (date.today() + timedelta(days=3)).isoformat()}, "date"),
    ],
)
def test_a_day_that_cannot_be_read_is_refused(
    client: TestClient, world: World, on: None, params: dict[str, Any], field: str
) -> None:
    response = read(client, world.manager_a, f"{base(world)}/day", **params)
    assert field in refused(response, 422, "VALIDATION")


def summary(client: TestClient, world: World, first: date, last: date, user: Any = None) -> dict[str, Any]:
    response = read(client, user or world.manager_a, f"{base(world)}/summary", **{"from": first, "to": last})
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_a_period_adds_up_by_category_by_method_and_by_day(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    days = [today() - timedelta(days=offset) for offset in (3, 2, 1, 0)]
    added(client, world, "income", 1_000_000, name="Boshlang'ich qoldiq", day=days[0].isoformat())
    added(client, world, "income", 500_000, day=days[1].isoformat())
    added(client, world, "income", 200_000, method="card", day=days[1].isoformat())
    added(client, world, "expense", 300_000, name="Ijara", day=days[2].isoformat())
    added(client, world, "expense", 50_000, name="Transport", day=days[2].isoformat())
    wrong = added(client, world, "expense", 999_000, name="Ish haqi", day=days[2].isoformat())
    assert cancel(client, world, wrong["id"]).status_code == 201
    added(client, world, "income", 100_000, day=days[3].isoformat())

    report = summary(client, world, days[1], days[2])
    assert (report["from"], report["to"]) == (days[1].isoformat(), days[2].isoformat())
    # What was in the till before the period is its opening balance, not its income.
    assert figures(line(report, "cash")) == (1_000_000, 500_000, 350_000, 1_150_000)
    assert figures(line(report, "card")) == (0, 200_000, 0, 200_000)
    assert report["totals"] == [
        {
            "currency": "UZS",
            "opening": 1_000_000,
            "income": 700_000,
            "expense": 350_000,
            "closing": 1_350_000,
            "count": 4,
        }
    ]
    # Income first, then expense, the largest first; the cancelled salary is in none of it.
    assert [
        (item["category"]["direction"], item["category"]["name"], item["currency"], item["amount"], item["count"])
        for item in report["categories"]
    ] == [
        ("income", "Savdo", "UZS", 700_000, 2),
        ("expense", "Ijara", "UZS", 300_000, 1),
        ("expense", "Transport", "UZS", 50_000, 1),
    ]
    assert report["days"] == [
        {"date": days[1].isoformat(), "currency": "UZS", "income": 700_000, "expense": 0},
        {"date": days[2].isoformat(), "currency": "UZS", "income": 0, "expense": 350_000},
    ]
    # The whole book: it closes with what the last day of it closes with.
    whole = summary(client, world, days[0], days[3])
    assert figures(line(whole, "cash")) == (0, 1_600_000, 350_000, 1_250_000)
    assert figures(line(day(client, world), "cash"))[3] == 1_250_000


def test_a_period_shows_an_archived_category_that_was_used_in_it(client: TestClient, world: World, on: None) -> None:
    added(client, world, "expense", 300_000, name="Ijara")
    assert patch(client, world, category(client, world, "expense", "Ijara"), archived=True).status_code == 200
    report = summary(client, world, today(), today())
    assert [(item["category"]["name"], item["category"]["archived"]) for item in report["categories"]] == [
        ("Ijara", True)
    ]


@pytest.mark.parametrize(
    ("first", "last", "fields"),
    [
        (None, "2026-01-10", {"from": "DATE_INVALID"}),
        ("2026-01-01", "10.01.2026", {"to": "DATE_INVALID"}),
        ("2026-01-10", "2026-01-01", {"from": "FROM_AFTER_TO"}),
        ("2024-01-01", "2025-01-01", {"to": "PERIOD_TOO_LONG"}),
        ("2026-01-01", "2999-01-01", {"to": "IN_FUTURE"}),
    ],
)
def test_a_period_that_is_not_valid_is_refused(
    client: TestClient, world: World, on: None, first: str | None, last: str, fields: dict[str, str]
) -> None:
    params = {name: value for name, value in (("from", first), ("to", last)) if value is not None}
    assert refused(read(client, world.manager_a, f"{base(world)}/summary", **params), 422, "VALIDATION") == fields


# --- the shop's state and who may ------------------------------------------------------------------------


def test_a_suspended_shop_writes_nothing_and_only_its_owner_still_reads(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    entry = added(client, world, "income", 5000)
    rent = category(client, world, "expense", "Ijara")
    owner.execute("UPDATE subscription SET state = 'suspended' WHERE shop_id = %s", (world.shop_a,))
    for response in (
        add(client, world, "income", 5000, user=world.owner_a, category_id=entry["category"]["id"]),
        cancel(client, world, entry["id"], user=world.owner_a),
        new_category(client, world, "expense", "Soliq", user=world.owner_a),
        patch(client, world, rent, user=world.owner_a, name="Arenda"),
        write(client, world.owner_a, "DELETE", f"{base(world)}/categories/{rent}"),
        write(client, world.owner_a, "POST", f"{base(world)}/backfill", {}),
    ):
        refused(response, 403, "SHOP_SUSPENDED")
    assert len(rows(owner, world)) == 1 and rows(owner, world)[0][6] is False
    # BR-30: the owner still looks; a manager does not.
    assert read(client, world.owner_a, f"{base(world)}/day").status_code == 200
    assert read(client, world.owner_a, f"{base(world)}/categories").status_code == 200
    for path in ("day", f"summary?from={today()}&to={today()}", "categories"):
        refused(read(client, world.manager_a, f"{base(world)}/{path}"), 403, "SHOP_SUSPENDED")


def test_a_shop_whose_subscription_ran_out_still_keeps_its_book(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    """Limited mode stops new credit and nothing else (BR-29): money still comes in and goes out."""
    owner.execute("UPDATE subscription SET state = 'limited' WHERE shop_id = %s", (world.shop_a,))
    assert add(client, world, "income", 5000).status_code == 201
    assert add(client, world, "expense", 5000).status_code == 201


def test_a_seller_has_no_part_in_the_cash_book_by_default(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    rent = category(client, world, "expense", "Ijara")
    sale = category(client, world, "income", "Savdo")
    entry = added(client, world, "income", 5000)
    body = {"direction": "income", "method": "cash", "amount": 5000, "category_id": sale}
    for response in (
        read(client, world.seller_a, f"{base(world)}/day"),
        read(client, world.seller_a, f"{base(world)}/summary", **{"from": today(), "to": today()}),
        read(client, world.seller_a, f"{base(world)}/categories"),
        write(client, world.seller_a, "POST", f"{base(world)}/entries", body),
        cancel(client, world, entry["id"], user=world.seller_a),
        new_category(client, world, "expense", "Soliq", user=world.seller_a),
        patch(client, world, rent, user=world.seller_a, name="Arenda"),
        write(client, world.seller_a, "DELETE", f"{base(world)}/categories/{rent}"),
    ):
        assert refused(response, 403, "FORBIDDEN_ROLE") == {"needed_role": "manager"}
    # Copying the ledger's past into the book is the owner's alone.
    for user in (world.seller_a, world.manager_a):
        fields = refused(write(client, user, "POST", f"{base(world)}/backfill", {}), 403, "FORBIDDEN_ROLE")
        assert fields == {"needed_role": "owner"}
    assert len(rows(owner, world)) == 1


def test_taking_money_in_and_paying_it_out_are_allowed_separately(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    """With the per-member permissions on, an owner lets a seller write down cash sales and nothing more."""
    sale = category(client, world, "income", "Savdo")
    rent = category(client, world, "expense", "Ijara")
    switch_permissions_on(owner)
    set_overrides(owner, world.seller_a_membership, granted=["cash.record_income"])

    def record_as(user: uuid.UUID, direction: str, category_id: str) -> Any:
        body = {"direction": direction, "method": "cash", "amount": 5000, "category_id": category_id}
        return write(client, user, "POST", f"{base(world)}/entries", body)

    assert record_as(world.seller_a, "income", sale).status_code == 201
    fields = refused(record_as(world.seller_a, "expense", rent), 403, "FORBIDDEN_PERMISSION")
    assert fields == {"permission": "cash.record_expense"}
    # A recorder sees the categories to choose from, and not the book.
    assert read(client, world.seller_a, f"{base(world)}/categories").status_code == 200
    assert refused(read(client, world.seller_a, f"{base(world)}/day"), 403, "FORBIDDEN_PERMISSION") == {
        "permission": "cash.view"
    }
    # And the other way round: a manager the owner does not let pay money out.
    set_overrides(owner, world.manager_a_membership, denied=["cash.record_expense"])
    assert record_as(world.manager_a, "income", sale).status_code == 201
    fields = refused(record_as(world.manager_a, "expense", rent), 403, "FORBIDDEN_PERMISSION")
    assert fields == {"permission": "cash.record_expense"}
    # The owner holds everything, whatever is stored.
    assert record_as(world.owner_a, "expense", rent).status_code == 201
    assert [(row[0], row[3]) for row in rows(owner, world)] == [("income", 5000), ("income", 5000), ("expense", 5000)]


def test_a_member_without_either_permission_to_record_is_told_which_one_is_missing(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    sale = category(client, world, "income", "Savdo")
    switch_permissions_on(owner)
    set_overrides(owner, world.manager_a_membership, denied=["cash.record_income", "cash.record_expense"])
    body = {"direction": "income", "method": "cash", "amount": 5000, "category_id": sale}
    fields = refused(
        write(client, world.manager_a, "POST", f"{base(world)}/entries", body), 403, "FORBIDDEN_PERMISSION"
    )
    assert fields == {"permission": "cash.record_income"}
    # The book itself stays open to them: reading is a permission of its own.
    assert read(client, world.manager_a, f"{base(world)}/day").status_code == 200
    assert rows(owner, world) == []
