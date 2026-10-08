"""Asking Telegram what a person is in a chat (Bot API `getChatMember`).

Used for one thing: whether someone who pressed a button in the review group administers that group
(DEC-064). The answer is always asked for at that moment and never kept.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError

log = logging.getLogger("qarz.telegram_members")

# A button press waits for this answer; Telegram shows the presser a spinner meanwhile.
REQUEST_TIMEOUT_SECONDS = 5


class TelegramMemberReader:
    """Each question opens its own Bot API session and closes it, so nothing outlives the request."""

    def __init__(self, open_bot: Callable[[], AbstractAsyncContextManager[Any]]) -> None:
        self._open_bot = open_bot

    @classmethod
    def for_token(cls, token: str) -> "TelegramMemberReader":
        return cls(lambda: Bot(token).context())

    async def status(self, chat_id: int, user_id: int) -> str | None:
        try:
            async with self._open_bot() as bot:
                member = await bot.get_chat_member(chat_id, user_id, request_timeout=REQUEST_TIMEOUT_SECONDS)
        except (TelegramAPIError, TelegramNetworkError, OSError, TimeoutError) as error:
            # Never who was asked about: only what kind of failure it was. No answer is no right.
            log.warning("telegram_member_failed", extra={"error": type(error).__name__})
            return None
        status = getattr(member, "status", None)
        if not isinstance(status, str):
            return None
        # aiogram gives a string enumeration; its value is the Bot API's own word.
        return str(getattr(status, "value", status))
