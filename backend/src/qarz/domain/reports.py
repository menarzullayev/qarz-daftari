"""Pure rules of the shop reports (REQ-046): the period, its Tashkent day bounds, overdue age bands, and
which debt has fallen due in a period.

A period is a run of Tashkent calendar days with both ends included. Nothing here reads the clock.
"""

import re
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum

from qarz.domain.promise import TASHKENT

MAX_PERIOD_DAYS = 366

_ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class PeriodProblem(StrEnum):
    """Why a period is refused. The values are what the API reports for the field."""

    DATE_INVALID = "DATE_INVALID"
    FROM_AFTER_TO = "FROM_AFTER_TO"
    TOO_LONG = "PERIOD_TOO_LONG"
    IN_FUTURE = "IN_FUTURE"


def parse_day(raw: str | None) -> date | None:
    """A calendar date written exactly as YYYY-MM-DD; None for anything else."""
    if raw is None or not _ISO_DAY.fullmatch(raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def validate_period(first: date, last: date, today: date) -> PeriodProblem | None:
    """`today` is the Tashkent calendar date. A period may end today but not later."""
    if first > last:
        return PeriodProblem.FROM_AFTER_TO
    if last > today:
        return PeriodProblem.IN_FUTURE
    if (last - first).days + 1 > MAX_PERIOD_DAYS:
        return PeriodProblem.TOO_LONG
    return None


def day_start(day: date) -> datetime:
    """The instant the Tashkent calendar day begins, in UTC."""
    return datetime.combine(day, time.min, tzinfo=TASHKENT).astimezone(UTC)


def period_bounds(first: date, last: date) -> tuple[datetime, datetime]:
    """The period as instants: from the start of `first`, up to but not including the start of the day after `last`."""
    return day_start(first), day_start(last + timedelta(days=1))


def period_days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


class AgeBand(StrEnum):
    DAYS_1_7 = "1_7"
    DAYS_8_30 = "8_30"
    DAYS_31_90 = "31_90"
    OVER_90 = "over_90"


# Each band with its first and last day past the promised date; the last band has no end.
AGE_BANDS: tuple[tuple[AgeBand, int, int | None], ...] = (
    (AgeBand.DAYS_1_7, 1, 7),
    (AgeBand.DAYS_8_30, 8, 30),
    (AgeBand.DAYS_31_90, 31, 90),
    (AgeBand.OVER_90, 91, None),
)


def age_band(promised: date, today: date) -> AgeBand | None:
    """The band of a debt promised for `promised`, as of `today`. None when it is not overdue (BR-4)."""
    days = (today - promised).days
    if days < 1:
        return None
    for band, _, last in AGE_BANDS:
        if last is not None and days <= last:
            return band
    return AgeBand.OVER_90


def due_before(last: date, today: date) -> date:
    """The day before which a debt must have been promised to have fallen due in a period ending on `last`.

    As in `ledger.payment_history` (BR-9), a debt promised for today or later has not fallen due yet: it
    can still be paid on time. So a period that ends today counts debt promised up to yesterday.
    """
    return min(last + timedelta(days=1), today)


def on_time_percent(on_time_amount: int, due_amount: int) -> int | None:
    """The share as a whole percentage, halves rounded up, as in BR-9. None when nothing fell due."""
    if due_amount == 0:
        return None
    return (200 * on_time_amount + due_amount) // (2 * due_amount)
