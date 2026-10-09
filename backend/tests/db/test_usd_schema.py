"""US dollars beside so'm in the database (migration 0041).

Every amount of a customer's debt names its currency, so'm unless it says otherwise; only the two
currencies exist; a reversal carries the currency of what it reverses; and the stored open debts are kept
per currency, agreeing entry by entry with the domain's allocation of each book.
"""

import random
import uuid
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from psycopg import errors

from qarz.domain import ledger
from qarz.domain.ledger import Entry, EntryKind
from qarz.domain.money import Currency

from ..conftest import AppSession, Shop, add_entry, refused

pytestmark = pytest.mark.db

UZS, USD = Currency.UZS, Currency.USD


def add(
    conn: psycopg.Connection,
    shop: Shop,
    seq: int,
    amount: int,
    currency: str = "USD",
    kind: str = "credit",
    reverses: uuid.UUID | None = None,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, reverses_id, author_id, currency) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (entry_id, shop.shop_id, shop.customer_id, seq, kind, amount, reverses, shop.member_id, currency),
    )
    return entry_id


def open_debts(owner: psycopg.Connection, shop: Shop) -> list[tuple[uuid.UUID, str, int]]:
    rows = owner.execute(
        "SELECT d.entry_id, d.currency, d.remaining FROM open_debt d JOIN ledger_entry e ON e.id = d.entry_id "
        "WHERE d.customer_id = %s ORDER BY e.seq",
        (shop.customer_id,),
    ).fetchall()
    return [(row[0], row[1], int(row[2])) for row in rows]


# --- the columns ---------------------------------------------------------------------------------------


def test_an_entry_recorded_without_a_currency_is_som(owner: psycopg.Connection, shop_a: Shop) -> None:
    """Every statement written before dollars existed keeps its meaning."""
    entry = add_entry(owner, shop_a, seq=1, amount=50_000)
    assert owner.execute("SELECT currency FROM ledger_entry WHERE id = %s", (entry,)).fetchone() == ("UZS",)
    assert owner.execute("SELECT currency FROM open_debt WHERE entry_id = %s", (entry,)).fetchone() == ("UZS",)
    settings = owner.execute(
        "SELECT usd_on, default_credit_limit_usd FROM shop WHERE id = %s", (shop_a.shop_id,)
    ).fetchone()
    assert settings == (False, None)


@pytest.mark.parametrize("currency", ["EUR", "usd", "", "$", "RUB"])
def test_only_som_and_dollars_exist(owner: psycopg.Connection, shop_a: Shop, currency: str) -> None:
    with pytest.raises(errors.CheckViolation):
        add(owner, shop_a, 1, 1_000, currency)
    with pytest.raises(errors.CheckViolation):
        owner.execute(
            "INSERT INTO payment_notice (id, shop_id, customer_id, amount, currency) "
            "VALUES (gen_random_uuid(), %s, %s, 1000, %s)",
            (shop_a.shop_id, shop_a.customer_id, currency),
        )
    with pytest.raises(errors.CheckViolation):
        owner.execute(
            "INSERT INTO measure.event (id, shop_ref, kind, amount, currency) "
            "VALUES (gen_random_uuid(), gen_random_uuid(), 'credit', 1000, %s)",
            (currency,),
        )
    entry = add(owner, shop_a, 1, 1_000, "USD")
    with pytest.raises(errors.CheckViolation):
        owner.execute("UPDATE open_debt SET currency = %s WHERE entry_id = %s", (currency, entry))


def test_a_currency_is_never_missing(owner: psycopg.Connection, shop_a: Shop) -> None:
    with pytest.raises(errors.NotNullViolation):
        owner.execute(
            "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id, currency) "
            "VALUES (gen_random_uuid(), %s, %s, 1, 'credit', 1000, %s, NULL)",
            (shop_a.shop_id, shop_a.customer_id, shop_a.member_id),
        )


def test_a_limit_in_dollars_is_above_zero_or_absent(owner: psycopg.Connection, shop_a: Shop) -> None:
    owner.execute("UPDATE customer SET credit_limit_usd = 5000 WHERE id = %s", (shop_a.customer_id,))
    owner.execute("UPDATE shop SET default_credit_limit_usd = 100 WHERE id = %s", (shop_a.shop_id,))
    with pytest.raises(errors.CheckViolation):
        owner.execute("UPDATE customer SET credit_limit_usd = 0 WHERE id = %s", (shop_a.customer_id,))
    with pytest.raises(errors.CheckViolation):
        owner.execute("UPDATE shop SET default_credit_limit_usd = -1 WHERE id = %s", (shop_a.shop_id,))


# --- reversals -----------------------------------------------------------------------------------------


def test_a_reversal_carries_the_currency_of_the_entry_it_reverses(owner: psycopg.Connection, shop_a: Shop) -> None:
    in_dollars = add(owner, shop_a, 1, 5_000, "USD")
    in_som = add(owner, shop_a, 2, 50_000, "UZS")
    # The negative cases: a so'm reversal of a dollar entry, and the reverse, are refused by the database.
    with pytest.raises(errors.CheckViolation, match="currency"):
        add(owner, shop_a, 3, 5_000, "UZS", "reversal", in_dollars)
    with pytest.raises(errors.CheckViolation, match="currency"):
        add(owner, shop_a, 3, 50_000, "USD", "reversal", in_som)
    # A reversal that says nothing of its currency is so'm, and so cannot reverse dollars either.
    with pytest.raises(errors.CheckViolation, match="currency"):
        add_entry(owner, shop_a, seq=3, amount=5_000, kind="reversal", reverses=in_dollars)
    add(owner, shop_a, 3, 5_000, "USD", "reversal", in_dollars)
    add_entry(owner, shop_a, seq=4, amount=50_000, kind="reversal", reverses=in_som)
    assert open_debts(owner, shop_a) == []


def test_the_application_role_meets_the_same_rule(as_app: AppSession, owner: psycopg.Connection, shop_a: Shop) -> None:
    in_dollars = add(owner, shop_a, 1, 5_000, "USD")
    with pytest.raises(errors.CheckViolation, match="currency"), as_app(shop_a.shop_id) as conn:
        conn.execute(
            "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, reverses_id, author_id) "
            "VALUES (gen_random_uuid(), %s, %s, 2, 'reversal', 5000, %s, %s)",
            (shop_a.shop_id, shop_a.customer_id, in_dollars, shop_a.member_id),
        )


# --- open debts, per currency ----------------------------------------------------------------------------


def test_a_payment_covers_the_debts_of_its_own_currency_only(owner: psycopg.Connection, shop_a: Shop) -> None:
    som = add(owner, shop_a, 1, 50_000, "UZS")
    dollars = add(owner, shop_a, 2, 12_000, "USD")
    assert open_debts(owner, shop_a) == [(som, "UZS", 50_000), (dollars, "USD", 12_000)]

    # 20.00 $ paid: the dollar debt shrinks and the so'm debt, though older, is untouched.
    add(owner, shop_a, 3, 2_000, "USD", "payment")
    assert open_debts(owner, shop_a) == [(som, "UZS", 50_000), (dollars, "USD", 10_000)]
    # 50 000 so'm paid: so'm is settled, dollars are as they were.
    add(owner, shop_a, 4, 50_000, "UZS", "payment")
    assert open_debts(owner, shop_a) == [(dollars, "USD", 10_000)]
    # A number that would settle the dollars if it were dollars does nothing to them as so'm: here there
    # is no so'm debt left, so the so'm book would go below zero, which is the application's to refuse;
    # the stored rows simply still show the dollars.
    add(owner, shop_a, 5, 10_000, "USD", "payment")
    assert open_debts(owner, shop_a) == []
    assert owner.execute("SELECT * FROM open_debt_mismatches(%s)", (shop_a.shop_id,)).fetchall() == []


def test_generated_accounts_in_two_currencies_agree_with_the_domain_book_by_book(
    owner: psycopg.Connection, shop_a: Shop
) -> None:
    """Random sales, payments and reversals in both currencies: after every entry the stored rows are what
    `qarz.domain.ledger.allocate` leaves uncovered in each book."""
    rng = random.Random(20261009)  # noqa: S311  (a fixed seed for a repeatable test)
    for _account in range(10):
        customer = uuid.uuid4()
        owner.execute(
            "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Sinov', 'sinov')",
            (customer, shop_a.shop_id),
        )
        account = Shop(shop_a.shop_id, shop_a.user_id, shop_a.member_id, customer)
        entries: list[Entry] = []
        at = datetime(2050, 1, 1, tzinfo=UTC)
        for seq in range(1, rng.randint(8, 22)):
            at += timedelta(minutes=1)
            currency = rng.choice([UZS, USD])
            book = ledger.in_currency(entries, currency)
            live = [e for e in book if e.kind is not EntryKind.REVERSAL and all(r.reverses_id != e.id for r in book)]
            balance = ledger.balance(book)
            reversible = [e for e in live if e.kind is EntryKind.PAYMENT or balance - e.amount >= 0]
            choice = rng.random()
            if choice < 0.2 and reversible:
                target = rng.choice(reversible)
                new = add(owner, account, seq, target.amount, currency.value, "reversal", target.id)
                entries.append(
                    Entry(new, seq, EntryKind.REVERSAL, target.amount, at, reverses_id=target.id, currency=currency)
                )
            elif choice < 0.55 and balance > 0:
                amount = rng.randint(1, balance)
                new = add(owner, account, seq, amount, currency.value, "payment")
                entries.append(Entry(new, seq, EntryKind.PAYMENT, amount, at, currency=currency))
            else:
                amount = rng.randint(1, 90) * (1000 if currency is UZS else 25)
                new = add(owner, account, seq, amount, currency.value)
                entries.append(
                    Entry(new, seq, EntryKind.CREDIT, amount, at, promised_date=date(2050, 2, 1), currency=currency)
                )
            expected = sorted(
                (allocation.seq, allocation.entry_id, currency.value, allocation.remaining)
                for currency in Currency
                for allocation in ledger.allocate(ledger.in_currency(entries, currency))
                if allocation.remaining > 0
            )
            assert open_debts(owner, account) == [row[1:] for row in expected], (seq, len(entries))
    assert owner.execute("SELECT * FROM open_debt_mismatches(%s)", (shop_a.shop_id,)).fetchall() == []


def test_the_comparison_reports_a_stored_row_in_the_wrong_currency(owner: psycopg.Connection, shop_a: Shop) -> None:
    entry = add(owner, shop_a, 1, 5_000, "USD")
    assert owner.execute("SELECT * FROM open_debt_mismatches(%s)", (shop_a.shop_id,)).fetchall() == []
    owner.execute("UPDATE open_debt SET currency = 'UZS' WHERE entry_id = %s", (entry,))
    assert [row[0] for row in owner.execute("SELECT * FROM open_debt_mismatches(NULL)").fetchall()] == [entry]


def test_what_a_customer_owes_in_one_currency_is_read_through_the_customers_index(
    owner: psycopg.Connection, shop_a: Shop
) -> None:
    """The refresh and the per-currency figures both find a customer's rows without reading the table."""
    add(owner, shop_a, 1, 5_000, "USD")
    add(owner, shop_a, 2, 50_000, "UZS")
    statements = (
        "DELETE FROM open_debt d WHERE d.customer_id = ANY (%s::uuid[])",
        "SELECT customer_id, sum(remaining) FROM open_debt WHERE customer_id = ANY (%s::uuid[]) "
        "AND currency = 'USD' GROUP BY customer_id",
    )
    owner.execute("SET enable_seqscan = off")
    try:
        for statement in statements:
            plan = " ".join(
                row[0] for row in owner.execute("EXPLAIN (COSTS OFF) " + statement, ([shop_a.customer_id],)).fetchall()
            )
            assert "open_debt_customer" in plan, plan
            assert "Seq Scan on open_debt" not in plan, plan
    finally:
        owner.execute("RESET enable_seqscan")


def test_the_stored_open_debts_stay_closed_to_the_worker(
    as_worker: AppSession, owner: psycopg.Connection, shop_a: Shop
) -> None:
    """Dollars gave the worker nothing new to read or write here."""
    add(owner, shop_a, 1, 5_000, "USD")
    for statement in (
        "SELECT currency FROM open_debt",
        "UPDATE open_debt SET remaining = 1",
        "DELETE FROM open_debt",
        "SELECT refresh_open_debts(ARRAY[gen_random_uuid()])",
    ):
        refused(as_worker, statement, shop_a.shop_id)


# --- reminders and a person's own accounts ----------------------------------------------------------------


def test_a_reminder_states_an_amount_in_at_least_one_currency(owner: psycopg.Connection, shop_a: Shop) -> None:
    insert = (
        "INSERT INTO reminder (id, shop_id, customer_id, kind, channel, amount, amount_usd, sent_on) "
        "VALUES (gen_random_uuid(), %s, %s, 'manual', 'telegram', %s, %s, %s)"
    )
    ids = (shop_a.shop_id, shop_a.customer_id)
    owner.execute(insert, (*ids, 0, 1_200, date(2050, 1, 1)))  # dollars alone
    owner.execute(insert, (*ids, 50_000, 0, date(2050, 1, 2)))  # so'm alone, as always
    owner.execute(insert, (*ids, 50_000, 1_200, date(2050, 1, 3)))
    for amount, amount_usd in ((0, 0), (-1, 5), (5, -1)):
        with pytest.raises(errors.CheckViolation):
            owner.execute(insert, (*ids, amount, amount_usd, date(2050, 1, 4)))


def test_a_persons_own_accounts_show_each_currency_by_itself(owner: psycopg.Connection, shop_a: Shop) -> None:
    person = uuid.uuid4()
    owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (person, uuid.uuid4().int % 10**15))
    owner.execute(
        "INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at) "
        "VALUES (gen_random_uuid(), %s, %s, %s, 'active', 2, now())",
        (shop_a.shop_id, shop_a.customer_id, person),
    )
    add(owner, shop_a, 1, 50_000, "UZS")
    paid = add(owner, shop_a, 2, 20_000, "UZS", "payment")
    add(owner, shop_a, 3, 12_000, "USD")
    add(owner, shop_a, 4, 2_000, "USD", "payment")
    add(owner, shop_a, 5, 20_000, "UZS", "reversal", paid)
    row = owner.execute("SELECT balance, balance_usd, usd_on FROM my_accounts(%s)", (person,)).fetchone()
    assert row == (50_000, 10_000, False), "two balances, and never 60 000 of anything"
    owner.execute("UPDATE shop SET usd_on = true WHERE id = %s", (shop_a.shop_id,))
    assert owner.execute("SELECT usd_on FROM my_accounts(%s)", (person,)).fetchone() == (True,)


def test_erasing_a_shop_clears_its_dollar_settings(owner: psycopg.Connection, shop_a: Shop) -> None:
    owner.execute("UPDATE shop SET usd_on = true, default_credit_limit_usd = 5000 WHERE id = %s", (shop_a.shop_id,))
    add(owner, shop_a, 1, 5_000, "USD")
    owner.execute(
        "SET session_replication_role = replica"
    )  # past the guard on a shop's status, as the tests of erasure do
    try:
        owner.execute(
            "UPDATE shop SET status = 'deletion_pending', deletion_due = now() - interval '1 minute' WHERE id = %s",
            (shop_a.shop_id,),
        )
    finally:
        owner.execute("RESET session_replication_role")
    assert owner.execute("SELECT erase_shop(%s)", (shop_a.shop_id,)).fetchone() == (True,)
    assert owner.execute(
        "SELECT usd_on, default_credit_limit_usd FROM shop WHERE id = %s", (shop_a.shop_id,)
    ).fetchone() == (False, None)
    assert owner.execute("SELECT count(*) FROM open_debt WHERE shop_id = %s", (shop_a.shop_id,)).fetchone() == (0,)
