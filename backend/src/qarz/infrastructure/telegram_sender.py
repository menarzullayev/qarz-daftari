"""Delivery of outbox messages through the Telegram Bot API."""

from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter

from qarz.application.ports import RecipientBlocked, RetryLater, SendFailed


class TelegramSender:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, channel: str, recipient: str, payload: dict[str, Any]) -> None:
        if channel != "telegram":
            raise SendFailed(f"channel {channel!r} is not handled by the Telegram sender")
        try:
            await self._bot.send_message(chat_id=int(recipient), **payload)
        except TelegramRetryAfter as error:
            raise RetryLater(float(error.retry_after)) from error
        except TelegramForbiddenError as error:
            raise RecipientBlocked() from error
        except (TelegramAPIError, TelegramNetworkError, ValueError) as error:
            # Never include the payload: it may contain a person's name and what they owe.
            raise SendFailed(type(error).__name__) from error
