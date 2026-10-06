from datetime import date, timedelta

import pytest

from qarz.domain.ledger import OverdueStatus
from qarz.domain.reminders import (
    Channel,
    ReminderKind,
    ReminderPlan,
    choose_channel,
    hours_to_run,
    plan_automatic,
    plan_manual,
)

TODAY = date(2026, 10, 7)


def status(*, overdue: int = 0, due_today: int = 0, disputed_overdue: int = 0, disputed_due: int = 0) -> OverdueStatus:
    """`overdue` and `due_today` are what a reminder may mention; the disputed parts are owed but left out."""
    return OverdueStatus(
        overdue_amount=overdue + disputed_overdue,
        earliest_unmet_promised_date=TODAY - timedelta(days=3) if overdue + disputed_overdue else None,
        days_overdue=3 if overdue + disputed_overdue else 0,
        due_today_amount=due_today + disputed_due,
        reminder_overdue_amount=overdue,
        reminder_due_today_amount=due_today,
    )


def test_a_reminder_goes_out_on_the_promised_date() -> None:
    assert plan_automatic(status(due_today=40000), None, TODAY) == ReminderPlan(ReminderKind.DUE_TODAY, 40000)
    # Even if another one went out yesterday for an older debt: this is a new promised date.
    yesterday = TODAY - timedelta(days=1)
    assert plan_automatic(status(due_today=40000, overdue=10000), yesterday, TODAY) == ReminderPlan(
        ReminderKind.DUE_TODAY, 50000
    )


def test_an_overdue_debt_is_reminded_of_at_most_once_in_seven_days() -> None:
    owed = status(overdue=70000)
    assert plan_automatic(owed, None, TODAY) == ReminderPlan(ReminderKind.OVERDUE, 70000)
    assert plan_automatic(owed, TODAY - timedelta(days=7), TODAY) == ReminderPlan(ReminderKind.OVERDUE, 70000)
    assert plan_automatic(owed, TODAY - timedelta(days=6), TODAY) is None
    assert plan_automatic(owed, TODAY - timedelta(days=1), TODAY) is None
    assert plan_automatic(owed, TODAY - timedelta(days=30), TODAY) is not None


def test_never_two_automatic_reminders_in_one_day() -> None:
    assert plan_automatic(status(due_today=40000), TODAY, TODAY) is None
    assert plan_automatic(status(overdue=40000), TODAY, TODAY) is None


def test_nothing_due_means_no_reminder() -> None:
    assert plan_automatic(status(), None, TODAY) is None
    assert plan_manual(status()) is None


def test_what_is_under_dispute_is_not_mentioned_and_does_not_cause_a_reminder() -> None:
    assert plan_automatic(status(disputed_overdue=50000), None, TODAY) is None
    assert plan_automatic(status(disputed_due=50000), None, TODAY) is None
    assert plan_manual(status(disputed_overdue=50000, disputed_due=1000)) is None
    mixed = status(overdue=20000, disputed_overdue=50000)
    assert plan_automatic(mixed, None, TODAY) == ReminderPlan(ReminderKind.OVERDUE, 20000)
    assert plan_manual(mixed) == ReminderPlan(ReminderKind.OVERDUE, 20000)


def test_a_manual_reminder_needs_the_same_ground() -> None:
    assert plan_manual(status(due_today=5000)) == ReminderPlan(ReminderKind.DUE_TODAY, 5000)
    assert plan_manual(status(overdue=5000, due_today=1000)) == ReminderPlan(ReminderKind.OVERDUE, 6000)


def channel(**overrides: object) -> Channel | None:
    arguments: dict[str, object] = {
        "telegram_reachable": False,
        "has_phone": True,
        "sms_on_platform": True,
        "sms_on_shop": True,
        "sms_quota_left": 5,
    }
    arguments.update(overrides)
    return choose_channel(**arguments)  # type: ignore[arg-type]


def test_telegram_comes_first_and_sms_needs_every_switch() -> None:
    assert channel(telegram_reachable=True) is Channel.TELEGRAM
    assert channel(telegram_reachable=True, sms_on_platform=False, has_phone=False) is Channel.TELEGRAM
    assert channel() is Channel.SMS
    assert channel(sms_quota_left=1) is Channel.SMS


@pytest.mark.parametrize(
    "missing", [{"has_phone": False}, {"sms_on_platform": False}, {"sms_on_shop": False}, {"sms_quota_left": 0}]
)
def test_without_any_one_of_them_the_customer_is_unreachable(missing: dict[str, object]) -> None:
    assert channel(**missing) is None


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(0, []), (7, []), (8, [8]), (10, [8, 9, 10]), (20, list(range(8, 21))), (23, list(range(8, 21)))],
)
def test_the_hours_that_have_begun_today(hour: int, expected: list[int]) -> None:
    assert list(hours_to_run(hour)) == expected
