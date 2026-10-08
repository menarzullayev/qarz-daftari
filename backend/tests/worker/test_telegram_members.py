"""Asking Telegram what a person is in a chat, against a fake bot. The real Bot API is never called.

The answer decides whether a press in the review group counts (DEC-064), so every failure must come out
as no answer, never as an exception and never as a status.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import GetChatMember

from qarz.domain.subscription_receipts import decides_in_review_group
from qarz.infrastructure.telegram_members import REQUEST_TIMEOUT_SECONDS, TelegramMemberReader

GROUP, PERSON = -1001234567890, 7001


class FakeBot:
    def __init__(self, member: Any = None) -> None:
        self.member = member
        self.calls: list[tuple[int, int, Any]] = []
        self.closed = False
        self.fail: Exception | None = None

    async def get_chat_member(self, chat_id: int, user_id: int, request_timeout: Any = None) -> Any:
        self.calls.append((chat_id, user_id, request_timeout))
        if self.fail is not None:
            raise self.fail
        return self.member


def ask(bot: FakeBot) -> str | None:
    @asynccontextmanager
    async def opened() -> Any:
        try:
            yield bot
        finally:
            bot.closed = True

    return asyncio.run(TelegramMemberReader(opened).status(GROUP, PERSON))


@pytest.mark.parametrize("status", list(ChatMemberStatus))
def test_the_status_is_given_as_the_bot_apis_own_word_and_the_session_is_closed(status: ChatMemberStatus) -> None:
    bot = FakeBot(SimpleNamespace(status=status))
    answer = ask(bot)
    assert answer == status.value and type(answer) is str
    assert bot.calls == [(GROUP, PERSON, REQUEST_TIMEOUT_SECONDS)], "asked once, about that person in that chat"
    assert bot.closed
    # What the application then makes of it: only the creator and the administrators decide.
    assert decides_in_review_group(answer) is (status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR))


def test_a_plain_string_status_is_passed_on_unchanged() -> None:
    assert ask(FakeBot(SimpleNamespace(status="administrator"))) == "administrator"


@pytest.mark.parametrize("member", [None, SimpleNamespace(), SimpleNamespace(status=None), SimpleNamespace(status=1)])
def test_an_answer_without_a_status_is_no_answer(member: Any) -> None:
    assert ask(FakeBot(member)) is None


_ASKED = GetChatMember(chat_id=GROUP, user_id=PERSON)


@pytest.mark.parametrize(
    "failure",
    [
        TelegramBadRequest(method=_ASKED, message="Bad Request: chat not found"),
        TelegramForbiddenError(method=_ASKED, message="Forbidden: bot is not a member of the supergroup chat"),
        TelegramNetworkError(method=_ASKED, message="timeout"),
        TimeoutError(),
        ConnectionResetError(),
    ],
)
def test_a_failure_of_telegram_or_the_network_is_no_answer_and_so_no_right(failure: Exception) -> None:
    bot = FakeBot(SimpleNamespace(status="creator"))
    bot.fail = failure
    answer = ask(bot)
    assert answer is None
    assert decides_in_review_group(answer) is False
    assert bot.closed


def test_the_reader_built_from_a_token_opens_a_bot_only_when_asked() -> None:
    # Building it makes no network call and needs no running loop.
    assert isinstance(TelegramMemberReader.for_token("123456:TEST-ONLY-token"), TelegramMemberReader)
