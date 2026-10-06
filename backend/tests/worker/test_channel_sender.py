"""Outbox messages are routed by channel; SMS has no provider yet and must fail visibly, not vanish."""

import asyncio
from typing import Any

import pytest

from qarz.application.ports import SendFailed
from qarz.infrastructure.sms_sender import ChannelSender, NoSmsProvider

TEXT = "Shop A: Ali, 70 000 so'm qarz muddati o'tgan."


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def send(self, channel: str, recipient: str, payload: dict[str, Any]) -> None:
        self.calls.append((channel, recipient, payload))


class FakeSms:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def send(self, phone: str, text: str) -> str:
        self.calls.append((phone, text))
        return "provider-id-1"


def deliver(sender: ChannelSender, channel: str, recipient: str, payload: dict[str, Any]) -> None:
    asyncio.run(sender.send(channel, recipient, payload))


def test_each_channel_goes_to_its_own_sender() -> None:
    telegram, sms = FakeTelegram(), FakeSms()
    sender = ChannelSender(telegram=telegram, sms=sms)
    deliver(sender, "telegram", "12345", {"text": TEXT})
    deliver(sender, "sms", "+998901234567", {"text": TEXT})
    assert telegram.calls == [("telegram", "12345", {"text": TEXT})]
    assert sms.calls == [("+998901234567", TEXT)]


def test_without_a_provider_an_sms_fails_and_says_nothing_of_its_content() -> None:
    sender = ChannelSender(telegram=FakeTelegram(), sms=NoSmsProvider())
    with pytest.raises(SendFailed) as raised:
        deliver(sender, "sms", "+998901234567", {"text": TEXT})
    assert "Ali" not in str(raised.value) and "998" not in str(raised.value)


@pytest.mark.parametrize(
    ("channel", "recipient", "payload"),
    [
        ("sms", "12345", {"text": TEXT}),  # a chat identifier is not a phone number
        ("sms", "+998901234567", {"text": ""}),
        ("sms", "+998901234567", {"method": "editMessageText"}),
        ("email", "a@example.com", {"text": TEXT}),
        ("", "12345", {"text": TEXT}),
    ],
)
def test_what_is_not_a_message_of_a_known_channel_reaches_no_sender(
    channel: str, recipient: str, payload: dict[str, Any]
) -> None:
    telegram, sms = FakeTelegram(), FakeSms()
    with pytest.raises(SendFailed):
        deliver(ChannelSender(telegram=telegram, sms=sms), channel, recipient, payload)
    assert telegram.calls == [] and sms.calls == []
