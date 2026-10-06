"""Production wiring: what the deployed application exposes for a given configuration."""

import asyncio
import base64

import pytest
from fastapi.routing import APIRoute

from qarz.infrastructure.settings import Settings
from qarz.interface.asgi import build
from qarz.interface.worker import run

DB = "postgresql://qd_app:unused@127.0.0.1:1/unused"  # never connected to in these tests


def _paths(settings: Settings) -> set[str]:
    return {route.path for route in build(settings).routes if isinstance(route, APIRoute)}


def test_with_a_bot_token_and_secret_the_api_and_webhook_are_served() -> None:
    paths = _paths(Settings(database_url=DB, bot_token="123:test", webhook_secret="a-long-enough-secret"))
    assert {"/healthz", "/tg/webhook", "/api/v1/me", "/api/v1/auth/telegram-webapp"} <= paths


def test_without_a_bot_token_no_api_is_served() -> None:
    """No token means no way to verify who is calling, so the API must not exist at all."""
    paths = _paths(Settings(database_url=DB, bot_token="", webhook_secret="a-long-enough-secret"))
    assert not {path for path in paths if path.startswith("/api/")}
    assert "/healthz" in paths


def test_without_a_webhook_secret_the_webhook_is_not_served() -> None:
    paths = _paths(Settings(database_url=DB, bot_token="123:test", webhook_secret=""))
    assert "/tg/webhook" not in paths


def test_a_short_webhook_secret_is_refused_at_start() -> None:
    with pytest.raises(ValueError, match="too short"):
        build(Settings(database_url=DB, bot_token="123:test", webhook_secret="short"))


def test_the_test_authenticator_cannot_reach_production_wiring() -> None:
    """The header-based authenticator exists only under tests/; the package must not contain it."""
    import pathlib

    import qarz

    source = pathlib.Path(qarz.__file__).parent
    assert not [p for p in source.rglob("*.py") if "X-Test-User" in p.read_text(encoding="utf-8")]


def test_the_worker_refuses_to_start_without_a_bot_token() -> None:
    with pytest.raises(RuntimeError, match="QD_BOT_TOKEN"):
        asyncio.run(run(Settings(database_url=DB, bot_token=""), asyncio.Event()))


# --- the administrator's side (ADR-017) -------------------------------------------------------------

SECRETS_KEY = base64.b64encode(bytes(range(32))).decode()


def _admin_paths(**admin: str) -> set[str]:
    settings = Settings(database_url=DB, bot_token="123:test", webhook_secret="a-long-enough-secret", **admin)
    return {path for path in _paths(settings) if path.startswith("/api/admin/")}


def test_with_an_allow_list_and_a_secrets_key_the_administrators_side_is_served() -> None:
    paths = _admin_paths(admin_tg_ids="1001, 1002", secrets_key=SECRETS_KEY)
    assert {"/api/admin/v1/auth", "/api/admin/v1/shops", "/api/admin/v1/settings", "/api/admin/v1/audit"} <= paths


@pytest.mark.parametrize(
    "admin",
    [
        {"admin_tg_ids": "", "secrets_key": ""},
        {"admin_tg_ids": "1001", "secrets_key": ""},
        {"admin_tg_ids": "", "secrets_key": SECRETS_KEY},
        {"admin_tg_ids": " , ", "secrets_key": SECRETS_KEY},
    ],
)
def test_without_an_allow_list_or_without_a_key_the_administrators_side_does_not_exist(admin: dict[str, str]) -> None:
    """Nobody can be an administrator then, so there is nothing to serve."""
    assert _admin_paths(**admin) == set()


def test_without_a_bot_token_the_administrators_side_is_not_served_either() -> None:
    settings = Settings(database_url=DB, bot_token="", admin_tg_ids="1001", secrets_key=SECRETS_KEY)
    assert not {path for path in _paths(settings) if path.startswith("/api/")}


@pytest.mark.parametrize("ids", ["1001,abc", "-5", "0", "1.5", "1001;1002", "١٢٣"])
def test_a_malformed_allow_list_refuses_to_start(ids: str) -> None:
    with pytest.raises(ValueError, match="QD_ADMIN_TG_IDS"):
        build(Settings(database_url=DB, bot_token="123:test", admin_tg_ids=ids, secrets_key=SECRETS_KEY))


@pytest.mark.parametrize("key", ["not base64 !!", base64.b64encode(bytes(16)).decode()])
def test_a_malformed_secrets_key_refuses_to_start(key: str) -> None:
    with pytest.raises(ValueError, match="secrets key"):
        build(Settings(database_url=DB, bot_token="123:test", admin_tg_ids="1001", secrets_key=key))


def test_the_allow_list_is_read_as_numbers() -> None:
    assert Settings(admin_tg_ids=" 1001, 1002 ,,1001 ").admin_allow_list() == frozenset({1001, 1002})
    assert Settings(admin_tg_ids="").admin_allow_list() == frozenset()


def test_the_secrets_key_and_the_allow_list_are_not_shown_when_settings_are_printed() -> None:
    shown = repr(Settings(admin_tg_ids="1001", secrets_key=SECRETS_KEY))
    assert SECRETS_KEY not in shown
    assert "1001" not in shown
