"""Support access: how long it may last and what state it is in (BR-31; DOM-022).

An administrator asks for it with a reason; it is active at once, lasts at most 24 hours, and ends by
itself or when the owner or the administrator closes it.
"""

from datetime import datetime, timedelta

MAX_HOURS = 24  # BR-31
DEFAULT_HOURS = 1
MIN_REASON, MAX_REASON = 3, 500

ACTIVE, EXPIRED, CLOSED = "active", "expired", "closed"


def valid_hours(hours: object) -> bool:
    # bool is an int in Python; true must not be read as one hour.
    return isinstance(hours, int) and not isinstance(hours, bool) and 1 <= hours <= MAX_HOURS


def ends_at(now: datetime, hours: int) -> datetime:
    if not valid_hours(hours):
        raise ValueError(f"a support access lasts between 1 and {MAX_HOURS} hours")
    return now + timedelta(hours=hours)


def state(starts_at: datetime, ends: datetime, closed_at: datetime | None, now: datetime) -> str:
    """Closed wins over expired: an access ended early stays "closed" after its time has passed too."""
    if closed_at is not None:
        return CLOSED
    return ACTIVE if starts_at <= now < ends else EXPIRED
