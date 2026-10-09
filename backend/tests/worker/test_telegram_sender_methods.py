"""The Telegram sender chooses the Bot API method named in the payload and builds real keyboard objects."""

import asyncio
from typing import Any

import pytest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from qarz.application.ports import SendFailed
from qarz.infrastructure.telegram_sender import TelegramSender

MARKUP = {"inline_keyboard": [[{"text": "Ertaga", "callback_data": "v2:pd:" + "0" * 32 + ":t"}]]}
SECRET_TEXT = "Ali: +45 000 so'm"


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.calls.append(("send_message", kwargs))

    async def edit_message_text(self, **kwargs: Any) -> None:
        self.calls.append(("edit_message_text", kwargs))

    async def edit_message_reply_markup(self, **kwargs: Any) -> None:
        self.calls.append(("edit_message_reply_markup", kwargs))

    async def send_photo(self, **kwargs: Any) -> None:
        self.calls.append(("send_photo", kwargs))

    async def send_document(self, **kwargs: Any) -> None:
        self.calls.append(("send_document", kwargs))

    async def edit_message_caption(self, **kwargs: Any) -> None:
        self.calls.append(("edit_message_caption", kwargs))


def deliver(bot: FakeBot, payload: dict[str, Any]) -> None:
    asyncio.run(TelegramSender(bot).send("telegram", "777", payload))  # type: ignore[arg-type]


def test_a_message_with_buttons_carries_a_keyboard_object() -> None:
    bot = FakeBot()
    deliver(bot, {"text": SECRET_TEXT, "reply_markup": MARKUP})
    name, arguments = bot.calls[0]
    assert name == "send_message"
    assert arguments["chat_id"] == 777
    assert arguments["text"] == SECRET_TEXT
    keyboard = arguments["reply_markup"]
    assert isinstance(keyboard, InlineKeyboardMarkup)
    assert keyboard.inline_keyboard[0][0].callback_data == "v2:pd:" + "0" * 32 + ":t"


def test_an_edit_replaces_the_text_of_the_named_message() -> None:
    bot = FakeBot()
    deliver(
        bot,
        {"method": "editMessageText", "message_id": 12, "text": SECRET_TEXT, "reply_markup": {"inline_keyboard": []}},
    )
    name, arguments = bot.calls[0]
    assert name == "edit_message_text"
    assert (arguments["chat_id"], arguments["message_id"], arguments["text"]) == (777, 12, SECRET_TEXT)
    assert arguments["reply_markup"].inline_keyboard == []
    assert "method" not in arguments


def test_buttons_alone_can_be_replaced() -> None:
    bot = FakeBot()
    deliver(bot, {"method": "editMessageReplyMarkup", "message_id": 12, "reply_markup": MARKUP})
    name, arguments = bot.calls[0]
    assert name == "edit_message_reply_markup"
    assert (arguments["chat_id"], arguments["message_id"]) == (777, 12)
    assert isinstance(arguments["reply_markup"], InlineKeyboardMarkup)


@pytest.mark.parametrize(
    ("method", "call", "argument"),
    [("sendPhoto", "send_photo", "photo"), ("sendDocument", "send_document", "document")],
)
def test_a_file_is_uploaded_with_its_caption_and_buttons(method: str, call: str, argument: str) -> None:
    bot = FakeBot()
    file = {"name": "receipt-0a1b2c3d.jpg", "content": bytes([0xFF, 0xD8]) + b" the image"}
    deliver(bot, {"method": method, "file": file, "caption": SECRET_TEXT, "reply_markup": MARKUP})
    name, arguments = bot.calls[0]
    assert name == call
    assert set(arguments) == {"chat_id", argument, "caption", "reply_markup"}
    assert (arguments["chat_id"], arguments["caption"]) == (777, SECRET_TEXT)
    uploaded = arguments[argument]
    assert isinstance(uploaded, BufferedInputFile)
    assert (uploaded.data, uploaded.filename) == (file["content"], file["name"])
    assert isinstance(arguments["reply_markup"], InlineKeyboardMarkup)


def test_a_caption_is_edited_as_a_caption() -> None:
    bot = FakeBot()
    deliver(
        bot,
        {
            "method": "editMessageCaption",
            "message_id": 12,
            "caption": SECRET_TEXT,
            "reply_markup": {"inline_keyboard": []},
        },
    )
    name, arguments = bot.calls[0]
    assert name == "edit_message_caption"
    assert (arguments["chat_id"], arguments["message_id"], arguments["caption"]) == (777, 12, SECRET_TEXT)
    assert arguments["reply_markup"].inline_keyboard == [] and "text" not in arguments


@pytest.mark.parametrize(
    "payload",
    [
        # A file is bytes handed over in memory by the code that checked who may see it. A row of the
        # outbox is JSON: whatever it names as a file, a path, a link, a file identifier, is not sent.
        {"method": "sendPhoto", "caption": SECRET_TEXT},
        {"method": "sendPhoto", "photo": "https://example.org/receipt.jpg", "caption": SECRET_TEXT},
        {"method": "sendDocument", "document": "BQACAgIAAxkBAAIB", "caption": SECRET_TEXT},
        {"method": "sendDocument", "file": {"name": "a.pdf", "content": "/var/lib/qarz/files/a"}},
        {"method": "sendDocument", "file": "/var/lib/qarz/files/a"},
        {"method": "sendPhoto", "file": {"content": "aGVsbG8="}},
        {"method": "deleteMessage", "message_id": 12},
        {"method": "banChatMember", "user_id": 1},
        {"method": "", "text": "x"},
        {"text": SECRET_TEXT, "reply_markup": {"inline_keyboard": "not rows"}},
        {"text": SECRET_TEXT, "reply_markup": {"keyboard": [[{"text": "x"}]]}},
    ],
)
def test_a_payload_the_sender_does_not_know_is_not_sent(payload: dict[str, Any]) -> None:
    """Only the methods the chat uses can leave through the outbox, whatever a row says."""
    bot = FakeBot()
    with pytest.raises(SendFailed) as raised:
        deliver(bot, payload)
    assert bot.calls == []
    assert SECRET_TEXT not in str(raised.value)
