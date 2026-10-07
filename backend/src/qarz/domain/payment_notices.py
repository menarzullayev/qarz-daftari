"""Payment notices: a customer says they paid; only a staff decision changes the balance (BR-14, INV-15).

A notice is open ("sent") until staff accept or decline it, or until it is fourteen days old (domain
model, lifecycle table).
"""

from datetime import datetime, timedelta
from enum import StrEnum

from qarz.domain.files import RECEIPT_RETENTION

# The same bounds as a ledger entry: an accepted notice becomes a payment of this amount.
MIN_AMOUNT = 100
MAX_AMOUNT = 100_000_000
# How many notices of one customer may wait for the shop at once.
MAX_OPEN_NOTICES = 3
NOTICE_LIFETIME = timedelta(days=14)

SENT, ACCEPTED, DECLINED, EXPIRED = "sent", "accepted", "declined", "expired"


class NoticeRefusal(StrEnum):
    AMOUNT_OUT_OF_RANGE = "amount_out_of_range"
    EXCEEDS_BALANCE = "exceeds_balance"
    TOO_MANY_OPEN = "too_many_open"


def valid_amount(amount: object) -> bool:
    """A whole number of UZS within the bounds. `True` counts as 1 in Python and falls below them."""
    return isinstance(amount, int) and MIN_AMOUNT <= amount <= MAX_AMOUNT


def may_send(*, amount: int, balance: int, open_notices: int) -> NoticeRefusal | None:
    """Returns None when the customer may send a notice for this amount.

    `open_notices` counts the customer's notices that still wait for the shop, not the stale ones.
    """
    if not valid_amount(amount):
        return NoticeRefusal.AMOUNT_OUT_OF_RANGE
    if amount > balance:
        return NoticeRefusal.EXCEEDS_BALANCE
    if open_notices >= MAX_OPEN_NOTICES:
        return NoticeRefusal.TOO_MANY_OPEN
    return None


def is_stale(created_at: datetime, now: datetime) -> bool:
    """The last moment of the fourteenth day is still in time."""
    return now - created_at > NOTICE_LIFETIME


def effective_status(status: str, created_at: datetime, now: datetime) -> str:
    """A notice nobody decided in time is expired, whether or not the hourly job has marked it yet."""
    return EXPIRED if status == SENT and is_stale(created_at, now) else status


def receipt_bound(created_at: datetime) -> datetime:
    """The latest a receipt can be needed: its notice closes within the lifetime, then the retention runs."""
    return created_at + NOTICE_LIFETIME + RECEIPT_RETENTION
