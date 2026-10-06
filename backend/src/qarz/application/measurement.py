"""Weekly figures from the identity-free measurement events (REQ-030, ADR-010).

A week runs from Monday 00:00 to the next Monday 00:00, Tashkent time. The figures are totals over all
shops: nothing in them, or in the events they come from, names a shop, a person or an entry.
"""

import csv
import io
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta

from qarz.application.ports import Storage
from qarz.domain.promise import TASHKENT, tashkent_date

WEEK = timedelta(days=7)


def week_start_of(day: date) -> date:
    """The Monday of the week the day belongs to."""
    return day - timedelta(days=day.weekday())


def week_bounds(week_start: date) -> tuple[datetime, datetime]:
    if week_start.weekday() != 0:
        raise ValueError("a week starts on a Monday")
    start = datetime.combine(week_start, time.min, tzinfo=TASHKENT)
    return start, start + WEEK


class MeasurementService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def last_finished_week(self) -> date:
        return week_start_of(tashkent_date(self._now())) - WEEK

    async def compute_week(self, week_start: date) -> dict[str, float | None]:
        """Compute and store the figures of one week. Safe to repeat: the stored figures are replaced."""
        start, end = week_bounds(week_start)
        async with self._storage.platform() as session:
            metrics = await session.measure_between(start, end)
            await session.store_week(week_start, metrics)
        return metrics

    async def export_csv(self, weeks: int = 12) -> str:
        """The stored figures of the last weeks as CSV: one row per week and metric."""
        async with self._storage.platform() as session:
            rows = await session.stored_weeks(weeks)
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["week_start", "metric", "value"])
        for week_start, metric, value in rows:
            shown = "" if value is None else (str(int(value)) if value == int(value) else f"{value:.4f}")
            writer.writerow([week_start.isoformat(), metric, shown])
        return out.getvalue()
