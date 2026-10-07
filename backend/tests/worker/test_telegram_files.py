"""Fetching a file a person sent to the bot, against a fake bot. The real Bot API is never called."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import GetFile

from qarz.infrastructure.telegram_files import TelegramFileFetcher

CONTENT = b"\xff\xd8\xff receipt bytes"
SIZE = len(CONTENT)


class FakeBot:
    def __init__(self, size: Any = SIZE, path: str | None = "photos/file_1.jpg", content: bytes = CONTENT) -> None:
        self.size, self.path, self.content = size, path, content
        self.calls: list[tuple[str, str]] = []
        self.closed = False
        self.fail: Exception | None = None

    async def get_file(self, file_id: str) -> Any:
        self.calls.append(("get_file", file_id))
        if self.fail is not None:
            raise self.fail
        return SimpleNamespace(file_size=self.size, file_path=self.path)

    async def download_file(self, path: str, destination: Any) -> None:
        self.calls.append(("download_file", path))
        destination.write(self.content)


def fetch(bot: FakeBot, limit: int = 1024) -> bytes | None:
    @asynccontextmanager
    async def opened() -> Any:
        try:
            yield bot
        finally:
            bot.closed = True

    return asyncio.run(TelegramFileFetcher(opened).fetch("AgACAgIAAxkBAAIB", limit))


def test_a_file_is_described_then_downloaded_and_the_session_is_closed() -> None:
    bot = FakeBot()
    assert fetch(bot) == CONTENT
    assert bot.calls == [("get_file", "AgACAgIAAxkBAAIB"), ("download_file", "photos/file_1.jpg")]
    assert bot.closed


def test_a_file_at_the_limit_is_fetched_and_one_byte_more_is_not_even_downloaded() -> None:
    assert fetch(FakeBot(), limit=len(CONTENT)) == CONTENT
    bot = FakeBot()
    assert fetch(bot, limit=len(CONTENT) - 1) is None
    assert bot.calls == [("get_file", "AgACAgIAAxkBAAIB")]
    assert bot.closed


@pytest.mark.parametrize("size", [None, 0, -1, True, "20"])
def test_a_file_whose_size_telegram_does_not_state_is_not_downloaded(size: Any) -> None:
    bot = FakeBot(size=size)
    assert fetch(bot) is None
    assert ("download_file", "photos/file_1.jpg") not in bot.calls


def test_a_file_without_a_path_is_not_downloaded() -> None:
    bot = FakeBot(path=None)
    assert fetch(bot) is None
    assert len(bot.calls) == 1


def test_content_longer_than_stated_is_not_returned() -> None:
    """The stated size passed the check, but more bytes arrived than the service accepts."""
    assert fetch(FakeBot(size=10, content=b"x" * 2000), limit=1024) is None
    assert fetch(FakeBot(size=10, content=b""), limit=1024) is None


@pytest.mark.parametrize(
    "failure",
    [
        TelegramBadRequest(method=GetFile(file_id="x"), message="Bad Request: file is too big"),
        TelegramNetworkError(method=GetFile(file_id="x"), message="timeout"),
        TimeoutError(),
        ConnectionResetError(),
    ],
)
def test_a_failure_of_telegram_or_the_network_is_no_file_not_an_exception(failure: Exception) -> None:
    bot = FakeBot()
    bot.fail = failure
    assert fetch(bot) is None
    assert bot.closed


def test_the_fetcher_built_from_a_token_opens_a_bot_only_when_asked() -> None:
    # Building it makes no network call and needs no running loop.
    assert isinstance(TelegramFileFetcher.for_token("123456:TEST-ONLY-token"), TelegramFileFetcher)
