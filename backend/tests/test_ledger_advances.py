"""An advance in the pure ledger (INV-3 as the founder decided it on 2026-10-10).

A customer may pay more than they owe where the shop accepts advances: the book then stands below zero,
the next credit sales are covered by what ran ahead, oldest first, and nothing is overdue meanwhile.
Where the shop does not, every rule is what it was; the last tests here hold that.
"""

import random
from datetime import date, datetime, timedelta
from uuid import UUID

import pytest

from qarz.domain.ledger import (
    Entry,
    EntryKind,
    LedgerIntegrityError,
    Refusal,
    advance,
    allocate,
    balance,
    lowest_balance,
    overdue,
    payment_history,
    payment_timeliness,
    validate_new_entry,
)
from qarz.domain.money import Currency
from qarz.domain.promise import TASHKENT

CREDIT, OPENING, PAYMENT, REVERSAL = EntryKind.CREDIT, EntryKind.OPENING, EntryKind.PAYMENT, EntryKind.REVERSAL
BASE = datetime(2026, 3, 2, 9, 0, tzinfo=TASHKENT)
CAP = 1_000_000


def entry(
    seq: int,
    kind: EntryKind,
    amount: int,
    *,
    promised: date | None = None,
    reverses: Entry | None = None,
    day: int = 0,
    currency: Currency = Currency.UZS,
) -> Entry:
    if kind in (CREDIT, OPENING) and promised is None:
        promised = date(2026, 12, 31)
    return Entry(
        id=UUID(int=seq),
        seq=seq,
        kind=kind,
        amount=amount,
        created_at=BASE + timedelta(days=day, minutes=seq),
        reverses_id=None if reverses is None else reverses.id,
        promised_date=promised,
        currency=currency,
    )


def undo(seq: int, target: Entry) -> Entry:
    return entry(seq, REVERSAL, target.amount, reverses=target, currency=target.currency)


# --- what a book in credit says ------------------------------------------------------------------------


def test_a_payment_beyond_the_debt_leaves_the_book_below_zero_by_the_rest() -> None:
    book = [entry(1, CREDIT, 30_000), entry(2, PAYMENT, 45_000)]
    assert balance(book) == -15_000
    assert advance(book) == 15_000
    assert lowest_balance(book) == -15_000
    # Reading such a book is not an error: it is a state valid data can be in.
    assert [a.remaining for a in allocate(book)] == [0]


def test_a_book_that_owes_or_is_settled_has_no_advance() -> None:
    assert advance([entry(1, CREDIT, 30_000), entry(2, PAYMENT, 10_000)]) == 0
    assert advance([entry(1, CREDIT, 30_000), entry(2, PAYMENT, 30_000)]) == 0
    assert advance([]) == 0 and lowest_balance([]) == 0


def test_the_lowest_balance_remembers_an_advance_that_was_used_up() -> None:
    book = [entry(1, PAYMENT, 50_000), entry(2, CREDIT, 80_000)]
    assert balance(book) == 30_000
    assert lowest_balance(book) == -50_000


# --- the advance is used up by later credit sales, oldest first, as they are written ---------------------


def test_a_sale_recorded_while_in_credit_is_covered_from_the_moment_it_is_written() -> None:
    paid = entry(1, PAYMENT, 50_000)
    first = entry(2, CREDIT, 20_000)
    assert [(a.remaining, a.covered) for a in allocate([paid, first])] == [(0, 20_000)]
    assert balance([paid, first]) == -30_000
    second = entry(3, CREDIT, 45_000)
    # The oldest sale is covered first; the newest carries what the advance could not reach.
    assert [(a.entry_id, a.covered, a.remaining) for a in allocate([paid, first, second])] == [
        (first.id, 20_000, 0),
        (second.id, 30_000, 15_000),
    ]
    assert balance([paid, first, second]) == 15_000
    assert advance([paid, first, second]) == 0


def test_a_sale_covered_by_an_advance_is_settled_at_the_time_of_the_payment_and_counts_as_paid_in_time() -> None:
    paid = entry(1, PAYMENT, 50_000, day=0)
    sale = entry(2, CREDIT, 50_000, promised=date(2026, 3, 10), day=3)
    (covered,) = allocate([paid, sale])
    assert covered.settled_at == paid.created_at
    history = payment_history([paid, sale], date(2026, 4, 1))
    assert history is not None and (history.on_time_percent, history.longest_delay_days) == (100, 0)


def test_nothing_is_overdue_while_the_customer_is_in_credit() -> None:
    paid = entry(1, PAYMENT, 90_000)
    old = entry(2, CREDIT, 40_000, promised=date(2026, 3, 5))
    status = overdue([paid, old], date(2026, 6, 1))
    assert (status.overdue_amount, status.due_today_amount, status.reminder_overdue_amount) == (0, 0, 0)
    assert not status.is_overdue and status.earliest_unmet_promised_date is None


def test_only_what_the_advance_could_not_cover_can_be_overdue() -> None:
    paid = entry(1, PAYMENT, 30_000)
    old = entry(2, CREDIT, 40_000, promised=date(2026, 3, 5))
    status = overdue([paid, old], date(2026, 6, 1))
    assert (status.overdue_amount, status.earliest_unmet_promised_date) == (10_000, date(2026, 3, 5))


def test_the_part_of_a_payment_that_stands_as_an_advance_is_neither_in_time_nor_late() -> None:
    sale = entry(1, CREDIT, 30_000, promised=date(2026, 3, 1))  # promised before it was paid: late
    paid = entry(2, PAYMENT, 45_000, day=5)
    assert payment_timeliness([sale, paid], paid.id) == (0, 30_000)
    # Once a later sale uses the rest, that part is in time: it was paid before it was promised.
    later = entry(3, CREDIT, 15_000, promised=date(2026, 12, 31), day=6)
    assert payment_timeliness([sale, paid, later], paid.id) == (15_000, 30_000)


def test_an_advance_in_one_currency_never_covers_a_debt_in_the_other() -> None:
    som = entry(1, PAYMENT, 80_000)
    dollars = entry(2, CREDIT, 5_000, currency=Currency.USD)
    with pytest.raises(LedgerIntegrityError, match="different currencies"):
        balance([som, dollars])
    assert balance([som]) == -80_000 and balance([dollars]) == 5_000
    assert [a.remaining for a in allocate([dollars])] == [5_000]


# --- reversals --------------------------------------------------------------------------------------------


def test_reversing_a_sale_the_advance_had_covered_gives_the_advance_back() -> None:
    paid, sale = entry(1, PAYMENT, 50_000), entry(2, CREDIT, 20_000)
    assert validate_new_entry([paid, sale], REVERSAL, 20_000, sale.id, advance_cap=CAP) is None
    book = [paid, sale, undo(3, sale)]
    assert balance(book) == -50_000 and allocate(book) == []


def test_reversing_the_advance_payment_after_sales_used_it_leaves_those_sales_owed_again() -> None:
    paid = entry(1, PAYMENT, 50_000)
    first, second = entry(2, CREDIT, 20_000, promised=date(2026, 3, 5)), entry(3, CREDIT, 45_000)
    book = [paid, first, second]
    # A reversal that raises the balance is never refused, whichever way the shop has chosen.
    assert validate_new_entry(book, REVERSAL, 50_000, paid.id) is None
    assert validate_new_entry(book, REVERSAL, 50_000, paid.id, advance_cap=CAP) is None
    after = [*book, undo(4, paid)]
    assert balance(after) == 65_000 and lowest_balance(after) == -50_000
    assert [(a.entry_id, a.covered, a.remaining) for a in allocate(after)] == [
        (first.id, 0, 20_000),
        (second.id, 0, 45_000),
    ]
    # With their own promised dates: what was covered ahead of time may now be overdue.
    assert overdue(after, date(2026, 6, 1)).overdue_amount == 20_000


def test_a_later_payment_takes_the_place_of_a_reversed_advance_oldest_first() -> None:
    paid = entry(1, PAYMENT, 50_000)
    first, second = entry(2, CREDIT, 20_000), entry(3, CREDIT, 45_000)
    again = entry(5, PAYMENT, 30_000)
    after = [paid, first, second, undo(4, paid), again]
    assert [(a.covered, a.remaining) for a in allocate(after)] == [(20_000, 0), (10_000, 35_000)]


# --- whether a new entry may take the book below zero ---------------------------------------------------


@pytest.mark.parametrize(
    ("book", "amount", "cap", "expected"),
    [
        # The shop does not accept advances: as it always was.
        ([], 1, None, Refusal.EXCEEDS_BALANCE),
        ([entry(1, CREDIT, 30_000)], 30_001, None, Refusal.EXCEEDS_BALANCE),
        ([entry(1, CREDIT, 30_000)], 30_000, None, None),
        # It does: up to the cap, counted on what stands, not on one payment.
        ([], 1, CAP, None),
        ([entry(1, CREDIT, 30_000)], 30_001, CAP, None),
        ([entry(1, CREDIT, 30_000)], 30_000 + CAP, CAP, None),
        ([entry(1, CREDIT, 30_000)], 30_001 + CAP, CAP, Refusal.ADVANCE_TOO_LARGE),
        ([entry(1, PAYMENT, CAP)], 1, CAP, Refusal.ADVANCE_TOO_LARGE),
        ([entry(1, PAYMENT, CAP - 5)], 5, CAP, None),
        # A cap of nothing accepts no advance, but says so by its own name.
        ([entry(1, CREDIT, 30_000)], 30_001, 0, Refusal.ADVANCE_TOO_LARGE),
        # A book already in credit in a shop that no longer accepts advances takes no further payment.
        ([entry(1, PAYMENT, 10)], 1, None, Refusal.EXCEEDS_BALANCE),
    ],
)
def test_a_payment_beyond_the_debt_is_the_shops_choice(
    book: list[Entry], amount: int, cap: int | None, expected: Refusal | None
) -> None:
    assert validate_new_entry(book, PAYMENT, amount, advance_cap=cap) == expected


def test_reversing_a_paid_sale_is_refused_without_advances_and_bounded_with_them() -> None:
    sale, paid = entry(1, CREDIT, 30_000), entry(2, PAYMENT, 30_000)
    assert validate_new_entry([sale, paid], REVERSAL, 30_000, sale.id) == Refusal.NEGATIVE_BALANCE
    assert validate_new_entry([sale, paid], REVERSAL, 30_000, sale.id, advance_cap=30_000) is None
    assert validate_new_entry([sale, paid], REVERSAL, 30_000, sale.id, advance_cap=29_999) == Refusal.ADVANCE_TOO_LARGE


def test_a_credit_sale_is_never_refused_by_the_rule_whatever_the_balance() -> None:
    in_credit = [entry(1, PAYMENT, 500)]
    for cap in (None, 0, CAP):
        assert validate_new_entry(in_credit, CREDIT, 100, advance_cap=cap) is None
        assert validate_new_entry(in_credit, CREDIT, 10_000, advance_cap=cap) is None


@pytest.mark.parametrize("cap", [-1, 1.5, True, "10"])
def test_a_cap_that_is_not_a_whole_amount_is_a_programming_error(cap: object) -> None:
    with pytest.raises(ValueError, match="advance_cap"):
        validate_new_entry([], PAYMENT, 1, advance_cap=cap)  # type: ignore[arg-type]


# --- properties over generated books -----------------------------------------------------------------------


def _generated(seed: int, cap: int | None) -> list[Entry]:
    """A book built only from entries `validate_new_entry` allowed under the given choice."""
    rng = random.Random(seed)  # noqa: S311
    book: list[Entry] = []
    for seq in range(1, 60):
        live = [e for e in book if e.kind is not REVERSAL and e.id not in {r.reverses_id for r in book}]
        roll = rng.random()
        if roll < 0.45:
            candidate = entry(seq, CREDIT, rng.randint(1, 90_000), promised=date(2026, 3, rng.randint(1, 28)), day=seq)
        elif roll < 0.85 or not live:
            candidate = entry(seq, PAYMENT, rng.randint(1, 120_000), day=seq)
        else:
            target = rng.choice(live)
            candidate = Entry(
                id=UUID(int=seq),
                seq=seq,
                kind=REVERSAL,
                amount=target.amount,
                created_at=BASE + timedelta(days=seq),
                reverses_id=target.id,
            )
        if validate_new_entry(book, candidate.kind, candidate.amount, candidate.reverses_id, advance_cap=cap) is None:
            book.append(candidate)
    return book


@pytest.mark.parametrize("seed", range(60))
def test_property_without_advances_no_book_ever_goes_below_zero(seed: int) -> None:
    book = _generated(seed, None)
    for upto in range(len(book) + 1):
        assert lowest_balance(book[:upto]) == 0


@pytest.mark.parametrize("seed", range(60))
def test_property_with_advances_what_is_owed_minus_what_is_held_is_the_balance(seed: int) -> None:
    book = _generated(seed, 150_000)
    went_below = False
    for upto in range(len(book) + 1):
        part = book[:upto]
        owed = sum(a.remaining for a in allocate(part))
        # Never both: a customer in credit owes nothing, and one who owes has no advance.
        assert owed - advance(part) == balance(part)
        assert owed == 0 or advance(part) == 0
        assert advance(part) <= 150_000
        if advance(part):
            went_below = True
            assert not overdue(part, date(2030, 1, 1)).is_overdue
    assert went_below or lowest_balance(book) == 0


# --- an import does not make an advance --------------------------------------------------------------------


@pytest.mark.parametrize("cell", ["-45000", "-1", "- 45 000"])
def test_an_imported_opening_balance_cannot_be_an_advance(cell: str) -> None:
    """Kept refused in this pass: an opening balance is a debt. Advances are recorded as payments."""
    from qarz.domain.imports import RowProblem, parse_amount, parse_amount_in

    assert parse_amount(cell) in (RowProblem.AMOUNT_TOO_SMALL, RowProblem.AMOUNT_INVALID)
    assert isinstance(parse_amount_in(Currency.USD, cell), RowProblem)
