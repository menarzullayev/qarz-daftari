"""Scheduled work (technical specification, "Worker schedule"). Times are Tashkent time.

A period of a job is done when `job_run` has its row. The row is written after the work, so a worker that
dies in the middle repeats the period: every job here must be safe to repeat. Two workers may run the same
period at once for the same reason, and nothing is sent twice.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from qarz.application.exports import ExportService
from qarz.application.measurement import MeasurementService
from qarz.application.payment_notices import PaymentNoticeService
from qarz.application.ports import Storage
from qarz.application.reminders import ReminderService
from qarz.application.shop_deletion import ShopDeletionService
from qarz.application.subscription import SubscriptionService
from qarz.domain.promise import TASHKENT
from qarz.domain.reminders import hours_to_run

REMINDERS = "reminders"
SUBSCRIPTIONS = "subscriptions"
SUBSCRIPTION_HOUR = 9
ERASURE = "erasure"
MEASURE_WEEK = "measure_week"
RECEIPTS = "receipts"


class Scheduler:
    def __init__(
        self,
        storage: Storage,
        reminders: ReminderService,
        now: Callable[[], datetime] | None = None,
        subscriptions: SubscriptionService | None = None,
        deletion: ShopDeletionService | None = None,
        measurement: MeasurementService | None = None,
        notices: PaymentNoticeService | None = None,
        exports: ExportService | None = None,
    ) -> None:
        self._storage = storage
        self._reminders = reminders
        self._subscriptions = subscriptions
        self._deletion = deletion
        self._measurement = measurement
        self._notices = notices
        self._exports = exports
        self._now = now or (lambda: datetime.now(UTC))

    async def tick(self) -> int:
        """Do whatever is due and not yet done. Returns how many reminders were sent."""
        local = self._now().astimezone(TASHKENT)
        sent = 0
        if self._exports is not None:
            # At every tick, not once a period: each job is claimed by exactly one worker, which is
            # what makes this safe to repeat. First, so that someone waiting for a file is not kept
            # behind the hourly work.
            await self._exports.run_pending()
        for hour in hours_to_run(local.hour):
            period = f"{local.date().isoformat()}T{hour:02d}"
            async with self._storage.platform() as session:
                if await session.job_done(REMINDERS, period):
                    continue
            sent += await self._reminders.run_hour(hour)
            async with self._storage.platform() as session:
                await session.finish_job(REMINDERS, period)
        if self._deletion is not None:
            period = f"{local.date().isoformat()}T{local.hour:02d}"
            async with self._storage.platform() as session:
                done = await session.job_done(ERASURE, period)
            if not done:
                await self._deletion.erase_due()
                async with self._storage.platform() as session:
                    await session.finish_job(ERASURE, period)
        if self._notices is not None:
            # Expiry of payment notices and deletion of receipts past their retention, once an hour.
            period = f"{local.date().isoformat()}T{local.hour:02d}"
            async with self._storage.platform() as session:
                done = await session.job_done(RECEIPTS, period)
            if not done:
                await self._notices.run_hourly()
                async with self._storage.platform() as session:
                    await session.finish_job(RECEIPTS, period)
        if self._measurement is not None and local.hour >= SUBSCRIPTION_HOUR:
            # The week that ended last Sunday, computed once; any day of the following week will do.
            week = self._measurement.last_finished_week()
            async with self._storage.platform() as session:
                done = await session.job_done(MEASURE_WEEK, week.isoformat())
            if not done:
                await self._measurement.compute_week(week)
                async with self._storage.platform() as session:
                    await session.finish_job(MEASURE_WEEK, week.isoformat())
        if self._subscriptions is not None and local.hour >= SUBSCRIPTION_HOUR:
            period = local.date().isoformat()
            async with self._storage.platform() as session:
                done = await session.job_done(SUBSCRIPTIONS, period)
            if not done:
                await self._subscriptions.run_daily()
                async with self._storage.platform() as session:
                    await session.finish_job(SUBSCRIPTIONS, period)
        return sent
