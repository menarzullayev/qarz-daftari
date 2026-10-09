import random
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from uuid import UUID

import pytest

from qarz.domain.ledger import (
    MAX_AMOUNT,
    Allocation,
    Entry,
    EntryKind,
    LedgerIntegrityError,
    OverdueStatus,
    PaymentHistory,
    Refusal,
    allocate,
    balance,
    not_before,
    overdue,
    payment_history,
    validate_new_entry,
)
from qarz.domain.promise import TASHKENT, tashkent_date

D = date
CREDIT, OPENING, PAYMENT, REVERSAL = EntryKind.CREDIT, EntryKind.OPENING, EntryKind.PAYMENT, EntryKind.REVERSAL

BASE = datetime(2026, 3, 2, 9, 0, tzinfo=TASHKENT)  # a Monday morning in the shop
TODAY = D(2026, 4, 10)
LATER = D(2026, 12, 31)  # a promised date that is never due in these examples


def uid(n: int) -> UUID:
    return UUID(int=n)


def tk(month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    """A wall-clock time in Tashkent in 2026."""
    return datetime(2026, month, day, hour, minute, tzinfo=TASHKENT)


def credit(
    seq: int,
    amount: int,
    promised: date | None = LATER,
    *,
    at: datetime | None = None,
    disputed: bool = False,
    kind: EntryKind = CREDIT,
) -> Entry:
    return Entry(
        id=uid(seq),
        seq=seq,
        kind=kind,
        amount=amount,
        created_at=at or BASE + timedelta(hours=seq),
        promised_date=promised,
        disputed=disputed,
    )


def opening(seq: int, amount: int, promised: date | None = LATER, *, disputed: bool = False) -> Entry:
    return credit(seq, amount, promised, disputed=disputed, kind=OPENING)


def payment(seq: int, amount: int, *, at: datetime | None = None) -> Entry:
    return Entry(id=uid(seq), seq=seq, kind=PAYMENT, amount=amount, created_at=at or BASE + timedelta(hours=seq))


def reversal(seq: int, target: Entry, *, amount: int | None = None, at: datetime | None = None) -> Entry:
    return Entry(
        id=uid(seq),
        seq=seq,
        kind=REVERSAL,
        amount=target.amount if amount is None else amount,
        created_at=at or BASE + timedelta(hours=seq),
        reverses_id=target.id,
    )


# ---------------------------------------------------------------------------------------------------------
# Balance (INV-2, BR-5)
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([], 0),
        ([credit(1, 50_000)], 50_000),
        ([opening(1, 30_000)], 30_000),
        ([opening(1, 30_000), credit(2, 20_000)], 50_000),
        ([credit(1, 50_000), payment(2, 20_000)], 30_000),
        ([credit(1, 50_000), payment(2, 20_000), payment(3, 30_000)], 0),
        ([credit(1, 50_000), credit(2, 30_000), payment(3, 80_000)], 0),
        # A reversed credit and its reversal cancel out: the balance goes down.
        ([credit(1, 50_000), credit(2, 30_000), reversal(3, credit(1, 50_000))], 30_000),
        ([opening(1, 30_000), reversal(2, opening(1, 30_000))], 0),
        # A reversed payment and its reversal cancel out: the balance goes back up.
        ([credit(1, 50_000), payment(2, 20_000), reversal(3, payment(2, 20_000))], 50_000),
        (
            [
                credit(1, 50_000),
                payment(2, 20_000),
                reversal(3, payment(2, 20_000)),
                payment(4, 50_000),
                credit(5, 7_000),
                reversal(6, credit(5, 7_000)),
            ],
            0,
        ),
        ([credit(1, 1), credit(2, MAX_AMOUNT)], MAX_AMOUNT + 1),  # Python integers do not overflow
    ],
)
def test_balance(entries: list[Entry], expected: int) -> None:
    assert balance(entries) == expected


# ---------------------------------------------------------------------------------------------------------
# Validation of a proposed entry (INV-3 to INV-6, BR-5)
# ---------------------------------------------------------------------------------------------------------

C1 = credit(1, 100_000)
C2 = credit(2, 60_000)
O1 = opening(1, 100_000)
P3 = payment(3, 100_000)


@pytest.mark.parametrize(
    ("entries", "kind", "amount", "reverses_id"),
    [
        ([], CREDIT, 1, None),
        ([], OPENING, 250_000, None),
        ([C1], CREDIT, MAX_AMOUNT, None),
        ([C1], PAYMENT, 1, None),
        ([C1], PAYMENT, 99_999, None),
        ([C1], PAYMENT, 100_000, None),  # exactly the balance
        ([O1], PAYMENT, 100_000, None),
        ([C1, C2, P3], PAYMENT, 60_000, None),
        ([C1], REVERSAL, 100_000, C1.id),  # an unpaid credit
        ([O1], REVERSAL, 100_000, O1.id),
        ([C1, C2, payment(3, 60_000)], REVERSAL, 100_000, C1.id),  # the other credit keeps the balance at zero
        ([C1, C2, payment(3, 60_000)], REVERSAL, 60_000, C2.id),
        ([C1, C2, P3], REVERSAL, 100_000, P3.id),  # a payment can always be reversed
        ([C1, P3], REVERSAL, 100_000, P3.id),
        # After the payment is reversed the credit is unpaid again and may itself be reversed.
        ([C1, P3, reversal(4, P3)], REVERSAL, 100_000, C1.id),
    ],
)
def test_validate_new_entry_accepts(
    entries: list[Entry], kind: EntryKind, amount: int, reverses_id: UUID | None
) -> None:
    assert validate_new_entry(entries, kind, amount, reverses_id) is None


@pytest.mark.parametrize(
    ("entries", "kind", "amount", "reverses_id", "expected"),
    [
        # Overpayment (INV-3).
        ([], PAYMENT, 1, None, Refusal.EXCEEDS_BALANCE),
        ([C1], PAYMENT, 100_001, None, Refusal.EXCEEDS_BALANCE),
        ([C1, P3], PAYMENT, 1, None, Refusal.EXCEEDS_BALANCE),
        ([C1, reversal(2, C1)], PAYMENT, 1, None, Refusal.EXCEEDS_BALANCE),
        ([C1, C2, P3], PAYMENT, 60_001, None, Refusal.EXCEEDS_BALANCE),
        # Amounts are positive whole UZS (INV-4).
        ([C1], CREDIT, 0, None, Refusal.INVALID_AMOUNT),
        ([C1], CREDIT, -5, None, Refusal.INVALID_AMOUNT),
        ([C1], OPENING, 0, None, Refusal.INVALID_AMOUNT),
        ([C1], PAYMENT, 0, None, Refusal.INVALID_AMOUNT),
        ([C1], PAYMENT, -100_000, None, Refusal.INVALID_AMOUNT),
        ([C1], PAYMENT, 1.5, None, Refusal.INVALID_AMOUNT),
        ([C1], PAYMENT, 100.0, None, Refusal.INVALID_AMOUNT),
        ([C1], PAYMENT, True, None, Refusal.INVALID_AMOUNT),
        ([C1], CREDIT, MAX_AMOUNT + 1, None, Refusal.INVALID_AMOUNT),
        ([C1], REVERSAL, 0, C1.id, Refusal.INVALID_AMOUNT),
        # A reversal names an entry of this account; other kinds name none.
        ([C1], REVERSAL, 100_000, None, Refusal.REVERSAL_TARGET_REQUIRED),
        ([C1], REVERSAL, 100_000, uid(999), Refusal.REVERSAL_TARGET_NOT_FOUND),  # another account's entry
        ([], REVERSAL, 100_000, C1.id, Refusal.REVERSAL_TARGET_NOT_FOUND),
        ([C1], PAYMENT, 100_000, C1.id, Refusal.REVERSAL_TARGET_NOT_ALLOWED),
        ([C1], CREDIT, 100_000, C1.id, Refusal.REVERSAL_TARGET_NOT_ALLOWED),
        # INV-5: at most once, and never a reversal.
        ([C1, reversal(2, C1)], REVERSAL, 100_000, C1.id, Refusal.ALREADY_REVERSED),
        ([C1, P3, reversal(4, P3)], REVERSAL, 100_000, P3.id, Refusal.ALREADY_REVERSED),
        ([C1, reversal(2, C1)], REVERSAL, 100_000, uid(2), Refusal.REVERSAL_OF_REVERSAL),
        ([C1, P3, reversal(4, P3)], REVERSAL, 100_000, uid(4), Refusal.REVERSAL_OF_REVERSAL),
        # INV-6: the full amount, nothing else.
        ([C1], REVERSAL, 99_999, C1.id, Refusal.REVERSAL_AMOUNT_MISMATCH),
        ([C1], REVERSAL, 100_001, C1.id, Refusal.REVERSAL_AMOUNT_MISMATCH),
        ([C1, C2, P3], REVERSAL, 60_000, P3.id, Refusal.REVERSAL_AMOUNT_MISMATCH),
        # BR-5: reversing a credit that has since been paid off would leave a negative balance.
        ([C1, P3], REVERSAL, 100_000, C1.id, Refusal.NEGATIVE_BALANCE),
        ([O1, P3], REVERSAL, 100_000, O1.id, Refusal.NEGATIVE_BALANCE),
        ([C1, payment(2, 1)], REVERSAL, 100_000, C1.id, Refusal.NEGATIVE_BALANCE),  # paid in part is enough
        ([C1, C2, P3], REVERSAL, 100_000, C1.id, Refusal.NEGATIVE_BALANCE),  # 60 000 left, 100 000 to remove
        ([C1, C2, payment(3, 60_001)], REVERSAL, 100_000, C1.id, Refusal.NEGATIVE_BALANCE),
    ],
)
def test_validate_new_entry_refuses(
    entries: list[Entry], kind: EntryKind, amount: int, reverses_id: UUID | None, expected: Refusal
) -> None:
    assert validate_new_entry(entries, kind, amount, reverses_id) == expected


def test_refusal_codes_match_the_technical_specification() -> None:
    assert Refusal.EXCEEDS_BALANCE.value == "EXCEEDS_BALANCE"
    assert Refusal.ALREADY_REVERSED.value == "ALREADY_REVERSED"


# ---------------------------------------------------------------------------------------------------------
# Oldest-first allocation (BR-2, BR-3)
# ---------------------------------------------------------------------------------------------------------


def brief(allocations: list[Allocation]) -> list[tuple[int, int, int, datetime | None]]:
    return [(a.seq, a.covered, a.remaining, a.settled_at) for a in allocations]


THREE_CREDITS = [
    credit(1, 100_000, at=tk(3, 2)),
    credit(2, 60_000, at=tk(3, 3)),
    credit(3, 40_000, at=tk(3, 4)),
    payment(4, 30_000, at=tk(3, 5)),
    payment(5, 90_000, at=tk(3, 8)),
    payment(6, 50_000, at=tk(3, 12)),
]


def test_partial_payments_across_three_credits() -> None:
    result = allocate(THREE_CREDITS)
    assert brief(result) == [
        (1, 100_000, 0, tk(3, 8)),  # completed by the second payment
        (2, 60_000, 0, tk(3, 12)),  # 20 000 from the second payment, 40 000 from the third
        (3, 10_000, 30_000, None),
    ]
    assert [(p.payment_id, p.amount, p.paid_at) for p in result[0].parts] == [
        (uid(4), 30_000, tk(3, 5)),
        (uid(5), 70_000, tk(3, 8)),
    ]
    assert [(p.payment_id, p.amount) for p in result[1].parts] == [(uid(5), 20_000), (uid(6), 40_000)]
    assert [(p.payment_id, p.amount) for p in result[2].parts] == [(uid(6), 10_000)]
    assert [a.entry_id for a in result] == [uid(1), uid(2), uid(3)]
    assert all(a.amount == a.covered + a.remaining for a in result)
    assert balance(THREE_CREDITS) == 30_000


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([], []),
        ([credit(1, 100_000)], [(1, 0, 100_000, None)]),
        ([credit(1, 100_000), payment(2, 100_000, at=tk(3, 9))], [(1, 100_000, 0, tk(3, 9))]),
        # The oldest credit is served first even though the payment came between the two sales.
        (
            [credit(1, 100_000), payment(2, 40_000, at=tk(3, 9)), credit(3, 50_000), payment(4, 70_000, at=tk(3, 10))],
            [(1, 100_000, 0, tk(3, 10)), (3, 10_000, 40_000, None)],
        ),
        # Opening balances are debt like any credit sale and take their place by seq.
        (
            [opening(1, 70_000), credit(2, 30_000), payment(3, 80_000, at=tk(3, 9))],
            [(1, 70_000, 0, tk(3, 9)), (2, 10_000, 20_000, None)],
        ),
        (
            [credit(1, 30_000), opening(2, 70_000), payment(3, 80_000, at=tk(3, 9))],
            [(1, 30_000, 0, tk(3, 9)), (2, 50_000, 20_000, None)],
        ),
        # One payment completes two entries: both are settled at its time.
        (
            [credit(1, 10_000), credit(2, 20_000), payment(3, 30_000, at=tk(3, 9))],
            [(1, 10_000, 0, tk(3, 9)), (2, 20_000, 0, tk(3, 9))],
        ),
        # A reversed credit takes no payment: it and its reversal are not in the result at all.
        (
            [credit(1, 100_000), credit(2, 60_000), reversal(3, credit(1, 100_000)), payment(4, 50_000, at=tk(3, 9))],
            [(2, 50_000, 10_000, None)],
        ),
        # A reversed payment no longer covers anything.
        (
            [credit(1, 100_000), credit(2, 50_000), payment(3, 100_000), reversal(4, payment(3, 100_000))],
            [(1, 0, 100_000, None), (2, 0, 50_000, None)],
        ),
        # Reversing the first of two payments re-opens the oldest debt; the surviving payment moves up to it.
        (
            [
                credit(1, 100_000),
                credit(2, 50_000),
                payment(3, 100_000, at=tk(3, 9)),
                payment(4, 30_000, at=tk(3, 10)),
                reversal(5, payment(3, 100_000)),
            ],
            [(1, 30_000, 70_000, None), (2, 0, 50_000, None)],
        ),
        # The payment that covered a credit reversed later flows on to the next debt, recorded after it.
        (
            [
                credit(1, 100_000),
                payment(2, 100_000, at=tk(3, 9)),
                credit(3, 100_000, at=tk(3, 20)),
                reversal(4, credit(1, 100_000)),
            ],
            [(3, 100_000, 0, tk(3, 9))],
        ),
        # Order is by seq, not by clock time: seq 1 is served first although its timestamp is later.
        (
            [credit(1, 50_000, at=tk(3, 20)), credit(2, 50_000, at=tk(3, 10)), payment(3, 50_000, at=tk(3, 21))],
            [(1, 50_000, 0, tk(3, 21)), (2, 0, 50_000, None)],
        ),
    ],
)
def test_allocate(entries: list[Entry], expected: list[tuple[int, int, int, datetime | None]]) -> None:
    assert brief(allocate(entries)) == expected


def test_reversing_a_payment_reopens_the_oldest_debt() -> None:
    paid = [credit(1, 100_000), credit(2, 50_000), payment(3, 100_000, at=tk(3, 9))]
    assert brief(allocate(paid)) == [(1, 100_000, 0, tk(3, 9)), (2, 0, 50_000, None)]
    assert validate_new_entry(paid, REVERSAL, 100_000, uid(3)) is None
    undone = [*paid, reversal(4, paid[2])]
    assert brief(allocate(undone)) == [(1, 0, 100_000, None), (2, 0, 50_000, None)]
    assert balance(undone) == 150_000


def test_reversing_an_unpaid_credit_removes_it() -> None:
    account = [credit(1, 100_000, D(2026, 3, 20)), credit(2, 60_000)]
    assert validate_new_entry(account, REVERSAL, 100_000, uid(1)) is None
    undone = [*account, reversal(3, account[0])]
    assert balance(undone) == 60_000
    assert brief(allocate(undone)) == [(2, 0, 60_000, None)]
    assert not overdue(undone, TODAY).is_overdue


def test_allocation_carries_the_current_promise_and_dispute_flag() -> None:
    result = allocate([credit(1, 10_000, D(2026, 3, 20), disputed=True), opening(2, 5_000, D(2026, 5, 1))])
    assert [(a.promised_date, a.disputed) for a in result] == [(D(2026, 3, 20), True), (D(2026, 5, 1), False)]


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        (tk(3, 1), []),  # before the first sale
        (tk(3, 2), [(1, 0, 100_000, None)]),  # the instant itself is included
        (tk(3, 4, 18), [(1, 0, 100_000, None), (2, 0, 60_000, None), (3, 0, 40_000, None)]),
        (tk(3, 5), [(1, 30_000, 70_000, None), (2, 0, 60_000, None), (3, 0, 40_000, None)]),
        (tk(3, 11), [(1, 100_000, 0, tk(3, 8)), (2, 20_000, 40_000, None), (3, 0, 40_000, None)]),
        (tk(3, 12), [(1, 100_000, 0, tk(3, 8)), (2, 60_000, 0, tk(3, 12)), (3, 10_000, 30_000, None)]),
        # The same instant written in UTC.
        (
            datetime(2026, 3, 5, 7, 0, tzinfo=UTC),
            [(1, 30_000, 70_000, None), (2, 0, 60_000, None), (3, 0, 40_000, None)],
        ),
        (datetime(2026, 3, 5, 6, 59, tzinfo=UTC), [(1, 0, 100_000, None), (2, 0, 60_000, None), (3, 0, 40_000, None)]),
    ],
)
def test_allocate_as_of(as_of: datetime, expected: list[tuple[int, int, int, datetime | None]]) -> None:
    assert brief(allocate(THREE_CREDITS, as_of)) == expected


def test_allocate_as_of_still_ignores_entries_reversed_later() -> None:
    wrong = payment(2, 100_000, at=tk(3, 5))
    account = [credit(1, 100_000, at=tk(3, 2)), wrong, reversal(3, wrong, at=tk(3, 20))]
    assert brief(allocate(account, tk(3, 10))) == [(1, 0, 100_000, None)]


def test_allocate_rejects_a_naive_as_of() -> None:
    with pytest.raises(ValueError):
        allocate(THREE_CREDITS, datetime(2026, 3, 5, 12, 0))


# ---------------------------------------------------------------------------------------------------------
# Overdue status (BR-4, BR-13)
# ---------------------------------------------------------------------------------------------------------

NOT_OVERDUE = OverdueStatus(
    overdue_amount=0,
    earliest_unmet_promised_date=None,
    days_overdue=0,
    due_today_amount=0,
    reminder_overdue_amount=0,
    reminder_due_today_amount=0,
)


def status(
    overdue_amount: int,
    earliest: date | None,
    days: int,
    due_today: int = 0,
    reminder: int | None = None,
    reminder_today: int | None = None,
) -> OverdueStatus:
    return OverdueStatus(
        overdue_amount=overdue_amount,
        earliest_unmet_promised_date=earliest,
        days_overdue=days,
        due_today_amount=due_today,
        reminder_overdue_amount=overdue_amount if reminder is None else reminder,
        reminder_due_today_amount=due_today if reminder_today is None else reminder_today,
    )


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([], NOT_OVERDUE),
        ([credit(1, 100_000, D(2026, 4, 11))], NOT_OVERDUE),  # promised for tomorrow
        ([credit(1, 100_000, D(2026, 4, 10))], status(0, None, 0, due_today=100_000)),  # today is not late yet
        ([credit(1, 100_000, D(2026, 4, 9))], status(100_000, D(2026, 4, 9), 1)),  # yesterday
        ([opening(1, 100_000, D(2026, 3, 11))], status(100_000, D(2026, 3, 11), 30)),
        ([credit(1, 100_000, D(2025, 4, 10))], status(100_000, D(2025, 4, 10), 365)),
        # Paid in full: nothing is unmet whatever the date.
        ([credit(1, 100_000, D(2026, 3, 1)), payment(2, 100_000)], NOT_OVERDUE),
        # Paid in part: only the uncovered remainder is overdue.
        ([credit(1, 100_000, D(2026, 3, 31)), payment(2, 35_000)], status(65_000, D(2026, 3, 31), 10)),
        ([credit(1, 100_000, D(2026, 4, 10)), payment(2, 35_000)], status(0, None, 0, due_today=65_000)),
        # Three credits: the first is covered, the second in part, the third is due today.
        (
            [
                credit(1, 100_000, D(2026, 4, 1)),
                credit(2, 50_000, D(2026, 3, 20)),
                credit(3, 70_000, D(2026, 4, 10)),
                payment(4, 120_000),
            ],
            status(30_000, D(2026, 3, 20), 21, due_today=70_000),
        ),
        # The earliest unmet promise decides the days, not the oldest entry.
        (
            [credit(1, 10_000, D(2026, 4, 5)), credit(2, 20_000, D(2026, 3, 1)), credit(3, 5_000, D(2026, 5, 1))],
            status(30_000, D(2026, 3, 1), 40),
        ),
        # Oldest first: the payment goes to the older credit, whose promise is still ahead, so the newer
        # credit with a passed promise stays uncovered and overdue.
        (
            [credit(1, 100_000, D(2026, 5, 1)), credit(2, 100_000, D(2026, 4, 1)), payment(3, 100_000)],
            status(100_000, D(2026, 4, 1), 9),
        ),
        # A reversed overdue credit no longer counts.
        ([credit(1, 100_000, D(2026, 3, 1)), reversal(2, credit(1, 100_000, D(2026, 3, 1)))], NOT_OVERDUE),
        # A reversed payment covers nothing: the debt is overdue again.
        (
            [credit(1, 100_000, D(2026, 4, 1)), payment(2, 100_000), reversal(3, payment(2, 100_000))],
            status(100_000, D(2026, 4, 1), 9),
        ),
        # BR-13: an entry under open dispute is overdue and in the balance, but left out of the reminder amount.
        (
            [credit(1, 100_000, D(2026, 4, 1), disputed=True), credit(2, 40_000, D(2026, 4, 5))],
            status(140_000, D(2026, 4, 1), 9, reminder=40_000),
        ),
        ([credit(1, 100_000, D(2026, 4, 1), disputed=True)], status(100_000, D(2026, 4, 1), 9, reminder=0)),
        (
            [credit(1, 100_000, D(2026, 4, 1), disputed=True), payment(2, 30_000)],
            status(70_000, D(2026, 4, 1), 9, reminder=0),
        ),
        (
            [opening(1, 100_000, D(2026, 4, 10), disputed=True), credit(2, 5_000, D(2026, 4, 10))],
            status(0, None, 0, due_today=105_000, reminder_today=5_000),
        ),
        # Disputed and fully paid: nothing to leave out.
        ([credit(1, 100_000, D(2026, 4, 1), disputed=True), payment(2, 100_000)], NOT_OVERDUE),
    ],
)
def test_overdue(entries: list[Entry], expected: OverdueStatus) -> None:
    result = overdue(entries, TODAY)
    assert result == expected
    assert result.is_overdue == (expected.overdue_amount > 0)


def test_a_disputed_overdue_entry_is_left_out_of_reminders_but_not_of_the_balance() -> None:
    account = [credit(1, 100_000, D(2026, 4, 1), disputed=True), credit(2, 40_000, D(2026, 4, 5))]
    result = overdue(account, TODAY)
    assert balance(account) == 140_000
    assert result.is_overdue
    assert result.overdue_amount == 140_000
    assert result.reminder_overdue_amount == 40_000
    # Once the dispute is declined the entry counts again.
    declined = [replace(account[0], disputed=False), account[1]]
    assert overdue(declined, TODAY).reminder_overdue_amount == 140_000


def test_overdue_follows_the_current_promised_date() -> None:
    late = [credit(1, 100_000, D(2026, 4, 1))]
    assert overdue(late, TODAY).is_overdue
    moved = [replace(late[0], promised_date=D(2026, 4, 20))]  # the shop agreed to a later date
    assert overdue(moved, TODAY) == NOT_OVERDUE


@pytest.mark.parametrize(
    ("today", "days", "due_today"),
    [
        (D(2026, 3, 31), 0, 0),
        (D(2026, 4, 1), 0, 100_000),
        (D(2026, 4, 2), 1, 0),
        (D(2026, 5, 1), 30, 0),
    ],
)
def test_overdue_by_day(today: date, days: int, due_today: int) -> None:
    result = overdue([credit(1, 100_000, D(2026, 4, 1))], today)
    assert (result.days_overdue, result.due_today_amount, result.is_overdue) == (days, due_today, days > 0)


# ---------------------------------------------------------------------------------------------------------
# Payment history indicator (BR-9)
# ---------------------------------------------------------------------------------------------------------

DUE = D(2026, 3, 10)


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [credit(1, 100_000, D(2026, 4, 11))],
        [credit(1, 100_000, TODAY)],  # due today has not passed
        [credit(1, 100_000, TODAY), payment(2, 100_000)],
        [credit(1, 100_000, DUE), reversal(2, credit(1, 100_000, DUE))],  # the only due credit was reversed
    ],
)
def test_payment_history_is_absent_when_nothing_is_yet_due(entries: list[Entry]) -> None:
    assert payment_history(entries, TODAY) is None


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        # Paid early, paid on the day, paid late, never paid.
        ([credit(1, 100_000, DUE), payment(2, 100_000, at=tk(3, 5))], (100_000, 100_000, 100, 0)),
        ([credit(1, 100_000, DUE), payment(2, 100_000, at=tk(3, 10))], (100_000, 100_000, 100, 0)),
        ([credit(1, 100_000, DUE), payment(2, 100_000, at=tk(3, 11))], (0, 100_000, 0, 1)),
        ([credit(1, 100_000, DUE)], (0, 100_000, 0, 31)),
        ([opening(1, 100_000, D(2026, 4, 9))], (0, 100_000, 0, 1)),
        # Partly on time, the rest five days late.
        (
            [credit(1, 300_000, DUE), payment(2, 100_000, at=tk(3, 9)), payment(3, 200_000, at=tk(3, 15))],
            (100_000, 300_000, 33, 5),
        ),
        # Partly on time, the rest still unpaid: the delay runs to today.
        ([credit(1, 300_000, DUE), payment(2, 200_000, at=tk(3, 9))], (200_000, 300_000, 67, 31)),
        # Debt not yet due is outside the indicator, paid or not.
        (
            [credit(1, 100_000, DUE), credit(2, 500_000, D(2026, 5, 1)), payment(3, 100_000, at=tk(3, 10))],
            (100_000, 100_000, 100, 0),
        ),
        (
            [credit(1, 500_000, D(2026, 5, 1)), credit(2, 100_000, DUE), payment(3, 500_000, at=tk(3, 10))],
            (0, 100_000, 0, 31),  # oldest first: the payment went to the credit that is not yet due
        ),
        # Oldest first with payment times: the early payment serves seq 1, so seq 2 is late.
        (
            [credit(1, 100_000, DUE), credit(2, 100_000, D(2026, 3, 5)), payment(3, 100_000, at=tk(3, 4))],
            (100_000, 200_000, 50, 36),
        ),
        # The longest delay is the worst entry, settled or not.
        (
            [
                credit(1, 50_000, D(2026, 3, 1)),
                credit(2, 50_000, D(2026, 3, 20)),
                credit(3, 50_000, D(2026, 4, 8)),
                payment(4, 50_000, at=tk(3, 15)),  # 14 days late
                payment(5, 50_000, at=tk(3, 20)),  # on time
            ],
            (50_000, 150_000, 33, 14),
        ),
        # A reversed payment covered nothing; a reversed credit was never due.
        (
            [credit(1, 100_000, DUE), payment(2, 100_000, at=tk(3, 9)), reversal(3, payment(2, 100_000))],
            (0, 100_000, 0, 31),
        ),
        (
            [
                credit(1, 900_000, D(2026, 3, 1)),
                reversal(2, credit(1, 900_000, D(2026, 3, 1))),
                credit(3, 100_000, DUE),
                payment(4, 100_000, at=tk(3, 10)),
            ],
            (100_000, 100_000, 100, 0),
        ),
        # An entry under open dispute still counts as due.
        ([credit(1, 100_000, DUE, disputed=True)], (0, 100_000, 0, 31)),
        # The percentage rounds halves up; the exact fraction stays available.
        ([credit(1, 3, DUE), payment(2, 1, at=tk(3, 9))], (1, 3, 33, 31)),
        ([credit(1, 3, DUE), payment(2, 2, at=tk(3, 9))], (2, 3, 67, 31)),
        ([credit(1, 200, DUE), payment(2, 1, at=tk(3, 9))], (1, 200, 1, 31)),  # 0.5 % rounds up
        ([credit(1, 200, DUE), payment(2, 199, at=tk(3, 9))], (199, 200, 100, 31)),  # 99.5 % rounds up
        ([credit(1, 1_000, DUE), payment(2, 4, at=tk(3, 9))], (4, 1_000, 0, 31)),  # 0.4 % rounds down
        ([credit(1, 1_000, DUE), payment(2, 994, at=tk(3, 9))], (994, 1_000, 99, 31)),
    ],
)
def test_payment_history(entries: list[Entry], expected: tuple[int, int, int, int]) -> None:
    assert payment_history(entries, TODAY) == PaymentHistory(*expected)


@pytest.mark.parametrize(
    ("paid_at", "expected"),
    [
        # 23:59 Tashkent time on the promised date, 10 March: on time.
        (datetime(2026, 3, 10, 18, 59, tzinfo=UTC), (100_000, 100_000, 100, 0)),
        (datetime(2026, 3, 10, 18, 59, 59, 999_999, tzinfo=UTC), (100_000, 100_000, 100, 0)),
        # 00:01 Tashkent time on 11 March, still 10 March in UTC: one day late.
        (datetime(2026, 3, 10, 19, 1, tzinfo=UTC), (0, 100_000, 0, 1)),
        (datetime(2026, 3, 10, 19, 0, tzinfo=UTC), (0, 100_000, 0, 1)),  # midnight belongs to the next day
        # 00:30 Tashkent time on 10 March is still 9 March in UTC: on time, on the day.
        (datetime(2026, 3, 9, 19, 30, tzinfo=UTC), (100_000, 100_000, 100, 0)),
    ],
)
def test_payment_on_the_promised_date_is_on_time_in_tashkent(
    paid_at: datetime, expected: tuple[int, int, int, int]
) -> None:
    account = [credit(1, 100_000, DUE), payment(2, 100_000, at=paid_at)]
    assert payment_history(account, TODAY) == PaymentHistory(*expected)


def test_payment_history_follows_the_current_promised_date() -> None:
    """The functions receive the current promise only: changing it after a payment changes the verdict."""
    account = [credit(1, 100_000, D(2026, 3, 10)), payment(2, 100_000, at=tk(3, 12))]
    assert payment_history(account, TODAY) == PaymentHistory(0, 100_000, 0, 2)
    moved_later = [replace(account[0], promised_date=D(2026, 3, 15)), account[1]]
    assert payment_history(moved_later, TODAY) == PaymentHistory(100_000, 100_000, 100, 0)
    moved_earlier = [replace(account[0], promised_date=D(2026, 3, 8)), account[1]]
    assert payment_history(moved_earlier, TODAY) == PaymentHistory(0, 100_000, 0, 4)
    moved_ahead = [replace(account[0], promised_date=D(2026, 4, 20)), account[1]]
    assert payment_history(moved_ahead, TODAY) is None


# ---------------------------------------------------------------------------------------------------------
# Lists that are not a valid account history
# ---------------------------------------------------------------------------------------------------------

CORRUPT: dict[str, list[Entry]] = {
    "duplicate seq": [credit(1, 100), replace(credit(2, 100), seq=1)],
    "duplicate id": [credit(1, 100), replace(credit(2, 100), id=uid(1))],
    "reversal of an entry that is not in the account": [credit(1, 100), reversal(2, credit(9, 100))],
    "reversal recorded before its target": [reversal(1, credit(2, 100)), credit(2, 100)],
    "reversal of itself": [credit(1, 100), replace(reversal(2, credit(1, 100)), reverses_id=uid(2))],
    "reversal of a reversal": [credit(1, 100), reversal(2, credit(1, 100)), reversal(3, reversal(2, credit(1, 100)))],
    "entry reversed twice": [credit(1, 100), reversal(2, credit(1, 100)), reversal(3, credit(1, 100))],
    "reversal with a different amount": [credit(1, 100), reversal(2, credit(1, 100), amount=99)],
    "reversal without a target": [credit(1, 100), replace(reversal(2, credit(1, 100)), reverses_id=None)],
    "credit that refers to another entry": [credit(1, 100), replace(credit(2, 100), reverses_id=uid(1))],
    "zero amount": [credit(1, 0)],
    "negative amount": [credit(1, 100), payment(2, -5)],
    "amount that is not whole": [credit(1, 100.5)],  # type: ignore[arg-type]
    "amount beyond bigint": [credit(1, MAX_AMOUNT + 1)],
    "seq that is not whole": [replace(credit(1, 100), seq=1.0)],  # type: ignore[arg-type]
    "naive timestamp": [credit(1, 100, at=datetime(2026, 3, 2, 9, 0))],
    "credit without a promised date": [credit(1, 100, None)],
    "opening balance without a promised date": [opening(1, 100, None)],
    "payment with a promised date": [credit(1, 100), replace(payment(2, 50), promised_date=LATER)],
    "disputed payment": [credit(1, 100), replace(payment(2, 50), disputed=True)],
    "disputed reversal": [credit(1, 100), replace(reversal(2, credit(1, 100)), disputed=True)],
}

CALLS: dict[str, Callable[[list[Entry]], object]] = {
    "balance": balance,
    "allocate": allocate,
    "overdue": lambda entries: overdue(entries, TODAY),
    "payment_history": lambda entries: payment_history(entries, TODAY),
    "validate_new_entry": lambda entries: validate_new_entry(entries, CREDIT, 1),
}


@pytest.mark.parametrize("case", sorted(CORRUPT))
@pytest.mark.parametrize("call", sorted(CALLS))
def test_corrupt_ledgers_are_rejected_by_every_function(call: str, case: str) -> None:
    with pytest.raises(LedgerIntegrityError):
        CALLS[call](CORRUPT[case])


def test_integrity_error_is_a_value_error() -> None:
    assert issubclass(LedgerIntegrityError, ValueError)


def test_seq_may_have_gaps_and_need_not_start_at_one() -> None:
    account = [credit(40, 100), payment(7_000, 30), credit(12, 50)]
    assert balance(account) == 120
    assert brief(allocate(account)) == [(12, 30, 20, None), (40, 0, 100, None)]


# ---------------------------------------------------------------------------------------------------------
# Properties over generated accounts
# ---------------------------------------------------------------------------------------------------------

DEBT_KINDS = (CREDIT, OPENING)


@dataclass(frozen=True)
class Attempt:
    """One proposed entry, with the verdict of the naive model below; accepted ones are in the account."""

    position: int  # how many entries the account held when it was proposed
    kind: EntryKind
    amount: int
    reverses_id: UUID | None
    verdict: Refusal | None


@dataclass(frozen=True)
class Generated:
    entries: tuple[Entry, ...]
    attempts: tuple[Attempt, ...]
    today: date


def naive_balance(entries: list[Entry] | tuple[Entry, ...]) -> int:
    """INV-2 written the long way round, independent of the implementation."""
    reversed_ids = {e.reverses_id for e in entries if e.kind == REVERSAL}
    total = 0
    for e in entries:
        if e.kind == REVERSAL or e.id in reversed_ids:
            continue
        total += e.amount if e.kind in DEBT_KINDS else -e.amount
    return total


def naive_verdict(entries: list[Entry], kind: EntryKind, amount: int, reverses_id: UUID | None) -> Refusal | None:
    current = naive_balance(entries)
    if kind == PAYMENT:
        return Refusal.EXCEEDS_BALANCE if amount > current else None
    if kind != REVERSAL:
        return None
    target = next(e for e in entries if e.id == reverses_id)
    if target.kind == REVERSAL:
        return Refusal.REVERSAL_OF_REVERSAL
    if any(e.reverses_id == target.id for e in entries):
        return Refusal.ALREADY_REVERSED
    if amount != target.amount:
        return Refusal.REVERSAL_AMOUNT_MISMATCH
    if target.kind in DEBT_KINDS and current - target.amount < 0:
        return Refusal.NEGATIVE_BALANCE
    return None


def generate(rng: random.Random) -> Generated:
    """Grow an account by proposing random operations, valid and invalid, and keeping the valid ones."""
    entries: list[Entry] = []
    attempts: list[Attempt] = []
    now = datetime(2026, 1, 1, 4, 0, tzinfo=UTC)
    seq = 0
    for _ in range(rng.randint(1, 45)):
        now += timedelta(minutes=rng.randint(1, 4 * 24 * 60))
        seq += rng.randint(1, 3)
        current = naive_balance(entries)
        roll = rng.random()
        reverses_id: UUID | None = None
        if roll < 0.35 or not entries:
            kind = OPENING if rng.random() < 0.15 else CREDIT
            amount = rng.choice([1, 500, 1_000, 12_500, 100_000]) * rng.randint(1, 9)
        elif roll < 0.72:
            kind = PAYMENT
            # Usually within the balance, sometimes exactly it, sometimes beyond it.
            amount = rng.choice([current, current + rng.randint(1, 1_000), rng.randint(1, max(1, current))]) or 1
        else:
            kind = REVERSAL
            target = rng.choice(entries)
            reverses_id = target.id
            amount = target.amount + (1 if rng.random() < 0.1 else 0)
        verdict = naive_verdict(entries, kind, amount, reverses_id)
        attempts.append(Attempt(len(entries), kind, amount, reverses_id, verdict))
        if verdict is None:
            is_debt = kind in DEBT_KINDS
            entries.append(
                Entry(
                    id=uid(seq),
                    seq=seq,
                    kind=kind,
                    amount=amount,
                    created_at=now,
                    reverses_id=reverses_id,
                    promised_date=tashkent_date(now) + timedelta(days=rng.randint(0, 40)) if is_debt else None,
                    disputed=is_debt and rng.random() < 0.15,
                )
            )
    today = tashkent_date(now) + timedelta(days=rng.randint(-30, 30))
    return Generated(tuple(entries), tuple(attempts), today)


@pytest.fixture(scope="module")
def accounts() -> list[Generated]:
    rng = random.Random(20261006)  # noqa: S311 - a reproducible test generator, not a secret
    return [generate(rng) for _ in range(400)]


def test_the_generator_exercises_every_rule(accounts: list[Generated]) -> None:
    verdicts = [a.verdict for g in accounts for a in g.attempts]
    kinds = [e.kind for g in accounts for e in g.entries]
    for kind in EntryKind:
        assert kinds.count(kind) > 200
    for refusal in (
        Refusal.EXCEEDS_BALANCE,
        Refusal.REVERSAL_OF_REVERSAL,
        Refusal.ALREADY_REVERSED,
        Refusal.REVERSAL_AMOUNT_MISMATCH,
        Refusal.NEGATIVE_BALANCE,
    ):
        assert verdicts.count(refusal) > 30
    reversed_kinds = {
        next(t.kind for t in g.entries if t.id == e.reverses_id) for g in accounts for e in g.entries if e.reverses_id
    }
    assert reversed_kinds == {CREDIT, OPENING, PAYMENT}
    assert any(payment_history(g.entries, g.today) is not None for g in accounts)
    assert sum(overdue(g.entries, g.today).is_overdue for g in accounts) > 50
    assert sum(any(e.disputed for e in g.entries) for g in accounts) > 50


def test_property_validation_agrees_with_the_naive_model_at_every_step(accounts: list[Generated]) -> None:
    for g in accounts:
        for a in g.attempts:
            prefix = g.entries[: a.position]
            assert validate_new_entry(prefix, a.kind, a.amount, a.reverses_id) == a.verdict


def test_property_balance_is_never_negative(accounts: list[Generated]) -> None:
    for g in accounts:
        for length in range(len(g.entries) + 1):
            prefix = g.entries[:length]
            assert balance(prefix) == naive_balance(prefix) >= 0


def test_property_balance_equals_the_sum_of_remaining(accounts: list[Generated]) -> None:
    for g in accounts:
        for length in range(len(g.entries) + 1):
            prefix = g.entries[:length]
            allocations = allocate(prefix)
            assert sum(a.remaining for a in allocations) == balance(prefix)
            assert all(a.covered + a.remaining == a.amount and a.covered >= 0 for a in allocations)


def test_property_total_covered_equals_total_payments_not_reversed(accounts: list[Generated]) -> None:
    for g in accounts:
        reversed_ids = {e.reverses_id for e in g.entries if e.kind == REVERSAL}
        payments = [e for e in g.entries if e.kind == PAYMENT and e.id not in reversed_ids]
        allocations = allocate(g.entries)
        assert sum(a.covered for a in allocations) == sum(p.amount for p in payments)
        # Every payment is spent whole, and each part names the payment it came from.
        spent: dict[UUID, int] = {}
        for a in allocations:
            assert sum(p.amount for p in a.parts) == a.covered
            for p in a.parts:
                spent[p.payment_id] = spent.get(p.payment_id, 0) + p.amount
        assert spent == {p.id: p.amount for p in payments}


def test_property_allocation_is_oldest_first(accounts: list[Generated]) -> None:
    for g in accounts:
        reversed_ids = {e.reverses_id for e in g.entries if e.kind == REVERSAL}
        live = [e for e in g.entries if e.kind != REVERSAL and e.id not in reversed_ids]
        pool = sum(e.amount for e in live if e.kind == PAYMENT)
        expected = []
        for debt in sorted((e for e in live if e.kind in DEBT_KINDS), key=lambda e: e.seq):
            covered = min(debt.amount, pool)
            pool -= covered
            expected.append((debt.id, covered))
        allocations = allocate(g.entries)
        assert [(a.entry_id, a.covered) for a in allocations] == expected
        # No later entry receives anything while an earlier one is short.
        for earlier, later in pairwise(allocations):
            assert earlier.remaining == 0 or later.covered == 0
        for a in allocations:
            assert (a.settled_at is not None) == (a.remaining == 0)


def test_property_reversing_an_entry_restores_the_balance_before_it(accounts: list[Generated]) -> None:
    after = datetime(2027, 6, 1, tzinfo=UTC)
    for g in accounts:
        # The entry just recorded can always be reversed, and that undoes it exactly.
        for length in range(1, len(g.entries) + 1):
            prefix, last = g.entries[:length], g.entries[length - 1]
            if last.kind == REVERSAL:
                continue
            assert validate_new_entry(prefix, REVERSAL, last.amount, last.id) is None
            undo = Entry(uid(10**9), last.seq + 1, REVERSAL, last.amount, after, reverses_id=last.id)
            assert balance([*prefix, undo]) == balance(g.entries[: length - 1])
        # Any earlier entry: when the reversal is allowed, the balance moves by exactly that entry's amount.
        current = balance(g.entries)
        reversed_ids = {e.reverses_id for e in g.entries if e.kind == REVERSAL}
        for e in g.entries:
            verdict = validate_new_entry(g.entries, REVERSAL, e.amount, e.id)
            if e.kind == REVERSAL or e.id in reversed_ids:
                assert verdict in (Refusal.REVERSAL_OF_REVERSAL, Refusal.ALREADY_REVERSED)
                continue
            effect = e.amount if e.kind in DEBT_KINDS else -e.amount
            if verdict is None:
                undo = Entry(uid(10**9), g.entries[-1].seq + 1, REVERSAL, e.amount, after, reverses_id=e.id)
                assert balance([*g.entries, undo]) == current - effect >= 0
            else:
                assert verdict == Refusal.NEGATIVE_BALANCE
                assert current - effect < 0


def test_property_input_order_does_not_matter(accounts: list[Generated]) -> None:
    rng = random.Random(7)  # noqa: S311 - a reproducible test generator, not a secret
    for g in accounts:
        shuffled = list(g.entries)
        rng.shuffle(shuffled)
        middle = g.entries[len(g.entries) // 2].created_at
        assert balance(shuffled) == balance(g.entries)
        assert allocate(shuffled) == allocate(g.entries)
        assert allocate(shuffled, middle) == allocate(g.entries, middle)
        assert overdue(shuffled, g.today) == overdue(g.entries, g.today)
        assert payment_history(shuffled, g.today) == payment_history(g.entries, g.today)
        for a in g.attempts[-3:]:
            if a.position == len(g.entries):
                assert validate_new_entry(shuffled, a.kind, a.amount, a.reverses_id) == a.verdict
        assert balance(iter(shuffled)) == balance(tuple(reversed(g.entries)))  # any iterable, any order


def test_property_duplicate_seq_is_rejected(accounts: list[Generated]) -> None:
    for g in accounts:
        twin = replace(credit(10**6, 5), seq=g.entries[len(g.entries) // 2].seq)
        for call in CALLS.values():
            with pytest.raises(LedgerIntegrityError):
                call([*g.entries, twin])


def test_property_overdue_and_history_stay_within_bounds(accounts: list[Generated]) -> None:
    for g in accounts:
        total = balance(g.entries)
        for today in (g.today, g.today + timedelta(days=45), g.today - timedelta(days=400)):
            status_ = overdue(g.entries, today)
            assert 0 <= status_.reminder_overdue_amount <= status_.overdue_amount
            assert 0 <= status_.reminder_due_today_amount <= status_.due_today_amount
            assert status_.overdue_amount + status_.due_today_amount <= total
            assert (
                status_.is_overdue == (status_.days_overdue > 0) == (status_.earliest_unmet_promised_date is not None)
            )
            history = payment_history(g.entries, today)
            if history is None:
                assert not status_.is_overdue
                continue
            assert 0 <= history.on_time_amount <= history.due_amount
            assert 0 <= history.on_time_percent <= 100
            assert history.longest_delay_days >= status_.days_overdue
            assert (history.on_time_amount == history.due_amount) <= (history.longest_delay_days == 0)


def test_property_allocate_as_of_never_covers_more_than_was_paid_by_then(accounts: list[Generated]) -> None:
    for g in accounts:
        reversed_ids = {e.reverses_id for e in g.entries if e.kind == REVERSAL}
        for e in g.entries:
            as_of = e.created_at
            allocations = allocate(g.entries, as_of)
            paid = sum(
                p.amount for p in g.entries if p.kind == PAYMENT and p.id not in reversed_ids and p.created_at <= as_of
            )
            assert sum(a.covered for a in allocations) <= paid
            assert all(p.paid_at <= as_of for a in allocations for p in a.parts)
        assert allocate(g.entries, datetime(2030, 1, 1, tzinfo=UTC)) == allocate(g.entries)


# --- the time a new entry is written with -----------------------------------------------------------------


def test_a_new_entry_is_never_dated_before_the_accounts_last_entry() -> None:
    """Two writers at once: the one that read its clock first may be numbered second."""
    last = credit(2, 5_000, at=BASE + timedelta(seconds=10))
    account = [credit(1, 5_000, at=BASE), last]
    # Read before the other writer's, written after it: it takes the other's time.
    assert not_before(account, BASE + timedelta(seconds=9)) == last.created_at
    # The ordinary case, and an empty account, keep the clock's time.
    assert not_before(account, BASE + timedelta(seconds=11)) == BASE + timedelta(seconds=11)
    assert not_before([], BASE) == BASE
    # The latest time counts, not the last in the list.
    assert not_before(list(reversed(account)), BASE) == last.created_at


def test_a_book_below_zero_is_read_and_not_rejected() -> None:
    """It used to be corrupt data. Since advances it is a state a book can be in (INV-3): whether a NEW
    entry may put it there is asked of `validate_new_entry`, which still refuses by default
    (tests/test_ledger_advances.py)."""
    for entries in (
        [payment(1, 50), credit(2, 100)],
        [credit(1, 100), payment(2, 101)],
        [credit(1, 100), payment(2, 100), reversal(3, credit(1, 100))],
    ):
        for call in CALLS.values():
            call(entries)
    assert balance([credit(1, 100), payment(2, 101)]) == -1
    assert validate_new_entry([credit(1, 100)], PAYMENT, 101) == Refusal.EXCEEDS_BALANCE
