"""Pure rules of the reports (REQ-046): the period, Tashkent day bounds, age bands, what has fallen due."""

from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import pytest

from qarz.domain.promise import tashkent_date
from qarz.domain.reports import (
    AGE_BANDS,
    MAX_PERIOD_DAYS,
    AgeBand,
    PeriodProblem,
    age_band,
    day_start,
    due_before,
    on_time_percent,
    parse_day,
    period_bounds,
    period_days,
    validate_period,
)

D = date
TODAY = D(2026, 10, 7)
MICRO = timedelta(microseconds=1)


# --- the period ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2026-10-07", D(2026, 10, 7)), ("2024-02-29", D(2024, 2, 29)), ("0001-01-01", D(1, 1, 1))],
)
def test_a_day_is_written_as_year_month_day(raw: str, expected: date) -> None:
    assert parse_day(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "2026-10-7",
        "2026-1-07",
        "20261007",
        "2026-W41-3",
        "2026-10-07T00:00:00",
        " 2026-10-07",
        "2026-10-07 ",
        "2026-10-07\n",
        "2026-02-30",
        "2026-13-01",
        "2026-00-10",
        "07-10-2026",
        "07.10.2026",
        "today",
        "２０２６-10-07",  # full-width digits are digits to Python but not a date in this API
    ],
)
def test_anything_else_is_not_a_day(raw: str | None) -> None:
    assert parse_day(raw) is None


@pytest.mark.parametrize(
    ("first", "last", "problem"),
    [
        (TODAY, TODAY, None),  # one day, and it may be today
        (TODAY - timedelta(days=1), TODAY, None),
        (TODAY, TODAY - timedelta(days=1), PeriodProblem.FROM_AFTER_TO),
        (TODAY, TODAY + timedelta(days=1), PeriodProblem.IN_FUTURE),
        (TODAY + timedelta(days=1), TODAY + timedelta(days=1), PeriodProblem.IN_FUTURE),
        (TODAY - timedelta(days=MAX_PERIOD_DAYS - 1), TODAY, None),  # 366 days with both ends
        (TODAY - timedelta(days=MAX_PERIOD_DAYS), TODAY, PeriodProblem.TOO_LONG),  # 367
        (D(2024, 1, 1), D(2024, 12, 31), None),  # a whole leap year
        (D(2024, 1, 1), D(2025, 1, 1), PeriodProblem.TOO_LONG),
        # The order of the dates is judged first, so the caller is told about the mistake they made.
        (TODAY + timedelta(days=2), TODAY + timedelta(days=1), PeriodProblem.FROM_AFTER_TO),
        (TODAY - timedelta(days=400), TODAY + timedelta(days=1), PeriodProblem.IN_FUTURE),
    ],
)
def test_a_period_is_checked(first: date, last: date, problem: PeriodProblem | None) -> None:
    assert validate_period(first, last, TODAY) is problem


def test_a_tashkent_day_starts_five_hours_before_the_utc_day() -> None:
    assert day_start(D(2026, 10, 7)) == datetime(2026, 10, 6, 19, 0, tzinfo=UTC)
    assert day_start(D(2026, 7, 1)) == datetime(2026, 6, 30, 19, 0, tzinfo=UTC)  # no daylight saving
    assert day_start(D(2027, 1, 1)) == datetime(2026, 12, 31, 19, 0, tzinfo=UTC)
    assert day_start(D(2026, 10, 7)).utcoffset() == timedelta(0)


def test_the_bounds_hold_exactly_the_days_of_the_period() -> None:
    first, last = D(2026, 9, 28), D(2026, 10, 7)
    start, end = period_bounds(first, last)
    assert (start, end) == (datetime(2026, 9, 27, 19, 0, tzinfo=UTC), datetime(2026, 10, 7, 19, 0, tzinfo=UTC))
    # Both sides of both midnights: an instant belongs to the period when start <= instant < end.
    assert tashkent_date(start - MICRO) == first - timedelta(days=1)
    assert tashkent_date(start) == first
    assert tashkent_date(end - MICRO) == last
    assert tashkent_date(end) == last + timedelta(days=1)


def test_half_past_eleven_at_night_in_utc_is_already_the_next_day_in_tashkent() -> None:
    instant = datetime(2026, 10, 6, 23, 30, tzinfo=UTC)
    on_the_sixth, on_the_seventh = period_bounds(D(2026, 10, 6), D(2026, 10, 6)), period_bounds(TODAY, TODAY)
    assert not on_the_sixth[0] <= instant < on_the_sixth[1]
    assert on_the_seventh[0] <= instant < on_the_seventh[1]


def test_the_days_of_a_period_include_both_ends() -> None:
    assert period_days(TODAY, TODAY) == [TODAY]
    assert period_days(D(2026, 2, 27), D(2026, 3, 1)) == [D(2026, 2, 27), D(2026, 2, 28), D(2026, 3, 1)]
    assert len(period_days(D(2024, 1, 1), D(2024, 12, 31))) == MAX_PERIOD_DAYS


# --- age bands ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days_past", "band"),
    [
        (-5, None),
        (0, None),  # promised for today: not overdue yet (BR-4)
        (1, AgeBand.DAYS_1_7),
        (7, AgeBand.DAYS_1_7),
        (8, AgeBand.DAYS_8_30),
        (30, AgeBand.DAYS_8_30),
        (31, AgeBand.DAYS_31_90),
        (90, AgeBand.DAYS_31_90),
        (91, AgeBand.OVER_90),
        (5000, AgeBand.OVER_90),
    ],
)
def test_overdue_debt_is_banded_by_days_past_the_promised_date(days_past: int, band: AgeBand | None) -> None:
    assert age_band(TODAY - timedelta(days=days_past), TODAY) is band


def test_the_band_table_has_no_gap_and_agrees_with_the_rule() -> None:
    assert [band for band, _, _ in AGE_BANDS] == list(AgeBand)
    assert AGE_BANDS[0][1] == 1 and AGE_BANDS[-1][2] is None
    for (_, _, last), (_, first, _) in pairwise(AGE_BANDS):
        assert last is not None and first == last + 1
    for days in range(1, 200):
        band = age_band(TODAY - timedelta(days=days), TODAY)
        (described,) = [b for b, first, last in AGE_BANDS if first <= days and (last is None or days <= last)]
        assert band is described


# --- what has fallen due in a period (BR-9) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("last", "before"),
    [
        (TODAY - timedelta(days=10), TODAY - timedelta(days=9)),  # a past period: everything up to its last day
        (TODAY - timedelta(days=2), TODAY - timedelta(days=1)),
        (TODAY - timedelta(days=1), TODAY),  # up to yesterday
        (TODAY, TODAY),  # debt promised for today can still be paid on time
    ],
)
def test_debt_promised_for_today_has_not_fallen_due_yet(last: date, before: date) -> None:
    assert due_before(last, TODAY) == before


@pytest.mark.parametrize(
    ("on_time", "due", "percent"),
    [(0, 0, None), (0, 100, 0), (100, 100, 100), (1, 2, 50), (1, 3, 33), (2, 3, 67), (1, 200, 1), (1, 201, 0)],
)
def test_the_share_is_a_whole_percentage_with_halves_rounded_up(on_time: int, due: int, percent: int | None) -> None:
    assert on_time_percent(on_time, due) == percent
