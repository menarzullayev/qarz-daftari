"""Pure rules for goods lines: quantities, the number of lines, and the time limit (INV-8)."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from qarz.domain.goods import MAX_LINES, format_qty, line_count_allowed, lines_window_open, parse_qty
from qarz.domain.promise import TASHKENT


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", "1"),
        ("1.5", "1.5"),
        ("0.001", "0.001"),
        ("0.125", "0.125"),
        ("2.500", "2.5"),
        ("10.0", "10"),
        ("100", "100"),
        ("007", "7"),
        ("999999999.999", "999999999.999"),
    ],
)
def test_a_quantity_is_read_and_written_as_a_plain_decimal(raw: str, expected: str) -> None:
    qty = parse_qty(raw)
    assert qty == Decimal(raw)
    assert format_qty(qty) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "0",
        "0.0",
        "0.000",
        "-1",
        "+1",
        "1.2345",  # four decimals
        "1.",
        ".5",
        "1,5",
        "1e3",
        "1E3",
        " 1",
        "1 ",
        "1\n",
        "1_000",
        "1 000",
        "abc",
        "NaN",
        "Infinity",
        "١",  # a digit, but not an ASCII one
        "1234567890",  # ten digits before the point do not fit numeric(12,3)
    ],
)
def test_an_unusable_quantity_is_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_qty(raw)


def test_a_stored_quantity_loses_its_trailing_zeros_only() -> None:
    assert format_qty(Decimal("3.000")) == "3"
    assert format_qty(Decimal("1200.000")) == "1200"
    assert format_qty(Decimal("0.050")) == "0.05"


@pytest.mark.parametrize(("count", "allowed"), [(0, False), (1, True), (50, True), (51, False), (-1, False)])
def test_a_sale_has_between_one_and_fifty_lines(count: int, allowed: bool) -> None:
    assert MAX_LINES == 50
    assert line_count_allowed(count) is allowed


# Sold on 10 March 2026 in Tashkent; the window closes when 12 March begins there (11 March 19:00 UTC).
_CLOSES = datetime(2026, 3, 11, 19, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "sold_at",
    [
        datetime(2026, 3, 9, 19, 0, tzinfo=UTC),  # the first instant of 10 March in Tashkent
        datetime(2026, 3, 10, 7, 0, tzinfo=UTC),
        datetime(2026, 3, 10, 18, 59, 59, 999999, tzinfo=UTC),  # the last instant of 10 March in Tashkent
        datetime(2026, 3, 10, 12, 0, tzinfo=TASHKENT),
        datetime(2026, 3, 9, 23, 30, tzinfo=timezone(timedelta(hours=-8))),  # the same day seen from elsewhere
    ],
)
def test_the_window_closes_at_the_end_of_the_day_after_the_sale(sold_at: datetime) -> None:
    assert lines_window_open(sold_at, sold_at)
    assert lines_window_open(sold_at, _CLOSES - timedelta(microseconds=1))
    assert not lines_window_open(sold_at, _CLOSES)
    assert not lines_window_open(sold_at, _CLOSES + timedelta(microseconds=1))
    assert not lines_window_open(sold_at, _CLOSES + timedelta(days=30))


def test_the_window_follows_the_tashkent_day_not_the_utc_day() -> None:
    # 18:59 UTC and 19:00 UTC are one minute apart but fall on different Tashkent days.
    late = datetime(2026, 3, 10, 18, 59, tzinfo=UTC)
    after_midnight = datetime(2026, 3, 10, 19, 0, tzinfo=UTC)
    probe = datetime(2026, 3, 11, 19, 0, tzinfo=UTC)
    assert not lines_window_open(late, probe)
    assert lines_window_open(after_midnight, probe)
    assert not lines_window_open(after_midnight, probe + timedelta(days=1))


def test_the_window_needs_aware_times() -> None:
    with pytest.raises(ValueError):
        lines_window_open(datetime(2026, 3, 10, 12, 0), datetime(2026, 3, 10, 13, 0, tzinfo=UTC))
