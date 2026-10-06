"""Downloading a file a person sent to the bot (Bot API `getFile`)."""

import io
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError

log = logging.getLogger("qarz.telegram_files")


class TelegramFileFetcher:
    """Each download opens its own Bot API session and closes it, so nothing outlives the request."""

    def __init__(self, open_bot: Callable[[], AbstractAsyncContextManager[Any]]) -> None:
        self._open_bot = open_bot

    @classmethod
    def for_token(cls, token: str) -> "TelegramFileFetcher":
        return cls(lambda: Bot(token).context())

    async def fetch(self, file_id: str, max_bytes: int) -> bytes | None:
        try:
            async with self._open_bot() as bot:
                described = await bot.get_file(file_id)
                size, path = described.file_size, described.file_path
                # The size is checked before a single byte is downloaded; a file without one is not trusted.
                if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= max_bytes or not path:
                    return None
                buffer = io.BytesIO()
                await bot.download_file(path, destination=buffer)
                data = buffer.getvalue()
        except (TelegramAPIError, TelegramNetworkError, OSError, TimeoutError) as error:
            # Never the file identifier or the content: only what kind of failure it was.
            log.warning("telegram_file_failed", extra={"error": type(error).__name__})
            return None
        return data if 0 < len(data) <= max_bytes else None
