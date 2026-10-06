"""When a reminder may be sent, and through which channel (REQ-023, REQ-025, REQ-043; BR-17, BR-18).

These limits belong to the system: no shop setting can raise them (REQ-N10).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from qarz.domain.ledger import OverdueStatus

REPEAT_AFTER = timedelta(days=7)
FIRST_HOUR, LAST_HOUR = 8, 20  # Tashkent time; the hour a shop may choose (REQ-042)
TEMPLATES = (1, 2, 3)


class ReminderKind(StrEnum):
    DUE_TODAY = "due_today"
    OVERDUE = "overdue"


class Channel(StrEnum):
    TELEGRAM = "telegram"
    SMS = "sms"


@dataclass(frozen=True, slots=True)
class ReminderPlan:
    kind: ReminderKind
    amount: int  # what the message states: uncovered and due, without entries under open dispute (BR-13)


def amount_to_mention(status: OverdueStatus) -> int:
    return status.reminder_due_today_amount + status.reminder_overdue_amount


def plan_automatic(status: OverdueStatus, last_sent: date | None, today: date) -> ReminderPlan | None:
    """The automatic reminder owed today, if any.

    One on the promised date itself; while the debt stays overdue, at most one every seven days after
    the last one. `last_sent` is the day of the customer's last automatic reminder. Never two in one day.
    """
    if last_sent == today:
        return None
    amount = amount_to_mention(status)
    if status.reminder_due_today_amount > 0:
        return ReminderPlan(ReminderKind.DUE_TODAY, amount)
    if status.reminder_overdue_amount > 0 and (last_sent is None or today - last_sent >= REPEAT_AFTER):
        return ReminderPlan(ReminderKind.OVERDUE, amount)
    return None


def plan_manual(status: OverdueStatus) -> ReminderPlan | None:
    """A manual reminder needs the same ground as an automatic one: something overdue or due today (BR-17).

    That it is sent at most once a day per customer is enforced where reminders are stored.
    """
    amount = amount_to_mention(status)
    if amount <= 0:
        return None
    kind = ReminderKind.OVERDUE if status.reminder_overdue_amount > 0 else ReminderKind.DUE_TODAY
    return ReminderPlan(kind, amount)


def choose_channel(
    *, telegram_reachable: bool, has_phone: bool, sms_on_platform: bool, sms_on_shop: bool, sms_quota_left: int
) -> Channel | None:
    """BR-18. None means the customer cannot be reached and is listed for the shop as such."""
    if telegram_reachable:
        return Channel.TELEGRAM
    if has_phone and sms_on_platform and sms_on_shop and sms_quota_left > 0:
        return Channel.SMS
    return None


def hours_to_run(current_hour: int) -> range:
    """The reminder hours of today that have begun: a worker that starts late still serves them."""
    return range(FIRST_HOUR, min(current_hour, LAST_HOUR) + 1)
