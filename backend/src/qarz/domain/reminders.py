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
    """What one reminder states: uncovered and due, without entries under open dispute (BR-13).

    One reminder speaks of everything the customer owes: the so'm amount and, in a shop that works in
    dollars, the dollar amount (whole cents) beside it. They are two figures; at least one is above zero.
    """

    kind: ReminderKind
    amount: int  # whole so'm
    amount_usd: int = 0  # whole cents


def amount_to_mention(status: OverdueStatus) -> int:
    """Of one currency's book: what is due today and what is overdue, which are the same currency."""
    return status.reminder_due_today_amount + status.reminder_overdue_amount


def plan_automatic(
    status: OverdueStatus, last_sent: date | None, today: date, dollars: OverdueStatus | None = None
) -> ReminderPlan | None:
    """The automatic reminder owed today, if any.

    One on the promised date itself; while the debt stays overdue, at most one every seven days after
    the last one. `last_sent` is the day of the customer's last automatic reminder. Never two in one day.

    `status` is the so'm book's and `dollars` the dollar book's, when the shop works in dollars. The
    limits are per customer, not per currency (INV-14): a debt due in either currency is the ground, and
    the one reminder states both amounts.
    """
    if last_sent == today:
        return None
    books = [status] if dollars is None else [status, dollars]
    amount, amount_usd = amount_to_mention(status), 0 if dollars is None else amount_to_mention(dollars)
    if any(book.reminder_due_today_amount > 0 for book in books):
        return ReminderPlan(ReminderKind.DUE_TODAY, amount, amount_usd)
    if any(book.reminder_overdue_amount > 0 for book in books) and (
        last_sent is None or today - last_sent >= REPEAT_AFTER
    ):
        return ReminderPlan(ReminderKind.OVERDUE, amount, amount_usd)
    return None


def plan_manual(status: OverdueStatus, dollars: OverdueStatus | None = None) -> ReminderPlan | None:
    """A manual reminder needs the same ground as an automatic one: something overdue or due today (BR-17).

    That it is sent at most once a day per customer is enforced where reminders are stored.
    """
    books = [status] if dollars is None else [status, dollars]
    amount, amount_usd = amount_to_mention(status), 0 if dollars is None else amount_to_mention(dollars)
    if amount <= 0 and amount_usd <= 0:
        return None
    overdue = any(book.reminder_overdue_amount > 0 for book in books)
    return ReminderPlan(ReminderKind.OVERDUE if overdue else ReminderKind.DUE_TODAY, amount, amount_usd)


def choose_channel(
    *, telegram_reachable: bool, has_phone: bool, sms_on_platform: bool, sms_on_shop: bool, sms_quota_left: int
) -> Channel | None:
    """BR-18. None means the customer cannot be reached and is listed for the shop as such.

    An SMS states so'm only (its wordings are registered with the SMS provider one by one, and none
    carries dollars yet): the caller passes `has_phone=False` for a reminder with no so'm amount.
    """
    if telegram_reachable:
        return Channel.TELEGRAM
    if has_phone and sms_on_platform and sms_on_shop and sms_quota_left > 0:
        return Channel.SMS
    return None


def hours_to_run(current_hour: int) -> range:
    """The reminder hours of today that have begun: a worker that starts late still serves them."""
    return range(FIRST_HOUR, min(current_hour, LAST_HOUR) + 1)
