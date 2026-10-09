"""Worker process: delivers the outbox, runs the scheduled jobs, and watches the service (DEC-078).

Run with:  python -m qarz.interface.worker
"""

import asyncio
import contextlib
import logging
import signal
from datetime import UTC, datetime

from aiogram import Bot

from qarz.application.dispatch import Dispatcher
from qarz.application.exports import ExportService
from qarz.application.files import FileService
from qarz.application.imports import ImportService
from qarz.application.measurement import MeasurementService
from qarz.application.ops_watch import OpsWatch, Sources, WorkerHealth
from qarz.application.payment_notices import PaymentNoticeService
from qarz.application.reminders import ReminderService
from qarz.application.scheduler import Scheduler
from qarz.application.shop_deletion import ShopDeletionService
from qarz.application.subscription import SubscriptionService
from qarz.domain.exports import MAX_EXPORT_BYTES
from qarz.infrastructure.db import Database
from qarz.infrastructure.file_store import build_file_store
from qarz.infrastructure.ops_probes import ApiProbe, disk_shares, read_figure_files
from qarz.infrastructure.settings import Settings
from qarz.infrastructure.sms_sender import ChannelSender, build_sms_provider, platform_switch
from qarz.infrastructure.telegram_alerts import TelegramAlerts
from qarz.infrastructure.telegram_sender import TelegramSender
from qarz.interface.observability import configure_logging

log = logging.getLogger("qarz.worker")
IDLE_SECONDS = 0.5
SCHEDULE_EVERY_SECONDS = 30.0
WATCH_EVERY_SECONDS = 60.0


def build_watch(settings: Settings, database: Database, bot: Bot, health: WorkerHealth) -> OpsWatch:
    """The operations watch as this deployment configures it. What is not configured is not watched."""
    backup_dir, files_dir = settings.alert_backup_figures_dir, settings.alert_files_figures_dir
    disks = settings.alert_disks()
    sources = Sources(
        backup=(lambda: read_figure_files(backup_dir)) if backup_dir else None,
        files=(lambda: read_figure_files(files_dir)) if files_dir else None,
        disk=(lambda: disk_shares(disks)) if disks else None,
        api=ApiProbe(settings.alert_api_url, settings.metrics_token) if settings.alert_api_url else None,
        metrics=bool(settings.metrics_token),
        health=health,
    )
    return OpsWatch(database, TelegramAlerts(bot), chats=settings.alert_chats(), sources=sources)


async def watch_loop(watch: OpsWatch, stop: asyncio.Event) -> None:
    """A round a minute, in its own task: a round that waits for Telegram or the API holds no message back."""
    while not stop.is_set():
        try:
            await watch.run_once()
        except Exception:
            log.exception("watch_failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=WATCH_EVERY_SECONDS)


async def run(settings: Settings, stop: asyncio.Event) -> None:
    if not settings.bot_token:
        raise RuntimeError("QD_BOT_TOKEN is not set")
    if not settings.worker_database_url:
        raise RuntimeError("QD_WORKER_DATABASE_URL is not set")
    # The worker's own role (qd_worker): it holds the outbox and the scheduled jobs, and nothing of the
    # administrators' side or of sign-in.
    database = Database(settings.worker_database_url, statement_timeout_ms=settings.worker_statement_timeout_ms)
    bot = Bot(settings.bot_token)
    # SMS goes through Eskiz when its account is configured and the platform switch `sms_on` is on at the
    # moment of sending; otherwise the SMS path refuses to send. The switch is off by default.
    sms = build_sms_provider(settings, switched_on=platform_switch(database))
    dispatcher = Dispatcher(database, ChannelSender(telegram=TelegramSender(bot), sms=sms))
    file_store = build_file_store(settings, max_object_bytes=MAX_EXPORT_BYTES)
    files = FileService(database, file_store)
    scheduler = Scheduler(
        database,
        ReminderService(database),
        subscriptions=SubscriptionService(database),
        # Erasing a shop deletes its receipts from the file store too.
        deletion=ShopDeletionService(database, files=file_store),
        # Stale payment notices are marked expired and receipts past their retention are deleted.
        notices=PaymentNoticeService(database, files),
        # Exports that were asked for are written, a few at each tick.
        exports=ExportService(database, files),
        # Import files are checked, and confirmed imports applied and undone, a few steps at each tick.
        imports=ImportService(database, files),
        measurement=MeasurementService(database),
        # Used sign-in data past its expiry and sessions that expired or were revoked are deleted.
        sign_in_cleanup=True,
        # The stored open debts are compared with the ledger once a day.
        ledger_check=True,
    )
    health = WorkerHealth(started_at=datetime.now(UTC))
    watching = asyncio.create_task(watch_loop(build_watch(settings, database, bot, health), stop))
    next_schedule = 0.0
    try:
        while not stop.is_set():
            if asyncio.get_running_loop().time() >= next_schedule:
                next_schedule = asyncio.get_running_loop().time() + SCHEDULE_EVERY_SECONDS
                try:
                    await scheduler.tick()
                except Exception:
                    # Tried again at the next tick; a period is marked done only after its work.
                    log.exception("schedule_failed")
            try:
                result = await dispatcher.run_once()
            except Exception:
                # Never log message contents; the exception text here comes from the database driver.
                log.exception("dispatch_failed")
                result = None
            else:
                health.dispatched(datetime.now(UTC))
            if result is None or result.sent == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=IDLE_SECONDS)
    finally:
        watching.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watching
        await bot.session.close()
        await database.dispose()


def main() -> None:
    configure_logging()
    stop = asyncio.Event()

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for name in ("SIGINT", "SIGTERM"):
            # Not available on Windows, where Ctrl+C still raises KeyboardInterrupt.
            with contextlib.suppress(NotImplementedError, AttributeError):
                loop.add_signal_handler(getattr(signal, name), stop.set)
        await run(Settings(), stop)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
