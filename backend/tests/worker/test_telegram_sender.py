"""The Telegram sender maps Bot API errors to what the dispatcher understands, without leaking message text."""

import asyncio
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage

from qarz.application.ports import RecipientBlocked, RetryLater, SendFailed
from qarz.infrastructure.telegram_sender import TelegramSender

METHOD = SendMessage(chat_id=1, text="x")
SECRET_TEXT = "Ali aka, qarzingiz 450 000 so'm"


class FakeBot:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


def send(bot: FakeBot, channel: str = "telegram", recipient: str = "12345") -> None:
    asyncio.run(TelegramSender(bot).send(channel, recipient, {"text": SECRET_TEXT}))  # type: ignore[arg-type]


def test_a_message_is_passed_to_the_bot() -> None:
    bot = FakeBot()
    send(bot)
    assert bot.calls == [{"chat_id": 12345, "text": SECRET_TEXT}]


def test_flood_control_becomes_retry_later_with_the_same_delay() -> None:
    with pytest.raises(RetryLater) as raised:
        send(FakeBot(TelegramRetryAfter(METHOD, "Too Many Requests", retry_after=17)))
    assert raised.value.seconds == 17


def test_forbidden_becomes_recipient_blocked() -> None:
    with pytest.raises(RecipientBlocked):
        send(FakeBot(TelegramForbiddenError(METHOD, "Forbidden: bot was blocked by the user")))


@pytest.mark.parametrize(
    "error",
    [
        TelegramBadRequest(METHOD, f"Bad Request: can't parse entities in {SECRET_TEXT}"),
        TelegramNetworkError(METHOD, "connection reset"),
    ],
)
def test_other_errors_become_send_failed_without_the_message_text(error: Exception) -> None:
    with pytest.raises(SendFailed) as raised:
        send(FakeBot(error))
    assert SECRET_TEXT not in str(raised.value)
    assert "450" not in str(raised.value)


def test_a_non_numeric_recipient_fails_without_calling_the_bot() -> None:
    bot = FakeBot()
    with pytest.raises(SendFailed):
        send(bot, recipient="not-a-chat-id")
    assert bot.calls == []


def test_another_channel_is_refused() -> None:
    bot = FakeBot()
    with pytest.raises(SendFailed):
        send(bot, channel="sms", recipient="+998901234567")
    assert bot.calls == []
