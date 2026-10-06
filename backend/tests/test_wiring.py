"""Production wiring: what the deployed application exposes for a given configuration."""

import asyncio

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
