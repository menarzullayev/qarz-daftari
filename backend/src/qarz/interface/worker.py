"""Worker process: delivers the outbox. Scheduled jobs join it in later stories.

Run with:  python -m qarz.interface.worker
"""

import asyncio
import contextlib
import logging
import signal

from aiogram import Bot

from qarz.application.dispatch import Dispatcher
from qarz.infrastructure.db import Database
from qarz.infrastructure.settings import Settings
from qarz.infrastructure.telegram_sender import TelegramSender

log = logging.getLogger("qarz.worker")
IDLE_SECONDS = 0.5


async def run(settings: Settings, stop: asyncio.Event) -> None:
    if not settings.bot_token:
        raise RuntimeError("QD_BOT_TOKEN is not set")
    database = Database(settings.database_url)
    bot = Bot(settings.bot_token)
    dispatcher = Dispatcher(database, TelegramSender(bot))
    try:
        while not stop.is_set():
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
    logging.basicConfig(level=logging.INFO)
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
