"""What the database itself holds the stock and the suppliers to (migration 0043), without the
application in between: the two ledgers are insert-only, the kept figures follow their ledgers and cannot
be written, a document moves one way, and the lists are read through indexes.
"""

import re
import uuid
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg import errors

from qarz.domain import stock
from qarz.infrastructure import db_stock

from ..conftest import AppSession, Shop, add_entry

pytestmark = pytest.mark.db


def item(owner: psycopg.Connection, shop: Shop, name: str = "Shakar", *, tracked: bool = True) -> uuid.UUID:
    item_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO catalog_item (id, shop_id, name, name_norm, unit, price, tracked) "
        "VALUES (%s, %s, %s, %s, 'kg', 12000, %s)",
        (item_id, shop.shop_id, name, name.lower(), tracked),
    )
    return item_id


def move(
    conn: psycopg.Connection,
    shop: Shop,
    item_id: uuid.UUID,
    seq: int,
    qty: str,
    value_delta: int,
    on_hand_after: str,
    value_after: int,
    *,
    kind: str = "receipt",
    currency: str | None = "UZS",
    reverses: uuid.UUID | None = None,
    entry: uuid.UUID | None = None,
    sale_total: int | None = None,
    cost_total: int | None = None,
) -> uuid.UUID:
    movement = uuid.uuid4()
    conn.execute(
        "INSERT INTO stock_movement (id, shop_id, item_id, item_seq, kind, qty, value_delta, currency, on_hand_after, "
        "  value_after, reverses_id, ledger_entry_id, sale_total, cost_total, author_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            movement,
            shop.shop_id,
            item_id,
            seq,
            kind,
            Decimal(qty),
            value_delta,
            currency,
            Decimal(on_hand_after),
            value_after,
            reverses,
            entry,
            sale_total,
            cost_total,
            shop.member_id,
        ),
    )
    return movement


def level(owner: psycopg.Connection, item_id: uuid.UUID) -> tuple[Any, ...] | None:
    return owner.execute(
        "SELECT on_hand, cost_value, cost_currency, last_cost, last_seq FROM stock_level WHERE item_id = %s", (item_id,)
    ).fetchone()


def supplier(owner: psycopg.Connection, shop: Shop, name: str = "Ulgurji") -> uuid.UUID:
    supplier_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO supplier (id, shop_id, name, name_norm) VALUES (%s, %s, %s, %s)",
        (supplier_id, shop.shop_id, name, name.lower()),
    )
    return supplier_id


def supplier_entry(
    conn: psycopg.Connection,
    shop: Shop,
    supplier_id: uuid.UUID,
    seq: int,
    kind: str,
    amount: int,
    *,
    currency: str = "UZS",
    reverses: uuid.UUID | None = None,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO supplier_entry (id, shop_id, supplier_id, seq, kind, amount, currency, reverses_id, author_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (entry_id, shop.shop_id, supplier_id, seq, kind, amount, currency, reverses, shop.member_id),
    )
    return entry_id


def balances(owner: psycopg.Connection, supplier_id: uuid.UUID) -> dict[str, int]:
    rows = owner.execute("SELECT currency, balance FROM supplier_balance WHERE supplier_id = %s", (supplier_id,))
    return {str(currency): int(balance) for currency, balance in rows.fetchall()}


def document(owner: psycopg.Connection, shop: Shop, kind: str = "write_off", **columns: Any) -> uuid.UUID:
    document_id = uuid.uuid4()
    values = {
        "id": document_id,
        "shop_id": shop.shop_id,
        "kind": kind,
        "number": int(uuid.uuid4().int % 10**8) + 1,
        "doc_date": "2026-10-09",
        "draft": "{}",
        "created_by": shop.member_id,
        **({"reason": "lost"} if kind == "write_off" else {}),
        **columns,
    }
    names = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    owner.execute(f"INSERT INTO stock_document ({names}) VALUES ({marks})", tuple(values.values()))
    return document_id


# --- the catalogue item -----------------------------------------------------------------------------------


def test_the_units_of_a_counted_item_are_the_domains_list(owner: psycopg.Connection) -> None:
    definition = owner.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'catalog_item_tracked_unit'"
    ).fetchone()
    assert definition is not None
    assert set(re.findall(r"'([a-z0-9]+)'::text", definition[0])) == stock.UNIT_KEYS
    for name, wanted in (("stock_movement", stock.MOVEMENT_KINDS), ("stock_document", stock.DOCUMENT_KINDS)):
        row = owner.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s", (f"{name}_kind_check",)
        ).fetchone()
        assert row is not None and set(re.findall(r"'([a-z_]+)'::text", row[0])) == set(wanted), name
    reasons = owner.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'stock_document_reason_check'"
    ).fetchone()
    assert reasons is not None and set(re.findall(r"'([a-z_]+)'::text", reasons[0])) == stock.WRITE_OFF_KEYS


def test_every_item_that_existed_is_not_counted(owner: psycopg.Connection, shop_a: Shop) -> None:
    """The columns are added with defaults that change nothing for a shop that only uses the catalogue."""
    plain = uuid.uuid4()
    owner.execute(
        "INSERT INTO catalog_item (id, shop_id, name, name_norm, unit, price) "
        "VALUES (%s, %s, 'Ko''kat', 'kokat', 'bog''', 2000)",
        (plain, shop_a.shop_id),
    )
    assert owner.execute("SELECT tracked, low_stock FROM catalog_item WHERE id = %s", (plain,)).fetchone() == (
        False,
        None,
    )
    for change, constraint in (
        ("tracked = true", "catalog_item_tracked_unit"),  # a unit of the shop's own
        ("tracked = true, unit = 'kg', learned = true", "catalog_item_tracked_reviewed"),
        ("low_stock = -1", "catalog_item_low_stock_check"),
    ):
        with pytest.raises(errors.CheckViolation, match=constraint):
            owner.execute(f"UPDATE catalog_item SET {change} WHERE id = %s", (plain,))


def test_a_barcode_belongs_to_one_item_of_a_shop_and_to_that_shops_item(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    mine, theirs = item(owner, shop_a), item(owner, shop_b)
    with as_app(shop_a.shop_id) as app:
        app.execute(
            "INSERT INTO catalog_barcode (shop_id, code, item_id) VALUES (%s, 'A-1', %s)", (shop_a.shop_id, mine)
        )
    with pytest.raises(errors.UniqueViolation), as_app(shop_a.shop_id) as app:
        app.execute(
            "INSERT INTO catalog_barcode (shop_id, code, item_id) VALUES (%s, 'A-1', %s)", (shop_a.shop_id, mine)
        )
    # The same code in another shop is that shop's own; another shop's item cannot be named.
    with as_app(shop_b.shop_id) as app:
        app.execute(
            "INSERT INTO catalog_barcode (shop_id, code, item_id) VALUES (%s, 'A-1', %s)", (shop_b.shop_id, theirs)
        )
    with pytest.raises(errors.ForeignKeyViolation), as_app(shop_a.shop_id) as app:
        app.execute(
            "INSERT INTO catalog_barcode (shop_id, code, item_id) VALUES (%s, 'A-2', %s)", (shop_a.shop_id, theirs)
        )
    with as_app(shop_a.shop_id) as app:
        assert app.execute("SELECT code FROM catalog_barcode").fetchall() == [("A-1",)]


# --- the stock ledger and the level -------------------------------------------------------------------------


def test_the_level_is_set_from_each_movement_and_equals_their_sum(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    sugar = item(owner, shop_a)
    assert level(owner, sugar) is None, "an item nothing moved has no level row"
    with as_app(shop_a.shop_id) as app:
        move(app, shop_a, sugar, 1, "10", 100_000, "10", 100_000)
        move(app, shop_a, sugar, 2, "30", 360_000, "40", 460_000)
        move(app, shop_a, sugar, 3, "-4", -46_000, "36", 414_000, kind="sale")
    assert level(owner, sugar) == (Decimal("36.000"), 414_000, "UZS", Decimal("11500.0000"), 3)
    with as_app(shop_a.shop_id) as app:
        move(app, shop_a, sugar, 4, "-40", -414_000, "-4", 0, kind="sale")
    assert level(owner, sugar) == (Decimal("-4.000"), 0, "UZS", Decimal("11500.0000"), 4), "the last cost is kept"
    assert owner.execute("SELECT * FROM stock_level_mismatches(%s)", (shop_a.shop_id,)).fetchall() == []


@pytest.mark.parametrize(
    ("wrong", "constraint"),
    [
        ({"seq": 3}, "continues the level"),  # a number skipped: computed from a level that is not current
        ({"seq": 1}, "stock_movement_item_id_item_seq_key"),
        ({"on_hand_after": "16"}, "continues the level"),
        ({"value_after": 149_999}, "continues the level"),
        ({"currency": "USD"}, "kept in one currency"),
        ({"currency": None}, "kept in one currency"),
        ({"qty": "0", "on_hand_after": "10"}, "stock_movement_qty_check"),
        ({"kind": "gift"}, "stock_movement_kind_check"),
    ],
)
def test_a_movement_that_does_not_continue_the_level_is_refused(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, wrong: dict[str, Any], constraint: str
) -> None:
    sugar = item(owner, shop_a)
    with as_app(shop_a.shop_id) as app:
        move(app, shop_a, sugar, 1, "10", 100_000, "10", 100_000)
    right: dict[str, Any] = {"seq": 2, "qty": "5", "value_delta": 50_000, "on_hand_after": "15", "value_after": 150_000}
    args = {**right, **{name: value for name, value in wrong.items() if name in right}}
    extra = {name: value for name, value in wrong.items() if name not in right}
    with (
        pytest.raises((errors.CheckViolation, errors.UniqueViolation), match=constraint),
        as_app(shop_a.shop_id) as app,
    ):
        move(
            app,
            shop_a,
            sugar,
            args["seq"],
            args["qty"],
            args["value_delta"],
            args["on_hand_after"],
            args["value_after"],
            **extra,
        )
    assert level(owner, sugar) == (Decimal("10.000"), 100_000, "UZS", Decimal("10000.0000"), 1)


def test_nothing_on_hand_is_worth_nothing(owner: psycopg.Connection, as_app: AppSession, shop_a: Shop) -> None:
    sugar = item(owner, shop_a)
    with as_app(shop_a.shop_id) as app:
        move(app, shop_a, sugar, 1, "10", 100_000, "10", 100_000)
    with pytest.raises(errors.CheckViolation), as_app(shop_a.shop_id) as app:
        move(app, shop_a, sugar, 2, "-10", -90_000, "0", 10_000, kind="sale")


def test_the_application_cannot_write_the_level_or_change_a_movement(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    sugar = item(owner, shop_a)
    with as_app(shop_a.shop_id) as app:
        move(app, shop_a, sugar, 1, "10", 100_000, "10", 100_000)
    for statement in (
        "UPDATE stock_level SET on_hand = 999",
        "DELETE FROM stock_level",
        f"INSERT INTO stock_level (shop_id, item_id) VALUES ('{shop_a.shop_id}', '{item(owner, shop_a, 'Tuz')}')",
        "UPDATE stock_movement SET qty = 999",
        "DELETE FROM stock_movement",
        "UPDATE stock_document_line SET qty = 1",
        "DELETE FROM stock_document_line",
        "DELETE FROM stock_document",
        "DELETE FROM supplier",
    ):
        with pytest.raises(errors.InsufficientPrivilege, match="permission denied"), as_app(shop_a.shop_id) as app:
            app.execute(statement)


def test_a_movement_is_reversed_once_by_the_same_quantity_the_other_way(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    sugar, salt = item(owner, shop_a), item(owner, shop_a, "Tuz")
    with as_app(shop_a.shop_id) as app:
        first = move(app, shop_a, sugar, 1, "10", 100_000, "10", 100_000)
        move(app, shop_a, salt, 1, "10", 100_000, "10", 100_000)
    for wrong_item, qty, after in ((sugar, "-9", "1"), (salt, "-10", "0")):
        with (
            pytest.raises(errors.CheckViolation, match="the same quantity the other way"),
            as_app(shop_a.shop_id) as app,
        ):
            move(app, shop_a, wrong_item, 2, qty, -100_000, after, 0, kind="reversal", reverses=first)
    with pytest.raises(errors.CheckViolation), as_app(shop_a.shop_id) as app:
        move(app, shop_a, sugar, 2, "-10", -100_000, "0", 0, kind="receipt", reverses=first)  # a reversal says so
    with as_app(shop_a.shop_id) as app:
        undo = move(app, shop_a, sugar, 2, "-10", -100_000, "0", 0, kind="reversal", reverses=first)
    with pytest.raises(errors.UniqueViolation), as_app(shop_a.shop_id) as app:
        move(app, shop_a, sugar, 3, "-10", 0, "-10", 0, kind="reversal", reverses=first)
    with pytest.raises(errors.CheckViolation, match="the same quantity the other way"), as_app(shop_a.shop_id) as app:
        move(app, shop_a, sugar, 3, "10", 0, "10", 0, kind="reversal", reverses=undo)  # a reversal is not reversed


def test_a_movement_cannot_name_another_shops_item_or_be_written_into_another_shop(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    theirs = item(owner, shop_b)
    with pytest.raises(errors.ForeignKeyViolation), as_app(shop_a.shop_id) as app:
        move(app, shop_a, theirs, 1, "10", 0, "10", 0, currency=None)
    with pytest.raises(errors.InsufficientPrivilege, match="row-level security"), as_app(shop_a.shop_id) as app:
        move(app, shop_b, theirs, 1, "10", 0, "10", 0, currency=None)
    assert level(owner, theirs) is None


def test_the_comparison_finds_a_level_that_differs_from_its_ledger(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    """The counterpart of every "mismatches == []" in the suite: a level changed behind the ledger's back
    (only the owner of the tables can) is found."""
    sugar = item(owner, shop_a)
    with as_app(shop_a.shop_id) as app:
        move(app, shop_a, sugar, 1, "10", 100_000, "10", 100_000)
    owner.execute("UPDATE stock_level SET on_hand = 9 WHERE item_id = %s", (sugar,))
    found = owner.execute("SELECT * FROM stock_level_mismatches(%s)", (shop_a.shop_id,)).fetchall()
    assert found == [(sugar, Decimal("9.000"), Decimal("10.000"), 100_000, 100_000)]
    owner.execute("UPDATE stock_level SET on_hand = 10 WHERE item_id = %s", (sugar,))


# --- suppliers ------------------------------------------------------------------------------------------------


def test_what_a_supplier_is_owed_follows_the_entries_each_currency_apart(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    who = supplier(owner, shop_a)
    with as_app(shop_a.shop_id) as app:
        supplier_entry(app, shop_a, who, 1, "opening", 50_000)
        bought = supplier_entry(app, shop_a, who, 2, "purchase", 100_000)
        paid = supplier_entry(app, shop_a, who, 3, "payment", 30_000)
        supplier_entry(app, shop_a, who, 4, "return", 20_000)
        supplier_entry(app, shop_a, who, 5, "purchase", 7_000, currency="USD")
    assert balances(owner, who) == {"UZS": 100_000, "USD": 7_000}
    with as_app(shop_a.shop_id) as app:
        supplier_entry(app, shop_a, who, 6, "reversal", 30_000, reverses=paid)
        supplier_entry(app, shop_a, who, 7, "reversal", 100_000, reverses=bought)
    assert balances(owner, who) == {"UZS": 30_000, "USD": 7_000}
    assert owner.execute("SELECT * FROM supplier_balance_mismatches(%s)", (shop_a.shop_id,)).fetchall() == []
    owner.execute("UPDATE supplier_balance SET balance = 1 WHERE supplier_id = %s AND currency = 'USD'", (who,))
    assert owner.execute("SELECT * FROM supplier_balance_mismatches(%s)", (shop_a.shop_id,)).fetchall() == [
        (who, "USD", 1, 7_000)
    ]


def test_a_supplier_reversal_repeats_the_entry_it_reverses(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    who, other = supplier(owner, shop_a), supplier(owner, shop_a, "Boshqa")
    with as_app(shop_a.shop_id) as app:
        paid = supplier_entry(app, shop_a, who, 1, "payment", 30_000)
    for target, amount, currency in ((who, 29_999, "UZS"), (who, 30_000, "USD"), (other, 30_000, "UZS")):
        with pytest.raises(errors.CheckViolation, match="repeats the supplier"), as_app(shop_a.shop_id) as app:
            supplier_entry(app, shop_a, target, 2, "reversal", amount, currency=currency, reverses=paid)
    with as_app(shop_a.shop_id) as app:
        undo = supplier_entry(app, shop_a, who, 2, "reversal", 30_000, reverses=paid)
    with pytest.raises(errors.UniqueViolation), as_app(shop_a.shop_id) as app:
        supplier_entry(app, shop_a, who, 3, "reversal", 30_000, reverses=paid)
    with pytest.raises(errors.CheckViolation, match="repeats the supplier"), as_app(shop_a.shop_id) as app:
        supplier_entry(app, shop_a, who, 3, "reversal", 30_000, reverses=undo)
    assert balances(owner, who) == {"UZS": 0}
    for statement in ("UPDATE supplier_balance SET balance = 5", "DELETE FROM supplier_balance"):
        with pytest.raises(errors.InsufficientPrivilege, match="permission denied"), as_app(shop_a.shop_id) as app:
            app.execute(statement)
    with pytest.raises(errors.InsufficientPrivilege, match="permission denied"), as_app(shop_a.shop_id) as app:
        app.execute("UPDATE supplier SET linked_shop_id = shop_id")


# --- documents ------------------------------------------------------------------------------------------------


def test_a_document_moves_one_way_and_a_posted_one_only_gains_its_cancellation(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    doc = document(owner, shop_a)
    sugar = item(owner, shop_a)
    line = "INSERT INTO stock_document_line (shop_id, document_id, line_no, item_id, qty) VALUES (%s, %s, %s, %s, 1)"
    with as_app(shop_a.shop_id) as app:
        app.execute("UPDATE stock_document SET note = 'hali qoralama' WHERE id = %s", (doc,))  # a draft is edited
        app.execute(line, (shop_a.shop_id, doc, 1, sugar))
        app.execute(
            "UPDATE stock_document SET status = 'posted', draft = NULL, posted_by = %s, posted_at = now() "
            "WHERE id = %s",
            (shop_a.member_id, doc),
        )
    guard = "does not change|can only be cancelled|does not go back"
    for change in ("note = 'boshqa'", "total = 5", "doc_date = current_date - 1", "status = 'draft', draft = '{}'"):
        with pytest.raises(errors.CheckViolation, match=guard), as_app(shop_a.shop_id) as app:
            app.execute(f"UPDATE stock_document SET {change} WHERE id = %s", (doc,))
    with pytest.raises(errors.CheckViolation, match="never afterwards"), as_app(shop_a.shop_id) as app:
        app.execute(line, (shop_a.shop_id, doc, 2, sugar))
    with as_app(shop_a.shop_id) as app:
        app.execute(
            "UPDATE stock_document SET status = 'cancelled', cancelled_by = %s, cancelled_at = now(), "
            "cancel_reason = 'xato' WHERE id = %s",
            (shop_a.member_id, doc),
        )
    with pytest.raises(errors.CheckViolation, match=guard), as_app(shop_a.shop_id) as app:
        app.execute("UPDATE stock_document SET cancel_reason = 'boshqa sabab' WHERE id = %s", (doc,))
    with pytest.raises(errors.InsufficientPrivilege, match="permission denied"), as_app(shop_a.shop_id) as app:
        app.execute("UPDATE stock_document SET number = 99 WHERE id = %s", (doc,))


@pytest.mark.parametrize(
    "columns",
    [
        {"kind": "write_off", "reason": None},
        {"kind": "receipt", "reason": "lost"},
        {"kind": "supplier_return"},  # no supplier
        {"kind": "customer_return"},  # no customer
        {"kind": "receipt", "total": 100, "paid": 101},
        {"kind": "receipt", "status": "posted"},  # posted without who and when, and still holding a draft
        {"kind": "receipt", "status": "cancelled"},  # cancelled without who, when and why
        {"kind": "receipt", "currency": "EUR"},
        {"kind": "receipt", "number": 0},
    ],
)
def test_a_document_that_contradicts_itself_is_refused(
    owner: psycopg.Connection, shop_a: Shop, columns: dict[str, Any]
) -> None:
    kind = columns.pop("kind")
    with pytest.raises(errors.CheckViolation):
        document(owner, shop_a, kind, **columns)


def test_a_document_cannot_name_another_shops_supplier(owner: psycopg.Connection, shop_a: Shop, shop_b: Shop) -> None:
    with pytest.raises(errors.ForeignKeyViolation):
        document(owner, shop_a, "receipt", supplier_id=supplier(owner, shop_b))


# --- reading: through indexes -----------------------------------------------------------------------------------

_STATEMENTS: dict[str, tuple[str, dict[str, Any]]] = {
    "one item": (db_stock._ITEM_BY_ID, {"id": uuid.uuid4()}),
    "the stock list": (
        db_stock._ITEMS_TRACKED,
        {"name": None, "after_name": "m", "after_id": uuid.uuid4(), "limit": 51},
    ),
    "the catalogue with stock": (
        db_stock._ITEMS_ALL,
        {"name": None, "after_name": None, "after_id": None, "limit": 51},
    ),
    "running low": (db_stock._ITEMS_LOW, {"name": None, "after_name": None, "after_id": None, "limit": 51}),
    "a barcode": ("SELECT b.item_id FROM catalog_barcode b WHERE b.code = :code", {"code": "4006381333931"}),
    "the movements of an item": (
        "SELECT m.id FROM stock_movement m WHERE m.item_id = :item AND m.item_seq < :before "
        "ORDER BY m.item_seq DESC LIMIT :limit",
        {"item": uuid.uuid4(), "before": 100, "limit": 51},
    ),
    "the movements of a ledger entry": (
        "SELECT m.id FROM stock_movement m WHERE m.ledger_entry_id = :entry AND " + db_stock._STANDING,
        {"entry": uuid.uuid4()},
    ),
    "the movements of a document": (
        "SELECT m.id FROM stock_movement m WHERE m.document_id = :document AND " + db_stock._STANDING,
        {"document": uuid.uuid4()},
    ),
    "the documents, newest first": (
        "SELECT d.id FROM stock_document d ORDER BY d.created_at DESC, d.id DESC LIMIT :limit",
        {"limit": 51},
    ),
    "the documents of a kind": (
        "SELECT d.id FROM stock_document d WHERE d.kind = :kind ORDER BY d.created_at DESC, d.id DESC LIMIT :limit",
        {"kind": "receipt", "limit": 51},
    ),
    "the documents of a supplier": (
        "SELECT d.id FROM stock_document d WHERE d.supplier_id = :supplier "
        "ORDER BY d.created_at DESC, d.id DESC LIMIT :limit",
        {"supplier": uuid.uuid4(), "limit": 51},
    ),
    "the documents in a state": (
        "SELECT d.id FROM stock_document d WHERE d.status = :status ORDER BY d.created_at DESC, d.id DESC LIMIT :limit",
        {"status": "draft", "limit": 51},
    ),
    "the lines of a document": (
        "SELECT n.line_no FROM stock_document_line n WHERE n.document_id = :id ORDER BY n.line_no",
        {"id": uuid.uuid4()},
    ),
    "the suppliers by name": (
        "SELECT s.id FROM supplier s WHERE s.status = 'active' AND (s.name_norm, s.id) > (:after_name, :after_id) "
        "ORDER BY s.name_norm, s.id LIMIT :limit",
        {"after_name": "m", "after_id": uuid.uuid4(), "limit": 51},
    ),
    "what suppliers are owed": (
        "SELECT b.balance FROM supplier_balance b WHERE b.supplier_id = ANY(:ids)",
        {"ids": [uuid.uuid4()]},
    ),
    "a supplier's account": (
        "SELECT e.id FROM supplier_entry e WHERE e.supplier_id = :supplier AND e.seq < :before "
        "ORDER BY e.seq DESC LIMIT :limit",
        {"supplier": uuid.uuid4(), "before": 100, "limit": 51},
    ),
    "not sold since": (
        "SELECT l.item_id FROM stock_level l WHERE l.shop_id = :shop_id AND l.on_hand > 0 "
        "AND (l.last_sale_at IS NULL OR l.last_sale_at < now()) "
        "ORDER BY l.last_sale_at NULLS FIRST, l.item_id LIMIT 51",
        {"shop_id": uuid.uuid4()},
    ),
    "sold below cost": (
        "SELECT m.id FROM stock_movement m WHERE m.shop_id = :shop_id AND m.kind = 'sale' AND m.currency = 'UZS' "
        "AND m.sale_total < m.cost_total AND m.created_at >= now() - interval '30 days' "
        "ORDER BY m.created_at DESC, m.id DESC LIMIT 51",
        {"shop_id": uuid.uuid4()},
    ),
    # What is asked before a shop's dollars are turned off (qarz.application.shops.set_dollars_in).
    "a supplier account open in dollars": (db_stock._SUPPLIER_DOLLARS_OPEN, {"shop_id": uuid.uuid4()}),
    "goods on hand costed in dollars": (db_stock._STOCK_DOLLARS_ON_HAND, {"shop_id": uuid.uuid4()}),
}


# The index each read goes through. Under row-level security every read of a tenant table can fall back
# on some index that leads with the shop, so "no sequential scan" alone would prove nothing: the read
# must use the index that finds its own rows.
_INDEX_OF: dict[str, tuple[str, ...]] = {
    "one item": ("catalog_item_pkey",),
    "the stock list": ("catalog_item_tracked",),
    "the catalogue with stock": ("catalog_item_shop_id_name_norm_key",),
    # Every item with a threshold is counted, so on a table this small the two partial indexes cost the same.
    "running low": ("catalog_item_low", "catalog_item_tracked"),
    "a barcode": ("catalog_barcode_pkey",),
    "the movements of an item": ("stock_movement_item_id_item_seq_key",),
    "the movements of a ledger entry": ("stock_movement_entry",),
    "the movements of a document": ("stock_movement_document",),
    "the documents, newest first": ("stock_document_recent",),
    # Likewise: the newest documents filtered by kind, or the documents of the kind, newest first.
    "the documents of a kind": ("stock_document_by_kind", "stock_document_recent"),
    # Narrowed to one supplier: that supplier's documents, or the shop's newest read until the page is full.
    "the documents of a supplier": ("stock_document_supplier", "stock_document_recent"),
    # A state has an index of its own (migration 0047), and only that one will do: through the shop's
    # newest documents a rare state is a walk over all of them (the two tests after the next one).
    "the documents in a state": ("stock_document_by_status",),
    "the lines of a document": ("stock_document_line_pkey",),
    "the suppliers by name": ("supplier_shop_id_name_norm_key",),
    # By supplier, or the shop's few balances by currency: both are read by key.
    "what suppliers are owed": ("supplier_balance_pkey", "supplier_balance_shop"),
    "a supplier's account": ("supplier_entry_supplier_id_seq_key",),
    "not sold since": ("stock_level_idle",),
    "sold below cost": ("stock_movement_below_cost",),
    "a supplier account open in dollars": ("supplier_balance_shop",),
    # The shop's items that are on hand, which is what the partial index holds; the dollar ones are
    # picked out of them.
    "goods on hand costed in dollars": ("stock_level_idle",),
}


# A `:name` parameter of the storage layer, and the same as psycopg writes it.
_PARAMETER = re.compile(r"(?<!:):([a-z_]+)")
_PSYCOPG = r"%(\1)s"


def _plan(conn: psycopg.Connection, statement: str, values: dict[str, Any]) -> str:
    """The plan of a statement written with the storage layer's `:name` parameters, with a sequential
    scan made the planner's last choice: on tables as small as a test's it would pick one for anything."""
    # Likewise a sort and a bitmap: a page that is read in order through its index needs neither, and
    # with a handful of rows the planner would as soon sort them.
    for setting in ("enable_seqscan", "enable_sort", "enable_bitmapscan"):
        conn.execute(f"SET LOCAL {setting} = off")
    rows = conn.execute("EXPLAIN (COSTS OFF) " + _PARAMETER.sub(_PSYCOPG, statement), values).fetchall()
    return " ".join(str(row[0]) for row in rows)


@pytest.mark.parametrize("what", sorted(_STATEMENTS))
def test_a_list_of_the_stock_is_read_through_its_index(as_app: AppSession, shop_a: Shop, what: str) -> None:
    """Each read finds its rows by key, as the application role under row-level security: what a page
    costs is what it holds, not what the shop, or every shop, holds."""
    assert set(_INDEX_OF) == set(_STATEMENTS)
    statement, values = _STATEMENTS[what]
    values = {name: shop_a.shop_id if name == "shop_id" else value for name, value in values.items()}
    with as_app(shop_a.shop_id) as app:
        plan = _plan(app, statement, values)
    assert "Seq Scan" not in plan, plan
    assert any(index in plan for index in _INDEX_OF[what]), plan


def test_the_plan_check_catches_a_read_that_has_no_index_of_its_own(as_app: AppSession, shop_a: Shop) -> None:
    """The counterpart: movements asked for by the member who wrote them have no index to go through.
    The planner still avoids a sequential scan, by reading every movement of the shop and filtering:
    which is why each read above is held to its own index and not only to "no sequential scan"."""
    with as_app(shop_a.shop_id) as app:
        plan = _plan(app, "SELECT m.id FROM stock_movement m WHERE m.author_id = :who", {"who": shop_a.member_id})
    assert "Filter: (author_id" in plan, plan
    assert not any(index in plan for index in ("stock_movement_item", "stock_movement_entry", "stock_movement_doc"))


_IN_A_STATE = (
    "SELECT d.id FROM stock_document d WHERE d.status = :status ORDER BY d.created_at DESC, d.id DESC LIMIT :limit"
)


def _many_documents_and_two_drafts(owner: psycopg.Connection, shop: Shop, posted: int = 400) -> None:
    """A shop whose drafts are its two OLDEST documents, under several hundred posted ones."""
    owner.execute(
        "INSERT INTO stock_document (id, shop_id, kind, number, status, doc_date, draft, created_by, created_at, "
        "  posted_by, posted_at) "
        "SELECT gen_random_uuid(), %(shop)s, 'stocktake', n, CASE WHEN n <= 2 THEN 'draft' ELSE 'posted' END, "
        "  current_date, CASE WHEN n <= 2 THEN '{}'::jsonb END, %(member)s, now() - interval '1 day' + n * interval "
        "  '1 second', CASE WHEN n > 2 THEN %(member)s::uuid END, CASE WHEN n > 2 THEN now() END "
        "FROM generate_series(1, %(count)s) n",
        {"shop": shop.shop_id, "member": shop.member_id, "count": posted + 2},
    )


def _measured(conn: psycopg.Connection, statement: str, values: dict[str, Any]) -> str:
    """Like `_plan`, but run: the plan says how many rows it read and threw away."""
    for setting in ("enable_seqscan", "enable_sort", "enable_bitmapscan"):
        conn.execute(f"SET LOCAL {setting} = off")
    rows = conn.execute(
        "EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF) " + _PARAMETER.sub(_PSYCOPG, statement),
        values,
    ).fetchall()
    return " ".join(str(row[0]) for row in rows)


def test_a_rare_state_is_found_without_walking_the_shops_documents(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    """The documents list narrowed to a state (`GET stock/documents?status=draft`): two drafts under four
    hundred posted documents are read from the index of the state, and no other document is looked at."""
    _many_documents_and_two_drafts(owner, shop_a)
    with as_app(shop_a.shop_id) as app:
        plan = _measured(app, _IN_A_STATE, {"status": "draft", "limit": 51})
    assert "stock_document_by_status" in plan, plan
    assert "Rows Removed by Filter" not in plan, plan
    assert "rows=2 " in plan, plan


def test_without_its_index_a_rare_state_is_a_walk_over_every_document_of_the_shop(
    owner: psycopg.Connection, database_url: str, shop_a: Shop
) -> None:
    """The counterpart, and what the list did before migration 0047: with the index taken away (inside a
    transaction that is rolled back) the same read goes through the shop's newest documents and throws
    away every one that is not a draft. So the test above passes because of the index, not by luck."""
    _many_documents_and_two_drafts(owner, shop_a)
    with psycopg.connect(database_url) as conn:
        try:
            conn.execute("DROP INDEX stock_document_by_status")
            conn.execute("SET LOCAL ROLE qd_app")
            conn.execute("SELECT set_config('qd.shop_id', %s, true)", (str(shop_a.shop_id),))
            plan = _measured(conn, _IN_A_STATE, {"status": "draft", "limit": 51})
        finally:
            conn.rollback()
    assert "stock_document_by_status" not in plan, plan
    assert "Rows Removed by Filter: 400" in plan, plan
    assert owner.execute("SELECT 1 FROM pg_indexes WHERE indexname = 'stock_document_by_status'").fetchone() == (1,)


def test_a_sale_and_its_movement_are_one_shops(owner: psycopg.Connection, as_app: AppSession, shop_a: Shop) -> None:
    """A movement points at the ledger entry that caused it; the level remembers when the item last sold."""
    sugar = item(owner, shop_a)
    entry = add_entry(owner, shop_a, seq=1, amount=24_000)
    with as_app(shop_a.shop_id) as app:
        move(app, shop_a, sugar, 1, "10", 100_000, "10", 100_000)
        move(
            app,
            shop_a,
            sugar,
            2,
            "-2",
            -20_000,
            "8",
            80_000,
            kind="sale",
            entry=entry,
            sale_total=24_000,
            cost_total=20_000,
        )
    row = owner.execute("SELECT last_sale_at IS NOT NULL FROM stock_level WHERE item_id = %s", (sugar,)).fetchone()
    assert row == (True,)
