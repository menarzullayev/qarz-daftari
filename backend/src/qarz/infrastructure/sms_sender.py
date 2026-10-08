"""Delivery by channel, and the SMS path behind its switches (REQ-043; specification, "SMS").

An SMS leaves only when all of this holds: the Eskiz account is configured in the environment, the
platform switch `sms_on` is on at the moment of sending, and (decided earlier, where the reminder is made)
the shop's own switch is on and its monthly quota is not used up. All are off by default. When the
account is not configured or the platform switch is off, the sender refuses, so a message queued by
mistake fails visibly instead of vanishing: it is retried with the outbox's backoff and given up after a
day, and nothing is sent.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from qarz.application.ports import Sender, SendFailed, Storage
from qarz.application.reminders import SMS_ON
from qarz.infrastructure.eskiz_sms import EskizSmsProvider
from qarz.infrastructure.file_store import Transport
from qarz.infrastructure.settings import Settings

log = logging.getLogger("qarz.sms")

Switch = Callable[[], Awaitable[bool]]


class SmsProvider(Protocol):
    async def send(self, phone: str, text: str) -> str:
        """Send one message and return the provider's identifier for it."""
        ...


class NoSmsProvider:
    async def send(self, phone: str, text: str) -> str:
        raise SendFailed("no SMS provider is configured")


class SwitchedSmsProvider:
    """A provider behind the platform switch, which is read again for every message: turning it off stops
    the very next one, whatever is already queued."""

    def __init__(self, provider: SmsProvider, switched_on: Switch) -> None:
        self._provider = provider
        self._switched_on = switched_on

    async def send(self, phone: str, text: str) -> str:
        if not await self._switched_on():
            raise SendFailed("SMS is switched off")
        return await self._provider.send(phone, text)


def platform_switch(storage: Storage) -> Switch:
    """Whether `sms_on` is on now. Only a stored `true` is on: no row, or anything else, is off."""

    async def switched_on() -> bool:
        async with storage.platform() as session:
            return await session.platform_setting(SMS_ON) is True

    return switched_on


def eskiz_configured(settings: Settings) -> bool:
    """All three or nothing: the account's e-mail, its password and the sender name."""
    return all(value.strip() for value in (settings.eskiz_email, settings.eskiz_password, settings.eskiz_sender))


def build_sms_provider(settings: Settings, *, switched_on: Switch, transport: Transport | None = None) -> SmsProvider:
    """The Eskiz sender behind the platform switch, or the one that refuses when Eskiz is not configured."""
    if not eskiz_configured(settings):
        if any(value.strip() for value in (settings.eskiz_email, settings.eskiz_password, settings.eskiz_sender)):
            # Names only: which of the three is missing is for the operator to look up, not for the log.
            log.warning("sms_partly_configured", extra={"channel": "sms"})
        return NoSmsProvider()
    return SwitchedSmsProvider(
        EskizSmsProvider(
            email=settings.eskiz_email.strip(),
            password=settings.eskiz_password.strip(),
            sender=settings.eskiz_sender.strip(),
            transport=transport,
        ),
        switched_on,
    )


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
