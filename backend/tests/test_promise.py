from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfoNotFoundError

import pytest

from qarz.domain import promise
from qarz.domain.promise import (
    TASHKENT,
    PromiseDateError,
    QuickChoice,
    default_promise_date,
    end_of_week,
    in_a_month,
    in_two_weeks,
    quick_choice_date,
    tashkent_date,
    tomorrow,
    validate_promise_date,
)

D = date


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (datetime(2026, 3, 10, 18, 59, tzinfo=UTC), D(2026, 3, 10)),  # 23:59 in Tashkent
        (datetime(2026, 3, 10, 19, 0, tzinfo=UTC), D(2026, 3, 11)),  # midnight in Tashkent
        (datetime(2026, 7, 10, 19, 0, tzinfo=UTC), D(2026, 7, 11)),  # no daylight saving in summer
        (datetime(2026, 12, 31, 20, 0, tzinfo=UTC), D(2027, 1, 1)),
        (datetime(2026, 3, 10, 23, 30, tzinfo=timezone(timedelta(hours=-8))), D(2026, 3, 11)),
        (datetime(2026, 3, 10, 0, 0, tzinfo=TASHKENT), D(2026, 3, 10)),
    ],
)
def test_tashkent_date(instant: datetime, expected: date) -> None:
    assert tashkent_date(instant) == expected


def test_tashkent_date_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        tashkent_date(datetime(2026, 3, 10, 12, 0))


@pytest.mark.parametrize("month", range(1, 13))
def test_tashkent_is_five_hours_ahead_all_year(month: int) -> None:
    assert datetime(2026, month, 15, 12, 0, tzinfo=TASHKENT).utcoffset() == timedelta(hours=5)


def test_fixed_offset_is_used_when_the_machine_has_no_time_zone_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(key: str) -> None:
        raise ZoneInfoNotFoundError(key)

    monkeypatch.setattr(promise, "ZoneInfo", missing)
    fallback = promise._tashkent()
    assert fallback.utcoffset(None) == timedelta(hours=5)
    for month in range(1, 13):
        instant = datetime(2026, month, 28, 19, 30, tzinfo=UTC)
        assert instant.astimezone(fallback).date() == tashkent_date(instant)


@pytest.mark.parametrize(
    ("sale_at", "days", "expected"),
    [
        (datetime(2026, 1, 5, 10, 0, tzinfo=TASHKENT), 30, D(2026, 2, 4)),
        (datetime(2026, 1, 5, 10, 0, tzinfo=TASHKENT), 1, D(2026, 1, 6)),
        (datetime(2026, 1, 5, 10, 0, tzinfo=TASHKENT), 7, D(2026, 1, 12)),
        (datetime(2026, 1, 5, 10, 0, tzinfo=TASHKENT), 365, D(2027, 1, 5)),
        # 20:30 UTC on 5 January is already 6 January in Tashkent: the count starts from the 6th.
        (datetime(2026, 1, 5, 20, 30, tzinfo=UTC), 30, D(2026, 2, 5)),
        (datetime(2026, 1, 5, 18, 59, tzinfo=UTC), 30, D(2026, 2, 4)),
        (datetime(2028, 1, 30, 9, 0, tzinfo=TASHKENT), 30, D(2028, 2, 29)),  # leap year
    ],
)
def test_default_promise_date(sale_at: datetime, days: int, expected: date) -> None:
    assert default_promise_date(sale_at, days) == expected


def test_default_promise_date_is_thirty_days_unless_the_shop_changes_it() -> None:
    assert default_promise_date(datetime(2026, 6, 1, 8, 0, tzinfo=TASHKENT)) == D(2026, 7, 1)


@pytest.mark.parametrize(
    ("sale_at", "days"),
    [
        (datetime(2026, 1, 5, 10, 0, tzinfo=TASHKENT), 0),
        (datetime(2026, 1, 5, 10, 0, tzinfo=TASHKENT), -1),
        (datetime(2026, 1, 5, 10, 0, tzinfo=TASHKENT), 366),
        (datetime(2026, 1, 5, 10, 0, tzinfo=TASHKENT), True),
        (datetime(2026, 1, 5, 10, 0, tzinfo=TASHKENT), 30.0),
        (datetime(2026, 1, 5, 10, 0), 30),  # naive
    ],
)
def test_default_promise_date_rejects_invalid_input(sale_at: datetime, days: int) -> None:
    with pytest.raises(ValueError):
        default_promise_date(sale_at, days)


@pytest.mark.parametrize(
    ("sale", "expected"),
    [
        (D(2026, 1, 5), D(2026, 1, 6)),
        (D(2026, 1, 31), D(2026, 2, 1)),
        (D(2026, 12, 31), D(2027, 1, 1)),
        (D(2028, 2, 28), D(2028, 2, 29)),
    ],
)
def test_tomorrow(sale: date, expected: date) -> None:
    assert tomorrow(sale) == expected


@pytest.mark.parametrize(
    ("sale", "expected"),
    [
        (D(2026, 1, 5), D(2026, 1, 11)),  # Monday -> Sunday of the same week
        (D(2026, 1, 7), D(2026, 1, 11)),  # Wednesday
        (D(2026, 1, 9), D(2026, 1, 11)),  # Friday
        (D(2026, 1, 10), D(2026, 1, 11)),  # Saturday -> tomorrow
        (D(2026, 1, 11), D(2026, 1, 18)),  # Sunday -> the next Sunday, not the day of the sale
        (D(2026, 12, 30), D(2027, 1, 3)),  # the week crosses the year
    ],
)
def test_end_of_week(sale: date, expected: date) -> None:
    assert end_of_week(sale) == expected


def test_end_of_week_is_always_a_coming_sunday() -> None:
    for offset in range(800):
        sale = D(2026, 1, 1) + timedelta(days=offset)
        result = end_of_week(sale)
        assert result.isoweekday() == 7
        assert 1 <= (result - sale).days <= 7


@pytest.mark.parametrize(
    ("sale", "expected"),
    [
        (D(2026, 1, 5), D(2026, 1, 19)),
        (D(2026, 2, 20), D(2026, 3, 6)),
        (D(2026, 12, 25), D(2027, 1, 8)),
    ],
)
def test_in_two_weeks(sale: date, expected: date) -> None:
    assert in_two_weeks(sale) == expected


@pytest.mark.parametrize(
    ("sale", "expected"),
    [
        (D(2026, 1, 15), D(2026, 2, 15)),
        (D(2026, 1, 28), D(2026, 2, 28)),
        (D(2026, 1, 29), D(2026, 2, 28)),  # clamped
        (D(2026, 1, 31), D(2026, 2, 28)),  # clamped
        (D(2028, 1, 31), D(2028, 2, 29)),  # clamped to a leap day
        (D(2026, 2, 28), D(2026, 3, 28)),  # same day, not "last day to last day"
        (D(2026, 3, 31), D(2026, 4, 30)),
        (D(2026, 5, 31), D(2026, 6, 30)),
        (D(2026, 12, 15), D(2027, 1, 15)),
        (D(2026, 12, 31), D(2027, 1, 31)),
    ],
)
def test_in_a_month(sale: date, expected: date) -> None:
    assert in_a_month(sale) == expected


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        (QuickChoice.TOMORROW, D(2026, 1, 31)),
        (QuickChoice.END_OF_WEEK, D(2026, 2, 1)),
        (QuickChoice.IN_TWO_WEEKS, D(2026, 2, 13)),
        (QuickChoice.IN_A_MONTH, D(2026, 2, 28)),
    ],
)
def test_quick_choice_date(choice: QuickChoice, expected: date) -> None:
    assert quick_choice_date(choice, D(2026, 1, 30)) == expected  # a Friday


def test_every_quick_choice_has_a_date() -> None:
    assert {c.value for c in QuickChoice} == {"tomorrow", "end_of_week", "in_two_weeks", "in_a_month"}
    for choice in QuickChoice:
        assert quick_choice_date(QuickChoice(choice.value), D(2026, 6, 15)) > D(2026, 6, 15)


@pytest.mark.parametrize(
    ("sale", "chosen"),
    [
        (D(2026, 1, 5), D(2026, 1, 5)),  # the day of the sale itself
        (D(2026, 1, 5), D(2026, 1, 6)),
        (D(2026, 1, 5), D(2026, 6, 30)),
        (D(2026, 1, 5), D(2027, 1, 5)),  # exactly 365 days
        (D(2028, 1, 5), D(2029, 1, 4)),  # exactly 365 days across a leap day
    ],
)
def test_validate_promise_date_accepts(sale: date, chosen: date) -> None:
    assert validate_promise_date(sale, chosen) is None


@pytest.mark.parametrize(
    ("sale", "chosen", "expected"),
    [
        (D(2026, 1, 5), D(2026, 1, 4), PromiseDateError.BEFORE_SALE),
        (D(2026, 1, 5), D(2025, 1, 5), PromiseDateError.BEFORE_SALE),
        (D(2026, 1, 5), D(2027, 1, 6), PromiseDateError.TOO_FAR),  # 366 days
        (D(2028, 1, 5), D(2029, 1, 5), PromiseDateError.TOO_FAR),  # 366 days across a leap day
        (D(2026, 1, 5), D(2030, 1, 1), PromiseDateError.TOO_FAR),
    ],
)
def test_validate_promise_date_rejects(sale: date, chosen: date, expected: PromiseDateError) -> None:
    assert validate_promise_date(sale, chosen) == expected


def test_defaults_and_quick_choices_are_always_valid_dates() -> None:
    for offset in range(800):
        sale = D(2026, 1, 1) + timedelta(days=offset)
        sale_at = datetime(sale.year, sale.month, sale.day, 15, 0, tzinfo=TASHKENT)
        candidates = [quick_choice_date(choice, sale) for choice in QuickChoice]
        candidates += [default_promise_date(sale_at, days) for days in (1, 30, 365)]
        for chosen in candidates:
            assert validate_promise_date(sale, chosen) is None
