"""Promised repayment dates (DOM-014; rule BR-1; requirement REQ-008).

A promised date is a calendar date in Tashkent time. Everything here is a pure function of the Tashkent
calendar date of the sale; nothing reads the clock.
"""

import calendar
import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_PROMISE_DAYS = 30
MAX_PROMISE_DAYS = 365

# ISO weekday of the day that ends the week for "end of week": Sunday. See `end_of_week`.
_WEEK_END_ISO_WEEKDAY = 7


def _tashkent() -> tzinfo:
    try:
        return ZoneInfo("Asia/Tashkent")
    except ZoneInfoNotFoundError:
        # No time zone database on this machine (Windows without tzdata). Uzbekistan has had no daylight
        # saving since 1992, so for every instant this service can meet a fixed UTC+5 offset is identical.
        return timezone(timedelta(hours=5), "Asia/Tashkent")


TASHKENT: tzinfo = _tashkent()


def tashkent_date(at: datetime) -> date:
    """The Tashkent calendar date of an instant. Naive datetimes are a programming error."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("an aware datetime is required")
    return at.astimezone(TASHKENT).date()


def default_promise_date(sale_at: datetime, default_days: int = DEFAULT_PROMISE_DAYS) -> date:
    """BR-1: when the seller chooses no date, the promise is `default_days` after the sale's Tashkent date.

    `default_days` is the shop setting, 30 unless the shop changed it, and between 1 and 365 as the schema
    allows (`shop.default_promise_days`).
    """
    if isinstance(default_days, bool) or not isinstance(default_days, int):
        raise ValueError("default days must be a whole number")
    if not 1 <= default_days <= MAX_PROMISE_DAYS:
        raise ValueError(f"default days must be between 1 and {MAX_PROMISE_DAYS}")
    return tashkent_date(sale_at) + timedelta(days=default_days)


def tomorrow(sale_date: date) -> date:
    return sale_date + timedelta(days=1)


def end_of_week(sale_date: date) -> date:
    """The coming Sunday, strictly after the sale date.

    The week in Uzbekistan runs Monday to Sunday (the ISO week), and a grocery shop is open on Sunday, so
    "by the end of the week" is read as "by Sunday". A sale made on a Sunday gets the following Sunday:
    a promise for the day of the sale itself would already be due and is not what a customer means.
    """
    days_ahead = (_WEEK_END_ISO_WEEKDAY - sale_date.isoweekday()) % 7
    return sale_date + timedelta(days=days_ahead or 7)


def in_two_weeks(sale_date: date) -> date:
    return sale_date + timedelta(days=14)


def in_a_month(sale_date: date) -> date:
    """The same day of the next month, or that month's last day when it is shorter (31 January -> 28 February)."""
    year, month = (sale_date.year + 1, 1) if sale_date.month == 12 else (sale_date.year, sale_date.month + 1)
    return date(year, month, min(sale_date.day, calendar.monthrange(year, month)[1]))


class QuickChoice(StrEnum):
    """The one-tap choices offered after a credit sale (REQ-008; technical specification, chat replies)."""

    TOMORROW = "tomorrow"
    END_OF_WEEK = "end_of_week"
    IN_TWO_WEEKS = "in_two_weeks"
    IN_A_MONTH = "in_a_month"


def quick_choice_date(choice: QuickChoice, sale_date: date) -> date:
    match choice:
        case QuickChoice.TOMORROW:
            return tomorrow(sale_date)
        case QuickChoice.END_OF_WEEK:
            return end_of_week(sale_date)
        case QuickChoice.IN_TWO_WEEKS:
            return in_two_weeks(sale_date)
        case QuickChoice.IN_A_MONTH:
            return in_a_month(sale_date)


_DAY_MONTH = re.compile(r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{4}))?")


def parse_day_month(text: str, today: date) -> date | None:
    """Read "25.10" or "25.10.2026" as a date. None when it is not a date.

    Without a year it is the next such day, today included: in December, "15.01" is next January.
    """
    match = _DAY_MONTH.fullmatch(text.strip())
    if match is None or not text.strip().isascii():
        return None
    day_number, month_number = int(match.group(1)), int(match.group(2))
    try:
        if match.group(3) is not None:
            return date(int(match.group(3)), month_number, day_number)
        candidate = date(today.year, month_number, day_number)
        return candidate if candidate >= today else date(today.year + 1, month_number, day_number)
    except ValueError:
        return None


class PromiseDateError(StrEnum):
    BEFORE_SALE = "PROMISE_BEFORE_SALE"
    TOO_FAR = "PROMISE_TOO_FAR"


def validate_promise_date(sale_date: date, chosen: date) -> PromiseDateError | None:
    """A chosen date may be the sale date itself and at most 365 days after it. Returns None when acceptable.

    `sale_date` is the Tashkent calendar date of the sale (`tashkent_date(entry.created_at)`). The same
    bounds apply to a later change of the promise: they are measured from the sale, not from the change.
    """
    if chosen < sale_date:
        return PromiseDateError.BEFORE_SALE
    if chosen > sale_date + timedelta(days=MAX_PROMISE_DAYS):
        return PromiseDateError.TOO_FAR
    return None
