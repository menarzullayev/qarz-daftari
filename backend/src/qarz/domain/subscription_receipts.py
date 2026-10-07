"""Subscription receipts (DOM-019; REQ-054, REQ-055; ADR-019).

An owner pays by card transfer outside the system and sends the receipt with what they say they paid and
for how many months. An administrator approves it, recording the months that count, or rejects it with a
reason. A decided receipt stays decided.
"""

from datetime import datetime, timedelta
from enum import StrEnum

from qarz.domain.subscription import MAX_MONTHS

SUBMITTED, APPROVED, REJECTED = "submitted", "approved", "rejected"
STATUSES = (SUBMITTED, APPROVED, REJECTED)

# The smallest price an administrator can set, and the largest price for the longest period.
MIN_AMOUNT = 1_000
MAX_AMOUNT = 10_000_000 * MAX_MONTHS
# How many receipts of one shop may wait for a decision at once.
MAX_WAITING = 3
# Technical specification, "Retention": subscription receipts are kept 3 years.
RETENTION = timedelta(days=3 * 365)
MIN_REASON, MAX_REASON = 3, 500
# The periods the bot offers; the API takes any whole number of months up to the limit.
OFFERED_MONTHS = (1, 3, 6, 12)


class ReceiptRefusal(StrEnum):
    AMOUNT_OUT_OF_RANGE = "amount_out_of_range"
    MONTHS_OUT_OF_RANGE = "months_out_of_range"
    TOO_MANY_WAITING = "too_many_waiting"


def valid_amount(amount: object) -> bool:
    # (bool is an int in Python, but true is 1 and so below the minimum.)
    return isinstance(amount, int) and MIN_AMOUNT <= amount <= MAX_AMOUNT


def valid_months(months: object) -> bool:
    return isinstance(months, int) and not isinstance(months, bool) and 1 <= months <= MAX_MONTHS


def may_submit(*, amount: object, months: object, waiting: int) -> ReceiptRefusal | None:
    """Whether a shop may send this receipt now, given how many of its receipts already wait."""
    if not valid_amount(amount):
        return ReceiptRefusal.AMOUNT_OUT_OF_RANGE
    if not valid_months(months):
        return ReceiptRefusal.MONTHS_OUT_OF_RANGE
    if waiting >= MAX_WAITING:
        return ReceiptRefusal.TOO_MANY_WAITING
    return None


def months_to_record(stated: int | None, corrected: int | None) -> int | None:
    """BR-27: "the whole months the administrator records". Theirs when given, else what the owner stated.

    None when neither is a valid number of months: the approval cannot be recorded.
    """
    chosen = stated if corrected is None else corrected
    return chosen if valid_months(chosen) else None


def clean_reason(raw: object) -> str | None:
    """A rejection reason as it is kept and told to the owner; None when it is not acceptable."""
    if not isinstance(raw, str):
        return None
    reason = " ".join(raw.split())
    return reason if MIN_REASON <= len(reason) <= MAX_REASON else None


def delete_file_after(submitted_at: datetime) -> datetime:
    return submitted_at + RETENTION


def expected_amount(price: int, months: int) -> int:
    return price * months
