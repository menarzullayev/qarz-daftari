"""Everything derived from a customer account's ledger entries (DOM-003).

A customer account is a strictly ordered list of immutable entries: credit sales, opening balances, payments,
and reversals. Balance, the oldest-first allocation of payments, overdue status, and the payment history
indicator are all calculated here from that list and never stored (INV-2, BR-3, BR-4, BR-9, BR-13).

Conventions shared by every function:

- Entries may be passed in any order; they are sorted by `seq`.
- A reversed entry and its reversal cancel out and take no further part in any calculation (INV-2).
- A list that could not have been produced by valid operations (duplicate `seq`, a reversal of a reversal,
  ...) raises `LedgerIntegrityError`: that is corrupt data or a programming error, never a business
  refusal. Refusals of a proposed entry are returned as a `Refusal` value.
- A balance below zero is an advance: the customer has paid more than they owe, and the shop owes them
  (the founder's decision of 2026-10-10; INV-3). It is a state valid data can be in, so nothing here
  raises on it. Whether a NEW entry may take a book below zero is the shop's choice ("accept advances"),
  asked of `validate_new_entry` by its `advance_cap`; `lowest_balance` says whether a book ever was.
- An advance is not a kind of entry and is never stored: it is what the payments exceed the debts by.
  The oldest-first allocation therefore uses it up by itself: a credit sale recorded while the customer
  is in credit is covered, wholly or partly, by the payment that ran ahead, from the moment the sale
  is written, and a reversal of either entry lets the allocation fall back to what the entries that
  remain give.
- The entries passed are of ONE currency. An account may hold so'm and dollars; they are two books that
  share one sequence, and nothing here ever adds one to the other. `in_currency` picks one book out of
  an account, `currencies_of` says which books it has, and a list that mixes currencies raises
  `LedgerIntegrityError` like any other list no valid operation could have produced.
- `promised_date` is the entry's CURRENT promised date (the latest `promise` row). The calculations never see
  the history of promise changes, so moving a promise changes overdue status and the payment history
  indicator from then on, including for debt that was already paid.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from qarz.domain.money import DEFAULT, Currency
from qarz.domain.promise import tashkent_date

# `ledger_entry.amount` is a PostgreSQL bigint.
MAX_AMOUNT = 2**63 - 1


class EntryKind(StrEnum):
    CREDIT = "credit"
    OPENING = "opening"
    PAYMENT = "payment"
    REVERSAL = "reversal"


_DEBT_KINDS = frozenset({EntryKind.CREDIT, EntryKind.OPENING})


@dataclass(frozen=True, slots=True)
class Entry:
    """One `ledger_entry` row, joined with its current promise and whether an open dispute exists on it.

    `promised_date` is the current promised date and is required for credit and opening entries (INV-9,
    BR-24); payments and reversals have none. `disputed` may be set only on credit and opening entries.
    `amount` is in the minor unit of `currency`: whole so'm, or whole cents (qarz.domain.money).
    """

    id: UUID
    seq: int
    kind: EntryKind
    amount: int
    created_at: datetime
    reverses_id: UUID | None = None
    promised_date: date | None = None
    disputed: bool = False
    currency: Currency = DEFAULT


class LedgerIntegrityError(ValueError):
    """The entries cannot be the history of one account built by valid operations."""


class Refusal(StrEnum):
    """Why a proposed entry is refused. Values are the API's domain error codes where the specification names one."""

    INVALID_AMOUNT = "INVALID_AMOUNT"
    EXCEEDS_BALANCE = "EXCEEDS_BALANCE"
    REVERSAL_TARGET_REQUIRED = "REVERSAL_TARGET_REQUIRED"
    REVERSAL_TARGET_NOT_ALLOWED = "REVERSAL_TARGET_NOT_ALLOWED"
    REVERSAL_TARGET_NOT_FOUND = "REVERSAL_TARGET_NOT_FOUND"
    REVERSAL_OF_REVERSAL = "REVERSAL_OF_REVERSAL"
    ALREADY_REVERSED = "ALREADY_REVERSED"
    REVERSAL_AMOUNT_MISMATCH = "REVERSAL_AMOUNT_MISMATCH"
    NEGATIVE_BALANCE = "NEGATIVE_BALANCE"
    ADVANCE_TOO_LARGE = "ADVANCE_TOO_LARGE"


@dataclass(frozen=True, slots=True)
class Coverage:
    """Part of one payment applied to one debt-increasing entry."""

    payment_id: UUID
    amount: int
    paid_at: datetime


@dataclass(frozen=True, slots=True)
class Allocation:
    """How far payments cover one credit or opening entry. `covered + remaining == amount`."""

    entry_id: UUID
    seq: int
    amount: int
    covered: int
    remaining: int
    settled_at: datetime | None  # created_at of the payment that completed it
    promised_date: date
    disputed: bool
    parts: tuple[Coverage, ...]


@dataclass(frozen=True, slots=True)
class OverdueStatus:
    """BR-4 and BR-13. Amounts are what is still uncovered after allocation.

    The `reminder_*` amounts leave out entries under open dispute and are what a reminder may mention;
    the other fields, like the balance, include them.
    """

    overdue_amount: int
    earliest_unmet_promised_date: date | None  # among overdue entries; None when nothing is overdue
    days_overdue: int  # today minus that date; 0 when nothing is overdue
    due_today_amount: int
    reminder_overdue_amount: int
    reminder_due_today_amount: int

    @property
    def is_overdue(self) -> bool:
        return self.overdue_amount > 0


@dataclass(frozen=True, slots=True)
class PaymentHistory:
    """BR-9. The on-time share is exactly `on_time_amount / due_amount`, both of one currency."""

    on_time_amount: int
    due_amount: int
    on_time_percent: int  # the share as a whole percentage, halves rounded up
    longest_delay_days: int


@dataclass(frozen=True, slots=True)
class _Account:
    by_id: dict[UUID, Entry]
    reversed_ids: frozenset[UUID]
    debts: tuple[Entry, ...]  # credit and opening entries not reversed, oldest first
    payments: tuple[Entry, ...]  # payments not reversed, oldest first
    balance: int  # below zero when the customer is in credit
    lowest: int  # the lowest the running balance has been; zero when it never went below


def _is_whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_entry(entry: Entry) -> None:
    if not _is_whole(entry.seq):
        raise LedgerIntegrityError(f"entry {entry.id}: seq must be a whole number")
    if not _is_whole(entry.amount) or not 0 < entry.amount <= MAX_AMOUNT:
        raise LedgerIntegrityError(f"entry {entry.id}: amount must be a positive whole number of minor units")
    if entry.created_at.tzinfo is None or entry.created_at.utcoffset() is None:
        raise LedgerIntegrityError(f"entry {entry.id}: created_at must be an aware datetime")
    if (entry.kind == EntryKind.REVERSAL) != (entry.reverses_id is not None):
        raise LedgerIntegrityError(f"entry {entry.id}: exactly the reversals refer to another entry")
    if entry.kind in _DEBT_KINDS:
        if entry.promised_date is None:
            raise LedgerIntegrityError(f"entry {entry.id}: a {entry.kind} entry needs a promised date")
    elif entry.promised_date is not None or entry.disputed:
        raise LedgerIntegrityError(f"entry {entry.id}: a {entry.kind} entry has no promise and no dispute")


def in_currency(entries: Iterable[Entry], currency: Currency) -> list[Entry]:
    """The book of one currency out of an account: its entries of that currency, in the order given."""
    return [entry for entry in entries if entry.currency is currency]


def currencies_of(entries: Iterable[Entry]) -> list[Currency]:
    """The currencies an account has entries in, in the order of `Currency` (so'm first)."""
    present = {entry.currency for entry in entries}
    return [currency for currency in Currency if currency in present]


def _load(entries: Iterable[Entry]) -> _Account:
    """Validate the list as one currency's book of one account and split it into the entries that still count."""
    ordered = sorted(entries, key=lambda e: e.seq)
    if len({entry.currency for entry in ordered}) > 1:
        raise LedgerIntegrityError("entries of different currencies are never calculated together")
    by_id: dict[UUID, Entry] = {}
    reversed_ids: set[UUID] = set()
    running = lowest = 0
    previous_seq: int | None = None
    for entry in ordered:
        _check_entry(entry)
        if entry.seq == previous_seq:
            raise LedgerIntegrityError(f"duplicate seq {entry.seq}")
        previous_seq = entry.seq
        if entry.id in by_id:
            raise LedgerIntegrityError(f"duplicate entry id {entry.id}")
        by_id[entry.id] = entry

        signed = entry
        if entry.reverses_id is not None:
            # Looking only among earlier entries also rejects a reversal that precedes or names itself.
            target = by_id.get(entry.reverses_id) if entry.reverses_id != entry.id else None
            if target is None:
                raise LedgerIntegrityError(f"reversal {entry.id} refers to no earlier entry of this account")
            if target.kind == EntryKind.REVERSAL:
                raise LedgerIntegrityError(f"reversal {entry.id} reverses a reversal (INV-5)")
            if target.id in reversed_ids:
                raise LedgerIntegrityError(f"entry {target.id} is reversed more than once (INV-5)")
            if target.amount != entry.amount:
                raise LedgerIntegrityError(f"reversal {entry.id} does not carry the amount it reverses (INV-6)")
            reversed_ids.add(target.id)
            signed = target
        # A reversal undoes its target: the opposite sign of what the target contributed.
        delta = signed.amount if signed.kind in _DEBT_KINDS else -signed.amount
        running += -delta if entry.kind == EntryKind.REVERSAL else delta
        lowest = min(lowest, running)

    live = [e for e in ordered if e.kind != EntryKind.REVERSAL and e.id not in reversed_ids]
    return _Account(
        by_id=by_id,
        reversed_ids=frozenset(reversed_ids),
        debts=tuple(e for e in live if e.kind in _DEBT_KINDS),
        payments=tuple(e for e in live if e.kind == EntryKind.PAYMENT),
        balance=running,
        lowest=lowest,
    )


def balance(entries: Iterable[Entry]) -> int:
    """INV-2: credits and opening balances minus payments, reversed entries and their reversals cancelling out.

    Below zero when the customer is in credit: what the shop holds of theirs as an advance (INV-3).
    """
    return _load(entries).balance


def advance(entries: Iterable[Entry]) -> int:
    """What the customer has paid beyond what they owe: the balance below zero, as a positive amount, else 0."""
    return max(0, -_load(entries).balance)


def lowest_balance(entries: Iterable[Entry]) -> int:
    """The lowest the running balance has ever been: below zero only in a book that once held an advance.

    INV-3 for a shop that does not accept advances is exactly `lowest_balance(book) == 0`.
    """
    return _load(entries).lowest


def validate_new_entry(
    entries: Iterable[Entry],
    kind: EntryKind,
    amount: int,
    reverses_id: UUID | None = None,
    *,
    advance_cap: int | None = None,
) -> Refusal | None:
    """Decide whether one more entry may be appended to the account. Returns None when it may.

    Covers only the ledger's own rules (INV-3 to INV-6, BR-5). Role permissions, the credit limit (BR-8),
    and the subscription state are checked elsewhere.

    `advance_cap` is INV-3 as the shop has chosen it. None: the book may not go below zero, so a payment
    larger than the debt and the reversal of a debt that payments already cover are refused. A number:
    the shop accepts advances, and the book may stand up to that many minor units in credit; an entry
    that would take it further is refused with `ADVANCE_TOO_LARGE`. An entry that RAISES the balance (a
    credit sale, the reversal of a payment) is never refused by either rule, whatever the balance is: it
    can only use an advance up, never make one.
    """
    account = _load(entries)
    if not _is_whole(amount) or not 0 < amount <= MAX_AMOUNT:
        return Refusal.INVALID_AMOUNT
    if advance_cap is not None and (not _is_whole(advance_cap) or advance_cap < 0):
        raise ValueError("advance_cap is a whole number of minor units, or None")

    if kind != EntryKind.REVERSAL:
        if reverses_id is not None:
            return Refusal.REVERSAL_TARGET_NOT_ALLOWED
        if kind == EntryKind.PAYMENT:
            return _below_zero(account.balance - amount, advance_cap, Refusal.EXCEEDS_BALANCE)
        return None

    if reverses_id is None:
        return Refusal.REVERSAL_TARGET_REQUIRED
    target = account.by_id.get(reverses_id)
    if target is None:
        return Refusal.REVERSAL_TARGET_NOT_FOUND
    if target.kind == EntryKind.REVERSAL:
        return Refusal.REVERSAL_OF_REVERSAL
    if target.id in account.reversed_ids:
        return Refusal.ALREADY_REVERSED
    if amount != target.amount:
        return Refusal.REVERSAL_AMOUNT_MISMATCH
    if target.kind in _DEBT_KINDS:
        return _below_zero(account.balance - target.amount, advance_cap, Refusal.NEGATIVE_BALANCE)
    return None


def _below_zero(balance_after: int, advance_cap: int | None, without_advances: Refusal) -> Refusal | None:
    """INV-3 for an entry that lowers the balance to `balance_after`."""
    if balance_after >= 0:
        return None
    if advance_cap is None:
        return without_advances
    return Refusal.ADVANCE_TOO_LARGE if -balance_after > advance_cap else None


def _allocate(debts: Iterable[Entry], payments: Iterable[Entry]) -> list[Allocation]:
    result: list[Allocation] = []
    queue = iter(payments)
    payment: Entry | None = None
    unspent = 0
    for debt in debts:
        parts: list[Coverage] = []
        covered = 0
        while covered < debt.amount:
            if unspent == 0:
                payment = next(queue, None)
                if payment is None:
                    break
                unspent = payment.amount
            assert payment is not None  # unspent > 0 implies a current payment
            taken = min(unspent, debt.amount - covered)
            parts.append(Coverage(payment_id=payment.id, amount=taken, paid_at=payment.created_at))
            covered += taken
            unspent -= taken
        assert debt.promised_date is not None  # guaranteed by _check_entry
        result.append(
            Allocation(
                entry_id=debt.id,
                seq=debt.seq,
                amount=debt.amount,
                covered=covered,
                remaining=debt.amount - covered,
                settled_at=parts[-1].paid_at if covered == debt.amount else None,
                promised_date=debt.promised_date,
                disputed=debt.disputed,
                parts=tuple(parts),
            )
        )
    return result


def allocate(entries: Iterable[Entry], as_of: datetime | None = None) -> list[Allocation]:
    """BR-3: apply payments to credit and opening entries from the oldest to the newest by `seq`.

    Returns one allocation per credit or opening entry that is not reversed, oldest first. Reversed entries
    and reversals are ignored, so a reversed payment covers nothing and a payment that used to cover a
    reversed credit flows on to the next debt, even one recorded after the payment; such a debt is settled
    at the payment's time, which is then earlier than the debt itself. An advance is the same thing seen
    from the other side: the part of a payment that no debt has taken yet covers the next debts recorded,
    oldest first, and what is left of it after the last debt is the customer's credit.

    With `as_of`, entries created after that instant are left out: the result is the allocation as it
    stood then, corrected by every reversal known now (a reversal recorded after `as_of` still removes
    its target).
    """
    account = _load(entries)
    debts, payments = account.debts, account.payments
    if as_of is not None:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be an aware datetime")
        debts = tuple(e for e in debts if e.created_at <= as_of)
        payments = tuple(e for e in payments if e.created_at <= as_of)
    return _allocate(debts, payments)


def payment_timeliness(entries: Iterable[Entry], payment_id: UUID) -> tuple[int, int]:
    """How much of one payment covered debt on or before its promised date, and how much covered it late.

    The payment is split by the oldest-first allocation (BR-3). A part is in time when the payment was
    made, in Tashkent, on or before the promised date of the debt it covers. The two amounts add up to
    what the payment covers: all of it, except a part that stands as an advance and covers nothing yet.
    A reversed payment covers nothing and gives (0, 0).
    """
    in_time = late = 0
    for allocation in allocate(entries):
        for part in allocation.parts:
            if part.payment_id != payment_id:
                continue
            if tashkent_date(part.paid_at) <= allocation.promised_date:
                in_time += part.amount
            else:
                late += part.amount
    return in_time, late


def overdue(entries: Iterable[Entry], today: date) -> OverdueStatus:
    """BR-4: what is uncovered after allocation on entries whose current promised date is before `today`.

    `today` is the Tashkent calendar date. An entry promised for today is not overdue yet; its uncovered
    amount is reported as due today. Entries under open dispute count in every field except the
    `reminder_*` amounts (BR-13).
    """
    account = _load(entries)
    unmet = [a for a in _allocate(account.debts, account.payments) if a.remaining > 0]
    late = [a for a in unmet if a.promised_date < today]
    due_today = [a for a in unmet if a.promised_date == today]
    earliest = min((a.promised_date for a in late), default=None)
    return OverdueStatus(
        overdue_amount=sum(a.remaining for a in late),
        earliest_unmet_promised_date=earliest,
        days_overdue=(today - earliest).days if earliest is not None else 0,
        due_today_amount=sum(a.remaining for a in due_today),
        reminder_overdue_amount=sum(a.remaining for a in late if not a.disputed),
        reminder_due_today_amount=sum(a.remaining for a in due_today if not a.disputed),
    )


def payment_history(entries: Iterable[Entry], today: date) -> PaymentHistory | None:
    """BR-9: how reliably this customer has repaid what has fallen due. None when nothing is yet due.

    Only credit and opening entries whose current promised date is before `today` (a Tashkent calendar
    date) count. The on-time amount is the part of them covered, by the oldest-first allocation, with
    payments made on or before the promised date; a payment at any time of the promised day in Tashkent
    is on time. The longest delay is the greatest number of days past the promised date among entries that
    were settled late (to the day they were settled) or are still not settled (to `today`).
    """
    account = _load(entries)
    due = [a for a in _allocate(account.debts, account.payments) if a.promised_date < today]
    due_amount = sum(a.amount for a in due)
    if not due:
        return None
    on_time_amount = 0
    longest_delay = 0
    for a in due:
        on_time_amount += sum(p.amount for p in a.parts if tashkent_date(p.paid_at) <= a.promised_date)
        closed_on = tashkent_date(a.settled_at) if a.settled_at is not None else today
        longest_delay = max(longest_delay, (closed_on - a.promised_date).days)
    return PaymentHistory(
        on_time_amount=on_time_amount,
        due_amount=due_amount,
        on_time_percent=(200 * on_time_amount + due_amount) // (2 * due_amount),
        longest_delay_days=longest_delay,
    )


def not_before(entries: Iterable[Entry], now: datetime) -> datetime:
    """The time a new entry of this account is written with: `now`, but never earlier than its last entry.

    The time is read before the customer is locked and the sequence number is taken under the lock, so of
    two writers at once the one that read its time first may be numbered second. Its entry is then written
    with the other's time: within an account the times follow the numbers, as every reader assumes.
    """
    return max([now, *(entry.created_at for entry in entries)])
