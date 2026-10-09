"""What the database itself holds a sale for cash to (migration 0049), without the application in between.

A sale is a stock document of a sixth kind: paid in full, in so'm, with the way it was paid. Its money in
the cash book is income of exactly its total; every other document's is an expense, as it was. And the
lists that the feature reads, or that it could have slowed down, go through indexes of their own.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from psycopg import errors

from qarz.infrastructure import db_stock

from ..conftest import AppSession, Shop
from .test_cash_book_schema import category
from .test_stock_schema import _measured, _plan, document, item, move

pytestmark = pytest.mark.db


def sale(owner: psycopg.Connection, shop: Shop, total: int = 30_000, **columns: Any) -> uuid.UUID:
    return document(owner, shop, "sale", **{"total": total, "paid": total, "method": "cash", **columns})


def cash(
    conn: psycopg.Connection,
    shop: Shop,
    category_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    direction: str = "income",
    amount: int = 30_000,
    method: str = "cash",
    currency: str = "UZS",
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO cash_entry (id, shop_id, direction, method, currency, amount, category_id, day, author_id, "
        "stock_document_id) VALUES (%s, %s, %s, %s, %s, %s, %s, current_date, %s, %s)",
        (entry_id, shop.shop_id, direction, method, currency, amount, category_id, shop.member_id, document_id),
    )
    return entry_id


# --- the document ----------------------------------------------------------------------------------------------


def test_a_sale_is_a_document_paid_in_full_in_som_with_its_method(owner: psycopg.Connection, shop_a: Shop) -> None:
    stored = sale(owner, shop_a, method="card")
    assert owner.execute(
        "SELECT kind, total, paid, currency, method FROM stock_document WHERE id = %s", (stored,)
    ).fetchone() == (
        "sale",
        30_000,
        30_000,
        "UZS",
        "card",
    )


@pytest.mark.parametrize(
    ("kind", "columns"),
    [
        ("sale", {"method": None}),  # a sale says how it was paid
        ("sale", {"method": "barter"}),
        ("sale", {"paid": 10_000}),  # not paid in full
        ("sale", {"total": 0, "paid": 0}),  # worth nothing
        ("sale", {"currency": "USD"}),  # selling prices are so'm
        ("sale", {"customer_id": "a customer"}),  # it names nobody
        ("sale", {"reason": "lost"}),
        ("receipt", {"method": "cash"}),  # only a sale has a method
        ("write_off", {"method": "cash"}),
        ("sold", {}),  # not a kind
    ],
)
def test_a_sale_that_contradicts_itself_is_refused(
    owner: psycopg.Connection, shop_a: Shop, kind: str, columns: dict[str, Any]
) -> None:
    if columns.get("customer_id"):
        columns["customer_id"] = shop_a.customer_id
    with pytest.raises(errors.CheckViolation):
        if kind == "sale":
            sale(owner, shop_a, **columns)
        else:
            document(owner, shop_a, kind, **columns)


def test_the_application_cannot_change_how_a_sale_was_paid_or_what_kind_a_document_is(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    stored = sale(owner, shop_a)
    for change in ("method = 'card'", "kind = 'receipt'", "created_by = gen_random_uuid()"):
        with pytest.raises(errors.InsufficientPrivilege, match="permission denied"), as_app(shop_a.shop_id) as app:
            app.execute(f"UPDATE stock_document SET {change} WHERE id = %s", (stored,))
    assert owner.execute("SELECT method FROM stock_document WHERE id = %s", (stored,)).fetchone() == ("cash",)


def test_a_sale_is_seen_by_its_own_shop_alone(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    stored = sale(owner, shop_a)
    with as_app(shop_b.shop_id) as app:
        assert app.execute("SELECT count(*) FROM stock_document WHERE id = %s", (stored,)).fetchone() == (0,)
    with as_app(shop_a.shop_id) as app:
        assert app.execute("SELECT count(*) FROM stock_document WHERE id = %s", (stored,)).fetchone() == (1,)


# --- the money -------------------------------------------------------------------------------------------------


def test_the_cash_entry_of_a_sale_is_income_of_its_whole_total_once(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    takings = category(owner, shop_a, "income", "Ombor: naqd savdo", "cash_sale")
    stored = sale(owner, shop_a, method="card")
    with as_app(shop_a.shop_id) as app:
        cash(app, shop_a, takings, stored, method="card")
    with (
        pytest.raises(errors.UniqueViolation, match="cash_entry_one_per_stock_document"),
        as_app(shop_a.shop_id) as app,
    ):
        cash(app, shop_a, takings, stored, method="card")
    assert owner.execute("SELECT count(*) FROM cash_entry WHERE stock_document_id = %s", (stored,)).fetchone() == (1,)


@pytest.mark.parametrize(
    "wrong",
    [
        {"amount": 29_999},  # not the whole total
        {"amount": 30_001},
        {"method": "card"},  # not the way the sale was paid
        {"direction": "expense"},  # a sale brings money in
    ],
)
def test_a_cash_entry_that_does_not_repeat_its_sale_is_refused(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, wrong: dict[str, Any]
) -> None:
    direction = wrong.get("direction", "income")
    under = category(owner, shop_a, direction, "Toifa")
    stored = sale(owner, shop_a)
    with pytest.raises(errors.CheckViolation, match="cash_entry_matches_stock_document"), as_app(shop_a.shop_id) as app:
        cash(app, shop_a, under, stored, **wrong)
    assert owner.execute("SELECT count(*) FROM cash_entry WHERE shop_id = %s", (shop_a.shop_id,)).fetchone() == (0,)


def test_the_cash_entry_of_any_other_document_stays_an_expense_and_a_suppliers_payment_too(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    spent = category(owner, shop_a, "expense", "Ombor: tovar xaridi", "goods_purchase")
    taken = category(owner, shop_a, "income", "Savdo")
    receipt = document(owner, shop_a, "receipt", total=50_000, paid=20_000)
    with pytest.raises(errors.CheckViolation, match="cash_entry_matches_stock_document"), as_app(shop_a.shop_id) as app:
        cash(app, shop_a, taken, receipt, direction="income", amount=20_000)
    with as_app(shop_a.shop_id) as app:
        cash(app, shop_a, spent, receipt, direction="expense", amount=20_000)
    # Another shop's sale is no sale of this shop: the entry would be of nothing.
    theirs = sale(owner, shop_b)
    with pytest.raises((errors.CheckViolation, errors.ForeignKeyViolation)), as_app(shop_a.shop_id) as app:
        cash(app, shop_a, taken, theirs)
    supplier_payment = owner.execute(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'cash_entry'::regclass AND conname LIKE 'cash_entry_%is_expense'"
    ).fetchall()
    assert supplier_payment == [("cash_entry_supplier_is_expense",)]


# --- reading: through indexes ------------------------------------------------------------------------------------

_DAY = {"since": datetime(2026, 10, 9, tzinfo=UTC), "until": datetime(2026, 10, 10, tzinfo=UTC)}
_STATEMENTS: dict[str, tuple[str, dict[str, Any]]] = {
    "the day's sales": (
        db_stock._SALES_PAGE,
        {**_DAY, "seller": None, "item": None, "status": None, "before_at": None, "before_id": None, "limit": 51},
    ),
    "a seller's sales of an item": (
        db_stock._SALES_PAGE,
        {
            **_DAY,
            "seller": uuid.uuid4(),
            "item": uuid.uuid4(),
            "status": "posted",
            "before_at": datetime(2026, 10, 9, 12, tzinfo=UTC),
            "before_id": uuid.uuid4(),
            "limit": 51,
        },
    ),
    "what the day's sales came to": (db_stock._SALES_TOTALS, {**_DAY, "seller": None, "item": None}),
    "what the goods of a sale cost": (db_stock._SALE_COSTS, {"document": uuid.uuid4()}),
    "what sold since": (db_stock._SOLD, {"since": datetime(2026, 9, 9, tzinfo=UTC), "limit": 51}),
    "the sales for the export": (
        db_stock._EXPORT_SALE_LINES,
        {
            "until": datetime(2026, 10, 10, tzinfo=UTC),
            "after_at": None,
            "after_id": None,
            "after_line": None,
            "limit": 1000,
        },
    ),
}
_INDEX_OF = {
    "the day's sales": ("stock_document_by_kind",),
    "a seller's sales of an item": ("stock_document_by_kind",),
    "what the day's sales came to": ("stock_document_by_kind",),
    "what the goods of a sale cost": ("stock_movement_document",),
    "what sold since": ("stock_movement_sold",),
    "the sales for the export": ("stock_document_by_kind",),
}


@pytest.mark.parametrize("what", sorted(_STATEMENTS))
def test_a_read_of_the_sales_goes_through_its_index(as_app: AppSession, shop_a: Shop, what: str) -> None:
    assert set(_INDEX_OF) == set(_STATEMENTS)
    statement, values = _STATEMENTS[what]
    with as_app(shop_a.shop_id) as app:
        plan = _plan(app, statement, {"shop_id": shop_a.shop_id, **values})
    assert "Seq Scan" not in plan, plan
    assert any(index in plan for index in _INDEX_OF[what]), plan


def _many_sales_around_three_documents(owner: psycopg.Connection, shop: Shop, sales: int = 400) -> None:
    """A shop that sold for cash four hundred times since its last three documents were written."""
    owner.execute(
        "INSERT INTO stock_document (id, shop_id, kind, number, status, doc_date, total, paid, method, created_by, "
        "  created_at, posted_by, posted_at) "
        "SELECT gen_random_uuid(), %(shop)s, CASE WHEN n <= 3 THEN 'stocktake' ELSE 'sale' END, n, 'posted', "
        "  current_date, CASE WHEN n <= 3 THEN 0 ELSE 1000 END, CASE WHEN n <= 3 THEN 0 ELSE 1000 END, "
        "  CASE WHEN n > 3 THEN 'cash' END, %(member)s, now() - interval '1 day' + n * interval '1 second', "
        "  %(member)s, now() FROM generate_series(1, %(count)s) n",
        {"shop": shop.shop_id, "member": shop.member_id, "count": sales + 3},
    )


_PAPERS = "SELECT d.id FROM stock_document d WHERE d.kind <> 'sale' ORDER BY d.created_at DESC, d.id DESC LIMIT :limit"
_POSTED_PAPERS = (
    "SELECT d.id FROM stock_document d WHERE d.kind <> 'sale' AND d.status = :status "
    "ORDER BY d.created_at DESC, d.id DESC LIMIT :limit"
)


@pytest.mark.parametrize(
    ("statement", "values", "index"),
    [
        (_PAPERS, {"limit": 51}, "stock_document_recent"),
        (_POSTED_PAPERS, {"status": "posted", "limit": 51}, "stock_document_by_status"),
    ],
)
def test_the_lists_of_documents_do_not_walk_the_sales(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, statement: str, values: dict[str, Any], index: str
) -> None:
    """Three documents under four hundred newer sales are read from an index that holds no sale: three
    rows are read and none is thrown away."""
    _many_sales_around_three_documents(owner, shop_a)
    with as_app(shop_a.shop_id) as app:
        plan = _measured(app, statement, values)
    assert index in plan, plan
    assert "Rows Removed by Filter" not in plan and "rows=3 " in plan, plan


def test_an_index_that_held_the_sales_would_make_the_list_walk_them(
    owner: psycopg.Connection, database_url: str, shop_a: Shop
) -> None:
    """The counterpart, and what the list would do had migration 0049 left the index as it was: rebuilt
    over every kind (in a transaction that is rolled back), the same read throws the four hundred sales away."""
    _many_sales_around_three_documents(owner, shop_a)
    with psycopg.connect(database_url) as conn:
        try:
            conn.execute("DROP INDEX stock_document_recent")
            conn.execute("DROP INDEX stock_document_by_status")
            conn.execute("DROP INDEX stock_document_by_kind")
            conn.execute("CREATE INDEX stock_document_recent ON stock_document (shop_id, created_at DESC, id DESC)")
            conn.execute("SET LOCAL ROLE qd_app")
            conn.execute("SELECT set_config('qd.shop_id', %s, true)", (str(shop_a.shop_id),))
            plan = _measured(conn, _PAPERS, {"limit": 51})
        finally:
            conn.rollback()
    assert "Rows Removed by Filter: 400" in plan, plan
    kept = owner.execute(
        "SELECT indexdef FROM pg_indexes WHERE indexname IN ('stock_document_recent', 'stock_document_by_status')"
    ).fetchall()
    assert len(kept) == 2 and all("kind <> 'sale'" in row[0] for row in kept), kept


def test_what_sold_is_added_up_from_the_sales_that_stand(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    """Credit and cash sales of one item together, a reversed sale left out, a receipt never counted."""
    sugar = item(owner, shop_a)
    stored = sale(owner, shop_a, total=24_000)
    move(owner, shop_a, sugar, 1, "10", 100_000, "10", 100_000)
    move(owner, shop_a, sugar, 2, "-2", -20_000, "8", 80_000, kind="sale", sale_total=24_000, cost_total=20_000)
    owner.execute("UPDATE stock_movement SET document_id = %s WHERE item_id = %s AND item_seq = 2", (stored, sugar))
    move(owner, shop_a, sugar, 3, "-1", -10_000, "7", 70_000, kind="sale", sale_total=13_000, cost_total=10_000)
    gone = move(owner, shop_a, sugar, 4, "-3", -30_000, "4", 40_000, kind="sale", sale_total=36_000, cost_total=30_000)
    move(owner, shop_a, sugar, 5, "3", 30_000, "7", 70_000, kind="reversal", reverses=gone)
    values = {"shop_id": shop_a.shop_id, "since": datetime.now(UTC) - timedelta(days=1), "limit": 10}
    with as_app(shop_a.shop_id) as app:
        rows = app.execute(_as_psycopg(db_stock._SOLD), values).fetchall()
    assert [tuple(row)[3:] for row in rows] == [(3, 37_000, 37_000, 30_000, 2, 24_000)]


def _as_psycopg(statement: str) -> str:
    import re

    return re.sub(r"(?<!:):([a-z_]+)", r"%(\1)s", statement)
