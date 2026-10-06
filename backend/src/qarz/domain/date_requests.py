"""When a customer may ask to move a promised date, and when the shop may move one (BR-15, INV-9).

A request never changes a date by itself (INV-15): it is an open question until a manager or owner
accepts or declines it, or until the entry it is about is reversed or fully paid.
"""

from datetime import date, datetime, timedelta
from enum import StrEnum

from qarz.domain.ledger import EntryKind
from qarz.domain.promise import PromiseDateError, validate_promise_date

# BR-15: after a decline the same entry cannot be asked about again for this long.
REPEAT_AFTER_DECLINE = timedelta(days=7)
REASON_MAX = 300

_DEBT_KINDS = (EntryKind.CREDIT, EntryKind.OPENING)


class DateRequestRefusal(StrEnum):
    NOT_A_DEBT = "not_a_debt"  # only a credit sale or an opening balance has a promised date
    REVERSED = "reversed"  # the shop has cancelled the entry
    FULLY_PAID = "fully_paid"  # nothing of it is still owed, so there is nothing to move
    ALREADY_OPEN = "already_open"  # one open request per entry
    NOT_LATER = "not_later"  # a request moves the date forward; this one is not after the current date
    DECLINED_RECENTLY = "declined_recently"


def may_request(
    *,
    kind: EntryKind,
    is_reversed: bool,
    remaining: int,
    has_open: bool,
    sale_date: date,
    current: date | None,
    requested: date,
    last_declined_at: datetime | None,
    now: datetime,
) -> DateRequestRefusal | PromiseDateError | None:
    """Returns None when the customer may ask for `requested` as the new promised date of the entry.

    `remaining` is what the oldest-first allocation leaves uncovered on the entry, `sale_date` its
    Tashkent calendar date, `current` its promised date now, and `last_declined_at` when a request for
    this entry was last declined. The seven days end exactly seven days after that decline: at that
    moment a new request is allowed again. The date itself is bound like every promised date, measured
    from the sale.
    """
    if kind not in _DEBT_KINDS:
        return DateRequestRefusal.NOT_A_DEBT
    if is_reversed:
        return DateRequestRefusal.REVERSED
    if remaining <= 0:
        return DateRequestRefusal.FULLY_PAID
    if has_open:
        return DateRequestRefusal.ALREADY_OPEN
    if current is not None and requested <= current:
        return DateRequestRefusal.NOT_LATER
    problem = validate_promise_date(sale_date, requested)
    if problem is not None:
        return problem
    if last_declined_at is not None and now - last_declined_at < REPEAT_AFTER_DECLINE:
        return DateRequestRefusal.DECLINED_RECENTLY
    return None


class PromiseChangeRefusal(StrEnum):
    NOT_A_DEBT = "not_a_debt"
    REVERSED = "reversed"
    UNCHANGED = "unchanged"  # the entry already carries this date


def may_change_promise(
    *, kind: EntryKind, is_reversed: bool, sale_date: date, current: date | None, chosen: date
) -> PromiseChangeRefusal | PromiseDateError | None:
    """Returns None when the shop may set `chosen` as the promised date of the entry (REQ-067).

    Earlier or later than the current date, at any time, as long as the entry is a debt that still
    stands; the bounds are those of every promised date, measured from the sale.
    """
    if kind not in _DEBT_KINDS:
        return PromiseChangeRefusal.NOT_A_DEBT
    if is_reversed:
        return PromiseChangeRefusal.REVERSED
    if chosen == current:
        return PromiseChangeRefusal.UNCHANGED
    return validate_promise_date(sale_date, chosen)


def request_is_met(requested: date, new_date: date) -> bool:
    """A promised date set on or after the one asked for leaves the request nothing more to ask."""
    return new_date >= requested


def tidy_reason(raw: str | None) -> str | None:
    """An optional reason with whitespace tidied. None when nothing was written."""
    if raw is None:
        return None
    return " ".join(raw.split()) or None


def reason_fits(reason: str | None) -> bool:
    return reason is None or len(reason) <= REASON_MAX
