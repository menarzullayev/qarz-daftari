"""Scheduled work (technical specification, "Worker schedule"). Times are Tashkent time.

A period of a job is done when `job_run` has its row. The row is written after the work, so a worker that
dies in the middle repeats the period: every job here must be safe to repeat. Two workers may run the same
period at once for the same reason, and nothing is sent twice.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from qarz.application.exports import ExportService
from qarz.application.imports import ImportService
from qarz.application.measurement import MeasurementService
from qarz.application.payment_notices import PaymentNoticeService
from qarz.application.ports import Storage
from qarz.application.reminders import ReminderService
from qarz.application.shop_deletion import ShopDeletionService
from qarz.application.subscription import SubscriptionService
from qarz.domain import platform_settings
from qarz.domain.ops_alerts import LEDGER_SERIES, STOCK_SERIES
from qarz.domain.promise import TASHKENT
from qarz.domain.reminders import hours_to_run

REMINDERS = "reminders"
SUBSCRIPTIONS = "subscriptions"
SUBSCRIPTION_HOUR = 9
ERASURE = "erasure"
MEASURE_WEEK = "measure_week"
RECEIPTS = "receipts"
SIGN_IN_CLEANUP = "sign_in_cleanup"
# The daily comparison of the stored open debts with the ledger (DEC-078).
LEDGER_CHECK = "ledger_check"
# The daily comparison of the stock's kept figures (on hand, owed to suppliers) with their ledgers.
STOCK_CHECK = "stock_check"
STOCK_SWITCH = "stock_on"


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
        imports: ImportService | None = None,
        sign_in_cleanup: bool = False,
        ledger_check: bool = False,
        stock_check: bool = False,
    ) -> None:
        self._storage = storage
        self._reminders = reminders
        self._subscriptions = subscriptions
        self._deletion = deletion
        self._measurement = measurement
        self._notices = notices
        self._exports = exports
        self._imports = imports
        self._sign_in_cleanup = sign_in_cleanup
        self._ledger_check = ledger_check
        # The day and hour of the last attempt: a check that fails is tried again an hour later, not at
        # every tick, because what makes it fail is most likely its own cost.
        self._ledger_tried: tuple[str, int] | None = None
        self._stock_check = stock_check
        self._stock_tried: tuple[str, int] | None = None
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
        if self._imports is not None:
            # Likewise at every tick: a file waiting to be checked, a batch waiting to be applied or undone.
            await self._imports.run_pending()
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
        # Sign-in records past their expiry, sessions that expired or were revoked, the administrator's
        # too (security review, finding 9). Once an hour; deleting what is already gone is safe to repeat.
        if self._sign_in_cleanup:
            period = f"{local.date().isoformat()}T{local.hour:02d}"
            async with self._storage.platform() as session:
                if not await session.job_done(SIGN_IN_CLEANUP, period):
                    await session.purge_expired_sign_ins()
                    await session.finish_job(SIGN_IN_CLEANUP, period)
        if self._notices is not None:
            # Expiry of payment notices and deletion of receipts past their retention (those of payment
            # notices after 90 days, those of the subscription after 3 years), once an hour.
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
        if self._ledger_check:
            # Once a day, at the first tick of the day (just after midnight in Tashkent, the quietest
            # hour; on the day a release first brings it, at once). Last, so that its failure holds
            # nothing else back. One statement over every shop, bounded by the worker's statement
            # timeout; the count is kept as a sample, where the operations watch reads it, and a day
            # without a finished check is itself an alert (JobNotRunning).
            period = local.date().isoformat()
            async with self._storage.platform() as session:
                done = await session.job_done(LEDGER_CHECK, period)
            if not done and self._ledger_tried != (period, local.hour):
                self._ledger_tried = (period, local.hour)
                async with self._storage.platform() as session:
                    count = await session.ledger_mismatch_count()
                    await session.add_ops_samples(self._now(), {LEDGER_SERIES: float(count)})
                    await session.finish_job(LEDGER_CHECK, period)
        if self._stock_check:
            # The same once a day for the stock's two kept figures, as a job of its own after the
            # ledger's: each is one statement over every shop whose cost grows with the history it
            # adds up (measured: 0.4 s and 0.15 s over two million movements and 400 000 supplier
            # entries), which is too much for the watch's every minute and nothing for once a day; and
            # one that is cancelled by the statement timeout must not take the other's day with it.
            # While the stock is switched off its tables are read by nothing, this included: the day
            # is finished without a sample, and the last sample taken while it was on stays.
            period = local.date().isoformat()
            async with self._storage.platform() as session:
                done = await session.job_done(STOCK_CHECK, period)
            if not done and self._stock_tried != (period, local.hour):
                self._stock_tried = (period, local.hour)
                async with self._storage.platform() as session:
                    stored = await session.platform_setting(STOCK_SWITCH)
                    if platform_settings.effective(STOCK_SWITCH, stored) is True:
                        counts = await session.stock_mismatch_counts()
                        samples = {STOCK_SERIES[label]: float(count) for label, count in counts.items()}
                        await session.add_ops_samples(self._now(), samples)
                    await session.finish_job(STOCK_CHECK, period)
        return sent
