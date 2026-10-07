"""Production wiring: what the deployed application exposes for a given configuration."""

import asyncio
import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from qarz.infrastructure.settings import Settings
from qarz.interface import http
from qarz.interface.asgi import build
from qarz.interface.worker import run

DB = "postgresql://qd_app:unused@127.0.0.1:1/unused"  # never connected to in these tests


def _paths(settings: Settings) -> set[str]:
    return {route.path for route in build(settings).routes if isinstance(route, APIRoute)}


def test_with_a_bot_token_and_secret_the_api_and_webhook_are_served() -> None:
    paths = _paths(Settings(database_url=DB, bot_token="123:test", webhook_secret="a-long-enough-secret"))
    assert {"/healthz", "/tg/webhook", "/api/v1/me", "/api/v1/auth/telegram-webapp"} <= paths


def test_the_payment_provider_endpoints_are_served_whatever_the_configuration() -> None:
    """They answer "disabled" until the switch is on, so they must exist even with nothing configured."""
    for settings in (
        Settings(database_url=DB, bot_token="", webhook_secret=""),
        Settings(database_url=DB, bot_token="123:test", webhook_secret="a-long-enough-secret"),
    ):
        assert {"/pay/payme", "/pay/click"} <= _paths(settings)


def test_the_provider_keys_come_from_the_environment_and_are_empty_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = ("PAYME_MERCHANT_ID", "PAYME_SECRET_KEY", "CLICK_SERVICE_ID", "CLICK_MERCHANT_ID", "CLICK_SECRET_KEY")
    for name in names:
        monkeypatch.delenv(f"QD_{name}", raising=False)
    empty = Settings(_env_file=None)  # type: ignore[call-arg]
    assert [getattr(empty, name.lower()) for name in names] == [""] * 5
    for name in names:
        monkeypatch.setenv(f"QD_{name}", f"value-of-{name}")
    filled = Settings(_env_file=None)  # type: ignore[call-arg]
    assert [getattr(filled, name.lower()) for name in names] == [f"value-of-{name}" for name in names]


def test_the_deployed_application_limits_the_rate_of_signed_in_callers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Built from settings that allow one request a minute, a user's second request is refused with 429."""
    known = uuid.uuid4()

    class Known:
        def __init__(self, *_: object) -> None: ...

        async def user_id(self, request: object) -> uuid.UUID:
            return known

    monkeypatch.setattr(http, "SessionAuthenticator", Known)
    app = build(
        Settings(
            database_url=DB,
            bot_token="123:test",
            webhook_secret="a-long-enough-secret",
            rate_user_per_minute=1,
            rate_user_burst=1,
        )
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        first = client.get("/api/v1/me")
        second = client.get("/api/v1/me")
    # The first goes on to the database, which is not there in this test; the second is refused before that.
    assert first.status_code == 500
    assert second.status_code == 429
    assert 30 <= int(second.headers["Retry-After"]) <= 60


@pytest.mark.parametrize("name", ["rate_user_per_minute", "rate_user_burst", "rate_shop_per_minute", "rate_shop_burst"])
def test_a_rate_limit_of_zero_is_refused_at_start(name: str) -> None:
    with pytest.raises(ValueError, match="positive"):
        build(Settings(database_url=DB, bot_token="123:test", webhook_secret="a-long-enough-secret", **{name: 0}))


def test_the_default_limits_are_the_documented_ones() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (settings.rate_user_per_minute, settings.rate_user_burst) == (120, 60)
    assert (settings.rate_shop_per_minute, settings.rate_shop_burst) == (600, 200)


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
