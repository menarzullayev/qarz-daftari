from datetime import date

import pytest

from qarz.domain.promise import parse_day_month

TODAY = date(2026, 10, 6)


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("25.10", date(2026, 10, 25)),
        (" 25.10 ", date(2026, 10, 25)),
        ("6.10", date(2026, 10, 6)),  # today itself
        ("06.10", date(2026, 10, 6)),
        ("5.10", date(2027, 10, 5)),  # already past this year: the next one
        ("15.01", date(2027, 1, 15)),
        ("25/10", date(2026, 10, 25)),
        ("25-10", date(2026, 10, 25)),
        ("25.10.2026", date(2026, 10, 25)),
        ("01.01.2020", date(2020, 1, 1)),  # a full date is taken as written; the range is checked elsewhere
        ("28.02", date(2027, 2, 28)),
    ],
)
def test_a_typed_day_and_month_is_read_as_a_date(typed: str, expected: date) -> None:
    assert parse_day_month(typed, TODAY) == expected


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "25",
        "25.",
        ".10",
        "32.10",
        "25.13",
        "31.11",
        "29.02",  # no such day in the coming year
        "00.10",
        "25.10.26",  # a two-digit year is not guessed
        "25.10.20266",
        "25 10",
        "25.10 ertaga",
        "Ali 45000",
        "45.000",
        "٢٥.١٠",  # digits of another script
        "25.10.",
    ],
)
def test_what_is_not_a_date_is_not_read_as_one(typed: str) -> None:
    assert parse_day_month(typed, TODAY) is None


def test_a_leap_day_is_found_in_a_leap_year() -> None:
    assert parse_day_month("29.02", date(2028, 1, 10)) == date(2028, 2, 29)
    assert parse_day_month("29.02.2028", TODAY) == date(2028, 2, 29)
