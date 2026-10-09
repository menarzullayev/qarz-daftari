"""A sale for cash, without a customer, through the API (BR-98 to BR-104).

Goods lines, stock out, money in, nobody's account touched. Behind `stock_on`; the money is in the cash
book while `cash_book_on` is on. Each rule has the case that works and the case that is refused. That no
route exists with the switch off is in test_stock.py; who may call what by role is in the authorization
suite.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from . import test_cash_book as book
from .conftest import World, as_user, set_overrides, switch_permissions_on
from .test_customers_ledger import another_client, key, read, write
from .test_exports import ask, no_jobs_left_by_earlier_tests, work, workbook
from .test_stock import (
    actions,
    counted_item,
    document,
    item_of,
    line,
    mismatches,
    movements,
    new_item,
    on,
    receive,
    rows_of_the_module,
    stock,
)

pytestmark = pytest.mark.db

__all__ = ["no_jobs_left_by_earlier_tests", "on"]

SALES = "Ombor: naqd savdo"


def sold(item: Any, qty: str, price: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"item_id": str(item), "qty": qty}
    if price is not None:
        body["price"] = price
    return body


def sell(client: TestClient, world: World, lines: list[dict[str, Any]], user: Any = None, **extra: Any) -> Any:
    return write(client, user or world.seller_a, "POST", f"{stock(world)}/sales", {"lines": lines, **extra})


def sale(client: TestClient, world: World, lines: list[dict[str, Any]], user: Any = None, **extra: Any) -> Any:
    response = sell(client, world, lines, user, **extra)
    assert response.status_code == 201, response.text
    return response.json()


def cancel(client: TestClient, world: World, sale_id: Any, user: Any = None, reason: Any = "Xato urilgan") -> Any:
    return write(client, user or world.manager_a, "POST", f"{stock(world)}/sales/{sale_id}/cancel", {"reason": reason})


def sales(client: TestClient, world: World, user: Any = None, **params: Any) -> dict[str, Any]:
    response = read(client, user or world.seller_a, f"{stock(world)}/sales", **params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _has_cost_key(value: Any) -> bool:
    """Whether anything of cost is in an answer about a sale. Its prices and totals are selling prices."""
    if isinstance(value, dict):
        return any(name in ("cost", "unit_cost", "margin") or _has_cost_key(inner) for name, inner in value.items())
    if isinstance(value, list):
        return any(_has_cost_key(inner) for inner in value)
    return False


def cash_rows(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    """(category, direction, method, currency, amount, note, cancelled, why, of a document), oldest first."""
    return owner.execute(
        "SELECT c.name, e.direction, e.method, e.currency, e.amount, e.note, e.cancelled_at IS NOT NULL, "
        "       e.cancel_reason, e.stock_document_id IS NOT NULL "
        "FROM cash_entry e JOIN cash_category c ON c.id = e.category_id WHERE e.shop_id = %s "
        "ORDER BY e.created_at, e.id",
        (world.shop_a,),
    ).fetchall()


def ledger_rows(owner: psycopg.Connection, world: World) -> int:
    row = owner.execute("SELECT count(*) FROM ledger_entry WHERE shop_id = %s", (world.shop_a,)).fetchone()
    assert row is not None
    return int(row[0])


# --- the sale ----------------------------------------------------------------------------------------------


def test_a_cash_sale_takes_counted_goods_out_and_touches_no_customer(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    tea = counted_item(client, world, "Choy", 8_000)
    receive(client, world, [line(rice, "10", 10_000), line(tea, "5", 5_000)])
    entries = ledger_rows(owner, world)

    body = sale(client, world, [sold(rice, "2.5"), sold(tea, "1", 9_000)], method="card", note="Qo'shni")
    assert (body["number"], body["status"], body["method"], body["currency"], body["total"]) == (
        1,
        "posted",
        "card",
        "UZS",
        37_500 + 9_000,
    )
    assert (body["mine"], body["seller_role"], body["note"], body["in_cash_book"]) == (True, "seller", "Qo'shni", False)
    assert [
        (row["item"]["name"], row["qty"], row["price"], row["line_total"], row["counted"]) for row in body["lines"]
    ] == [
        ("Guruch", "2.5", 15_000, 37_500, True),  # the item's own price
        ("Choy", "1", 9_000, 9_000, True),  # the price the seller gave
    ]
    assert body["warnings"] == []
    assert [item_of(client, world, item)["on_hand"] for item in (rice, tea)] == ["7.5", "4"]
    moved = movements(client, world, rice)[0]
    assert (moved["kind"], moved["qty"], moved["sale_total"], moved["ledger_entry_id"]) == (
        "sale",
        "-2.5",
        37_500,
        None,
    )
    assert moved["document"] == {"id": body["id"], "kind": "sale", "number": 1}
    assert moved["cost"]["total"] == 25_000, "it left at the average cost"
    assert item_of(client, world, rice)["cost"]["average"] == 10_000, "a sale leaves the average where it was"
    assert item_of(client, world, rice)["last_sale_at"] is not None
    assert ledger_rows(owner, world) == entries, "nobody's account is touched"
    assert actions(owner, world, "stock.sale") == ["stock.sale_recorded"]
    assert sale(client, world, [sold(tea, "1")])["number"] == 2
    assert mismatches(owner, world) == []


def test_an_item_that_is_not_counted_is_sold_without_a_movement(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    bread = new_item(client, world, "Bulka", 4_000)
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "3", 10_000)])
    body = sale(client, world, [sold(bread, "2"), sold(rice, "1")], world.manager_a)
    assert [(row["counted"], row["line_total"]) for row in body["lines"]] == [(False, 8_000), (True, 15_000)]
    assert body["total"] == 23_000
    assert body["lines"][0]["cost"] == {"currency": None, "total": None, "margin": None}
    assert body["cost"] == {"total": 10_000, "margin": 5_000, "complete": True}, "of the counted line alone"
    assert movements(client, world, bread) == []
    assert item_of(client, world, bread)["on_hand"] == "0"
    assert mismatches(owner, world) == []


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"lines": []}, "lines"),
        ({"lines": "twice"}, "lines.1.item_id"),
        ({"qty": "0"}, "lines.0.qty"),
        ({"qty": "1.2345"}, "lines.0.qty"),
        ({"qty": "-1"}, "lines.0.qty"),
        ({"price": 0}, "lines.0.price"),
        ({"price": 100_000_001}, "lines.0.price"),
        ({"qty": "0.001", "price": 1}, "lines.0.price"),  # a line that comes to nothing
        ({"item": "unknown"}, "lines.0.item_id"),
        ({"item": "other shop"}, "lines.0.item_id"),
        ({"method": "barter"}, "method"),
        ({"currency": "USD"}, "currency"),
        ({"note": "x" * 201}, "note"),
    ],
)
def test_a_wrong_sale_is_refused_and_nothing_is_stored(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, change: dict[str, Any], field: str
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "3", 10_000)])
    other = uuid.uuid4()
    owner.execute(
        "INSERT INTO catalog_item (id, shop_id, name, name_norm, unit, price) "
        "VALUES (%s, %s, 'Begona', 'begona', 'dona', 1000)",
        (other, world.shop_b),
    )
    before = rows_of_the_module(owner, world)
    item = {"unknown": uuid.uuid4(), "other shop": other}.get(change.get("item", ""), rice)
    lines = [sold(item, change.get("qty", "1"), change.get("price"))]
    if change.get("lines") == []:
        lines = []
    elif change.get("lines") == "twice":
        lines = [sold(rice, "1"), sold(rice, "1")]
    extra = {name: change[name] for name in ("method", "currency", "note") if name in change}
    response = sell(client, world, lines, **extra)
    assert response.status_code == 422, response.text
    assert field in response.json()["error"]["fields"], response.text
    assert rows_of_the_module(owner, world) == before
    assert item_of(client, world, rice)["on_hand"] == "3"


def test_a_sale_in_dollars_is_refused_even_in_a_shop_that_works_in_dollars(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('usd_on', 'true'::jsonb, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (str(uuid.uuid4()),),
    )
    owner.execute("UPDATE shop SET usd_on = true WHERE id = %s", (world.shop_a,))
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    refused = sell(client, world, [sold(rice, "1")], currency="USD")
    assert refused.status_code == 422 and "so'm" in refused.json()["error"]["fields"]["currency"]
    assert sale(client, world, [sold(rice, "1")], currency="UZS")["currency"] == "UZS"


def test_an_alias_of_a_merged_item_is_not_sold(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    rice = new_item(client, world, "Guruch", 15_000, "kg")
    alias = new_item(client, world, "Gurunch", 15_000, "kg")
    owner.execute("UPDATE catalog_item SET merged_into = %s, status = 'hidden' WHERE id = %s", (rice, alias))
    refused = sell(client, world, [sold(alias, "1")])
    assert refused.status_code == 422 and "lines.0.item_id" in refused.json()["error"]["fields"]


# --- below zero ------------------------------------------------------------------------------------------------


def test_a_cash_sale_may_take_an_item_below_zero_and_says_so(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "1", 10_000)])
    body = sale(client, world, [sold(rice, "3")])
    assert body["warnings"] == [{"kind": "negative", "item": rice, "name": "Guruch", "on_hand": "-2"}]
    assert item_of(client, world, rice)["on_hand"] == "-2"
    assert mismatches(owner, world) == []


def test_a_shop_that_refuses_sales_beyond_stock_refuses_the_whole_sale_and_keeps_nothing_of_it(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """The first line fits and the second does not: the sale is refused as a whole. No document, no line,
    no movement of the first item, no money, and the number it would have had is the next sale's."""
    book.switch(owner)
    assert (
        write(client, world.manager_a, "PUT", f"{stock(world)}/settings", {"refuse_negative": True}).status_code == 200
    )
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    tea = counted_item(client, world, "Choy", 8_000)
    receive(client, world, [line(rice, "5", 10_000), line(tea, "1", 5_000)])
    before = rows_of_the_module(owner, world)
    cash_before = cash_rows(owner, world)

    refused = sell(client, world, [sold(rice, "2"), sold(tea, "2")])
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "STOCK_INSUFFICIENT", refused.text
    assert refused.json()["error"]["fields"] == {"item": tea, "name": "Choy", "on_hand": "1", "wanted": "2"}
    assert rows_of_the_module(owner, world) == before
    assert cash_rows(owner, world) == cash_before
    assert [item_of(client, world, item)["on_hand"] for item in (rice, tea)] == ["5", "1"]
    assert actions(owner, world, "stock.sale") == []
    assert sales(client, world)["sales"] == []
    assert sale(client, world, [sold(rice, "2"), sold(tea, "1")])["number"] == 1
    assert mismatches(owner, world) == []


# --- the money -------------------------------------------------------------------------------------------------


def test_with_the_cash_book_on_a_sale_is_income_there_and_is_cancelled_only_with_the_sale(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    book.switch(owner)
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)])
    spent = cash_rows(owner, world)
    body = sale(client, world, [sold(rice, "2")], method="transfer")
    assert body["in_cash_book"] is True
    assert cash_rows(owner, world)[len(spent) :] == [
        (SALES, "income", "transfer", "UZS", 30_000, "Naqd savdo № 1", False, None, True)
    ]
    entry = owner.execute(
        "SELECT id, category_id FROM cash_entry WHERE stock_document_id = %s", (body["id"],)
    ).fetchone()
    assert entry is not None
    listed = next(row for row in book.day(client, world)["entries"] if row["id"] == str(entry[0]))
    assert listed["source"] == "stock"

    # The cash book cannot cancel it, and nobody writes under the category by hand.
    assert book.refused(book.cancel(client, world, entry[0]), 409, "CASH_ENTRY_OF_STOCK") == {}
    by_hand = book.add(client, world, "income", 5_000, category_id=str(entry[1]))
    assert by_hand.status_code == 422, by_hand.text
    assert cash_rows(owner, world)[len(spent) :][0][6] is False

    # A second sale lands in the same category; cancelling the first cancels its entry with the reason.
    sale(client, world, [sold(rice, "1")])
    assert cancel(client, world, body["id"], reason="Mijoz qaytardi").status_code == 200
    assert cash_rows(owner, world)[len(spent) :] == [
        (SALES, "income", "transfer", "UZS", 30_000, "Naqd savdo № 1", True, "Mijoz qaytardi", True),
        (SALES, "income", "cash", "UZS", 15_000, "Naqd savdo № 2", False, None, True),
    ]
    assert owner.execute(
        "SELECT count(*) FROM cash_category WHERE shop_id = %s AND system_key = 'cash_sale'", (world.shop_a,)
    ).fetchone() == (1,)
    assert mismatches(owner, world) == []


def test_with_the_cash_book_off_the_sale_and_its_total_are_on_the_sale_alone(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)])
    body = sale(client, world, [sold(rice, "2")], method="card")
    assert (body["in_cash_book"], body["method"], body["total"]) == (False, "card", 30_000)
    assert cash_rows(owner, world) == []
    assert owner.execute("SELECT count(*) FROM cash_category WHERE shop_id = %s", (world.shop_a,)).fetchone() == (0,)
    assert sales(client, world)["totals"] == {
        "count": 1,
        "total": 30_000,
        "by_method": [{"method": "card", "total": 30_000}],
    }
    # Turning the cash book on later copies nothing back, and cancelling the sale then touches nothing there.
    book.switch(owner)
    assert cancel(client, world, body["id"]).status_code == 200
    assert cash_rows(owner, world) == []


def test_a_sale_made_with_the_cash_book_on_is_cancelled_there_even_after_it_is_switched_off(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    book.switch(owner)
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    body = sale(client, world, [sold(rice, "1")])
    book.switch(owner, "false")
    assert cancel(client, world, body["id"]).status_code == 200
    assert [(row[0], row[6]) for row in cash_rows(owner, world)] == [(SALES, True)]


def test_the_cash_category_of_sales_is_named_in_the_shops_language(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    book.switch(owner)
    owner.execute("UPDATE shop SET lang = 'ru' WHERE id = %s", (world.shop_a,))
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    sale(client, world, [sold(rice, "1")])
    assert [(row[0], row[5]) for row in cash_rows(owner, world)] == [
        ("Склад: продажа за наличные", "Продажа за наличные № 1")
    ]


# --- taking a sale back ------------------------------------------------------------------------------------------


def test_cancelling_a_sale_brings_the_goods_back_at_the_cost_they_left_with(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    bread = new_item(client, world, "Bulka", 4_000)
    receive(client, world, [line(rice, "10", 10_000)])
    body = sale(client, world, [sold(rice, "4"), sold(bread, "1")])
    # The average moves in between: what comes back still comes at what it left with.
    receive(client, world, [line(rice, "6", 20_000)])

    for reason in ("", "  "):
        assert cancel(client, world, body["id"], reason=reason).status_code == 422
    assert cancel(client, world, uuid.uuid4()).status_code == 404
    taken = cancel(client, world, body["id"], reason="Mijoz qaytardi")
    assert taken.status_code == 200, taken.text
    assert (taken.json()["status"], taken.json()["cancel_reason"]) == ("cancelled", "Mijoz qaytardi")
    assert taken.json()["cancelled_at"] is not None
    stored = item_of(client, world, rice)
    assert stored["on_hand"] == "16" and stored["cost"]["value"] == 60_000 + 120_000 + 40_000
    assert [row["kind"] for row in movements(client, world, rice)] == ["reversal", "receipt", "sale", "receipt"]
    again = cancel(client, world, body["id"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "SALE_CANCELLED"
    assert item_of(client, world, rice)["on_hand"] == "16"
    assert actions(owner, world, "stock.sale") == ["stock.sale_recorded", "stock.sale_cancelled"]
    listed = sales(client, world)
    assert [row["status"] for row in listed["sales"]] == ["cancelled"]
    assert listed["totals"] == {"count": 0, "total": 0, "by_method": []}, "a cancelled sale brought nothing"
    assert mismatches(owner, world) == []


def test_a_sale_is_not_a_document_of_the_documents_routes(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """It has routes of its own: the list of documents leaves it out, and reading, changing, posting or
    cancelling it as a document answers as for a document that does not exist. Nor is a document a sale."""
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receipt = receive(client, world, [line(rice, "10", 10_000)])
    body = sale(client, world, [sold(rice, "1")])
    papers = f"{stock(world)}/documents"
    assert [row["kind"] for row in read(client, world.manager_a, papers).json()["documents"]] == ["receipt"]
    assert read(client, world.manager_a, papers, kind="sale").status_code == 422
    assert read(client, world.manager_a, f"{papers}/{body['id']}").status_code == 404
    write_off = {"kind": "write_off", "reason": "lost", "lines": [line(rice, "1")]}
    assert write(client, world.manager_a, "PUT", f"{papers}/{body['id']}", write_off).status_code == 404
    assert write(client, world.manager_a, "POST", f"{papers}/{body['id']}/post").status_code == 404
    assert write(client, world.manager_a, "POST", f"{papers}/{body['id']}/cancel", {"reason": "x"}).status_code == 404
    assert document(client, world, kind="sale", lines=[line(rice, "1")]).status_code == 422
    assert read(client, world.manager_a, f"{stock(world)}/settings").json()["document_kinds"] == [
        "receipt",
        "customer_return",
        "supplier_return",
        "write_off",
        "stocktake",
    ]
    assert read(client, world.seller_a, f"{stock(world)}/sales/{receipt['id']}").status_code == 404
    assert cancel(client, world, receipt["id"]).status_code == 404
    assert item_of(client, world, rice)["on_hand"] == "9"
    assert read(client, world.seller_a, f"{stock(world)}/sales/{body['id']}").json()["status"] == "posted"


def test_another_shops_sale_does_not_exist(client: TestClient, world: World, on: None) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    body = sale(client, world, [sold(rice, "1")])
    there = f"/api/v1/shops/{world.shop_b}/stock/sales"
    assert read(client, world.owner_b, f"{there}/{body['id']}").status_code == 404
    assert write(client, world.owner_b, "POST", f"{there}/{body['id']}/cancel", {"reason": "x"}).status_code == 404
    assert read(client, world.owner_b, there).json()["sales"] == []
    assert sell(client, world, [sold(rice, "1")], world.owner_b).status_code == 404, "not a member: no such shop"
    assert item_of(client, world, rice)["on_hand"] == "-1"


# --- who may, and who sees cost ------------------------------------------------------------------------------------


def test_cost_and_margin_of_a_sale_are_absent_for_a_member_who_may_not_see_them(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """A seller sells below cost and is told nothing that would say what the goods cost; a manager's
    answer carries the cost, the margin and the warning."""
    oil = counted_item(client, world, "Yog'", 30_000, "l")
    receive(client, world, [line(oil, "10", 32_000)])
    seen = sale(client, world, [sold(oil, "1")], world.seller_a)
    assert not _has_cost_key(seen) and seen["warnings"] == []
    for answer in (
        read(client, world.seller_a, f"{stock(world)}/sales/{seen['id']}").json(),
        sales(client, world, world.seller_a),
    ):
        assert not _has_cost_key(answer), answer
    assert not _has_cost_key(sales(client, world, world.manager_a)), "a list carries no cost for anyone"

    full = sale(client, world, [sold(oil, "2", 35_000)], world.manager_a)
    assert full["lines"][0]["cost"] == {"currency": "UZS", "total": 64_000, "margin": 6_000}
    assert full["cost"] == {"total": 64_000, "margin": 6_000, "complete": True} and full["warnings"] == []
    cheap = sale(client, world, [sold(oil, "1", 20_000)], world.manager_a)
    assert cheap["warnings"] == [{"kind": "below_cost", "item": oil, "name": "Yog'"}]
    assert cheap["cost"]["margin"] == -12_000
    assert read(client, world.manager_a, f"{stock(world)}/sales/{seen['id']}").json()["cost"]["margin"] == -2_000

    switch_permissions_on(owner)
    set_overrides(owner, world.manager_a_membership, denied=["stock.costs.view"])
    assert not _has_cost_key(read(client, world.manager_a, f"{stock(world)}/sales/{cheap['id']}").json())
    assert sale(client, world, [sold(oil, "1", 20_000)], world.manager_a)["warnings"] == []


def test_a_seller_sells_and_a_manager_takes_a_sale_back(client: TestClient, world: World, on: None) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    body = sale(client, world, [sold(rice, "1")], world.seller_a)
    assert sales(client, world, world.seller_a)["may_cancel"] is False
    assert sales(client, world, world.manager_a)["may_cancel"] is True
    refused = cancel(client, world, body["id"], world.seller_a)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "FORBIDDEN_ROLE", refused.text
    assert item_of(client, world, rice)["on_hand"] == "-1"
    assert cancel(client, world, body["id"], world.manager_a).status_code == 200
    for user in (world.stranger, world.customer_of_a):
        assert sell(client, world, [sold(rice, "1")], user).status_code == 404
        assert read(client, user, f"{stock(world)}/sales").status_code == 404


def test_with_the_matrix_on_selling_and_cancelling_follow_their_permissions(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    body = sale(client, world, [sold(rice, "1")], world.seller_a)
    switch_permissions_on(owner)

    def refused(response: Any, permission: str) -> bool:
        return bool(response.status_code == 403 and response.json()["error"]["fields"] == {"permission": permission})

    # Selling opens the list of items and the barcode by itself: finding the item is part of selling it.
    set_overrides(owner, world.seller_a_membership, denied=["stock.view"])
    assert read(client, world.seller_a, f"{stock(world)}/items").status_code == 200
    assert read(client, world.seller_a, f"{stock(world)}/settings").status_code == 200
    assert refused(read(client, world.seller_a, f"{stock(world)}/items/{rice}"), "stock.view")
    assert sell(client, world, [sold(rice, "1")], world.seller_a).status_code == 201

    set_overrides(owner, world.seller_a_membership, denied=["stock.sell"])
    assert refused(sell(client, world, [sold(rice, "1")], world.seller_a), "stock.sell")
    assert refused(read(client, world.seller_a, f"{stock(world)}/sales"), "stock.sell")
    assert refused(read(client, world.seller_a, f"{stock(world)}/sales/{body['id']}"), "stock.sell")
    assert read(client, world.seller_a, f"{stock(world)}/items").status_code == 200, "stock.view is back"
    assert refused(cancel(client, world, body["id"], world.seller_a), "stock.sell.cancel")

    set_overrides(owner, world.seller_a_membership, granted=["stock.sell.cancel"])
    set_overrides(owner, world.manager_a_membership, denied=["stock.sell.cancel"])
    assert refused(cancel(client, world, body["id"], world.manager_a), "stock.sell.cancel")
    assert sales(client, world, world.seller_a)["may_cancel"] is True
    assert cancel(client, world, body["id"], world.seller_a).status_code == 200
    assert item_of(client, world, rice, world.owner_a)["on_hand"] == "-1"


def test_a_repeated_request_records_one_sale(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    headers = {**as_user(world.seller_a), **key()}
    body = {"lines": [sold(rice, "1")]}
    first = client.post(f"{stock(world)}/sales", json=body, headers=headers)
    again = client.post(f"{stock(world)}/sales", json=body, headers=headers)
    assert first.status_code == again.status_code == 201 and first.json() == again.json()
    other = client.post(f"{stock(world)}/sales", json={"lines": [sold(rice, "2")]}, headers=headers)
    assert other.status_code == 409 and other.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert item_of(client, world, rice)["on_hand"] == "-1"
    assert len(sales(client, world)["sales"]) == 1


# --- reading -----------------------------------------------------------------------------------------------------


def test_the_days_sales_are_listed_newest_first_and_narrowed_by_day_item_seller_and_state(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    tea = counted_item(client, world, "Choy", 8_000)
    first = sale(client, world, [sold(rice, "1")], world.seller_a)
    second = sale(client, world, [sold(tea, "2")], world.manager_a, method="card")
    third = sale(client, world, [sold(rice, "2"), sold(tea, "1")], world.seller_a)
    assert cancel(client, world, third["id"]).status_code == 200
    # The first sale becomes yesterday's.
    owner.execute("ALTER TABLE stock_document DISABLE TRIGGER stock_document_guard")
    owner.execute(
        "UPDATE stock_document SET created_at = created_at - interval '1 day', doc_date = doc_date - 1, "
        "  posted_at = posted_at - interval '1 day' WHERE id = %s",
        (first["id"],),
    )
    owner.execute("ALTER TABLE stock_document ENABLE TRIGGER stock_document_guard")

    today = sales(client, world)
    assert [row["id"] for row in today["sales"]] == [third["id"], second["id"]]
    assert today["sales"][0]["lines"][0] == {
        "line_no": 1,
        "item": {"id": rice, "name": "Guruch", "unit": "kg"},
        "qty": "2",
        "price": 15_000,
        "line_total": 30_000,
        "counted": None,
    }
    assert today["totals"] == {"count": 1, "total": 16_000, "by_method": [{"method": "card", "total": 16_000}]}
    assert today["day_from"] == today["day_to"] == second["day"]

    owner_day = owner.execute("SELECT doc_date FROM stock_document WHERE id = %s", (first["id"],)).fetchone()
    assert owner_day is not None
    both = sales(client, world, day_from=owner_day[0].isoformat(), day_to=second["day"])
    assert [row["id"] for row in both["sales"]] == [third["id"], second["id"], first["id"]]
    assert both["totals"]["total"] == 15_000 + 16_000
    assert [row["id"] for row in sales(client, world, day_to=owner_day[0].isoformat())["sales"]] == [first["id"]]

    assert [row["id"] for row in sales(client, world, item_id=rice)["sales"]] == [third["id"]]
    assert [row["id"] for row in sales(client, world, item_id=tea, status="posted")["sales"]] == [second["id"]]
    assert [row["id"] for row in sales(client, world, status="cancelled")["sales"]] == [third["id"]]
    mine = sales(client, world, world.manager_a, mine="true")
    assert [row["id"] for row in mine["sales"]] == [second["id"]] and mine["sales"][0]["mine"] is True
    by_seller = sales(client, world, world.manager_a, seller_id=str(world.seller_a_membership))
    assert [(row["id"], row["mine"], row["seller_role"]) for row in by_seller["sales"]] == [
        (third["id"], False, "seller")
    ]
    assert by_seller["totals"]["count"] == 0

    page = sales(client, world, limit=1)
    assert [row["id"] for row in page["sales"]] == [third["id"]] and page["next_cursor"]
    rest = sales(client, world, limit=1, cursor=page["next_cursor"])
    assert [row["id"] for row in rest["sales"]] == [second["id"]] and rest["next_cursor"] is None

    for wrong in (
        {"limit": 0},
        {"limit": 101},
        {"status": "draft"},
        {"day_from": second["day"], "day_to": owner_day[0].isoformat()},
        {"day_from": "2020-01-01", "day_to": second["day"]},
        {"mine": "true", "seller_id": str(world.seller_a_membership)},
        {"cursor": "nonsense"},
    ):
        assert read(client, world.seller_a, f"{stock(world)}/sales", **wrong).status_code == 422, wrong


def test_the_report_counts_cash_sales_in_what_sold_and_in_what_does_not_sell(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    oil = counted_item(client, world, "Yog'", 30_000, "l")
    tea = counted_item(client, world, "Choy", 8_000)
    bread = new_item(client, world, "Bulka", 4_000)
    receive(client, world, [line(rice, "10", 10_000), line(oil, "4", 32_000), line(tea, "2", 5_000)])
    sale(client, world, [sold(rice, "2"), sold(oil, "1"), sold(bread, "3")])
    taken_back = sale(client, world, [sold(rice, "5")], method="card")
    sale(client, world, [sold(rice, "1", 16_000)], method="card")
    assert cancel(client, world, taken_back["id"]).status_code == 200

    report = read(client, world.manager_a, f"{stock(world)}/report", days=30).json()
    assert [row["id"] for row in report["not_sold"]["items"]] == [tea], "rice and oil sold today, for cash"
    assert [(row["name"], row["loss"]) for row in report["sold_below_cost"]["sales"]] == [("Yog'", 2_000)]
    assert report["sold"] == {
        "items": [
            {
                "item_id": rice,
                "name": "Guruch",
                "unit": "kg",
                "qty": "3",
                "revenue": 46_000,
                "cost": 30_000,
                "margin": 16_000,
                "cash_qty": "3",
                "cash_revenue": 46_000,
            },
            {
                "item_id": oil,
                "name": "Yog'",
                "unit": "l",
                "qty": "1",
                "revenue": 30_000,
                "cost": 32_000,
                "margin": -2_000,
                "cash_qty": "1",
                "cash_revenue": 30_000,
            },
        ],
        "more": False,
    }
    # Every line of the sales that stand, the bread that is not counted too.
    assert report["cash_sales"] == {
        "count": 2,
        "total": 30_000 + 30_000 + 12_000 + 16_000,
        "by_method": [{"method": "card", "total": 16_000}, {"method": "cash", "total": 72_000}],
    }
    assert read(client, world.seller_a, f"{stock(world)}/report").status_code == 403


def test_the_owners_export_has_a_sheet_of_cash_sales_only_for_a_shop_that_made_one(
    client: TestClient, world: World, on: None, worker_database_url: str, file_root: Path
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    bread = new_item(client, world, "Bulka", 4_000)
    receive(client, world, [line(rice, "10", 10_000)])
    job = ask(client, world).json()["id"]
    assert work(worker_database_url, file_root) == 1
    assert "Naqd savdo" not in workbook(client, world, job)

    first = sale(client, world, [sold(rice, "2.5"), sold(bread, "1")], method="card", note="Qo'shni")
    second = sale(client, world, [sold(rice, "1")])
    assert cancel(client, world, second["id"], reason="Xato").status_code == 200
    job = ask(client, world).json()["id"]
    assert work(worker_database_url, file_root) == 1
    pages = workbook(client, world, job)
    assert "sale" not in {row[1] for row in pages["Ombor hujjatlari"][1:]}
    assert [row[1] for row in pages["Ombor hujjatlari"][1:]] == ["Kirim"]
    rows = pages["Naqd savdo"]
    assert rows[0][:5] == ["Savdo raqami", "Vaqt", "Holati", "To'lov usuli", "Tovar"]
    assert [[row[0], *row[2:14]] for row in rows[1:]] == [
        [
            1,
            "O'tkazilgan",
            "Karta",
            "Guruch",
            "kg",
            Decimal("2.5"),
            15_000,
            37_500,
            25_000,
            "UZS",
            41_500,
            "Qo'shni",
            None,
        ],
        [1, "O'tkazilgan", "Karta", "Bulka", "dona", 1, 4_000, 4_000, None, None, 41_500, "Qo'shni", None],
        [2, "Bekor qilingan", "Naqd", "Guruch", "kg", 1, 15_000, 15_000, 10_000, "UZS", 15_000, None, "Xato"],
    ]
    assert rows[1][14] == first["id"]
    assert "Naqd savdo № 1" in {row[10] for row in pages["Ombor harakatlari"][1:]}


# --- at the same moment -----------------------------------------------------------------------------------------


def test_cash_sales_and_a_receipt_of_the_same_items_at_once_take_turns_and_the_books_add_up(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, app_database_url: str
) -> None:
    """A cash sale and a purchase receipt name the same two items in opposite orders, twenty times over,
    with the cash book on so both also write money: neither waits for the other in a ring, every movement
    continues the level before it, and each sale has its one cash entry."""
    book.switch(owner)
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    tea = counted_item(client, world, "Choy", 8_000)
    receive(client, world, [line(rice, "100", 10_000), line(tea, "100", 5_000)])
    with another_client(app_database_url) as second:
        for _ in range(20):
            barrier = threading.Barrier(2)

            def counter(barrier: threading.Barrier = barrier) -> int:
                barrier.wait(timeout=10)
                return int(sell(client, world, [sold(rice, "1"), sold(tea, "1")]).status_code)

            def receipt(barrier: threading.Barrier = barrier) -> int:
                barrier.wait(timeout=10)
                body = {"kind": "receipt", "post": True, "lines": [line(tea, "2", 6_000), line(rice, "2", 11_000)]}
                headers = {**as_user(world.manager_a), **key()}
                return int(second.post(f"{stock(world)}/documents", json=body, headers=headers).status_code)

            with ThreadPoolExecutor(max_workers=2) as pool:
                one, other = pool.submit(counter), pool.submit(receipt)
                assert (one.result(), other.result()) == (201, 201)
        assert [item_of(client, world, item)["on_hand"] for item in (rice, tea)] == ["120", "120"]
        assert [row["seq"] for row in movements(client, world, rice)] == list(range(41, 0, -1))
    listed = sales(client, world, limit=100)
    assert sorted(row["number"] for row in listed["sales"]) == list(range(1, 21))
    assert listed["totals"] == {"count": 20, "total": 20 * 23_000, "by_method": [{"method": "cash", "total": 460_000}]}
    income = [row for row in cash_rows(owner, world) if row[1] == "income"]
    assert len(income) == 20 and {row[4] for row in income} == {23_000}
    assert mismatches(owner, world) == []


def test_two_counters_selling_the_same_item_at_once_both_count_and_the_last_unit_is_sold_once(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, app_database_url: str
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    tea = counted_item(client, world, "Choy", 8_000)
    receive(client, world, [line(rice, "100", 10_000), line(tea, "100", 5_000)])
    with another_client(app_database_url) as second:
        for _ in range(15):
            barrier = threading.Barrier(2)

            def counter(pair: tuple[TestClient, list[dict[str, Any]]], barrier: threading.Barrier = barrier) -> int:
                which, lines = pair
                barrier.wait(timeout=10)
                return int(sell(which, world, lines).status_code)

            # The two name the items in opposite orders.
            pairs = ((client, [sold(rice, "1"), sold(tea, "2")]), (second, [sold(tea, "1"), sold(rice, "3")]))
            with ThreadPoolExecutor(max_workers=2) as pool:
                assert list(pool.map(counter, pairs)) == [201, 201]
        assert [item_of(client, world, item)["on_hand"] for item in (rice, tea)] == ["40", "55"]
        numbers = [row["number"] for row in sales(client, world, limit=100)["sales"]]
        assert sorted(numbers) == list(range(1, 31)), "no number twice and none missing"

        # The last unit, in a shop that refuses sales beyond stock: two counters reach for it at once.
        assert (
            write(client, world.manager_a, "PUT", f"{stock(world)}/settings", {"refuse_negative": True}).status_code
            == 200
        )
        last = counted_item(client, world, "Oxirgi")
        receive(client, world, [line(last, "1", 1_000)])
        barrier = threading.Barrier(2)

        def reach(which: TestClient) -> int:
            barrier.wait(timeout=10)
            return int(sell(which, world, [sold(last, "1")]).status_code)

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(reach, (client, second))) == [201, 409]
        assert item_of(client, world, last)["on_hand"] == "0"
        assert len(sales(client, world, item_id=last)["sales"]) == 1
    assert mismatches(owner, world) == []


def test_a_cash_sale_and_a_credit_sale_of_the_same_items_at_once(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, app_database_url: str
) -> None:
    from .test_customers_ledger import new_customer
    from .test_goods_lines import chosen
    from .test_goods_lines import sell as on_credit

    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    tea = counted_item(client, world, "Choy", 8_000)
    receive(client, world, [line(rice, "50", 10_000), line(tea, "50", 5_000)])
    customer = new_customer(client, world, "Vali")
    with another_client(app_database_url) as second:
        for _ in range(10):
            barrier = threading.Barrier(2)

            def cash(barrier: threading.Barrier = barrier) -> int:
                barrier.wait(timeout=10)
                return int(sell(second, world, [sold(tea, "1"), sold(rice, "1")]).status_code)

            def credit(barrier: threading.Barrier = barrier) -> int:
                barrier.wait(timeout=10)
                return int(
                    on_credit(client, world, customer, [chosen(rice, "1", 15_000), chosen(tea, "1", 8_000)]).status_code
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                one, other = pool.submit(cash), pool.submit(credit)
                assert (one.result(), other.result()) == (201, 201)
    assert [item_of(client, world, item)["on_hand"] for item in (rice, tea)] == ["30", "30"]
    assert mismatches(owner, world) == []
