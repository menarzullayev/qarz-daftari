"""Delivery of outbox messages through the Telegram Bot API."""

from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup
from pydantic import ValidationError

from qarz.application.ports import RecipientBlocked, RetryLater, SendFailed

SEND, EDIT_TEXT, EDIT_BUTTONS = "sendMessage", "editMessageText", "editMessageReplyMarkup"


class TelegramSender:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, channel: str, recipient: str, payload: dict[str, Any]) -> None:
        if channel != "telegram":
            raise SendFailed(f"channel {channel!r} is not handled by the Telegram sender")
        arguments = dict(payload)
        method = arguments.pop("method", SEND)
        try:
            if "reply_markup" in arguments:
                arguments["reply_markup"] = InlineKeyboardMarkup.model_validate(arguments["reply_markup"])
            if method == SEND:
                await self._bot.send_message(chat_id=int(recipient), **arguments)
            elif method == EDIT_TEXT:
                await self._bot.edit_message_text(chat_id=int(recipient), **arguments)
            elif method == EDIT_BUTTONS:
                await self._bot.edit_message_reply_markup(chat_id=int(recipient), **arguments)
            else:
                raise SendFailed("unknown method")
        except TelegramRetryAfter as error:
            raise RetryLater(float(error.retry_after)) from error
        except TelegramForbiddenError as error:
            raise RecipientBlocked() from error
        except (TelegramAPIError, TelegramNetworkError, ValueError, TypeError, ValidationError) as error:
            # Never include the payload: it may contain a person's name and what they owe.
            raise SendFailed(type(error).__name__) from None
