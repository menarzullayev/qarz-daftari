from datetime import date, timedelta

import pytest

from qarz.domain.subscription import (
    add_months,
    effective_state,
    extend_paid_through,
    period_end,
    warning_days,
)

TODAY = date(2026, 10, 7)
YESTERDAY, TOMORROW = TODAY - timedelta(days=1), TODAY + timedelta(days=1)


@pytest.mark.parametrize(
    ("state", "trial_ends", "paid_through", "expected"),
    [
        ("trial", TOMORROW, None, "trial"),
        ("trial", TODAY, None, "trial"),  # the last day still counts
        ("trial", YESTERDAY, None, "limited"),
        ("trial", None, None, "limited"),
        ("active", None, TODAY, "active"),
        ("active", None, YESTERDAY, "limited"),
        ("active", TOMORROW, None, "limited"),  # a trial date does not keep a paid state alive
        ("limited", TOMORROW, TOMORROW, "limited"),
        ("suspended", TOMORROW, TOMORROW, "suspended"),
        ("anything else", TOMORROW, TOMORROW, "limited"),
    ],
)
def test_what_applies_today(state: str, trial_ends: date | None, paid_through: date | None, expected: str) -> None:
    assert effective_state(state, trial_ends, paid_through, TODAY) == expected


def test_only_a_running_period_has_an_end() -> None:
    assert period_end("trial", TODAY, TOMORROW) == TODAY
    assert period_end("active", TODAY, TOMORROW) == TOMORROW
    assert period_end("limited", TODAY, TOMORROW) is None
    assert period_end("suspended", TODAY, TOMORROW) is None


@pytest.mark.parametrize(("days", "expected"), [(8, None), (7, 7), (6, None), (2, None), (1, 1), (0, None), (-1, None)])
def test_the_owner_is_warned_seven_days_and_one_day_before(days: int, expected: int | None) -> None:
    assert warning_days(TODAY + timedelta(days=days), TODAY) == expected


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2026, 10, 7), 1, date(2026, 11, 7)),
        (date(2026, 1, 31), 1, date(2026, 2, 28)),
        (date(2028, 1, 31), 1, date(2028, 2, 29)),
        (date(2026, 12, 15), 1, date(2027, 1, 15)),
        (date(2026, 10, 31), 6, date(2027, 4, 30)),
        (date(2026, 10, 7), 12, date(2027, 10, 7)),
    ],
)
def test_adding_months_keeps_the_day_or_takes_the_months_last(start: date, months: int, expected: date) -> None:
    assert add_months(start, months) == expected


def test_a_payment_extends_from_the_later_of_today_and_the_paid_date() -> None:
    # Paid early: the new period follows the current one, and no paid day is lost.
    assert extend_paid_through(date(2026, 10, 10), TODAY, 1) == date(2026, 11, 10)
    assert extend_paid_through(TODAY, TODAY, 1) == date(2026, 11, 7)
    # Paid after the period ended, or never before: today is the first paid day.
    assert extend_paid_through(YESTERDAY, TODAY, 1) == date(2026, 11, 6)
    assert extend_paid_through(None, TODAY, 1) == date(2026, 11, 6)
    assert extend_paid_through(date(2026, 1, 1), TODAY, 3) == date(2027, 1, 6)


@pytest.mark.parametrize("months", [0, -1, 37])
def test_months_out_of_range_are_refused(months: int) -> None:
    with pytest.raises(ValueError, match="months"):
        extend_paid_through(None, TODAY, months)
