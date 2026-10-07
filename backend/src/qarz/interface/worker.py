"""Worker process: delivers the outbox. Scheduled jobs join it in later stories.

Run with:  python -m qarz.interface.worker
"""

import asyncio
import contextlib
import logging
import signal

from aiogram import Bot

from qarz.application.dispatch import Dispatcher
from qarz.application.measurement import MeasurementService
from qarz.application.reminders import ReminderService
from qarz.application.scheduler import Scheduler
from qarz.application.shop_deletion import ShopDeletionService
from qarz.application.subscription import SubscriptionService
from qarz.infrastructure.db import Database
from qarz.infrastructure.settings import Settings
from qarz.infrastructure.sms_sender import ChannelSender, NoSmsProvider
from qarz.infrastructure.telegram_sender import TelegramSender
from qarz.interface.observability import configure_logging

log = logging.getLogger("qarz.worker")
IDLE_SECONDS = 0.5
SCHEDULE_EVERY_SECONDS = 30.0


async def run(settings: Settings, stop: asyncio.Event) -> None:
    if not settings.bot_token:
        raise RuntimeError("QD_BOT_TOKEN is not set")
    database = Database(settings.database_url)
    bot = Bot(settings.bot_token)
    # No SMS provider is chosen yet: the SMS path exists, is switched off, and refuses to send.
    dispatcher = Dispatcher(database, ChannelSender(telegram=TelegramSender(bot), sms=NoSmsProvider()))
    scheduler = Scheduler(
        database,
        ReminderService(database),
        subscriptions=SubscriptionService(database),
        deletion=ShopDeletionService(database),
        measurement=MeasurementService(database),
    )
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
            if result is None or result.sent == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=IDLE_SECONDS)
    finally:
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
