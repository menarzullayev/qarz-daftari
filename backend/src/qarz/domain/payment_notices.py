"""Payment notices: a customer says they paid; only a staff decision changes the balance (BR-14, INV-15).

A notice is open ("sent") until staff accept or decline it, or until it is fourteen days old (domain
model, lifecycle table).
"""

from datetime import datetime, timedelta
from enum import StrEnum

from qarz.domain import money
from qarz.domain.files import RECEIPT_RETENTION
from qarz.domain.money import Currency

# The same bounds as a ledger entry: an accepted notice becomes a payment of this amount, in the same
# currency. These two are the so'm bounds, under the names they had before dollars existed.
MIN_AMOUNT = money.RULES[Currency.UZS].min_entry
MAX_AMOUNT = money.RULES[Currency.UZS].max_entry
# How many notices of one customer may wait for the shop at once.
MAX_OPEN_NOTICES = 3
NOTICE_LIFETIME = timedelta(days=14)

SENT, ACCEPTED, DECLINED, EXPIRED = "sent", "accepted", "declined", "expired"


class NoticeRefusal(StrEnum):
    AMOUNT_OUT_OF_RANGE = "amount_out_of_range"
    EXCEEDS_BALANCE = "exceeds_balance"
    TOO_MANY_OPEN = "too_many_open"


def valid_amount(amount: object, currency: Currency = Currency.UZS) -> bool:
    """A whole number of the currency's minor units within the bounds of one entry. `True` is not a number."""
    return money.valid_entry_amount(currency, amount)


def may_send(
    *, amount: int, balance: int, open_notices: int, currency: Currency = Currency.UZS
) -> NoticeRefusal | None:
    """Returns None when the customer may send a notice for this amount.

    `balance` is what the customer owes in the notice's currency: a notice of dollars paid is compared
    with the dollar debt alone. `open_notices` counts the customer's notices that still wait for the
    shop, not the stale ones, whatever their currency.
    """
    if not valid_amount(amount, currency):
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
