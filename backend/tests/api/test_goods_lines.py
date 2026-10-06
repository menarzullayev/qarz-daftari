"""Itemized credit sales and goods lines added later, through the API (stories S6.2, S6.3).

REQ-037, REQ-038, REQ-040, REQ-N06; INV-7, INV-8, INV-17; BR-6, BR-7.
"""

import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.auth import AuthService
from qarz.domain.promise import TASHKENT, tashkent_date
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app

from .conftest import TEST_BOT_TOKEN, HeaderAuthenticator, MovableClock, World, as_user
from .test_customers_ledger import (
    another_client,
    count,
    detail,
    key,
    record,
    reverse,
    seed_customer,
    shop,
)

pytestmark = pytest.mark.db

_TABLES = ("ledger_entry", "goods_line", "promise", "catalog_item", "activity", "request_key")


def typed(name: str, qty: str, unit_price: int, unit: str | None = None) -> dict[str, Any]:
    line: dict[str, Any] = {"name": name, "qty": qty, "unit_price": unit_price}
    if unit is not None:
        line["unit"] = unit
    return line


def chosen(item: Any, qty: str, unit_price: int) -> dict[str, Any]:
    return {"catalog_item_id": str(item), "qty": qty, "unit_price": unit_price}


def sell(
    client: TestClient,
    world: World,
    customer: Any,
    lines: Any,
    *,
    user: uuid.UUID | None = None,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> Any:
    """A credit sale with goods lines; `amount` is left out unless given."""
    body = {"kind": "credit", "lines": lines, **extra}
    return client.post(
        f"{shop(world)}/customers/{customer}/entries",
        json=body,
        headers={**as_user(user or world.seller_a), **(headers or key())},
    )


def add(
    client: TestClient,
    world: World,
    entry: Any,
    lines: Any,
    *,
    user: uuid.UUID | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    return client.post(
        f"{shop(world)}/entries/{entry}/lines",
        json={"lines": lines},
        headers={**as_user(user or world.seller_a), **(headers or key())},
    )


def refusal(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def stored(owner: psycopg.Connection, world: World) -> tuple[int, ...]:
    """Everything a refused sale or a refused addition could have left behind in shop A."""
    return tuple(count(owner, table, world.shop_a) for table in _TABLES)


def lines_of(owner: psycopg.Connection, entry: Any) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT line_no, catalog_item_id, name, qty, unit, unit_price, line_total FROM goods_line "
        "WHERE entry_id = %s ORDER BY line_no",
        (entry,),
    ).fetchall()


def catalog_row(owner: psycopg.Connection, shop_id: uuid.UUID, name_norm: str) -> tuple[Any, ...] | None:
    return owner.execute(
        "SELECT id, name, unit, price, learned, status FROM catalog_item WHERE shop_id = %s AND name_norm = %s",
        (shop_id, name_norm),
    ).fetchone()


def seed_sale(
    owner: psycopg.Connection,
    world: World,
    customer: uuid.UUID,
    seq: int,
    amount: int,
    created_at: datetime,
    *,
    kind: str = "credit",
    shop_id: uuid.UUID | None = None,
    author: uuid.UUID | None = None,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            entry_id,
            shop_id or world.shop_a,
            customer,
            seq,
            kind,
            amount,
            author or world.seller_a_membership,
            created_at,
        ),
    )
    if kind == "credit":
        owner.execute(
            "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor, created_at) "
            "VALUES (%s, %s, %s, %s, 'default', %s)",
            (
                uuid.uuid4(),
                shop_id or world.shop_a,
                entry_id,
                tashkent_date(created_at) + timedelta(days=30),
                created_at,
            ),
        )
    return entry_id


def another_seller(owner: psycopg.Connection, world: World) -> uuid.UUID:
    user_id = uuid.uuid4()
    owner.execute("INSERT INTO app_user (id, tg_id, lang) VALUES (%s, %s, 'uz')", (user_id, uuid.uuid4().int % 10**15))
    owner.execute(
        "INSERT INTO membership (id, shop_id, user_id, role, status) VALUES (%s, %s, %s, 'seller', 'active')",
        (uuid.uuid4(), world.shop_a, user_id),
    )
    return user_id


# --- an itemized sale (REQ-037, REQ-040) --------------------------------------------------------------


def test_a_line_chosen_from_the_catalog_takes_its_name_and_unit(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = stored(owner, world)
    response = sell(client, world, world.customer_a, [chosen(world.catalog_item_a, "3", 4000)])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["entry"]["kind"] == "credit"
    assert body["entry"]["amount"] == 12000  # left out of the request: the sum of the lines
    assert body["entry"]["lines"] == [
        {
            "line_no": 1,
            "catalog_item_id": str(world.catalog_item_a),
            "name": "Non",
            "qty": "3",
            "unit": "dona",
            "unit_price": 4000,
            "line_total": 12000,
        }
    ]
    assert body["customer"]["balance"] == 62000
    entry = body["entry"]["id"]
    assert lines_of(owner, entry) == [(1, world.catalog_item_a, "Non", Decimal("3"), "dona", 4000, 12000)]
    assert owner.execute("SELECT amount, kind FROM ledger_entry WHERE id = %s", (entry,)).fetchone() == (
        12000,
        "credit",
    )
    # One entry, one line, one promise, no new catalog item, one activity row, one stored request.
    assert stored(owner, world) == tuple(a + b for a, b in zip(before, (1, 1, 1, 0, 1, 1), strict=True))


def test_a_price_changed_for_one_line_never_rewrites_the_catalog(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """BR-6: the catalog price is only what the form starts with."""
    response = sell(client, world, world.customer_a, [chosen(world.catalog_item_a, "2", 3500)])
    assert response.status_code == 201, response.text
    line = response.json()["entry"]["lines"][0]
    assert (line["unit_price"], line["line_total"]) == (3500, 7000)
    assert catalog_row(owner, world.shop_a, "non") == (world.catalog_item_a, "Non", "dona", 4000, False, "active")


def test_a_typed_good_is_learned_by_the_catalog(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    assert catalog_row(owner, world.shop_a, "shakar") is None
    response = sell(client, world, world.customer_a, [typed("  Shakar ", "1.5", 14000, unit="кг")])
    assert response.status_code == 201, response.text
    learned = catalog_row(owner, world.shop_a, "shakar")
    assert learned is not None
    assert learned[1:] == ("Shakar", "kg", 14000, True, "active")
    assert response.json()["entry"]["amount"] == 21000
    assert response.json()["entry"]["lines"] == [
        {
            "line_no": 1,
            "catalog_item_id": str(learned[0]),
            "name": "Shakar",
            "qty": "1.5",
            "unit": "kg",
            "unit_price": 14000,
            "line_total": 21000,
        }
    ]
    logged = owner.execute(
        "SELECT count(*) FROM activity WHERE shop_id = %s AND action = 'catalog.item.learned' AND subject_id = %s",
        (world.shop_a, learned[0]),
    ).fetchone()
    assert logged == (1,)


def test_a_typed_line_without_a_unit_is_sold_by_the_piece(client: TestClient, world: World) -> None:
    response = sell(client, world, world.customer_a, [typed("Gugurt", "4", 500)])
    assert response.status_code == 201, response.text
    assert response.json()["entry"]["lines"][0]["unit"] == "dona"


def test_a_typed_name_the_catalog_knows_points_at_that_item_and_leaves_it_alone(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    items = count(owner, "catalog_item", world.shop_a)
    response = sell(client, world, world.customer_a, [typed("NON", "2", 5000, unit="kg")])
    assert response.status_code == 201, response.text
    line = response.json()["entry"]["lines"][0]
    # The line keeps what the seller typed; the catalog keeps its own name, unit and price.
    assert (line["catalog_item_id"], line["name"], line["unit"], line["unit_price"]) == (
        str(world.catalog_item_a),
        "NON",
        "kg",
        5000,
    )
    assert count(owner, "catalog_item", world.shop_a) == items
    assert catalog_row(owner, world.shop_a, "non") == (world.catalog_item_a, "Non", "dona", 4000, False, "active")


def test_a_mixed_sale_keeps_its_line_order_and_may_state_the_total(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    items = count(owner, "catalog_item", world.shop_a)
    lines = [
        typed("Un", "2.500", 8000, unit="kg"),
        chosen(world.catalog_item_a, "1.0", 4000),
        chosen(world.learned_item_a, "2", 9000),  # learned, not yet reviewed, but shown: it can be chosen
        typed("un", "0.5", 9000, unit="kg"),  # the same good again at another price
        chosen(world.catalog_item_a, "3", 3900),
    ]
    response = sell(client, world, world.customer_a, lines, amount=58200, note="  to'y  uchun ")
    assert response.status_code == 201, response.text
    entry = response.json()["entry"]
    assert (entry["amount"], entry["note"]) == (58200, "to'y uchun")
    un = catalog_row(owner, world.shop_a, "un")
    assert un is not None
    assert un[1:] == ("Un", "kg", 8000, True, "active")  # taught by the first of the two lines
    assert count(owner, "catalog_item", world.shop_a) == items + 1
    assert [
        (line["line_no"], line["catalog_item_id"], line["name"], line["qty"], line["unit_price"], line["line_total"])
        for line in entry["lines"]
    ] == [
        (1, str(un[0]), "Un", "2.5", 8000, 20000),
        (2, str(world.catalog_item_a), "Non", "1", 4000, 4000),
        (3, str(world.learned_item_a), "Qatiq", "2", 9000, 18000),
        (4, str(un[0]), "un", "0.5", 9000, 4500),
        (5, str(world.catalog_item_a), "Non", "3", 3900, 11700),
    ]
    assert [row[0] for row in lines_of(owner, entry["id"])] == [1, 2, 3, 4, 5]
    batches = owner.execute("SELECT count(DISTINCT batch_at) FROM goods_line WHERE entry_id = %s", (entry["id"],))
    assert batches.fetchone() == (1,)


@pytest.mark.parametrize(
    ("qty", "unit_price", "expected"),
    [
        ("0.125", 4, 1),  # 0.5 rounds up
        ("0.375", 4, 2),  # 1.5 rounds up, not to the even 2 by luck: see the next case
        ("0.625", 4, 3),  # 2.5 rounds up, not to the even 2
        ("1.5", 333, 500),  # 499.5
        ("0.333", 10000, 3330),  # exact
        ("1.234", 999, 1233),  # 1232.766
        ("0.001", 1499, 1),  # 1.499 rounds down
    ],
)
def test_a_line_total_is_rounded_to_whole_uzs_with_halves_up(
    client: TestClient, world: World, owner: psycopg.Connection, qty: str, unit_price: int, expected: int
) -> None:
    """BR-7. The second line only lifts the sale over the smallest entry the ledger takes."""
    response = sell(
        client,
        world,
        world.customer_a,
        [typed("Tuz", qty, unit_price, unit="kg"), chosen(world.catalog_item_a, "1", 4000)],
    )
    assert response.status_code == 201, response.text
    entry = response.json()["entry"]
    assert entry["lines"][0]["line_total"] == expected
    assert entry["amount"] == expected + 4000
    assert lines_of(owner, entry["id"])[0][6] == expected


def test_fifty_lines_are_accepted_and_fifty_one_are_not(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = stored(owner, world)
    too_many = sell(client, world, world.customer_a, [chosen(world.catalog_item_a, "1", 4000)] * 51)
    assert refusal(too_many) == (422, "VALIDATION")
    assert set(too_many.json()["error"]["fields"]) == {"lines"}
    assert stored(owner, world) == before

    full = sell(client, world, world.customer_a, [chosen(world.catalog_item_a, "1", 4000)] * 50)
    assert full.status_code == 201, full.text
    assert full.json()["entry"]["amount"] == 200_000
    assert [line["line_no"] for line in full.json()["entry"]["lines"]] == list(range(1, 51))


def test_the_bounds_of_an_entry_apply_to_the_sum_of_its_lines(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = stored(owner, world)
    for lines in (
        [typed("Arzon", "1", 99)],
        [typed("Qimmat", "1", 100_000_000), typed("Arzon", "1", 1)],
    ):
        response = sell(client, world, world.settled_customer_a, lines)
        assert refusal(response) == (422, "VALIDATION")
        assert set(response.json()["error"]["fields"]) == {"amount"}
    assert stored(owner, world) == before
    assert sell(client, world, world.settled_customer_a, [typed("Arzon", "1", 100)]).status_code == 201
    assert sell(client, world, world.settled_customer_a, [typed("Qimmat", "1", 100_000_000)]).status_code == 201


# --- refusals: the whole write is atomic --------------------------------------------------------------

GOOD = {"name": "Yangi mahsulot", "qty": "1", "unit_price": 5000}
NON = "catalog_item_a"  # replaced by the item's identifier


@pytest.mark.parametrize(
    ("lines", "field"),
    [
        ([], "lines"),
        ([GOOD, {"name": "Tuz", "qty": "0", "unit_price": 5000}], "lines.1.qty"),
        ([{"name": "Tuz", "qty": "0.000", "unit_price": 5000}], "lines.0.qty"),
        ([{"name": "Tuz", "qty": "-1", "unit_price": 5000}], "lines.0.qty"),
        ([{"name": "Tuz", "qty": "1.2345", "unit_price": 5000}], "lines.0.qty"),
        ([{"name": "Tuz", "qty": "1,5", "unit_price": 5000}], "lines.0.qty"),
        ([{"name": "Tuz", "qty": "1e3", "unit_price": 5000}], "lines.0.qty"),
        ([{"name": "Tuz", "qty": " 1", "unit_price": 5000}], "lines.0.qty"),
        ([{"name": "Tuz", "qty": "", "unit_price": 5000}], "lines.0.qty"),
        ([{"name": "Tuz", "qty": "1234567890", "unit_price": 5000}], "lines.0.qty"),
        ([{"name": "Tuz", "qty": 1.5, "unit_price": 5000}], "lines.0.qty"),  # a number, not a decimal string
        ([{"name": "Tuz", "qty": 2, "unit_price": 5000}], "lines.0.qty"),
        ([{"name": "Tuz", "qty": None, "unit_price": 5000}], "lines.0.qty"),
        ([{"name": "Tuz", "unit_price": 5000}], "lines.0.qty"),
        ([{"name": "Tuz", "qty": "0.001", "unit_price": 100}], "lines.0.qty"),  # comes to 0 UZS
        ([{"name": "Tuz", "qty": "1", "unit_price": 0}], "lines.0.unit_price"),
        ([{"name": "Tuz", "qty": "1", "unit_price": -5}], "lines.0.unit_price"),
        ([{"name": "Tuz", "qty": "1", "unit_price": 100_000_001}], "lines.0.unit_price"),
        ([{"name": "Tuz", "qty": "1", "unit_price": "5000"}], "lines.0.unit_price"),
        ([{"name": "Tuz", "qty": "1", "unit_price": 5000.5}], "lines.0.unit_price"),
        ([{"name": "Tuz", "qty": "1", "unit_price": True}], "lines.0.unit_price"),
        ([{"name": "Tuz", "qty": "1"}], "lines.0.unit_price"),
        ([{"qty": "1", "unit_price": 5000}], "lines.0.name"),  # neither a catalog item nor a name
        ([{"name": None, "qty": "1", "unit_price": 5000}], "lines.0.name"),
        ([{"name": "", "qty": "1", "unit_price": 5000}], "lines.0.name"),
        ([{"name": "   ", "qty": "1", "unit_price": 5000}], "lines.0.name"),
        ([{"name": "x" * 81, "qty": "1", "unit_price": 5000}], "lines.0.name"),
        ([{"name": "ь", "qty": "1", "unit_price": 5000}], "lines.0.name"),
        ([{"name": 5, "qty": "1", "unit_price": 5000}], "lines.0.name"),
        ([{"name": "Tuz", "unit": "x" * 13, "qty": "1", "unit_price": 5000}], "lines.0.unit"),
        ([{"name": "Tuz", "unit": "12", "qty": "1", "unit_price": 5000}], "lines.0.unit"),
        ([{"catalog_item_id": NON, "name": "Non", "qty": "1", "unit_price": 5000}], "lines.0.name"),
        ([{"catalog_item_id": NON, "unit": "kg", "qty": "1", "unit_price": 5000}], "lines.0.unit"),
        ([{"catalog_item_id": "not-a-uuid", "qty": "1", "unit_price": 5000}], "lines.0.catalog_item_id"),
        ([{"name": "Tuz", "qty": "1", "unit_price": 5000, "line_total": 1}], "lines.0.line_total"),
        ([{"name": "Tuz", "qty": "1", "unit_price": 5000, "line_no": 3}], "lines.0.line_no"),
        ("Tuz", "lines"),
        ({"name": "Tuz", "qty": "1", "unit_price": 5000}, "lines"),
        (["Tuz"], "lines.0"),
    ],
)
def test_a_bad_line_is_refused_and_nothing_is_stored(
    client: TestClient, world: World, owner: psycopg.Connection, lines: Any, field: str
) -> None:
    """Neither the entry, nor a line, nor the good a correct line would have taught the catalog."""
    if isinstance(lines, list):
        lines = [
            {**line, "catalog_item_id": str(world.catalog_item_a)}
            if isinstance(line, dict) and line.get("catalog_item_id") == NON
            else line
            for line in lines
        ]
    before = stored(owner, world)
    as_sale = sell(client, world, world.customer_a, lines)
    assert refusal(as_sale) == (422, "VALIDATION"), as_sale.text
    assert set(as_sale.json()["error"]["fields"]) == {field}
    # The same lines are refused in the same way when added to an existing sale.
    as_addition = add(client, world, world.entry_a, lines)
    assert refusal(as_addition) == (422, "VALIDATION"), as_addition.text
    assert set(as_addition.json()["error"]["fields"]) == {field}
    assert stored(owner, world) == before
    assert catalog_row(owner, world.shop_a, "yangi mahsulot") is None


def test_every_bad_line_is_named_at_once(client: TestClient, world: World) -> None:
    lines = [typed("Tuz", "0", 5000), GOOD, {"qty": "1", "unit_price": 0}]
    response = sell(client, world, world.customer_a, lines)
    assert refusal(response) == (422, "VALIDATION")
    assert set(response.json()["error"]["fields"]) == {"lines.0.qty", "lines.2.name", "lines.2.unit_price"}


def test_a_hidden_item_or_another_shops_item_cannot_be_chosen(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    hidden, foreign = uuid.uuid4(), uuid.uuid4()
    owner.execute(
        "INSERT INTO catalog_item (id, shop_id, name, name_norm, unit, price, status) VALUES "
        "(%s, %s, 'Eski', 'eski', 'dona', 1000, 'hidden'), (%s, %s, 'Begona', 'begona', 'dona', 1000, 'active')",
        (hidden, world.shop_a, foreign, world.shop_b),
    )
    before = (stored(owner, world), count(owner, "goods_line", world.shop_b))
    answers = []
    for item in (hidden, foreign, uuid.uuid4()):
        response = sell(client, world, world.customer_a, [GOOD, chosen(item, "1", 5000)], amount=10000)
        assert refusal(response) == (422, "VALIDATION"), response.text
        assert set(response.json()["error"]["fields"]) == {"lines.1.catalog_item_id"}
        later = add(client, world, world.entry_a, [GOOD, chosen(item, "9", 5000)])
        assert refusal(later) == (422, "VALIDATION"), later.text
        answers.append((response.json(), later.json()))
    # Another shop's item is told apart from an unknown one by nothing at all.
    assert answers[1] == answers[2]
    assert (stored(owner, world), count(owner, "goods_line", world.shop_b)) == before
    assert catalog_row(owner, world.shop_a, "yangi mahsulot") is None


def test_lines_belong_to_a_credit_sale_only(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    before = stored(owner, world)
    response = client.post(
        f"{shop(world)}/customers/{world.customer_a}/entries",
        json={"kind": "payment", "amount": 5000, "lines": [GOOD]},
        headers={**as_user(world.seller_a), **key()},
    )
    assert refusal(response) == (422, "VALIDATION")
    assert set(response.json()["error"]["fields"]) == {"lines"}
    assert stored(owner, world) == before


def test_an_entry_needs_an_amount_or_lines(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    before = stored(owner, world)
    for body in ({"kind": "credit"}, {"kind": "credit", "lines": None}, {"kind": "payment"}):
        response = client.post(
            f"{shop(world)}/customers/{world.customer_a}/entries",
            json=body,
            headers={**as_user(world.seller_a), **key()},
        )
        assert refusal(response) == (422, "VALIDATION")
        assert set(response.json()["error"]["fields"]) == {"amount"}
    assert stored(owner, world) == before
    # Without lines everything is as before: the entry carries an empty list.
    plain = record(client, world, world.customer_a, "credit", 45000)
    assert plain.status_code == 201
    assert plain.json()["entry"]["lines"] == []
    assert record(client, world, world.customer_a, "payment", 1000).json()["entry"]["lines"] == []


def test_other_bad_fields_are_reported_beside_the_lines(client: TestClient, world: World) -> None:
    response = sell(client, world, world.customer_a, [typed("Tuz", "0", 5000)], note="x" * 201)
    assert set(response.json()["error"]["fields"]) == {"lines.0.qty", "note"}
    for amount in (50, "5000", 5000.0):  # out of bounds, or not a whole number although it equals the sum
        stated = sell(client, world, world.customer_a, [typed("Tuz", "1", 5000)], amount=amount)
        assert refusal(stated) == (422, "VALIDATION")
        assert set(stated.json()["error"]["fields"]) == {"amount"}


@pytest.mark.parametrize("amount", [11999, 12001, 4000, 100_000_000])
def test_a_stated_total_must_equal_the_sum_of_the_lines(
    client: TestClient, world: World, owner: psycopg.Connection, amount: int
) -> None:
    """INV-7: answered with its own code before anything is written, never by the trigger at commit."""
    before = stored(owner, world)
    lines = [typed("Yangi mahsulot", "2", 4000), chosen(world.catalog_item_a, "1", 4000)]
    response = sell(client, world, world.customer_a, lines, amount=amount)
    assert refusal(response) == (409, "LINES_SUM_MISMATCH"), response.text
    assert response.json()["error"]["fields"] == {"lines_sum": "12000", "amount": str(amount)}
    assert stored(owner, world) == before
    assert catalog_row(owner, world.shop_a, "yangi mahsulot") is None
    assert sell(client, world, world.customer_a, lines, amount=12000).status_code == 201


# --- lines added later (REQ-038, INV-8) ---------------------------------------------------------------


def test_the_author_adds_lines_once(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    before = stored(owner, world)
    started = owner.execute("SELECT clock_timestamp()").fetchone()
    assert started is not None
    lines = [typed("Guruch", "2", 15000, unit="kg"), chosen(world.catalog_item_a, "5", 4000)]
    response = add(client, world, world.entry_a, lines)
    assert response.status_code == 201, response.text
    guruch = catalog_row(owner, world.shop_a, "guruch")
    assert guruch is not None
    assert guruch[1:] == ("Guruch", "kg", 15000, True, "active")
    assert response.json() == {
        "entry": {
            "id": str(world.entry_a),
            "amount": 50000,
            "lines": [
                {
                    "line_no": 1,
                    "catalog_item_id": str(guruch[0]),
                    "name": "Guruch",
                    "qty": "2",
                    "unit": "kg",
                    "unit_price": 15000,
                    "line_total": 30000,
                },
                {
                    "line_no": 2,
                    "catalog_item_id": str(world.catalog_item_a),
                    "name": "Non",
                    "qty": "5",
                    "unit": "dona",
                    "unit_price": 4000,
                    "line_total": 20000,
                },
            ],
        }
    }
    # No entry and no promise; two lines, one learned good, two activity rows, one stored request.
    assert stored(owner, world) == tuple(a + b for a, b in zip(before, (0, 2, 0, 1, 2, 1), strict=True))
    logged = owner.execute(
        "SELECT actor_id, subject_type, subject_id FROM activity WHERE shop_id = %s AND action = %s",
        (world.shop_a, "ledger.lines_added"),
    ).fetchall()
    assert logged == [(world.seller_a_membership, "customer", world.customer_a)]
    # Measurement rows carry no shop identifier, so this test's row is found by its time.
    measured = owner.execute(
        "SELECT amount, promised FROM measure.event WHERE kind = 'lines_added' AND at >= %s", (started[0],)
    ).fetchall()
    assert measured == [(50000, None)]
    # The entry itself is untouched (REQ-011).
    assert owner.execute("SELECT amount, seq, kind FROM ledger_entry WHERE id = %s", (world.entry_a,)).fetchone() == (
        50000,
        1,
        "credit",
    )
    assert detail(client, world, world.customer_a)["balance"] == 50000

    after = stored(owner, world)
    for user in (world.seller_a, world.manager_a, world.owner_a):
        again = add(client, world, world.entry_a, [typed("Boshqa", "1", 50000)], user=user)
        assert refusal(again) == (409, "LINES_ALREADY_ADDED"), again.text
    assert stored(owner, world) == after
    assert catalog_row(owner, world.shop_a, "boshqa") is None
    assert len(lines_of(owner, world.entry_a)) == 2


def test_a_sale_recorded_with_lines_takes_no_more(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    entry = sell(client, world, world.customer_a, [chosen(world.catalog_item_a, "3", 4000)]).json()["entry"]["id"]
    before = stored(owner, world)
    again = add(client, world, entry, [chosen(world.catalog_item_a, "3", 4000)], user=world.manager_a)
    assert refusal(again) == (409, "LINES_ALREADY_ADDED")
    assert stored(owner, world) == before


@pytest.mark.parametrize("unit_price", [24999, 25001])
def test_added_lines_must_sum_to_the_recorded_total(
    client: TestClient, world: World, owner: psycopg.Connection, unit_price: int
) -> None:
    before = stored(owner, world)
    response = add(client, world, world.entry_a, [typed("Yangi mahsulot", "2", unit_price)])
    assert refusal(response) == (409, "LINES_SUM_MISMATCH"), response.text
    assert response.json()["error"]["fields"] == {"lines_sum": str(2 * unit_price), "amount": "50000"}
    assert stored(owner, world) == before
    assert catalog_row(owner, world.shop_a, "yangi mahsulot") is None
    # The refusal used nothing up: the right lines still go in.
    assert add(client, world, world.entry_a, [typed("Yangi mahsulot", "2", 25000)]).status_code == 201


def test_lines_can_be_added_until_the_end_of_the_day_after_the_sale(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    midnight = datetime.combine(tashkent_date(datetime.now(UTC)), time.min, tzinfo=TASHKENT)
    customer = seed_customer(owner, world.shop_a, "Kechagi")
    # Sold in the first second of yesterday: today is still "the day after the sale".
    in_time = seed_sale(owner, world, customer, 1, 8000, midnight - timedelta(days=1))
    # Sold one second earlier, on the day before yesterday: the window closed when today began.
    too_late = seed_sale(owner, world, customer, 2, 8000, midnight - timedelta(days=1, seconds=1))
    before = stored(owner, world)
    for user in (world.seller_a, world.manager_a, world.owner_a):
        late = add(client, world, too_late, [typed("Kechikkan", "1", 8000)], user=user)
        assert refusal(late) == (409, "LINES_WINDOW_CLOSED"), late.text
    assert stored(owner, world) == before
    assert catalog_row(owner, world.shop_a, "kechikkan") is None
    assert add(client, world, in_time, [typed("Ulgurgan", "1", 8000)]).status_code == 201
    assert lines_of(owner, too_late) == []


@contextmanager
def clocked_client(app_database_url: str, clock: MovableClock) -> Iterator[TestClient]:
    """The application with a clock the test can move."""
    database = Database(app_database_url)
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        authenticator=HeaderAuthenticator(),
        now=clock.now,
    )
    with TestClient(app) as test_client:
        yield test_client
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]


def test_the_window_is_judged_by_the_service_clock(
    world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    """The application refuses by its own clock; the database trigger, which reads its own, is the backstop."""
    clock = MovableClock()
    with clocked_client(app_database_url, clock) as client:
        first = record(client, world, world.settled_customer_a, "credit", 8000).json()["entry"]
        second = record(client, world, world.settled_customer_a, "credit", 8000).json()["entry"]
        sold_on = tashkent_date(datetime.fromisoformat(first["created_at"]))
        closes_at = datetime.combine(sold_on + timedelta(days=2), time.min, tzinfo=TASHKENT)

        # Half a minute before the end of the next day.
        clock.offset = closes_at - datetime.now(UTC) - timedelta(seconds=30)
        assert add(client, world, first["id"], [typed("Ulgurgan", "1", 8000)]).status_code == 201

        before = stored(owner, world)
        clock.offset = closes_at - datetime.now(UTC)
        late = add(client, world, second["id"], [typed("Kechikkan", "1", 8000)])
        assert refusal(late) == (409, "LINES_WINDOW_CLOSED"), late.text
        assert stored(owner, world) == before
        assert lines_of(owner, second["id"]) == []


def test_a_reversed_sale_takes_no_lines(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    sale = record(client, world, world.settled_customer_a, "credit", 20000).json()["entry"]["id"]
    reversal = reverse(client, world, sale).json()["entry"]["id"]
    before = stored(owner, world)
    refused = add(client, world, sale, [typed("Yangi mahsulot", "1", 20000)], user=world.manager_a)
    assert refusal(refused) == (409, "ALREADY_REVERSED"), refused.text
    on_reversal = add(client, world, reversal, [typed("Yangi mahsulot", "1", 20000)], user=world.manager_a)
    assert refusal(on_reversal) == (422, "VALIDATION"), on_reversal.text
    assert set(on_reversal.json()["error"]["fields"]) == {"entry"}
    assert stored(owner, world) == before
    assert catalog_row(owner, world.shop_a, "yangi mahsulot") is None


@pytest.mark.parametrize("kind", ["payment", "opening"])
def test_only_a_credit_sale_takes_lines(client: TestClient, world: World, owner: psycopg.Connection, kind: str) -> None:
    entry = seed_sale(owner, world, world.customer_a, 2, 5000, datetime.now(UTC), kind=kind)
    before = stored(owner, world)
    refused = add(client, world, entry, [typed("Yangi mahsulot", "1", 5000)])
    assert refusal(refused) == (422, "VALIDATION"), refused.text
    assert set(refused.json()["error"]["fields"]) == {"entry"}
    assert stored(owner, world) == before
    assert catalog_row(owner, world.shop_a, "yangi mahsulot") is None


def test_another_seller_is_refused_and_the_author_a_manager_and_the_owner_are_not(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    other = another_seller(owner, world)
    entries = [record(client, world, world.settled_customer_a, "credit", 20000).json()["entry"]["id"] for _ in range(3)]
    before = stored(owner, world)
    for entry in entries:
        refused = add(client, world, entry, [typed("Yangi mahsulot", "1", 20000)], user=other)
        assert refusal(refused) == (403, "FORBIDDEN_ROLE"), refused.text
        assert refused.json()["error"]["fields"] == {"needed_role": "manager"}
    assert stored(owner, world) == before
    assert catalog_row(owner, world.shop_a, "yangi mahsulot") is None

    for entry, user in zip(entries, (world.seller_a, world.manager_a, world.owner_a), strict=True):
        assert add(client, world, entry, [typed("Yangi mahsulot", "1", 20000)], user=user).status_code == 201

    # The other seller's own sale is theirs to complete.
    own = client.post(
        f"{shop(world)}/customers/{world.settled_customer_a}/entries",
        json={"kind": "credit", "amount": 7000},
        headers={**as_user(other), **key()},
    ).json()["entry"]["id"]
    assert add(client, world, own, [typed("Yangi mahsulot", "1", 7000)], user=other).status_code == 201


def test_an_entry_of_another_shop_or_no_entry_is_not_found(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    membership_b = owner.execute("SELECT id FROM membership WHERE shop_id = %s", (world.shop_b,)).fetchone()
    assert membership_b is not None
    customer_b = seed_customer(owner, world.shop_b, "Begona")
    entry_b = seed_sale(
        owner, world, customer_b, 1, 20000, datetime.now(UTC), shop_id=world.shop_b, author=membership_b[0]
    )
    before = (
        stored(owner, world),
        count(owner, "goods_line", world.shop_b),
        count(owner, "catalog_item", world.shop_b),
    )
    answers = []
    for entry in (entry_b, uuid.uuid4()):
        response = add(client, world, entry, [typed("Yangi mahsulot", "1", 20000)], user=world.owner_a)
        assert refusal(response) == (404, "NOT_FOUND"), response.text
        answers.append(response.json())
    assert answers[0] == answers[1]
    malformed = add(client, world, "not-a-uuid", [typed("Yangi mahsulot", "1", 20000)], user=world.owner_a)
    assert malformed.status_code == 404
    after = (stored(owner, world), count(owner, "goods_line", world.shop_b), count(owner, "catalog_item", world.shop_b))
    assert after == before
    assert catalog_row(owner, world.shop_a, "yangi mahsulot") is None


def test_an_outsider_learns_nothing_from_bad_lines(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    """Membership is checked before the lines are: a bad body still looks like a missing shop."""
    before = stored(owner, world)
    bad = [typed("Tuz", "0", 5000)]
    for user in (world.owner_b, world.stranger, world.suspended_a):
        assert refusal(add(client, world, world.entry_a, bad, user=user)) == (404, "NOT_FOUND")
        assert refusal(sell(client, world, world.customer_a, bad, user=user)) == (404, "NOT_FOUND")
    # A member without a request key hears about the key first.
    for response in (
        client.post(
            f"{shop(world)}/entries/{world.entry_a}/lines", json={"lines": bad}, headers=as_user(world.seller_a)
        ),
        client.post(
            f"{shop(world)}/customers/{world.customer_a}/entries",
            json={"kind": "credit", "lines": bad},
            headers=as_user(world.seller_a),
        ),
    ):
        assert refusal(response) == (422, "VALIDATION")
        assert set(response.json()["error"]["fields"]) == {"Idempotency-Key"}
    assert stored(owner, world) == before


# --- repeats (ADR-006) --------------------------------------------------------------------------------


def test_a_repeated_itemized_sale_writes_one_entry_and_one_batch(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    headers = key()
    lines = [typed("Yangi mahsulot", "2", 4000), chosen(world.catalog_item_a, "1", 4000)]
    before = stored(owner, world)
    first = sell(client, world, world.customer_a, lines, headers=headers)
    second = sell(client, world, world.customer_a, lines, headers=headers)
    # The same quantity written another way is the same request.
    third = sell(client, world, world.customer_a, [{**lines[0], "qty": "2.0"}, lines[1]], headers=headers)
    assert first.status_code == second.status_code == third.status_code == 201
    assert first.json() == second.json() == third.json()
    after = stored(owner, world)
    assert after == tuple(a + b for a, b in zip(before, (1, 2, 1, 1, 2, 1), strict=True))

    for changed in (
        [typed("Yangi mahsulot", "3", 4000), lines[1]],
        [lines[1], lines[0]],
        [typed("Yangi mahsulot", "2", 4000, unit="kg"), lines[1]],
        [lines[0], chosen(world.learned_item_a, "1", 4000)],
        [lines[0], chosen(world.catalog_item_a, "1", 4001)],
        [lines[0]],
    ):
        response = sell(client, world, world.customer_a, changed, headers=headers)
        assert refusal(response) == (409, "IDEMPOTENCY_KEY_REUSED"), response.text
    stated = sell(client, world, world.customer_a, lines, headers=headers, amount=12000)
    assert refusal(stated) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert stored(owner, world) == after


def test_a_repeated_addition_writes_one_batch(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    headers = key()
    lines = [typed("Yangi mahsulot", "2", 25000)]
    before = stored(owner, world)
    first = add(client, world, world.entry_a, lines, headers=headers)
    second = add(client, world, world.entry_a, lines, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    after = stored(owner, world)
    assert after == tuple(a + b for a, b in zip(before, (0, 1, 0, 1, 2, 1), strict=True))

    changed = add(client, world, world.entry_a, [typed("Yangi mahsulot", "1", 50000)], headers=headers)
    assert refusal(changed) == (409, "IDEMPOTENCY_KEY_REUSED")
    other_entry = record(client, world, world.customer_a, "credit", 50000).json()["entry"]["id"]
    elsewhere = add(client, world, other_entry, lines, headers=headers)
    assert refusal(elsewhere) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert lines_of(owner, other_entry) == []
    assert len(lines_of(owner, world.entry_a)) == 1


# --- at the same time ---------------------------------------------------------------------------------


def test_two_additions_at_once_leave_one_batch(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    with another_client(app_database_url) as second:
        for attempt in range(5):
            entry = record(client, world, world.settled_customer_a, "credit", 10_000).json()["entry"]["id"]
            barrier = threading.Barrier(2)

            good = typed(f"Poyga {attempt}", "1", 10_000)

            def complete(
                pair: tuple[TestClient, uuid.UUID],
                entry: str = entry,
                good: dict[str, Any] = good,
                barrier: threading.Barrier = barrier,
            ) -> int:
                which, user = pair
                barrier.wait(timeout=10)
                return int(add(which, world, entry, [good], user=user).status_code)

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = sorted(pool.map(complete, ((client, world.seller_a), (second, world.manager_a))))
            assert statuses == [201, 409]
            assert len(lines_of(owner, entry)) == 1


def test_two_sales_typing_the_same_new_goods_at_once_both_succeed(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    """Each sale teaches the catalog two goods the other also types, in the opposite order."""
    items = count(owner, "catalog_item", world.shop_a)
    with another_client(app_database_url) as second:
        for attempt in range(8):
            one, two = f"Anor {attempt}", f"Behi {attempt}"
            barrier = threading.Barrier(2)

            def trade(job: tuple[TestClient, uuid.UUID, list[str]], barrier: threading.Barrier = barrier) -> int:
                which, customer, goods = job
                barrier.wait(timeout=10)
                return int(sell(which, world, customer, [typed(name, "1", 5000) for name in goods]).status_code)

            jobs = ((client, world.customer_a, [one, two]), (second, world.settled_customer_a, [two, one]))
            with ThreadPoolExecutor(max_workers=2) as pool:
                assert list(pool.map(trade, jobs)) == [201, 201]
    assert count(owner, "catalog_item", world.shop_a) == items + 16


# --- subscription state (BR-29, BR-30) ----------------------------------------------------------------


def test_a_limited_shop_takes_no_itemized_sale_but_still_completes_an_old_one(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE subscription SET state = 'limited' WHERE shop_id = %s", (world.shop_a,))
    before = stored(owner, world)
    refused = sell(client, world, world.customer_a, [typed("Yangi mahsulot", "1", 5000)])
    assert refusal(refused) == (402, "SUBSCRIPTION_LIMITED")
    assert stored(owner, world) == before
    assert catalog_row(owner, world.shop_a, "yangi mahsulot") is None
    assert add(client, world, world.entry_a, [typed("Yangi mahsulot", "2", 25000)]).status_code == 201


def test_a_suspended_shop_takes_no_lines(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    owner.execute("UPDATE subscription SET state = 'suspended' WHERE shop_id = %s", (world.shop_a,))
    before = stored(owner, world)
    for response in (
        sell(client, world, world.customer_a, [typed("Yangi mahsulot", "1", 5000)], user=world.owner_a),
        add(client, world, world.entry_a, [typed("Yangi mahsulot", "2", 25000)], user=world.owner_a),
    ):
        assert refusal(response) == (403, "SHOP_SUSPENDED")
    assert stored(owner, world) == before
    assert catalog_row(owner, world.shop_a, "yangi mahsulot") is None


# --- reading (customer detail) and INV-17 -------------------------------------------------------------


def test_the_customer_detail_shows_the_lines_of_each_entry(client: TestClient, world: World) -> None:
    itemized = sell(
        client, world, world.customer_a, [chosen(world.catalog_item_a, "2", 4000), typed("Choy", "0.250", 60000, "kg")]
    ).json()["entry"]
    assert record(client, world, world.customer_a, "payment", 1000).status_code == 201
    entries = detail(client, world, world.customer_a)["entries"]
    assert [(entry["kind"], len(entry["lines"])) for entry in entries] == [("payment", 0), ("credit", 2), ("credit", 0)]
    assert entries[1]["id"] == itemized["id"]
    assert entries[1]["lines"] == itemized["lines"]
    assert [(line["line_no"], line["name"], line["qty"], line["line_total"]) for line in entries[1]["lines"]] == [
        (1, "Non", "2", 8000),
        (2, "Choy", "0.25", 15000),
    ]
    assert entries[1]["amount"] == 23000

    assert add(client, world, world.entry_a, [typed("Guruch", "2", 25000)]).status_code == 201
    later = detail(client, world, world.customer_a)["entries"]
    assert [line["name"] for line in later[2]["lines"]] == ["Guruch"]
    assert later[1]["lines"] == itemized["lines"]


def test_lines_are_shown_in_line_order_however_they_were_stored(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    entry = seed_sale(owner, world, world.settled_customer_a, 1, 9000, datetime.now(UTC))
    with owner.transaction():  # one transaction: one batch, and the sum is checked at its end
        for line_no, name, total in ((3, "Uchinchi", 3000), (1, "Birinchi", 1000), (2, "Ikkinchi", 5000)):
            owner.execute(
                "INSERT INTO goods_line (id, shop_id, entry_id, line_no, name, qty, unit, unit_price, line_total) "
                "VALUES (%s, %s, %s, %s, %s, 1, 'dona', %s, %s)",
                (uuid.uuid4(), world.shop_a, entry, line_no, name, total, total),
            )
    shown = detail(client, world, world.settled_customer_a)["entries"][0]["lines"]
    assert [(line["line_no"], line["name"], line["catalog_item_id"]) for line in shown] == [
        (1, "Birinchi", None),
        (2, "Ikkinchi", None),
        (3, "Uchinchi", None),
    ]


def test_a_later_catalog_change_never_alters_a_saved_line(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """REQ-007, INV-17: the sale keeps the name, the unit and the price it was made at."""
    sold = sell(
        client, world, world.customer_a, [chosen(world.catalog_item_a, "2", 4000), typed("Choy", "1", 60000, "kg")]
    ).json()["entry"]
    choy = sold["lines"][1]["catalog_item_id"]
    saved = lines_of(owner, sold["id"])
    for item, change in (
        (world.catalog_item_a, {"name": "Buxanka non", "unit": "kg", "price": 9999}),
        (choy, {"name": "Ko'k choy", "price": 75000}),
    ):
        patched = client.patch(
            f"{shop(world)}/catalog/{item}", json=change, headers={**as_user(world.manager_a), **key()}
        )
        assert patched.status_code == 200, patched.text
    hidden = client.post(f"{shop(world)}/catalog/{choy}/hide", headers={**as_user(world.manager_a), **key()})
    assert hidden.status_code == 200, hidden.text

    assert lines_of(owner, sold["id"]) == saved
    shown = next(entry for entry in detail(client, world, world.customer_a)["entries"] if entry["id"] == sold["id"])
    assert shown["lines"] == sold["lines"]
    assert (shown["amount"], detail(client, world, world.customer_a)["balance"]) == (68000, 118000)
    # A new sale takes the new name; the price is whatever the seller sends for that line.
    fresh = sell(client, world, world.customer_a, [chosen(world.catalog_item_a, "1", 9999)]).json()["entry"]["lines"]
    assert (fresh[0]["name"], fresh[0]["unit"]) == ("Buxanka non", "kg")
