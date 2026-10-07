"""Stored open debts (migration 0026): kept by the database from the ledger, never by a request.

The overview and the debtors list read this table instead of adding up the whole ledger. So it must say
exactly what the ledger says, after every kind of entry, and nobody but the triggers may write it.
"""

import random
import uuid
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from qarz.domain.ledger import Entry, EntryKind, allocate

from ..conftest import AppSession, Shop, add_entry

pytestmark = pytest.mark.db


def promise(owner: psycopg.Connection, shop: Shop, entry: uuid.UUID, day: date, at: str = "now()") -> None:
    owner.execute(
        "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor, created_at) "
        f"VALUES (gen_random_uuid(), %s, %s, %s, 'staff', {at})",
        (shop.shop_id, entry, day),
    )


def stored(owner: psycopg.Connection, shop: Shop) -> list[tuple[uuid.UUID, int, date | None]]:
    rows = owner.execute(
        "SELECT d.entry_id, d.remaining, d.promised_date FROM open_debt d JOIN ledger_entry e ON e.id = d.entry_id "
        "WHERE d.shop_id = %s AND d.customer_id = %s ORDER BY e.seq",
        (shop.shop_id, shop.customer_id),
    ).fetchall()
    return [(row[0], int(row[1]), row[2]) for row in rows]


def mismatches(owner: psycopg.Connection, shop: Shop | None = None) -> list[tuple[object, ...]]:
    return owner.execute("SELECT * FROM open_debt_mismatches(%s)", (None if shop is None else shop.shop_id,)).fetchall()


def test_every_kind_of_entry_keeps_the_open_debts_true(owner: psycopg.Connection, shop_a: Shop) -> None:
    day, later = date(2050, 5, 1), date(2050, 6, 1)
    assert stored(owner, shop_a) == []

    first = add_entry(owner, shop_a, seq=1, amount=50_000)
    assert stored(owner, shop_a) == [(first, 50_000, None)], "a debt is open as soon as it is recorded"
    promise(owner, shop_a, first, day)
    assert stored(owner, shop_a) == [(first, 50_000, day)]

    second = add_entry(owner, shop_a, seq=2, amount=30_000)
    promise(owner, shop_a, second, later)
    assert stored(owner, shop_a) == [(first, 50_000, day), (second, 30_000, later)]

    # A payment covers the oldest debt first (BR-3): part of it, then all of it and part of the next.
    add_entry(owner, shop_a, seq=3, amount=20_000, kind="payment")
    assert stored(owner, shop_a) == [(first, 30_000, day), (second, 30_000, later)]
    paid = add_entry(owner, shop_a, seq=4, amount=40_000, kind="payment")
    assert stored(owner, shop_a) == [(second, 20_000, later)], "a debt paid in full is no longer listed"

    # Reversing that payment opens what it had covered.
    add_entry(owner, shop_a, seq=5, amount=40_000, kind="reversal", reverses=paid)
    assert stored(owner, shop_a) == [(first, 30_000, day), (second, 30_000, later)]

    # Reversing a debt removes it, and the payment that covered part of it flows on to the next.
    add_entry(owner, shop_a, seq=6, amount=50_000, kind="reversal", reverses=first)
    assert stored(owner, shop_a) == [(second, 10_000, later)]

    # A promise moved later, then one recorded with an earlier time: the newest by time is the current one.
    promise(owner, shop_a, second, date(2050, 7, 1), "now() + interval '1 minute'")
    assert stored(owner, shop_a) == [(second, 10_000, date(2050, 7, 1))]
    promise(owner, shop_a, second, date(2049, 1, 1), "now() - interval '1 day'")
    assert stored(owner, shop_a) == [(second, 10_000, date(2050, 7, 1))]

    add_entry(owner, shop_a, seq=7, amount=10_000, kind="payment")
    assert stored(owner, shop_a) == []
    assert mismatches(owner, shop_a) == []


def test_one_statement_adding_entries_for_several_customers_refreshes_each(
    owner: psycopg.Connection, shop_a: Shop, shop_b: Shop
) -> None:
    other = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Ikkinchi', 'ikkinchi')",
        (other, shop_a.shop_id),
    )
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) VALUES "
        "(gen_random_uuid(), %s, %s, 1, 'opening', 70000, %s), (gen_random_uuid(), %s, %s, 1, 'credit', 15000, %s), "
        "(gen_random_uuid(), %s, %s, 2, 'credit', 5000, %s), (gen_random_uuid(), %s, %s, 1, 'credit', 900, %s)",
        (
            shop_a.shop_id, shop_a.customer_id, shop_a.member_id,
            shop_a.shop_id, other, shop_a.member_id,
            shop_a.shop_id, other, shop_a.member_id,
            shop_b.shop_id, shop_b.customer_id, shop_b.member_id,
        ),
    )  # fmt: skip
    totals = owner.execute(
        "SELECT shop_id, customer_id, sum(remaining)::bigint, count(*) FROM open_debt "
        "WHERE customer_id IN (%s, %s, %s) GROUP BY shop_id, customer_id",
        (shop_a.customer_id, other, shop_b.customer_id),
    ).fetchall()
    assert sorted(totals, key=lambda row: row[2]) == [
        (shop_b.shop_id, shop_b.customer_id, 900, 1),
        (shop_a.shop_id, other, 20_000, 2),
        (shop_a.shop_id, shop_a.customer_id, 70_000, 1),
    ]
    assert mismatches(owner) == []


def test_generated_accounts_agree_with_the_domains_allocation(owner: psycopg.Connection, shop_a: Shop) -> None:
    """Random credits, payments and reversals: after every entry the stored rows are what
    `qarz.domain.ledger.allocate` leaves uncovered, entry by entry."""
    rng = random.Random(20261007)  # noqa: S311  (a fixed seed for a repeatable test)
    for _account in range(12):
        customer = uuid.uuid4()
        owner.execute(
            "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Sinov', 'sinov')",
            (customer, shop_a.shop_id),
        )
        account = Shop(shop_a.shop_id, shop_a.user_id, shop_a.member_id, customer)
        entries: list[Entry] = []
        at = datetime(2050, 1, 1, tzinfo=UTC)
        for seq in range(1, rng.randint(6, 18)):
            at += timedelta(minutes=1)
            live = [
                e for e in entries if e.kind is not EntryKind.REVERSAL and all(r.reverses_id != e.id for r in entries)
            ]
            balance = sum(e.amount if e.kind in (EntryKind.CREDIT, EntryKind.OPENING) else -e.amount for e in live)
            choice = rng.random()
            reversible = [
                e for e in live
                if e.kind is EntryKind.PAYMENT or balance - e.amount >= 0
            ]  # fmt: skip
            if choice < 0.2 and reversible:
                target = rng.choice(reversible)
                new = add_entry(owner, account, seq=seq, amount=target.amount, kind="reversal", reverses=target.id)
                entries.append(Entry(new, seq, EntryKind.REVERSAL, target.amount, at, reverses_id=target.id))
            elif choice < 0.55 and balance > 0:
                amount = rng.randint(1, balance)
                new = add_entry(owner, account, seq=seq, amount=amount, kind="payment")
                entries.append(Entry(new, seq, EntryKind.PAYMENT, amount, at))
            else:
                amount = rng.randint(1, 90) * 1000
                new = add_entry(owner, account, seq=seq, amount=amount)
                entries.append(Entry(new, seq, EntryKind.CREDIT, amount, at, promised_date=date(2050, 2, 1)))
            expected = [(a.entry_id, a.remaining) for a in allocate(entries) if a.remaining > 0]
            assert [(entry, left) for entry, left, _ in stored(owner, account)] == expected, (seq, len(entries))
    assert mismatches(owner, shop_a) == []


def test_the_comparison_reports_a_row_that_differs_from_the_ledger(owner: psycopg.Connection, shop_a: Shop) -> None:
    """The counterpart: the check that says "all is well" does say otherwise when it is not."""
    kept = add_entry(owner, shop_a, seq=1, amount=40_000)
    promise(owner, shop_a, kept, date(2050, 5, 1))
    gone = add_entry(owner, shop_a, seq=2, amount=7_000)
    assert mismatches(owner, shop_a) == []
    for damage, expected in (
        ("UPDATE open_debt SET remaining = 39999 WHERE entry_id = %s", (kept, 39_999, 40_000)),
        ("UPDATE open_debt SET promised_date = '2050-05-02' WHERE entry_id = %s", (kept, 40_000, 40_000)),
        ("DELETE FROM open_debt WHERE entry_id = %s", (gone, None, 7_000)),
    ):
        target = expected[0]
        with owner.transaction(force_rollback=True):
            owner.execute(damage, (target,))
            found = [row[:3] for row in mismatches(owner, shop_a)]
            assert found == [expected], damage
            assert mismatches(owner, None) != []
    assert mismatches(owner, shop_a) == []
    # And a rebuild puts right whatever was wrong.
    owner.execute("DELETE FROM open_debt WHERE shop_id = %s", (shop_a.shop_id,))
    assert len(mismatches(owner, shop_a)) == 2
    owner.execute("SELECT refresh_open_debts(ARRAY[%s]::uuid[])", (shop_a.customer_id,))
    assert mismatches(owner, shop_a) == []


def test_the_application_reads_its_own_shops_rows_and_writes_none(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    mine = add_entry(owner, shop_a, seq=1, amount=40_000)
    add_entry(owner, shop_b, seq=1, amount=9_000)
    with as_app(None) as app:
        assert app.execute("SELECT count(*) FROM open_debt").fetchone() == (0,)
    with as_app(shop_a.shop_id) as app:
        assert app.execute("SELECT entry_id, remaining FROM open_debt").fetchall() == [(mine, 40_000)]
    denied = psycopg.errors.InsufficientPrivilege
    for statement in (
        "UPDATE open_debt SET remaining = 1",
        "DELETE FROM open_debt",
        "INSERT INTO open_debt (entry_id, shop_id, customer_id, remaining) SELECT entry_id, shop_id, customer_id, 5 "
        "FROM open_debt",
        "SELECT refresh_open_debts(ARRAY[]::uuid[])",
        "SELECT * FROM open_debts_of(ARRAY[]::uuid[])",
        "SELECT * FROM open_debt_mismatches(NULL)",
    ):
        with pytest.raises(denied), as_app(shop_a.shop_id) as app:
            app.execute(statement)
    # Yet an entry the application itself records refreshes the rows, through the trigger.
    with as_app(shop_a.shop_id) as app:
        app.execute(
            "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
            "VALUES (gen_random_uuid(), %s, %s, 2, 'payment', 15000, %s)",
            (shop_a.shop_id, shop_a.customer_id, shop_a.member_id),
        )
    assert stored(owner, shop_a) == [(mine, 25_000, None)]
    assert mismatches(owner) == []
