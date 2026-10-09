"""The cash book's tables, straight against the database as the application role (migration 0042).

What the database itself refuses, whatever the application does: changing or deleting an entry, taking a
cancellation back, a second entry for one payment, an entry that is not its payment, a category of the
wrong direction or of another shop, and reading the totals by walking the whole table.
"""

import uuid
from typing import Any

import psycopg
import pytest
from psycopg import errors

from ..conftest import AppSession, Shop, add_entry

pytestmark = pytest.mark.db


def category(
    owner: psycopg.Connection,
    shop: Shop,
    direction: str = "expense",
    name: str = "Ijara",
    system_key: str | None = None,
) -> uuid.UUID:
    category_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO cash_category (id, shop_id, direction, name, name_norm, system_key) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (category_id, shop.shop_id, direction, name, name.lower(), system_key),
    )
    return category_id


def entry(
    conn: psycopg.Connection,
    shop: Shop,
    category_id: uuid.UUID,
    *,
    direction: str = "expense",
    amount: Any = 300_000,
    method: str = "cash",
    currency: str = "UZS",
    payment: uuid.UUID | None = None,
    shop_id: uuid.UUID | None = None,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO cash_entry (id, shop_id, direction, method, currency, amount, category_id, day, author_id, "
        "ledger_entry_id) VALUES (%s, %s, %s, %s, %s, %s, %s, current_date, %s, %s)",
        (entry_id, shop_id or shop.shop_id, direction, method, currency, amount, category_id, shop.member_id, payment),
    )
    return entry_id


def cancel(conn: psycopg.Connection, shop: Shop, entry_id: uuid.UUID, reason: str | None = "Xato") -> None:
    conn.execute(
        "UPDATE cash_entry SET cancelled_at = now(), cancelled_by = %s, cancel_reason = %s WHERE id = %s",
        (shop.member_id, reason, entry_id),
    )


# --- an entry is written and cancelled, and nothing else ---------------------------------------------


def test_the_application_writes_an_entry_and_cancels_it_once(
    as_app: AppSession, shop_a: Shop, owner: psycopg.Connection
) -> None:
    rent = category(owner, shop_a)
    with as_app(shop_a.shop_id) as app:
        written = entry(app, shop_a, rent)
        cancel(app, shop_a, written)
    assert owner.execute(
        "SELECT cancelled_at IS NOT NULL, cancelled_by, cancel_reason FROM cash_entry WHERE id = %s", (written,)
    ).fetchone() == (True, shop_a.member_id, "Xato")
    # A second cancellation, and taking the cancellation back, are both refused by the trigger.
    with pytest.raises(errors.CheckViolation, match="cancelled once"), as_app(shop_a.shop_id) as app:
        cancel(app, shop_a, written, "Yana")
    with pytest.raises(errors.CheckViolation, match="cancelled once"), as_app(shop_a.shop_id) as app:
        app.execute(
            "UPDATE cash_entry SET cancelled_at = NULL, cancelled_by = NULL, cancel_reason = NULL WHERE id = %s",
            (written,),
        )
    assert owner.execute("SELECT cancel_reason FROM cash_entry WHERE id = %s", (written,)).fetchone() == ("Xato",)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE cash_entry SET amount = 1",
        "UPDATE cash_entry SET direction = 'income'",
        "UPDATE cash_entry SET method = 'card'",
        "UPDATE cash_entry SET currency = 'USD'",
        "UPDATE cash_entry SET category_id = gen_random_uuid()",
        "UPDATE cash_entry SET note = 'boshqa'",
        "UPDATE cash_entry SET day = current_date - 1",
        "UPDATE cash_entry SET created_at = now()",
        "UPDATE cash_entry SET author_id = gen_random_uuid()",
        "UPDATE cash_entry SET ledger_entry_id = NULL",
        "UPDATE cash_entry SET shop_id = gen_random_uuid()",
        "DELETE FROM cash_entry",
        "TRUNCATE cash_entry",
    ],
)
def test_what_an_entry_says_cannot_be_rewritten_or_removed(
    as_app: AppSession, shop_a: Shop, owner: psycopg.Connection, statement: str
) -> None:
    written = entry(owner, shop_a, category(owner, shop_a))
    with pytest.raises(errors.InsufficientPrivilege), as_app(shop_a.shop_id) as app:
        app.execute(statement)
    assert owner.execute("SELECT amount, direction, method FROM cash_entry WHERE id = %s", (written,)).fetchone() == (
        300_000,
        "expense",
        "cash",
    )


@pytest.mark.parametrize(
    ("columns", "values"),
    [
        # Half a cancellation: a time without a person, a person without a time, a reason alone.
        ("cancelled_at", "now()"),
        ("cancelled_by", "author_id"),
        ("cancel_reason", "'Xato'"),
        # An entry written by hand is cancelled with a reason.
        ("cancelled_at, cancelled_by", "now(), author_id"),
        ("cancelled_at, cancelled_by, cancel_reason", "now(), author_id, ''"),
    ],
)
def test_a_cancellation_is_whole_and_says_why(
    as_app: AppSession, shop_a: Shop, owner: psycopg.Connection, columns: str, values: str
) -> None:
    written = entry(owner, shop_a, category(owner, shop_a))
    with pytest.raises(errors.CheckViolation), as_app(shop_a.shop_id) as app:
        app.execute(f"UPDATE cash_entry SET ({columns}) = ROW({values}) WHERE id = %s", (written,))
    assert owner.execute("SELECT cancelled_at FROM cash_entry WHERE id = %s", (written,)).fetchone() == (None,)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"amount": 0}, errors.CheckViolation),
        ({"amount": -5}, errors.CheckViolation),
        ({"method": "cheque"}, errors.CheckViolation),
        ({"currency": "EUR"}, errors.CheckViolation),
        ({"direction": "both"}, errors.CheckViolation),
        # The category is of expense: an income entry cannot be put under it.
        ({"direction": "income"}, errors.ForeignKeyViolation),
    ],
)
def test_an_entry_that_breaks_a_rule_is_refused(
    as_app: AppSession, shop_a: Shop, owner: psycopg.Connection, change: dict[str, Any], error: type[Exception]
) -> None:
    rent = category(owner, shop_a)
    with pytest.raises(error), as_app(shop_a.shop_id) as app:
        entry(app, shop_a, rent, **change)
    assert owner.execute("SELECT count(*) FROM cash_entry WHERE shop_id = %s", (shop_a.shop_id,)).fetchone() == (0,)


def test_an_entry_cannot_be_put_under_another_shops_category(
    as_app: AppSession, shop_a: Shop, shop_b: Shop, owner: psycopg.Connection
) -> None:
    theirs = category(owner, shop_b)
    with pytest.raises(errors.ForeignKeyViolation), as_app(shop_a.shop_id) as app:
        entry(app, shop_a, theirs)
    # Nor written as the other shop's: row-level security refuses the row itself.
    mine = category(owner, shop_a)
    with pytest.raises(errors.InsufficientPrivilege), as_app(shop_a.shop_id) as app:
        entry(app, shop_a, mine, shop_id=shop_b.shop_id)


# --- the ledger's payments ---------------------------------------------------------------------------


def test_a_payment_is_in_the_book_once_and_as_itself(
    as_app: AppSession, shop_a: Shop, shop_b: Shop, owner: psycopg.Connection
) -> None:
    repaid = category(owner, shop_a, "income", "Qarz qaytdi", "debt_repaid")
    add_entry(owner, shop_a, seq=1, amount=50_000)
    payment = add_entry(owner, shop_a, seq=2, amount=20_000, kind="payment")
    with as_app(shop_a.shop_id) as app:
        entry(app, shop_a, repaid, direction="income", amount=20_000, payment=payment)
    with pytest.raises(errors.UniqueViolation), as_app(shop_a.shop_id) as app:
        entry(app, shop_a, repaid, direction="income", amount=20_000, payment=payment)
    assert owner.execute("SELECT count(*) FROM cash_entry WHERE ledger_entry_id = %s", (payment,)).fetchone() == (1,)


@pytest.mark.parametrize("what", ["another amount", "a credit sale", "another shop's payment", "an expense"])
def test_an_entry_of_the_ledger_is_that_payment_and_nothing_else(
    as_app: AppSession, shop_a: Shop, shop_b: Shop, owner: psycopg.Connection, what: str
) -> None:
    repaid = category(owner, shop_a, "income", "Qarz qaytdi", "debt_repaid")
    rent = category(owner, shop_a)
    sale = add_entry(owner, shop_a, seq=1, amount=50_000)
    payment = add_entry(owner, shop_a, seq=2, amount=20_000, kind="payment")
    add_entry(owner, shop_b, seq=1, amount=50_000)
    theirs = add_entry(owner, shop_b, seq=2, amount=20_000, kind="payment")
    with pytest.raises(errors.CheckViolation), as_app(shop_a.shop_id) as app:
        if what == "another amount":
            entry(app, shop_a, repaid, direction="income", amount=25_000, payment=payment)
        elif what == "a credit sale":
            entry(app, shop_a, repaid, direction="income", amount=50_000, payment=sale)
        elif what == "another shop's payment":
            entry(app, shop_a, repaid, direction="income", amount=20_000, payment=theirs)
        else:
            entry(app, shop_a, rent, direction="expense", amount=20_000, payment=payment)
    assert owner.execute("SELECT count(*) FROM cash_entry WHERE shop_id = %s", (shop_a.shop_id,)).fetchone() == (0,)


def test_the_ledger_cancels_its_entry_without_a_reason_and_nobody_else_can(
    as_app: AppSession, shop_a: Shop, owner: psycopg.Connection
) -> None:
    repaid = category(owner, shop_a, "income", "Qarz qaytdi", "debt_repaid")
    add_entry(owner, shop_a, seq=1, amount=50_000)
    payment = add_entry(owner, shop_a, seq=2, amount=20_000, kind="payment")
    with as_app(shop_a.shop_id) as app:
        of_ledger = entry(app, shop_a, repaid, direction="income", amount=20_000, payment=payment)
        cancel(app, shop_a, of_ledger, None)
    assert owner.execute("SELECT cancelled_at IS NOT NULL FROM cash_entry WHERE id = %s", (of_ledger,)).fetchone() == (
        True,
    )


# --- categories ----------------------------------------------------------------------------------------


def test_a_category_name_is_its_own_within_a_direction_of_a_shop(
    as_app: AppSession, shop_a: Shop, shop_b: Shop, owner: psycopg.Connection
) -> None:
    category(owner, shop_a, "expense", "Ijara")
    with pytest.raises(errors.UniqueViolation):
        category(owner, shop_a, "expense", "IJARA")
    # Free in the other direction and in another shop.
    category(owner, shop_a, "income", "Ijara")
    category(owner, shop_b, "expense", "Ijara")


def test_the_category_of_payments_is_one_per_shop_and_cannot_be_archived(
    as_app: AppSession, shop_a: Shop, owner: psycopg.Connection
) -> None:
    repaid = category(owner, shop_a, "income", "Qarz qaytdi", "debt_repaid")
    with pytest.raises(errors.UniqueViolation):
        category(owner, shop_a, "income", "Yana biri", "debt_repaid")
    with pytest.raises(errors.CheckViolation), as_app(shop_a.shop_id) as app:
        app.execute("UPDATE cash_category SET archived_at = now() WHERE id = %s", (repaid,))
    # The key itself is not the application's to set or clear on a row that exists.
    with pytest.raises(errors.InsufficientPrivilege), as_app(shop_a.shop_id) as app:
        app.execute("UPDATE cash_category SET system_key = NULL WHERE id = %s", (repaid,))


def test_a_category_with_entries_cannot_be_deleted_and_one_without_can(
    as_app: AppSession, shop_a: Shop, owner: psycopg.Connection
) -> None:
    used, unused = category(owner, shop_a, name="Ijara"), category(owner, shop_a, name="Transport")
    entry(owner, shop_a, used)
    with pytest.raises(errors.ForeignKeyViolation), as_app(shop_a.shop_id) as app:
        app.execute("DELETE FROM cash_category WHERE id = %s", (used,))
    with as_app(shop_a.shop_id) as app:
        app.execute("DELETE FROM cash_category WHERE id = %s", (unused,))
    assert owner.execute("SELECT id FROM cash_category WHERE shop_id = %s", (shop_a.shop_id,)).fetchall() == [(used,)]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE cash_category SET direction = 'income'",
        "UPDATE cash_category SET shop_id = gen_random_uuid()",
        "UPDATE cash_category SET system_key = 'debt_repaid'",
        "TRUNCATE cash_category",
    ],
)
def test_a_category_keeps_its_direction_its_shop_and_its_key(
    as_app: AppSession, shop_a: Shop, owner: psycopg.Connection, statement: str
) -> None:
    category(owner, shop_a)
    with pytest.raises(errors.InsufficientPrivilege), as_app(shop_a.shop_id) as app:
        app.execute(statement)


# --- who else may touch the tables -------------------------------------------------------------------


def test_one_shop_sees_only_its_own_book(
    as_app: AppSession, shop_a: Shop, shop_b: Shop, owner: psycopg.Connection
) -> None:
    mine, theirs = category(owner, shop_a), category(owner, shop_b)
    entry(owner, shop_a, mine, amount=1_000)
    entry(owner, shop_b, theirs, amount=2_000)
    with as_app(shop_a.shop_id) as app:
        assert app.execute("SELECT amount FROM cash_entry").fetchall() == [(1_000,)]
        assert app.execute("SELECT id FROM cash_category").fetchall() == [(mine,)]
        assert app.execute("SELECT 1 FROM cash_entry WHERE shop_id = %s", (shop_b.shop_id,)).fetchall() == []
        # Cancelling "every entry" cancels this shop's only.
        app.execute("UPDATE cash_entry SET cancelled_at = now(), cancelled_by = author_id, cancel_reason = 'x'")
    assert owner.execute(
        "SELECT cancelled_at IS NOT NULL FROM cash_entry WHERE shop_id = %s", (shop_b.shop_id,)
    ).fetchall() == [(False,)]
    # Without a shop set, nothing is visible at all.
    with as_app(None) as app:
        assert app.execute("SELECT count(*) FROM cash_entry").fetchone() == (0,)
        assert app.execute("SELECT count(*) FROM cash_category").fetchone() == (0,)


def test_the_worker_reads_the_book_for_the_export_and_writes_nothing(
    as_worker: AppSession, as_admin: AppSession, shop_a: Shop, owner: psycopg.Connection
) -> None:
    rent = category(owner, shop_a)
    written = entry(owner, shop_a, rent)
    with as_worker(shop_a.shop_id) as worker:
        assert worker.execute("SELECT id FROM cash_entry").fetchall() == [(written,)]
        assert worker.execute("SELECT id FROM cash_category").fetchall() == [(rent,)]
    for statement in (
        "INSERT INTO cash_category (id, shop_id, direction, name, name_norm) "
        f"VALUES (gen_random_uuid(), '{shop_a.shop_id}', 'income', 'X', 'x')",
        "UPDATE cash_entry SET cancelled_at = now(), cancelled_by = author_id, cancel_reason = 'x'",
        "UPDATE cash_category SET name = 'X'",
        "DELETE FROM cash_category",
        "DELETE FROM cash_entry",
    ):
        with pytest.raises(errors.InsufficientPrivilege), as_worker(shop_a.shop_id) as worker:
            worker.execute(statement)
    # The administrators' side has no part in a shop's cash book.
    for table in ("cash_entry", "cash_category"):
        with pytest.raises(errors.InsufficientPrivilege), as_admin(shop_a.shop_id) as admin:
            admin.execute(f"SELECT 1 FROM {table}")


# --- reading without walking the table -----------------------------------------------------------------

# The statements of qarz.infrastructure.db_cash, with their parameters.
READS = {
    "the balances before a day": (
        "SELECT method, currency, direction, sum(amount)::bigint, count(*) FROM cash_entry "
        "WHERE shop_id = %(shop)s AND day >= %(first)s AND day < %(before)s AND cancelled_at IS NULL "
        "GROUP BY method, currency, direction",
        "cash_entry_standing",
    ),
    "a period by category": (
        "SELECT category_id, direction, currency, sum(amount)::bigint, count(*) FROM cash_entry "
        "WHERE shop_id = %(shop)s AND day >= %(first)s AND day < %(before)s AND cancelled_at IS NULL "
        "GROUP BY category_id, direction, currency",
        "cash_entry_standing",
    ),
    "a period by day": (
        "SELECT day, direction, currency, sum(amount)::bigint FROM cash_entry "
        "WHERE shop_id = %(shop)s AND day >= %(first)s AND day < %(before)s AND cancelled_at IS NULL "
        "GROUP BY day, direction, currency",
        "cash_entry_standing",
    ),
    "a page of a day": (
        "SELECT id FROM cash_entry e WHERE e.shop_id = %(shop)s AND e.day = %(first)s "
        "AND (e.created_at, e.id) < (now(), %(shop)s) ORDER BY e.created_at DESC, e.id DESC LIMIT 51",
        "cash_entry_day",
    ),
    "a page of a period for its export": (
        "SELECT id FROM cash_entry e WHERE e.shop_id = %(shop)s AND e.day >= %(first)s AND e.day < %(before)s "
        "AND (e.day, e.created_at, e.id) > (%(first)s, now(), %(shop)s) AND e.created_at <= now() "
        "ORDER BY e.day, e.created_at, e.id LIMIT 1000",
        "cash_entry_day",
    ),
    "how many entries a period holds": (
        "SELECT count(*) FROM cash_entry WHERE shop_id = %(shop)s AND day >= %(first)s AND day < %(before)s",
        "cash_entry_",
    ),
    "the entry of a payment": (
        "UPDATE cash_entry SET cancelled_at = now(), cancelled_by = %(shop)s "
        "WHERE ledger_entry_id = %(shop)s AND shop_id = %(shop)s AND cancelled_at IS NULL",
        "cash_entry_one_per_payment",
    ),
    "whether a category is used": (
        "SELECT EXISTS (SELECT 1 FROM cash_entry WHERE category_id = %(shop)s)",
        "cash_entry_category",
    ),
}


@pytest.mark.parametrize("read", sorted(READS))
def test_the_books_reads_are_answered_from_an_index(owner: psycopg.Connection, shop_a: Shop, read: str) -> None:
    """Each statement the cash book runs on every request finds its rows by an index that leads with the
    shop (or with the one row it is after), so its cost is what it reads and not the size of the table."""
    statement, index = READS[read]
    # A book that is not tiny, and a planner that knows it: on an empty table every plan costs the same.
    owner.execute(
        "INSERT INTO cash_entry (id, shop_id, direction, method, amount, category_id, day, author_id) "
        "SELECT gen_random_uuid(), %s, 'expense', 'cash', 1000 + n, %s, current_date - (n %% 60), %s "
        "FROM generate_series(1, 400) AS n",
        (shop_a.shop_id, category(owner, shop_a), shop_a.member_id),
    )
    owner.execute("ANALYZE cash_entry")
    owner.execute("SET enable_seqscan = off")
    try:
        plan = owner.execute(
            "EXPLAIN (COSTS OFF) " + statement,
            {"shop": shop_a.shop_id, "first": "2026-01-01", "before": "2026-02-01"},
        ).fetchall()
    finally:
        owner.execute("RESET enable_seqscan")
    text = " ".join(row[0] for row in plan)
    assert index in text, text
    assert "Seq Scan" not in text, text


def test_the_check_above_would_notice_a_read_without_an_index(owner: psycopg.Connection, shop_a: Shop) -> None:
    """The counterpart: a read by a column no index leads with walks the table even when told not to."""
    owner.execute("SET enable_seqscan = off")
    try:
        plan = owner.execute("EXPLAIN (COSTS OFF) SELECT sum(amount) FROM cash_entry WHERE note = 'x'").fetchall()
    finally:
        owner.execute("RESET enable_seqscan")
    assert "Seq Scan" in " ".join(row[0] for row in plan)


def test_the_totals_are_read_from_the_index_alone(owner: psycopg.Connection, shop_a: Shop) -> None:
    """The index of standing entries carries everything a total needs, so a balance over a long history
    does not visit the table's rows at all once the table has been vacuumed."""
    included = owner.execute(
        "SELECT array_agg(a.attname::text ORDER BY k.ordinality) FROM pg_index i "
        "CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ordinality) "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum "
        "WHERE i.indexrelid = 'cash_entry_standing'::regclass"
    ).fetchone()
    assert included == (["shop_id", "day", "direction", "method", "currency", "amount", "category_id"],)
    predicate = owner.execute(
        "SELECT pg_get_expr(indpred, indrelid) FROM pg_index WHERE indexrelid = 'cash_entry_standing'::regclass"
    ).fetchone()
    assert predicate == ("(cancelled_at IS NULL)",)
