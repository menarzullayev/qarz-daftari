"""Operations alerts straight through the Telegram Bot API (DEC-078), never through the outbox.

An alert about the outbox not draining cannot wait in that outbox, so the watch sends its message itself,
with a short timeout, and reports what became of it in one fixed word. Nothing of Telegram's own answer
is kept or logged.
"""

import asyncio

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)

from qarz.application.ops_watch import REFUSED, REJECTED, UNREACHABLE, AlertNotDelivered

TIMEOUT_SECONDS = 10


class TelegramAlerts:
    def __init__(self, bot: Bot, *, timeout: int = TIMEOUT_SECONDS) -> None:
        self._bot = bot
        self._timeout = timeout

    async def send(self, chat_id: int, text: str) -> None:
        try:
            # Plain text: no formatting for Telegram to refuse. The outer limit covers what the HTTP
            # client's own timeout does not (a connection that is accepted and then says nothing).
            await asyncio.wait_for(
                self._bot.send_message(chat_id=chat_id, text=text, request_timeout=self._timeout),
                timeout=self._timeout + 5,
            )
        except TelegramUnauthorizedError:
            raise AlertNotDelivered(REFUSED) from None
        except (TelegramNetworkError, TelegramServerError, TelegramRetryAfter, TimeoutError, OSError):
            raise AlertNotDelivered(UNREACHABLE) from None
        except (TelegramAPIError, ValueError, TypeError):
            # The chat does not exist, the bot is not in it, or may not write there.
            raise AlertNotDelivered(REJECTED) from None

    async def state(self) -> str:
        """Whether Telegram accepts the bot's token: `ok`, `refused`, or `unreachable`."""
        try:
            await asyncio.wait_for(self._bot.get_me(request_timeout=self._timeout), timeout=self._timeout + 5)
        except TelegramUnauthorizedError:
            return REFUSED
        except Exception:
            return UNREACHABLE
        return "ok"
