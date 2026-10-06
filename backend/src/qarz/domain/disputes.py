"""When a customer may dispute an entry (domain rule BR-11).

A dispute is the customer's only way to object: they are never asked to confirm anything (BR-10).
"""

from datetime import datetime, timedelta
from enum import StrEnum

from qarz.domain.ledger import EntryKind

DISPUTE_WINDOW = timedelta(days=30)
REASON_MIN, REASON_MAX = 3, 300


class DisputeRefusal(StrEnum):
    NOT_A_DEBT = "not_a_debt"  # only an entry that increases the debt can be disputed
    REVERSED = "reversed"  # the shop has already cancelled it
    ALREADY_DISPUTED = "already_disputed"  # once per entry, whatever became of the first dispute
    TOO_LATE = "too_late"


def may_dispute(
    *, kind: EntryKind, is_reversed: bool, disputed_before: bool, notified_at: datetime, now: datetime
) -> DisputeRefusal | None:
    """Returns None when the entry may be disputed.

    `notified_at` is when the customer could first have known of the entry: its own time, or the time
    their link was made if that was later. The last moment of the thirtieth day is still in time.
    """
    if kind not in (EntryKind.CREDIT, EntryKind.OPENING):
        return DisputeRefusal.NOT_A_DEBT
    if is_reversed:
        return DisputeRefusal.REVERSED
    if disputed_before:
        return DisputeRefusal.ALREADY_DISPUTED
    if now - notified_at > DISPUTE_WINDOW:
        return DisputeRefusal.TOO_LATE
    return None


def clean_reason(raw: str) -> str | None:
    """The reason with whitespace tidied, or None when it is too short or too long to be one."""
    reason = " ".join(raw.split())
    return reason if REASON_MIN <= len(reason) <= REASON_MAX else None
