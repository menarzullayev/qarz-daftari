"""Verification of Telegram-signed identity data (ADR-017). Pure functions, no database."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest

from qarz.domain.telegram_auth import InvalidTelegramData, verify_login_data, verify_webapp_init_data

BOT_TOKEN = "1234567890:TEST-ONLY-token-not-a-real-bot"
OTHER_TOKEN = "1234567890:another-bot-entirely"
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
MAX_AGE = timedelta(hours=1)


def webapp_init_data(
    tg_id: object = 4242,
    *,
    token: str = BOT_TOKEN,
    auth_date: datetime = NOW - timedelta(minutes=5),
    language: str | None = "uz",
    extra: dict[str, str] | None = None,
) -> str:
    user: dict[str, object] = {"id": tg_id, "first_name": "Test"}
    if language is not None:
        user["language_code"] = language
    fields = {"auth_date": str(int(auth_date.timestamp())), "query_id": "AAE", "user": json.dumps(user)}
    fields |= extra or {}
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields)).encode()
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check, hashlib.sha256).hexdigest()
    return urlencode(fields)


def login_data(
    tg_id: int = 4242, *, token: str = BOT_TOKEN, auth_date: datetime = NOW - timedelta(minutes=5)
) -> dict[str, object]:
    fields: dict[str, object] = {"id": tg_id, "first_name": "Test", "auth_date": int(auth_date.timestamp())}
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields)).encode()
    secret = hashlib.sha256(token.encode()).digest()
    return {**fields, "hash": hmac.new(secret, check, hashlib.sha256).hexdigest()}


# --- Mini App launch data ---------------------------------------------------------------------------


def test_valid_webapp_data_yields_the_identity() -> None:
    identity = verify_webapp_init_data(webapp_init_data(4242, language="ru"), BOT_TOKEN, NOW, MAX_AGE)
    assert (identity.tg_id, identity.language_code) == (4242, "ru")
    assert identity.auth_date == NOW - timedelta(minutes=5)


def test_webapp_data_signed_by_another_bot_is_refused() -> None:
    with pytest.raises(InvalidTelegramData):
        verify_webapp_init_data(webapp_init_data(token=OTHER_TOKEN), BOT_TOKEN, NOW, MAX_AGE)


def test_webapp_data_with_a_changed_user_is_refused() -> None:
    genuine = webapp_init_data(4242)
    forged = genuine.replace("4242", "9999")
    assert forged != genuine
    with pytest.raises(InvalidTelegramData):
        verify_webapp_init_data(forged, BOT_TOKEN, NOW, MAX_AGE)


def test_webapp_data_with_an_added_field_is_refused() -> None:
    with pytest.raises(InvalidTelegramData):
        verify_webapp_init_data(webapp_init_data() + "&is_admin=1", BOT_TOKEN, NOW, MAX_AGE)


def test_webapp_data_with_a_repeated_field_is_refused() -> None:
    """Strictness, not an exploit: with a repeated key, parsers disagree about which value counts.

    Appended, the forged value would be the one read, and the signature fails. Prepended, the genuine value
    is read and the signature still holds, so only the explicit duplicate check refuses it.
    """
    forged_user = "user=%7B%22id%22%3A1%7D"
    with pytest.raises(InvalidTelegramData):
        verify_webapp_init_data(webapp_init_data() + "&" + forged_user, BOT_TOKEN, NOW, MAX_AGE)
    with pytest.raises(InvalidTelegramData):
        verify_webapp_init_data(forged_user + "&" + webapp_init_data(), BOT_TOKEN, NOW, MAX_AGE)


@pytest.mark.parametrize(
    "auth_date",
    [NOW - MAX_AGE - timedelta(seconds=1), NOW - timedelta(days=30), NOW + timedelta(minutes=2)],
    ids=["just too old", "a month old", "from the future"],
)
def test_webapp_data_outside_the_allowed_age_is_refused(auth_date: datetime) -> None:
    with pytest.raises(InvalidTelegramData):
        verify_webapp_init_data(webapp_init_data(auth_date=auth_date), BOT_TOKEN, NOW, MAX_AGE)


def test_webapp_data_at_the_edge_of_the_allowed_age_is_accepted() -> None:
    identity = verify_webapp_init_data(webapp_init_data(auth_date=NOW - MAX_AGE), BOT_TOKEN, NOW, MAX_AGE)
    assert identity.tg_id == 4242


@pytest.mark.parametrize("init_data", ["", "hash=abc", "not a query string", "a=1&b", "user=%7B%7D&auth_date=1"])
def test_malformed_webapp_data_is_refused(init_data: str) -> None:
    with pytest.raises(InvalidTelegramData):
        verify_webapp_init_data(init_data, BOT_TOKEN, NOW, MAX_AGE)


@pytest.mark.parametrize("tg_id", [0, -5, "4242", True, None, 4242.5])
def test_webapp_data_with_a_bad_user_id_is_refused_even_when_signed(tg_id: object) -> None:
    with pytest.raises(InvalidTelegramData):
        verify_webapp_init_data(webapp_init_data(tg_id), BOT_TOKEN, NOW, MAX_AGE)


def test_webapp_data_without_a_language_still_verifies() -> None:
    assert verify_webapp_init_data(webapp_init_data(language=None), BOT_TOKEN, NOW, MAX_AGE).language_code is None


# --- Login Widget data ------------------------------------------------------------------------------


def test_valid_login_data_yields_the_identity() -> None:
    assert verify_login_data(login_data(777), BOT_TOKEN, NOW, MAX_AGE).tg_id == 777


def test_login_data_signed_by_another_bot_is_refused() -> None:
    with pytest.raises(InvalidTelegramData):
        verify_login_data(login_data(token=OTHER_TOKEN), BOT_TOKEN, NOW, MAX_AGE)


def test_login_data_with_a_changed_id_is_refused() -> None:
    with pytest.raises(InvalidTelegramData):
        verify_login_data({**login_data(777), "id": 778}, BOT_TOKEN, NOW, MAX_AGE)


def test_login_data_with_an_added_field_is_refused() -> None:
    with pytest.raises(InvalidTelegramData):
        verify_login_data({**login_data(), "username": "admin"}, BOT_TOKEN, NOW, MAX_AGE)


@pytest.mark.parametrize("bad_hash", [None, "", 12345, "0" * 64])
def test_login_data_with_a_missing_or_wrong_hash_is_refused(bad_hash: object) -> None:
    with pytest.raises(InvalidTelegramData):
        verify_login_data({**login_data(), "hash": bad_hash}, BOT_TOKEN, NOW, MAX_AGE)


def test_old_login_data_is_refused() -> None:
    with pytest.raises(InvalidTelegramData):
        verify_login_data(login_data(auth_date=NOW - timedelta(hours=2)), BOT_TOKEN, NOW, MAX_AGE)


def test_webapp_data_cannot_be_replayed_as_login_data_or_the_reverse() -> None:
    """The two formats use different keys; neither verifies under the other's rules."""
    from urllib.parse import parse_qsl

    as_fields: dict[str, object] = dict(parse_qsl(webapp_init_data()))
    with pytest.raises(InvalidTelegramData):
        verify_login_data(as_fields, BOT_TOKEN, NOW, MAX_AGE)
    with pytest.raises(InvalidTelegramData):
        verify_webapp_init_data(urlencode({k: str(v) for k, v in login_data().items()}), BOT_TOKEN, NOW, MAX_AGE)
