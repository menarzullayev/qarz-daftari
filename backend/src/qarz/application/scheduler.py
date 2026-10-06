"""Scheduled work (technical specification, "Worker schedule"). Times are Tashkent time.

A period of a job is done when `job_run` has its row. The row is written after the work, so a worker that
dies in the middle repeats the period: every job here must be safe to repeat. Two workers may run the same
period at once for the same reason, and nothing is sent twice.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from qarz.application.ports import Storage
from qarz.application.reminders import ReminderService
from qarz.domain.promise import TASHKENT
from qarz.domain.reminders import hours_to_run

REMINDERS = "reminders"


class Scheduler:
    def __init__(self, storage: Storage, reminders: ReminderService, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._reminders = reminders
        self._now = now or (lambda: datetime.now(UTC))

    async def tick(self) -> int:
        """Do whatever is due and not yet done. Returns how many reminders were sent."""
        local = self._now().astimezone(TASHKENT)
        sent = 0
        for hour in hours_to_run(local.hour):
            period = f"{local.date().isoformat()}T{hour:02d}"
            async with self._storage.platform() as session:
                if await session.job_done(REMINDERS, period):
                    continue
            sent += await self._reminders.run_hour(hour)
            async with self._storage.platform() as session:
                await session.finish_job(REMINDERS, period)
        return sent
