"""Verification of identity data signed by Telegram (ADR-017).

Two formats exist. Mini App launch data is a URL-encoded string signed with a key derived from the bot
token through the constant "WebAppData". Login Widget data is a set of fields signed with the SHA-256 of
the bot token. In both, the signature covers every field except `hash`, sorted and joined by newlines.

Nothing here trusts the client: an identity is returned only for a valid signature that is not too old.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl


class InvalidTelegramData(Exception):
    """The data is malformed, not signed by this bot, or too old. The reason is never shown to callers."""


@dataclass(frozen=True)
class TelegramIdentity:
    tg_id: int
    language_code: str | None
    auth_date: datetime
    # The signature that was verified, as this server computed it: one value per signed payload,
    # whatever the order or spelling of the fields that carried it.
    signature: str


def _check_string(fields: dict[str, str]) -> bytes:
    return "\n".join(f"{key}={fields[key]}" for key in sorted(fields)).encode("utf-8")


def _auth_date(raw: str | None, now: datetime, max_age: timedelta) -> datetime:
    if raw is None or not raw.isascii() or not raw.isdigit():
        raise InvalidTelegramData("auth_date")
    auth_date = datetime.fromtimestamp(int(raw), tz=UTC)
    # A small allowance for clock difference; data dated in the future beyond that is refused.
    if auth_date - now > timedelta(minutes=1) or now - auth_date > max_age:
        raise InvalidTelegramData("age")
    return auth_date


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidTelegramData("id")
    return value


def verify_webapp_init_data(init_data: str, bot_token: str, now: datetime, max_age: timedelta) -> TelegramIdentity:
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise InvalidTelegramData("format") from error
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise InvalidTelegramData("duplicate field")
    given = fields.pop("hash", None)
    if not given:
        raise InvalidTelegramData("hash")

    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret, _check_string(fields), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), given.encode("utf-8")):
        raise InvalidTelegramData("signature")

    auth_date = _auth_date(fields.get("auth_date"), now, max_age)
    try:
        user = json.loads(fields.get("user", ""))
    except ValueError as error:
        raise InvalidTelegramData("user") from error
    if not isinstance(user, dict):
        raise InvalidTelegramData("user")
    language = user.get("language_code")
    return TelegramIdentity(
        _positive_int(user.get("id")), language if isinstance(language, str) else None, auth_date, expected
    )


def verify_login_data(data: dict[str, object], bot_token: str, now: datetime, max_age: timedelta) -> TelegramIdentity:
    fields = {key: str(value) for key, value in data.items() if key != "hash" and value is not None}
    given = data.get("hash")
    if not isinstance(given, str) or not given:
        raise InvalidTelegramData("hash")

    secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
    expected = hmac.new(secret, _check_string(fields), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), given.encode("utf-8")):
        raise InvalidTelegramData("signature")

    auth_date = _auth_date(fields.get("auth_date"), now, max_age)
    raw_id = fields.get("id", "")
    if not raw_id.isascii() or not raw_id.isdigit():
        raise InvalidTelegramData("id")
    return TelegramIdentity(_positive_int(int(raw_id)), None, auth_date, expected)
