"""SMS through Eskiz (eskiz.uz), the provider chosen for release 1.

Written from Eskiz's published Postman collection of its SMS gateway
(https://documenter.getpostman.com/view/663428/RzfmES4z, as published 2023-11-13 and read 2026-10-08)
and never run against Eskiz. What that document states and this module relies on:

- `POST /api/auth/login`, form fields `email` and `password`, answers `{"data": {"token": ...}}`; a token
  lives 30 days;
- `POST /api/message/sms/send`, form fields `mobile_phone` (digits, `998...`), `message`, `from` and an
  optional `callback_url`, with the token as a bearer; it answers `{"id": ..., "status": "waiting"}`.

The document names no error answer at all. How a failure is told apart is therefore this module's own
reading of the HTTP status, and is listed in `docs/10-operations/runbooks.md` (runbook 12) as unproven.

Nothing here logs, raises or stores the e-mail, the password, a token, a phone number or a message text:
a failure is reported by a fixed word and the HTTP status, and the token lives in this process's memory.
"""

import asyncio
import http.client
import json
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

from qarz.application.ports import SendFailed, SendRejected
from qarz.domain.phones import normalize_phone
from qarz.infrastructure.file_store import HttpTransport, Transport

log = logging.getLogger("qarz.sms")

ENDPOINT = "https://notify.eskiz.uz"
LOGIN_PATH = "/api/auth/login"
SEND_PATH = "/api/message/sms/send"
# Each socket operation may take this long; one whole call (connect, send, read) may take twice that.
SOCKET_TIMEOUT_SECONDS = 10.0
CALL_DEADLINE_SECONDS = 20.0
MAX_RESPONSE_BYTES = 64 * 1024
# The document gives a token 30 days. It is replaced well before, so that an expired one is not what
# tells us: nothing documents how Eskiz answers a token past its time.
TOKEN_MAX_AGE = timedelta(days=25)
# After a sign-in that was refused, or a new token that was refused at once, no sign-in is tried for this
# long: every queued message would otherwise repeat a wrong password against the account.
SIGN_IN_PAUSE = timedelta(minutes=5)

_UZ_PREFIX = "+998"
_UZ_LENGTH = len(_UZ_PREFIX) + 9
# `money()` joins thousands with a no-break space so an amount never wraps in a chat. In an SMS that one
# character would turn a Latin text into a Unicode one, of 70 characters a part instead of 160.
_PLAIN_SPACES = str.maketrans({" ": " ", " ": " "})

CHANNEL = "sms"


def eskiz_number(phone: str) -> str | None:
    """`+998901234567` as Eskiz wants it, `998901234567`; None for anything that is not an Uzbek number."""
    normal = normalize_phone(phone)
    if normal is None or not normal.startswith(_UZ_PREFIX) or len(normal) != _UZ_LENGTH:
        return None
    return normal[1:]


def wire_text(text: str) -> str:
    """The text as it is sent: the same words, with ordinary spaces."""
    return text.translate(_PLAIN_SPACES)


def _form(fields: dict[str, str]) -> tuple[str, bytes]:
    """`multipart/form-data`, the form the document shows for both calls."""
    boundary = secrets.token_hex(16)
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + value.encode() + b"\r\n"
        for name, value in fields.items()
    ]
    return f"multipart/form-data; boundary={boundary}", b"".join(parts) + f"--{boundary}--\r\n".encode()


def _object(answer: bytes) -> dict[str, Any]:
    try:
        data = json.loads(answer)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class EskizSmsProvider:
    """One message a call. The outcomes are those the outbox knows (`qarz.application.dispatch`):

    returns          Eskiz accepted the message: the outbox marks it sent;
    `SendRejected`   it will never be accepted (number, text, balance): failed at once, not retried;
    `SendFailed`     it may be accepted later (network, timeout, 5xx, 429, sign-in): retried with the
                     outbox's backoff and given up after a day, like any other message.
    """

    def __init__(
        self,
        *,
        email: str,
        password: str,
        sender: str,
        transport: Transport | None = None,
        now: Callable[[], datetime] | None = None,
        deadline: float = CALL_DEADLINE_SECONDS,
    ) -> None:
        if not email or not password or not sender:
            raise ValueError("the Eskiz account is not fully configured")
        self._email = email
        self._password = password
        self._sender = sender
        self._transport: Transport = transport or HttpTransport(
            ENDPOINT, timeout=SOCKET_TIMEOUT_SECONDS, max_response_bytes=MAX_RESPONSE_BYTES
        )
        self._now = now or (lambda: datetime.now(UTC))
        self._deadline = deadline
        self._token: str | None = None
        self._token_since = datetime.min.replace(tzinfo=UTC)
        self._no_sign_in_before = datetime.min.replace(tzinfo=UTC)
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        # Never the default: a dataclass-like dump would print the password and the token.
        return "EskizSmsProvider()"

    # --- outcomes ---------------------------------------------------------------------------------------

    def _later(self, kind: str, status: int | None = None) -> NoReturn:
        log.warning("sms_retry", extra={"channel": CHANNEL, "kind": kind, "status": status})
        raise SendFailed(kind) from None

    def _never(self, kind: str, status: int | None = None) -> NoReturn:
        log.warning("sms_rejected", extra={"channel": CHANNEL, "kind": kind, "status": status})
        raise SendRejected(kind) from None

    # --- calls ------------------------------------------------------------------------------------------

    async def _call(self, path: str, fields: dict[str, str], token: str | None) -> tuple[int, bytes]:
        content_type, body = _form(fields)
        headers = {"accept": "application/json", "content-type": content_type}
        if token is not None:
            headers["authorization"] = f"Bearer {token}"
        try:
            async with asyncio.timeout(self._deadline):
                return await self._transport("POST", path, headers, body)
        except TimeoutError:
            self._later("timeout")
        except (OSError, http.client.HTTPException):
            # Only the kind of failure: an error text could repeat the address or a header.
            self._later("network")

    async def _sign_in(self) -> str:
        if self._now() < self._no_sign_in_before:
            self._later("sign_in_paused")
        status, answer = await self._call(LOGIN_PATH, {"email": self._email, "password": self._password}, None)
        if 400 <= status < 500 and status not in (408, 429):
            self._no_sign_in_before = self._now() + SIGN_IN_PAUSE
            self._later("sign_in_refused", status)
        data = _object(answer).get("data") if 200 <= status < 300 else None
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            self._later("sign_in_failed", status)
        self._token, self._token_since = token, self._now()
        return token

    async def _current_token(self) -> str:
        if self._token is not None and self._now() - self._token_since < TOKEN_MAX_AGE:
            return self._token
        self._token = None
        return await self._sign_in()

    async def send(self, phone: str, text: str) -> str:
        number = eskiz_number(phone)
        if number is None:
            # Decided here, without a call: Eskiz's ordinary sending is for Uzbek numbers.
            self._never("not_uzbek_number")
        fields = {"mobile_phone": number, "message": wire_text(text), "from": self._sender}
        async with self._lock:
            status, answer = await self._call(SEND_PATH, fields, await self._current_token())
            if status == 401:
                # The token is no longer accepted: one new sign-in and one more attempt, never a third.
                self._token = None
                status, answer = await self._call(SEND_PATH, fields, await self._sign_in())
                if status == 401:
                    self._token = None
                    self._no_sign_in_before = self._now() + SIGN_IN_PAUSE
                    self._later("unauthorized", status)
        return self._accepted(status, answer)

    def _accepted(self, status: int, answer: bytes) -> str:
        """The provider's identifier of an accepted message, or the failure the status stands for."""
        if 200 <= status < 300:
            data = _object(answer)
            if str(data.get("status", "")).lower() == "error":
                self._never("rejected", status)
            log.info("sms_sent", extra={"channel": CHANNEL})
            identifier = data.get("id")
            return identifier if isinstance(identifier, str) else ""
        if status == 429:
            self._later("rate_limited", status)
        if status == 408 or status >= 500 or status < 400:
            self._later("provider_unavailable", status)
        # Every other 4xx: the number, the text (no approved template) or the balance. The same request
        # sent again would be refused again.
        self._never("rejected", status)
