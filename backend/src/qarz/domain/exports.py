"""Rules of shop exports (REQ-028). An export is a read of the shop's own data, written by the worker.

The documents fix that exports are spreadsheet files, run as jobs, and are for managers and owners. The
numbers below are decisions of this story where the documents are silent.
"""

from datetime import datetime, timedelta
from enum import StrEnum

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"

# How many exports one shop may ask for in one Tashkent day. Failed ones do not count.
DAILY_LIMIT = 5
# How long a finished workbook is kept before the cleanup deletes it.
EXPORT_RETENTION = timedelta(days=7)
# A job marked as running whose worker has been silent this long is taken to have died with it.
STALE_AFTER = timedelta(minutes=15)
# How many times a job is started before it is given up.
MAX_ATTEMPTS = 3
# The largest workbook that is kept. A shop of 200,000 entries makes one of about 34 MB.
MAX_EXPORT_BYTES = 256 * 1024 * 1024


class ExportRefusal(StrEnum):
    IN_PROGRESS = "in_progress"  # one export of the shop is already waiting or being written
    DAILY_LIMIT = "daily_limit"


class ExportError(StrEnum):
    """Why a job failed: a kind only, never a text that could carry data."""

    INTERRUPTED = "interrupted"
    TIMEOUT = "timeout"
    FILE_STORE = "file_store"
    INTERNAL = "internal"


def may_request(*, in_progress: bool, today_count: int) -> ExportRefusal | None:
    """Returns None when the shop may ask for another export now."""
    if in_progress:
        return ExportRefusal.IN_PROGRESS
    if today_count >= DAILY_LIMIT:
        return ExportRefusal.DAILY_LIMIT
    return None


def stale_before(now: datetime) -> datetime:
    return now - STALE_AFTER


def gives_up(attempts: int) -> bool:
    """`attempts` counts the start just made. The third start is still made; a fourth is not."""
    return attempts > MAX_ATTEMPTS


def delete_after(finished_at: datetime) -> datetime:
    return finished_at + EXPORT_RETENTION


def signed_effect(kind: str, amount: int, reversed_kind: str | None) -> int:
    """What an entry does to the debt: a sale or an opening balance raises it, a payment lowers it, and a
    reversal undoes exactly what the entry it reverses did."""
    if kind == "reversal":
        return -amount if reversed_kind in ("credit", "opening") else amount
    return amount if kind in ("credit", "opening") else -amount


def counts(kind: str, is_reversed: bool) -> bool:
    """Whether an entry still counts in totals: not a reversal and not reversed (INV-2)."""
    return kind != "reversal" and not is_reversed
