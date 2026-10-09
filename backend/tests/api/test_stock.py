"""The stock through the API: the switch, counted items, barcodes, what a sale takes, what is shown to whom.

Behind the platform switch `stock_on`. Each rule has the case that must work and the case that must be
refused. Documents are in test_stock_documents.py, suppliers in test_suppliers.py; who may call what, by
role and as an outsider, is in the authorization suite.
"""

import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.operations import all_operations

from .conftest import World, as_user, set_overrides, switch_permissions_on
from .test_chat import chat_of
from .test_customers_ledger import another_client, key, new_customer, read, record, reverse, shop, write
from .test_goods_lines import add, chosen, sell, typed

pytestmark = pytest.mark.db

NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "Topilmadi.", "fields": {}}}
STOCK_TABLES = (
    "catalog_barcode",
    "supplier",
    "supplier_entry",
    "supplier_balance",
    "stock_document",
    "stock_document_line",
    "stock_movement",
    "stock_level",
)


def switch(owner: psycopg.Connection, value: str = "true") -> None:
    """Store the switch as an administrator's change would (`value` is JSON). The row is signed with a
    user identifier, so the `admin_env` fixture behind `client` removes it after the test."""
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('stock_on', %s::jsonb, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by",
        (value, str(uuid.uuid4())),
    )


@pytest.fixture
def on(client: TestClient, owner: psycopg.Connection) -> Iterator[None]:
    switch(owner)
    yield


def stock(world: World) -> str:
    return f"{shop(world)}/stock"


def new_item(client: TestClient, world: World, name: str, price: int = 5_000, unit: str | None = None) -> str:
    body: dict[str, Any] = {"name": name, "price": price}
    if unit is not None:
        body["unit"] = unit
    response = write(client, world.manager_a, "POST", f"{shop(world)}/catalog", body)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def patch_item(client: TestClient, world: World, item: Any, user: uuid.UUID | None = None, **body: Any) -> Any:
    return write(client, user or world.manager_a, "PATCH", f"{stock(world)}/items/{item}", body)


def counted_item(client: TestClient, world: World, name: str, price: int = 5_000, unit: str | None = None) -> str:
    item = new_item(client, world, name, price, unit)
    assert patch_item(client, world, item, tracked=True).status_code == 200
    return item


def document(client: TestClient, world: World, user: uuid.UUID | None = None, **body: Any) -> Any:
    return write(client, user or world.manager_a, "POST", f"{stock(world)}/documents", body)


def line(item: Any, qty: str, unit_cost: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"item_id": str(item), "qty": qty}
    if unit_cost is not None:
        body["unit_cost"] = unit_cost
    return body


def receive(client: TestClient, world: World, lines: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    """A purchase receipt for cash, written and posted in one step."""
    response = document(client, world, kind="receipt", lines=lines, post=True, **extra)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    assert body["status"] == "posted"
    return body


def item_of(client: TestClient, world: World, item: Any, user: uuid.UUID | None = None) -> dict[str, Any]:
    response = read(client, user or world.manager_a, f"{stock(world)}/items/{item}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def movements(client: TestClient, world: World, item: Any, user: uuid.UUID | None = None) -> list[dict[str, Any]]:
    response = read(client, user or world.manager_a, f"{stock(world)}/items/{item}/movements")
    assert response.status_code == 200, response.text
    rows: list[dict[str, Any]] = response.json()["movements"]
    return rows


def mismatches(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    """Where the kept figures differ from their ledgers, for the shop: both lists must be empty."""
    return [
        *owner.execute("SELECT * FROM stock_level_mismatches(%s)", (world.shop_a,)).fetchall(),
        *owner.execute("SELECT * FROM supplier_balance_mismatches(%s)", (world.shop_a,)).fetchall(),
    ]


def rows_of_the_module(owner: psycopg.Connection, world: World) -> int:
    """How many rows the two shops of the test hold in the module's tables."""
    total = 0
    for table in STOCK_TABLES:
        row = owner.execute(
            f"SELECT count(*) FROM {table} WHERE shop_id IN (%s, %s)", (world.shop_a, world.shop_b)
        ).fetchone()
        assert row is not None
        total += int(row[0])
    return total


def actions(owner: psycopg.Connection, world: World, prefix: str) -> list[str]:
    return [
        str(row[0])
        for row in owner.execute(
            "SELECT action FROM activity WHERE shop_id = %s AND action LIKE %s ORDER BY at, id",
            (world.shop_a, prefix + "%"),
        ).fetchall()
    ]


# --- the switch ------------------------------------------------------------------------------------------


def _every_route(world: World) -> list[tuple[str, str, Any]]:
    one = uuid.uuid4()
    return [
        ("GET", f"{stock(world)}/settings", None),
        ("PUT", f"{stock(world)}/settings", {"refuse_negative": True}),
        ("GET", f"{stock(world)}/items", None),
        ("GET", f"{stock(world)}/lookup?code=ABC-1", None),
        ("GET", f"{stock(world)}/report", None),
        ("GET", f"{stock(world)}/items/{world.catalog_item_a}", None),
        ("PATCH", f"{stock(world)}/items/{world.catalog_item_a}", {"tracked": True}),
        ("GET", f"{stock(world)}/items/{world.catalog_item_a}/movements", None),
        ("GET", f"{stock(world)}/documents", None),
        ("POST", f"{stock(world)}/documents", {"kind": "write_off", "reason": "lost", "lines": []}),
        ("GET", f"{stock(world)}/documents/{one}", None),
        ("PUT", f"{stock(world)}/documents/{one}", {"kind": "write_off", "reason": "lost", "lines": []}),
        ("POST", f"{stock(world)}/documents/{one}/post", None),
        ("POST", f"{stock(world)}/documents/{one}/cancel", {"reason": "xato"}),
        ("GET", f"{shop(world)}/suppliers", None),
        ("POST", f"{shop(world)}/suppliers", {"name": "Ulgurji"}),
        ("GET", f"{shop(world)}/suppliers/{one}", None),
        ("PUT", f"{shop(world)}/suppliers/{one}", {"name": "Ulgurji"}),
        ("POST", f"{shop(world)}/suppliers/{one}/archive", None),
        ("POST", f"{shop(world)}/suppliers/{one}/unarchive", None),
        ("POST", f"{shop(world)}/suppliers/{one}/entries", {"kind": "payment", "amount": 1000}),
        ("POST", f"{shop(world)}/suppliers/{one}/entries/{one}/cancel", {"reason": "xato"}),
    ]


def test_the_list_of_routes_below_is_every_operation_of_the_module(world: World) -> None:
    """The switch test tries each route once; an operation added later must be added to it."""
    ours = [op for op in all_operations() if op.name.startswith(("stock.", "suppliers."))]
    assert len(ours) == len(_every_route(world)) == 22


@pytest.mark.parametrize("stored", [None, "false", '"true"', "1", "null"])
def test_with_the_switch_off_no_route_of_the_module_exists_for_anyone(
    client: TestClient, world: World, owner: psycopg.Connection, stored: str | None
) -> None:
    """Off is the default (no row), and only the JSON value `true` is on. The owner, a stranger and
    someone who is not signed in all get the answer of a route that was never there, and nothing is
    written."""
    if stored is not None:
        switch(owner, stored)
    before = rows_of_the_module(owner, world)
    for method, path, body in _every_route(world):
        for headers in (as_user(world.owner_a), as_user(world.stranger), {}):
            response = client.request(method, path, json=body, headers={**headers, **key()})
            assert response.status_code == 404 and response.json() == NOT_FOUND, (method, path, headers)
    assert rows_of_the_module(owner, world) == before


def test_with_the_switch_off_the_catalogue_and_a_sale_answer_exactly_as_before(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The same requests with the switch off and with it on (and nothing counted yet) give the same
    bodies, key for key: the catalogue says nothing of stock, and a sale's answer has no new key."""

    def run(suffix: str) -> list[Any]:
        customer = new_customer(client, world, f"Vali {suffix}")
        item = new_item(client, world, f"Guruch {suffix}", 12_000, "kg")
        sale = sell(client, world, customer, [chosen(item, "2", 12_000), typed(f"Tuz {suffix}", "1", 3_000)])
        assert sale.status_code == 201, sale.text
        added_to = record(client, world, customer, "credit", 8_000).json()["entry"]["id"]
        added = add(client, world, added_to, [chosen(item, "0.5", 16_000)])
        assert added.status_code == 201, added.text
        cancelled = reverse(client, world, sale.json()["entry"]["id"])
        assert cancelled.status_code == 201, cancelled.text
        listed = read(client, world.seller_a, f"{shop(world)}/catalog", q=f"Guruch {suffix}").json()
        return [sale.json(), added.json(), cancelled.json(), listed]

    def shape(value: Any) -> Any:
        if isinstance(value, dict):
            return {name: shape(inner) for name, inner in value.items()}
        if isinstance(value, list):
            return [shape(inner) for inner in value]
        return type(value).__name__

    off = run("bir")
    assert rows_of_the_module(owner, world) == 0
    switch(owner)
    on_ = run("ikki")
    assert [shape(body) for body in off] == [shape(body) for body in on_]
    assert set(off[3]["items"][0]) == {"id", "name", "unit", "price", "learned", "status", "merged_into"}
    assert rows_of_the_module(owner, world) == 0, "nothing is counted, so nothing moved"


# --- settings ----------------------------------------------------------------------------------------------


def test_the_settings_name_the_units_and_reasons_in_both_languages(client: TestClient, world: World, on: None) -> None:
    body = read(client, world.seller_a, f"{stock(world)}/settings").json()
    assert body["refuse_negative"] is False, "a sale may go below zero unless the shop says otherwise"
    assert body["currencies"] == ["UZS"]
    assert {unit["key"] for unit in body["units"]} >= {"dona", "kg", "l", "m", "quti"}
    assert all(unit["label"]["uz"] and unit["label"]["ru"] for unit in body["units"])
    assert [reason["key"] for reason in body["write_off_reasons"]] == ["damaged", "expired", "lost", "own_use"]


def test_a_manager_turns_refusing_beyond_stock_on_and_it_is_logged(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    changed = write(client, world.manager_a, "PUT", f"{stock(world)}/settings", {"refuse_negative": True})
    assert changed.status_code == 200 and changed.json()["refuse_negative"] is True
    assert read(client, world.seller_a, f"{stock(world)}/settings").json()["refuse_negative"] is True
    assert actions(owner, world, "stock.settings") == ["stock.settings_changed"]
    refused = write(client, world.seller_a, "PUT", f"{stock(world)}/settings", {"refuse_negative": False})
    assert refused.status_code == 403
    wrong = write(client, world.manager_a, "PUT", f"{stock(world)}/settings", {"refuse_negative": "yes"})
    assert wrong.status_code == 422


# --- counted items and barcodes ----------------------------------------------------------------------------


def test_an_item_is_not_counted_until_someone_says_so(client: TestClient, world: World, on: None) -> None:
    item = new_item(client, world, "Shakar", 12_000, "kg")
    body = item_of(client, world, item)
    assert (body["tracked"], body["on_hand"], body["low"], body["barcodes"]) == (False, "0", False, [])
    listed = read(client, world.seller_a, f"{stock(world)}/items").json()["items"]
    assert listed == [], "the stock list is the counted items"
    every = read(client, world.seller_a, f"{stock(world)}/items", filter="all", q="shakar").json()["items"]
    assert [row["id"] for row in every] == [item]

    changed = patch_item(client, world, item, tracked=True, low_stock="5")
    assert changed.status_code == 200, changed.text
    assert (changed.json()["tracked"], changed.json()["low_stock"], changed.json()["low"]) == (True, "5", True)
    listed = read(client, world.seller_a, f"{stock(world)}/items").json()["items"]
    assert [row["id"] for row in listed] == [item]
    low = read(client, world.seller_a, f"{stock(world)}/items", filter="low").json()["items"]
    assert [row["id"] for row in low] == [item], "nothing on hand is at or below five"

    receive(client, world, [line(item, "5.001", 10_000)])
    assert read(client, world.seller_a, f"{stock(world)}/items", filter="low").json()["items"] == []
    cleared = patch_item(client, world, item, clear_low_stock=True)
    assert cleared.json()["low_stock"] is None and cleared.json()["low"] is False


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "_"),
        ({"low_stock": "-1"}, "low_stock"),
        ({"low_stock": "1.2345"}, "low_stock"),
        ({"low_stock": "3", "clear_low_stock": True}, "low_stock"),
        ({"unit": "bog'"}, "unit"),
        ({"barcodes": ["4006381333932"]}, "barcodes"),
        ({"barcodes": ["ABC-1", "ABC-1"]}, "barcodes"),
        ({"barcodes": [f"C{n}" for n in range(11)]}, "barcodes"),
    ],
)
def test_a_wrong_change_to_an_item_is_refused_and_nothing_is_stored(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, body: dict[str, Any], field: str
) -> None:
    refused = patch_item(client, world, world.catalog_item_a, **body)
    assert refused.status_code == 422 and field in refused.json()["error"]["fields"], refused.text
    assert item_of(client, world, world.catalog_item_a)["tracked"] is False
    assert rows_of_the_module(owner, world) == 0


def test_an_item_that_cannot_be_counted_is_refused(client: TestClient, world: World, on: None) -> None:
    """A learned item is not reviewed yet, and a unit of the shop's own is not one the stock counts in."""
    learned = patch_item(client, world, world.learned_item_a, tracked=True)
    assert learned.status_code == 409 and learned.json()["error"]["code"] == "ITEM_NOT_COUNTABLE"
    assert learned.json()["error"]["fields"]["why"] == "learned"
    own_unit = new_item(client, world, "Ko'kat", 2_000, "bog'")
    refused = patch_item(client, world, own_unit, tracked=True)
    assert refused.status_code == 409 and refused.json()["error"]["fields"]["why"] == "unit"
    with_unit = patch_item(client, world, own_unit, tracked=True, unit="dona")
    assert with_unit.status_code == 200 and with_unit.json()["unit"] == "dona"


def test_a_counted_item_keeps_a_stock_unit_and_its_unit_once_it_has_moved(
    client: TestClient, world: World, on: None
) -> None:
    item = counted_item(client, world, "Yog'", 30_000, "l")
    catalog = f"{shop(world)}/catalog/{item}"
    own = write(client, world.manager_a, "PATCH", catalog, {"unit": "shisha"})
    assert own.status_code == 422 and "unit" in own.json()["error"]["fields"]
    assert write(client, world.manager_a, "PATCH", catalog, {"unit": "ml"}).status_code == 200, "nothing moved yet"
    receive(client, world, [line(item, "3", 25_000)])
    for change in (
        write(client, world.manager_a, "PATCH", catalog, {"unit": "l"}),
        patch_item(client, world, item, unit="l"),
    ):
        assert change.status_code == 422 and "unit" in change.json()["error"]["fields"]
    assert item_of(client, world, item)["unit"] == "ml"
    # Counting cannot be switched off while the books still hold something.
    off = patch_item(client, world, item, tracked=False)
    assert off.status_code == 422 and "tracked" in off.json()["error"]["fields"]


def test_a_barcode_finds_its_item_and_belongs_to_one_item_of_the_shop(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    non = world.catalog_item_a
    stored = patch_item(client, world, non, barcodes=["4006381333931", " NON-1 "])
    assert stored.status_code == 200 and stored.json()["barcodes"] == ["4006381333931", "NON-1"]
    found = read(client, world.seller_a, f"{stock(world)}/lookup", code="4006381333931")
    assert found.status_code == 200 and found.json()["id"] == str(non)
    assert read(client, world.seller_a, f"{stock(world)}/lookup", code="NON-1").json()["id"] == str(non)

    other = new_item(client, world, "Sut")
    taken = patch_item(client, world, other, barcodes=["SUT-1", "NON-1"])
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "BARCODE_TAKEN"
    assert taken.json()["error"]["fields"] == {"code": "NON-1"}
    assert item_of(client, world, other)["barcodes"] == [], "none of the two was stored"

    # Replacing the list drops what is no longer in it; the dropped code is free again.
    assert patch_item(client, world, non, barcodes=["NON-1"]).json()["barcodes"] == ["NON-1"]
    unknown = read(client, world.seller_a, f"{stock(world)}/lookup", code="4006381333931")
    assert unknown.status_code == 404 and unknown.json() == NOT_FOUND
    assert patch_item(client, world, other, barcodes=["4006381333931"]).status_code == 200
    wrong = read(client, world.seller_a, f"{stock(world)}/lookup", code="4006381333932")
    assert wrong.status_code == 422, "a mistyped digit is said, not searched for"

    # The same code in another shop is another shop's business.
    owner.execute(
        "INSERT INTO catalog_item (id, shop_id, name, name_norm, price) VALUES (%s, %s, 'Non', 'non', 4000)",
        (item_b := uuid.uuid4(), world.shop_b),
    )
    owner.execute(
        "INSERT INTO catalog_barcode (shop_id, code, item_id) VALUES (%s, 'NON-1', %s)", (world.shop_b, item_b)
    )
    assert read(client, world.seller_a, f"{stock(world)}/lookup", code="NON-1").json()["id"] == str(non)
    theirs = read(client, world.owner_b, f"/api/v1/shops/{world.shop_b}/stock/lookup", code="NON-1")
    assert theirs.json()["id"] == str(item_b)


def test_the_stock_list_is_paged_by_cursor_and_searched_by_name(client: TestClient, world: World, on: None) -> None:
    made = [counted_item(client, world, f"Tovar {number:02d}") for number in range(5)]
    first = read(client, world.seller_a, f"{stock(world)}/items", limit=2).json()
    assert [row["id"] for row in first["items"]] == made[:2] and first["next_cursor"]
    second = read(client, world.seller_a, f"{stock(world)}/items", limit=2, cursor=first["next_cursor"]).json()
    assert [row["id"] for row in second["items"]] == made[2:4]
    last = read(client, world.seller_a, f"{stock(world)}/items", limit=2, cursor=second["next_cursor"]).json()
    assert [row["id"] for row in last["items"]] == made[4:] and last["next_cursor"] is None
    assert [
        row["id"] for row in read(client, world.seller_a, f"{stock(world)}/items", q="ТОВАР 03").json()["items"]
    ] == [made[3]]
    for params in ({"limit": 0}, {"limit": 201}, {"filter": "none"}, {"cursor": "nonsense"}, {"q": "x" * 81}):
        assert read(client, world.seller_a, f"{stock(world)}/items", **params).status_code == 422, params


# --- a sale takes from the stock -----------------------------------------------------------------------------


def test_a_credit_sale_takes_counted_goods_out_and_its_cancellation_puts_them_back(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    customer = new_customer(client, world, "Vali")
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    salt = new_item(client, world, "Tuz", 3_000)  # in the catalogue, not counted
    receive(client, world, [line(rice, "10", 10_000)])

    sale = sell(client, world, customer, [chosen(rice, "2.5", 15_000), chosen(salt, "1", 3_000)])
    assert sale.status_code == 201 and "stock_warnings" not in sale.json()
    entry = sale.json()["entry"]["id"]
    assert item_of(client, world, rice)["on_hand"] == "7.5"
    assert item_of(client, world, salt)["on_hand"] == "0"
    newest = movements(client, world, rice)[0]
    assert (newest["kind"], newest["qty"], newest["on_hand_after"]) == ("sale", "-2.5", "7.5")
    assert newest["ledger_entry_id"] == entry and newest["sale_total"] == 37_500
    assert newest["cost"] == {"currency": "UZS", "unit_cost": None, "total": 25_000, "value_after": 75_000}

    cancelled = reverse(client, world, entry)
    assert cancelled.status_code == 201, cancelled.text
    assert item_of(client, world, rice)["on_hand"] == "10"
    assert item_of(client, world, rice)["cost"]["value"] == 100_000, "it came back at the cost it left with"
    kinds = [(row["kind"], row["qty"], row["reversed"]) for row in movements(client, world, rice)]
    assert kinds == [("reversal", "2.5", False), ("sale", "-2.5", True), ("receipt", "10", False)]
    assert mismatches(owner, world) == []


def test_goods_added_to_a_sale_afterwards_are_taken_out_too(client: TestClient, world: World, on: None) -> None:
    customer = new_customer(client, world, "Vali")
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)])
    entry = record(client, world, customer, "credit", 30_000).json()["entry"]["id"]
    assert item_of(client, world, rice)["on_hand"] == "10", "an amount-only sale names no goods"
    assert add(client, world, entry, [chosen(rice, "2", 15_000)]).status_code == 201
    assert item_of(client, world, rice)["on_hand"] == "8"
    assert reverse(client, world, entry).status_code == 201
    assert item_of(client, world, rice)["on_hand"] == "10"


def test_a_sale_may_take_an_item_below_zero_and_says_so(client: TestClient, world: World, on: None) -> None:
    customer = new_customer(client, world, "Vali")
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "1", 10_000)])
    sale = sell(client, world, customer, [chosen(rice, "3", 15_000)])
    assert sale.status_code == 201, sale.text
    assert sale.json()["stock_warnings"] == [{"kind": "negative", "item": rice, "name": "Guruch", "on_hand": "-2"}]
    body = item_of(client, world, rice)
    assert body["on_hand"] == "-2" and body["cost"]["value"] == 0
    assert body["cost"]["average"] == 10_000, "the last known cost"


def test_a_shop_that_refuses_sales_beyond_stock_gets_a_refusal_and_nothing_is_written(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    customer = new_customer(client, world, "Vali")
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "1", 10_000)])
    assert (
        write(client, world.manager_a, "PUT", f"{stock(world)}/settings", {"refuse_negative": True}).status_code == 200
    )
    entries = owner.execute("SELECT count(*) FROM ledger_entry WHERE customer_id = %s", (customer,)).fetchone()

    refused = sell(client, world, customer, [chosen(rice, "1.001", 15_000)])
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "STOCK_INSUFFICIENT"
    assert refused.json()["error"]["fields"] == {"item": rice, "name": "Guruch", "on_hand": "1", "wanted": "1.001"}
    assert owner.execute("SELECT count(*) FROM ledger_entry WHERE customer_id = %s", (customer,)).fetchone() == entries
    assert item_of(client, world, rice)["on_hand"] == "1"
    exactly = sell(client, world, customer, [chosen(rice, "1", 15_000)])
    assert exactly.status_code == 201 and "stock_warnings" not in exactly.json()


def test_a_line_in_another_unit_takes_nothing_and_says_so(client: TestClient, world: World, on: None) -> None:
    """Typed as "Guruch" in boxes while the stock counts it in kilograms: a box is not a kilogram."""
    customer = new_customer(client, world, "Vali")
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)])
    sale = sell(client, world, customer, [typed("guruch", "2", 150_000, "quti")])
    assert sale.status_code == 201, sale.text
    assert sale.json()["stock_warnings"] == [{"kind": "unit", "item": rice, "name": "Guruch", "unit": "kg"}]
    assert item_of(client, world, rice)["on_hand"] == "10"


def test_goods_sold_while_the_stock_was_on_come_back_even_after_it_is_switched_off(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    customer = new_customer(client, world, "Vali")
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)])
    entry = sell(client, world, customer, [chosen(rice, "4", 15_000)]).json()["entry"]["id"]
    switch(owner, "false")
    assert reverse(client, world, entry).status_code == 201
    later = sell(client, world, customer, [chosen(rice, "1", 15_000)])
    assert later.status_code == 201, "off: a sale takes nothing"
    switch(owner)
    assert item_of(client, world, rice)["on_hand"] == "10"
    assert mismatches(owner, world) == []


def test_two_writers_to_one_item_at_once_take_turns_and_the_books_add_up(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, app_database_url: str
) -> None:
    """A sale and a receipt of the same items at the same moment, each naming them in the opposite order:
    every movement continues the level the one before it left, neither waits for the other in a ring,
    and with the shop refusing sales beyond stock two sales of the last unit cannot both go through."""
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    tea = counted_item(client, world, "Choy", 8_000)
    receive(client, world, [line(rice, "100", 10_000), line(tea, "100", 5_000)])
    customers = [new_customer(client, world, "Vali"), new_customer(client, world, "G'ani")]
    with another_client(app_database_url) as second:
        for _ in range(5):
            barrier = threading.Barrier(2)

            def sale(barrier: threading.Barrier = barrier) -> int:
                barrier.wait(timeout=10)
                lines = [chosen(rice, "1", 15_000), chosen(tea, "1", 8_000)]
                return int(sell(client, world, customers[0], lines).status_code)

            def receipt(barrier: threading.Barrier = barrier) -> int:
                barrier.wait(timeout=10)
                body = {"kind": "receipt", "post": True, "lines": [line(tea, "2", 6_000), line(rice, "2", 11_000)]}
                headers = {**as_user(world.manager_a), **key()}
                return int(second.post(f"{stock(world)}/documents", json=body, headers=headers).status_code)

            with ThreadPoolExecutor(max_workers=2) as pool:
                first, other = pool.submit(sale), pool.submit(receipt)
                assert (first.result(), other.result()) == (201, 201)
        assert [item_of(client, world, item)["on_hand"] for item in (rice, tea)] == ["105", "105"]
        assert [row["seq"] for row in movements(client, world, rice)] == list(range(11, 0, -1))

        # The last unit: the shop refuses sales beyond stock, and two sellers reach for it at once.
        assert (
            write(client, world.manager_a, "PUT", f"{stock(world)}/settings", {"refuse_negative": True}).status_code
            == 200
        )
        last = counted_item(client, world, "Oxirgi")
        receive(client, world, [line(last, "1", 1_000)])
        barrier = threading.Barrier(2)

        def reach(pair: tuple[TestClient, str]) -> int:
            which, customer = pair
            barrier.wait(timeout=10)
            return int(sell(which, world, customer, [chosen(last, "1", 5_000)]).status_code)

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(reach, ((client, customers[0]), (second, customers[1]))))
        assert statuses == [201, 409]
        assert item_of(client, world, last)["on_hand"] == "0"
    assert mismatches(owner, world) == []


# --- who sees cost ---------------------------------------------------------------------------------------------


def _has_cost_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            name in ("cost", "unit_cost", "line_total", "total", "paid") or _has_cost_key(inner)
            for name, inner in value.items()
        )
    if isinstance(value, list):
        return any(_has_cost_key(inner) for inner in value)
    return False


def test_cost_and_margin_are_absent_for_a_member_who_may_not_see_them(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """A seller sees what is on hand and never what it was bought for: the keys are not there at all, in
    the list, the item, its movements, a lookup and the documents. A manager sees them."""
    customer = new_customer(client, world, "Vali")
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    assert patch_item(client, world, rice, barcodes=["RICE-1"]).status_code == 200
    posted = receive(client, world, [line(rice, "10", 10_000)])
    assert sell(client, world, customer, [chosen(rice, "1", 15_000)]).status_code == 201

    def answers(user: uuid.UUID) -> list[Any]:
        return [
            read(client, user, f"{stock(world)}/items").json(),
            read(client, user, f"{stock(world)}/items/{rice}").json(),
            read(client, user, f"{stock(world)}/items/{rice}/movements").json(),
            read(client, user, f"{stock(world)}/lookup", code="RICE-1").json(),
        ]

    for body in answers(world.seller_a):
        assert not _has_cost_key(body), body
    assert movements(client, world, rice, world.seller_a)[0]["sale_total"] == 15_000, "a selling price is no cost"
    seen = answers(world.manager_a)
    assert seen[1]["cost"] == {"currency": "UZS", "average": 10_000, "value": 90_000, "margin": 5_000}
    assert all(_has_cost_key(body) for body in seen)
    assert read(client, world.seller_a, f"{stock(world)}/report").status_code == 403

    # With the permission matrix on, the permission decides and not the role: a seller given the stock's
    # documents reads a receipt without its prices; a manager denied cost loses them everywhere.
    switch_permissions_on(owner)
    set_overrides(owner, world.seller_a_membership, granted=["stock.receive"])
    set_overrides(owner, world.manager_a_membership, denied=["stock.costs.view"])
    for user in (world.seller_a, world.manager_a):
        for body in answers(user):
            assert not _has_cost_key(body), body
        receipt = read(client, user, f"{stock(world)}/documents/{posted['id']}")
        assert receipt.status_code == 200 and not _has_cost_key(receipt.json()), receipt.text
        assert not _has_cost_key(read(client, user, f"{stock(world)}/documents").json())
        report = read(client, user, f"{stock(world)}/report")
        assert report.status_code == 403 and report.json()["error"]["fields"] == {"permission": "stock.costs.view"}
    set_overrides(owner, world.seller_a_membership, granted=["stock.costs.view"])
    assert item_of(client, world, rice, world.seller_a)["cost"]["average"] == 10_000


# --- the report ------------------------------------------------------------------------------------------------


def test_the_report_values_the_stock_and_lists_what_does_not_sell_and_what_sold_at_a_loss(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    customer = new_customer(client, world, "Vali")
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    oil = counted_item(client, world, "Yog'", 30_000, "l")
    tea = counted_item(client, world, "Choy", 8_000)
    assert patch_item(client, world, tea, low_stock="3").status_code == 200
    receive(client, world, [line(rice, "10", 10_000), line(oil, "4", 32_000), line(tea, "2", 5_000)])
    # Oil is sold for less than it cost; rice at a profit; tea not at all.
    assert sell(client, world, customer, [chosen(oil, "1", 30_000), chosen(rice, "2", 15_000)]).status_code == 201

    report = read(client, world.manager_a, f"{stock(world)}/report", days=30).json()
    assert report["totals"] == {
        "items": 3,
        "low": 1,
        "cost": [{"currency": "UZS", "value": 80_000 + 96_000 + 10_000}],
        "selling": 8 * 15_000 + 3 * 30_000 + 2 * 8_000,
        "margin": {"selling": 226_000, "cost": 186_000, "margin": 40_000},
    }
    assert [row["id"] for row in report["not_sold"]["items"]] == [tea], "never sold comes first; the others sold today"
    assert [(row["name"], row["loss"]) for row in report["sold_below_cost"]["sales"]] == [("Yog'", 2_000)]
    assert [row["id"] for row in report["low_stock"]["items"]] == [tea]

    # A sale that was cancelled was not a sale at a loss.
    entries = owner.execute(
        "SELECT id FROM ledger_entry WHERE customer_id = %s AND kind = 'credit'", (customer,)
    ).fetchall()
    assert reverse(client, world, entries[0][0]).status_code == 201
    assert read(client, world.manager_a, f"{stock(world)}/report").json()["sold_below_cost"]["sales"] == []
    for days in (0, 366):
        assert read(client, world.manager_a, f"{stock(world)}/report", days=days).status_code == 422


def test_the_movements_of_an_item_are_paged_newest_first(client: TestClient, world: World, on: None) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    for _ in range(5):
        receive(client, world, [line(rice, "1", 10_000)])
    first = read(client, world.seller_a, f"{stock(world)}/items/{rice}/movements", limit=2).json()
    assert [row["seq"] for row in first["movements"]] == [5, 4]
    rest = read(
        client, world.seller_a, f"{stock(world)}/items/{rice}/movements", limit=10, cursor=first["next_cursor"]
    ).json()
    assert [row["seq"] for row in rest["movements"]] == [3, 2, 1] and rest["next_cursor"] is None
    assert read(client, world.seller_a, f"{stock(world)}/items/{uuid.uuid4()}/movements").status_code == 404
    assert read(client, world.seller_a, f"{stock(world)}/items/{rice}/movements", cursor="x").status_code == 422


# --- the bot ----------------------------------------------------------------------------------------------------


def test_the_bot_lists_what_runs_low_and_only_while_the_stock_is_on(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """`/ombor` is a summary to read. With the switch off the command does not exist: the bot answers
    with its help, exactly as it answers any command it does not know."""
    seller = chat_of(client, owner, world.seller_a)
    unknown = seller.say("/shunaqabuyruqyoq")
    off = seller.say("/ombor")
    assert off.text == unknown.text and off.buttons == {}

    switch(owner)
    assert seller.say("/ombor").text == "📦 Shop A: kam qolgan tovar yo'q."
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    tea = counted_item(client, world, "Choy", 8_000)
    assert patch_item(client, world, rice, low_stock="5").status_code == 200
    assert patch_item(client, world, tea, low_stock="2").status_code == 200
    receive(client, world, [line(rice, "3.5", 10_000), line(tea, "10", 5_000)])
    said = seller.say("/ombor")
    assert said.text == "📦 Shop A: kam qolgan tovarlar\n\n• Guruch: 3.5 kg (chegara 5)"
    assert said.buttons == {}, "nothing to press: nothing here can be mistaken for an entry"
    assert "10000" not in said.text and "10 000" not in said.text, "no cost in the chat"

    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.seller_a,))
    assert seller.say("/ombor").text == "📦 Shop A: товары на исходе\n\n• Guruch: 3.5 кг (порог 5)"
    # A member who may not see the stock is told nothing of it.
    owner.execute("UPDATE app_user SET lang = 'uz' WHERE id = %s", (world.seller_a,))
    switch_permissions_on(owner)
    set_overrides(owner, world.seller_a_membership, denied=["stock.view"])
    assert seller.say("/ombor").text == unknown.text
