"""Delivery by channel. SMS has an interface and no provider yet (REQ-043; specification, "SMS").

The SMS path is behind two switches (the platform's and the shop's) and a monthly quota, all off by
default. Until a provider is chosen, the adapter here refuses to send, so a message queued by mistake
fails visibly instead of vanishing.
"""

from typing import Any, Protocol

from qarz.application.ports import Sender, SendFailed


class SmsProvider(Protocol):
    async def send(self, phone: str, text: str) -> str:
        """Send one message and return the provider's identifier for it."""
        ...


class NoSmsProvider:
    async def send(self, phone: str, text: str) -> str:
        raise SendFailed("no SMS provider is configured")


class ChannelSender:
    """Routes an outbox message to the sender of its channel."""

    def __init__(self, *, telegram: Sender, sms: SmsProvider) -> None:
        self._telegram = telegram
        self._sms = sms

    async def send(self, channel: str, recipient: str, payload: dict[str, Any]) -> None:
        if channel == "telegram":
            await self._telegram.send(channel, recipient, payload)
        elif channel == "sms":
            text = payload.get("text")
            if not isinstance(text, str) or not text or not recipient.startswith("+"):
                raise SendFailed("not an SMS")
            await self._sms.send(recipient, text)
        else:
            raise SendFailed(f"channel {channel!r} has no sender")
