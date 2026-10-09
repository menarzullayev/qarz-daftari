"""Subscription periods and what they allow (REQ-052, REQ-057; BR-27 to BR-30)."""

import calendar
from datetime import date, timedelta

TRIAL, ACTIVE, LIMITED, SUSPENDED = "trial", "active", "limited", "suspended"
# Never stored: what a shop without a period is called while the free plan holds it (BR-33).
FREE = "free"
WARN_DAYS_BEFORE = (7, 1)  # BR-28
DEFAULT_PRICE_UZS = 100_000  # REQ-053; the administrator can change it
MAX_MONTHS = 36


def period_end(state: str, trial_ends: date | None, paid_through: date | None) -> date | None:
    """The last day the shop may record new credit sales, if its state has one."""
    if state == TRIAL:
        return trial_ends
    if state == ACTIVE:
        return paid_through
    return None


def effective_state(state: str, trial_ends: date | None, paid_through: date | None, today: date) -> str:
    """What applies today. A period includes its last day; after it the shop is limited, whatever is stored."""
    if state == SUSPENDED:
        return SUSPENDED
    if state in (TRIAL, ACTIVE):
        end = period_end(state, trial_ends, paid_through)
        return state if end is not None and end >= today else LIMITED
    return LIMITED


def with_free_plan(effective: str, free_customers: int | None, customers: int) -> str:
    """BR-33: what applies once the free plan is taken into account.

    `effective` is what `effective_state` said, `free_customers` how many customers the free plan holds
    (None while the plan is switched off) and `customers` how many the shop has. A shop without a trial
    or paid period that has no more customers than the plan holds is free, not limited: it works in
    full. A suspended shop stays suspended, and a running period is what it was.
    """
    if effective == LIMITED and free_customers is not None and customers <= free_customers:
        return FREE
    return effective


def may_add_customers(effective: str, free_customers: int | None, customers: int, adding: int = 1) -> bool:
    """BR-34: whether `adding` more customers fit. Only a running trial or paid period goes beyond what
    the free plan holds; with the plan switched off nothing is counted."""
    if free_customers is None or effective in (TRIAL, ACTIVE):
        return True
    return customers + adding <= free_customers


def sms_included(effective: str, free_plan_on: bool) -> bool:
    """BR-35: with the free plan on, SMS belongs to a paid period alone: not to a trial, not to a free or
    limited shop. With the plan off nothing about SMS depends on the subscription, as before."""
    return not free_plan_on or effective == ACTIVE


def warning_days(end: date, today: date) -> int | None:
    """7 or 1 when the owner is to be warned today that the period ends in that many days, else None."""
    days = (end - today).days
    return days if days in WARN_DAYS_BEFORE else None


def add_months(day: date, months: int) -> date:
    """The same day `months` later, or that month's last day when it is shorter."""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def extend_paid_through(paid_through: date | None, today: date, months: int) -> date:
    """BR-27: whole months from the later of today and the current paid-through date.

    A shop paid through the 10th that pays early on the 5th is paid through the 10th of the next month;
    a shop that pays after its period ended is paid from today. Today itself is the first paid day, so
    one month paid on 7 October runs through 6 November.
    """
    if not 1 <= months <= MAX_MONTHS:
        raise ValueError(f"months must be between 1 and {MAX_MONTHS}")
    if paid_through is not None and paid_through >= today:
        return add_months(paid_through, months)
    return add_months(today, months) - timedelta(days=1)


def after_payment(
    state: str, trial_ends: date | None, paid_through: date | None, today: date, months: int
) -> tuple[str, date, str | None]:
    """The state, paid-through date and remembered state of a subscription once `months` are paid for.

    One rule for every way of paying: a transfer an administrator approves and an online payment. The
    period is extended by BR-27 and the shop is active again, except that a shop an administrator
    suspended stays suspended (BR-30) and will be active when the suspension is lifted.

    A shop that pays while its trial still runs keeps the days of the trial it has left: the months are
    counted from the trial's last day, as they are from a paid period's (the founder's decision of
    2026-10-09). Paying early must not cost the owner what was already theirs.
    """
    counted_from = paid_through
    if state == TRIAL and trial_ends is not None and trial_ends >= today:
        counted_from = trial_ends if paid_through is None else max(trial_ends, paid_through)
    until = extend_paid_through(counted_from, today, months)
    if state == SUSPENDED:
        return SUSPENDED, until, ACTIVE
    return ACTIVE, until, None
