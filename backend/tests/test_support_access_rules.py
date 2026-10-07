"""Support access: its length and its states, on both sides of each limit (BR-31)."""

from datetime import UTC, datetime, timedelta

import pytest

from qarz.domain.support_access import (
    ACTIVE,
    CLOSED,
    DEFAULT_HOURS,
    EXPIRED,
    MAX_HOURS,
    ends_at,
    state,
    valid_hours,
)

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
TICK = timedelta(microseconds=1)


def test_a_support_access_lasts_from_one_to_twenty_four_hours() -> None:
    assert MAX_HOURS == 24, "BR-31"
    assert DEFAULT_HOURS == 1
    assert valid_hours(1)
    assert valid_hours(24)
    for wrong in (0, 25, -1, True, 1.0, "2", None):
        assert not valid_hours(wrong)


def test_the_end_is_that_many_hours_from_now() -> None:
    assert ends_at(NOW, 1) == NOW + timedelta(hours=1)
    assert ends_at(NOW, 24) == NOW + timedelta(hours=24)
    for wrong in (0, 25):
        with pytest.raises(ValueError, match="between 1 and 24"):
            ends_at(NOW, wrong)


def test_it_is_active_from_its_start_until_just_before_its_end() -> None:
    start, end = NOW, NOW + timedelta(hours=2)
    assert state(start, end, None, start - TICK) == EXPIRED, "before its start it opens nothing"
    assert state(start, end, None, start) == ACTIVE
    assert state(start, end, None, end - TICK) == ACTIVE
    assert state(start, end, None, end) == EXPIRED


def test_closed_early_it_is_closed_at_once_and_stays_closed() -> None:
    start, end = NOW, NOW + timedelta(hours=2)
    closed = NOW + timedelta(minutes=5)
    assert state(start, end, closed, closed) == CLOSED
    assert state(start, end, closed, end + timedelta(days=3)) == CLOSED
