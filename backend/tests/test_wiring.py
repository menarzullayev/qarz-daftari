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
ADMIN_DB = "postgresql://qd_admin:unused@127.0.0.1:1/unused"
WORKER_DB = "postgresql://qd_worker:unused@127.0.0.1:1/unused"


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
        asyncio.run(run(Settings(database_url=DB, worker_database_url=WORKER_DB, bot_token=""), asyncio.Event()))


# --- each part connects as its own database role (migration 0031) ------------------------------------


class _Connected(Exception):
    """Raised in place of opening a connection pool: carries the connection string that was asked for."""


def _refuse_to_connect(url: str, **_: object) -> None:
    raise _Connected(url)


def test_the_worker_connects_with_its_own_connection_and_never_with_the_ordinary_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from qarz.interface import worker

    monkeypatch.setattr(worker, "Database", _refuse_to_connect)
    settings = Settings(
        database_url=DB, admin_database_url=ADMIN_DB, worker_database_url=WORKER_DB, bot_token="123:test"
    )
    with pytest.raises(_Connected) as connected:
        asyncio.run(run(settings, asyncio.Event()))
    assert connected.value.args == (WORKER_DB,)


def test_the_worker_refuses_to_start_without_its_own_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """It does not fall back to the ordinary role's connection, which is set here."""
    from qarz.interface import worker

    monkeypatch.setattr(worker, "Database", _refuse_to_connect)
    with pytest.raises(RuntimeError, match="QD_WORKER_DATABASE_URL"):
        asyncio.run(run(Settings(database_url=DB, admin_database_url=ADMIN_DB, bot_token="123:test"), asyncio.Event()))


def test_the_measurement_command_is_the_workers_and_refuses_to_run_without_its_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from qarz.interface import measure_export

    monkeypatch.setattr(measure_export, "Database", _refuse_to_connect)
    monkeypatch.setenv("QD_DATABASE_URL", DB)
    monkeypatch.delenv("QD_WORKER_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="QD_WORKER_DATABASE_URL"):
        asyncio.run(measure_export.run(1, None))
    monkeypatch.setenv("QD_WORKER_DATABASE_URL", WORKER_DB)
    with pytest.raises(_Connected) as connected:
        asyncio.run(measure_export.run(1, None))
    assert connected.value.args == (WORKER_DB,)


def test_the_rotation_command_is_the_administrators_and_refuses_to_run_without_their_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from qarz.interface import rotate_secrets

    monkeypatch.setattr(rotate_secrets, "Database", _refuse_to_connect)
    with pytest.raises(ValueError, match="QD_ADMIN_DATABASE_URL"):
        asyncio.run(
            rotate_secrets.run(Settings(database_url=DB, worker_database_url=WORKER_DB, secrets_key=SECRETS_KEY))
        )
    with pytest.raises(_Connected) as connected:
        asyncio.run(rotate_secrets.run(Settings(database_url=DB, admin_database_url=ADMIN_DB, secrets_key=SECRETS_KEY)))
    assert connected.value.args == (ADMIN_DB,)


def test_the_api_gives_the_ordinary_side_and_the_administrators_side_each_its_own_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What `build` hands to the application: the ordinary storage is the ordinary role's, and the
    administrators' access and storage are the administrators' role's. The worker's is never opened."""
    from qarz.interface import asgi

    opened: list[str] = []

    class Pool:
        def __init__(self, url: str, **_: object) -> None:
            self.url = url
            opened.append(url)

        async def reachable(self) -> bool:
            return True

    handed: dict[str, object] = {}

    def capture(reachable: object, storage: Pool, **more: object) -> str:
        handed.update(storage=storage, **more)
        return "the application"

    monkeypatch.setattr(asgi, "Database", Pool)
    monkeypatch.setattr(asgi, "create_app", capture)
    settings = Settings(
        database_url=DB,
        admin_database_url=ADMIN_DB,
        worker_database_url=WORKER_DB,
        bot_token="123:test",
        admin_tg_ids="1001",
        secrets_key=SECRETS_KEY,
    )
    assert asgi.build(settings) == "the application"  # type: ignore[comparison-overlap]
    assert opened == [DB, ADMIN_DB]
    assert handed["storage"].url == DB  # type: ignore[attr-defined]
    assert handed["admin_storage"].url == ADMIN_DB  # type: ignore[attr-defined]
    assert handed["admin"]._storage is handed["admin_storage"]  # type: ignore[attr-defined]
    assert handed["auth"]._storage is handed["storage"]  # type: ignore[attr-defined]

    # Without administrators there is no second connection at all.
    opened.clear()
    asgi.build(Settings(database_url=DB, admin_database_url=ADMIN_DB, bot_token="123:test"))
    assert opened == [DB] and handed["admin"] is None and handed["admin_storage"] is None


def test_with_administrators_named_and_no_connection_for_their_role_the_api_refuses_to_start() -> None:
    """It does not serve the administrators' side through the ordinary role's connection instead."""
    with pytest.raises(ValueError, match="QD_ADMIN_DATABASE_URL"):
        build(Settings(database_url=DB, bot_token="123:test", admin_tg_ids="1001", secrets_key=SECRETS_KEY))
    # Nobody named, or no key: there is no such side, and nothing is asked for.
    assert _paths(Settings(database_url=DB, bot_token="123:test", secrets_key=SECRETS_KEY))
    assert _paths(Settings(database_url=DB, bot_token="123:test", admin_tg_ids="1001"))


def test_the_application_cannot_be_built_with_administrators_and_no_storage_of_their_own() -> None:
    from qarz.application.admin_access import AdminAccess
    from qarz.infrastructure.db import Database
    from qarz.infrastructure.secret_box import SecretBox

    async def reachable() -> bool:
        return True

    ordinary = Database(DB)
    access = AdminAccess(ordinary, allowed_tg_ids={1001}, cipher=SecretBox(SECRETS_KEY))
    with pytest.raises(ValueError, match="own storage"):
        http.create_app(reachable, ordinary, admin=access)


# --- the administrator's side (ADR-017) -------------------------------------------------------------

SECRETS_KEY = "a-server-secret-for-wiring-tests-0123456789"


def _admin_paths(**admin: str) -> set[str]:
    settings = Settings(
        database_url=DB,
        admin_database_url=ADMIN_DB,
        bot_token="123:test",
        webhook_secret="a-long-enough-secret",
        **admin,
    )
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
    settings = Settings(
        database_url=DB, admin_database_url=ADMIN_DB, bot_token="", admin_tg_ids="1001", secrets_key=SECRETS_KEY
    )
    assert not {path for path in _paths(settings) if path.startswith("/api/")}


@pytest.mark.parametrize("ids", ["1001,abc", "-5", "0", "1.5", "1001;1002", "١٢٣"])
def test_a_malformed_allow_list_refuses_to_start(ids: str) -> None:
    with pytest.raises(ValueError, match="QD_ADMIN_TG_IDS"):
        build(Settings(database_url=DB, bot_token="123:test", admin_tg_ids=ids, secrets_key=SECRETS_KEY))


@pytest.mark.parametrize("key", ["short", "x" * 15])
def test_a_server_secret_that_is_too_short_refuses_to_start(key: str) -> None:
    with pytest.raises(ValueError, match="too short"):
        build(Settings(database_url=DB, bot_token="123:test", admin_tg_ids="1001", secrets_key=key))


@pytest.mark.parametrize("key", ["short", "x" * 15])
@pytest.mark.parametrize("admin_tg_ids", ["1001", ""], ids=["with administrators", "file links only"])
def test_a_previous_server_secret_that_is_too_short_refuses_to_start(key: str, admin_tg_ids: str) -> None:
    with pytest.raises(ValueError, match="too short"):
        build(
            Settings(
                database_url=DB,
                admin_database_url=ADMIN_DB,
                bot_token="123:test",
                admin_tg_ids=admin_tg_ids,
                secrets_key=SECRETS_KEY,
                secrets_key_previous=key,
            )
        )


def test_the_application_starts_with_a_previous_server_secret_and_without_one() -> None:
    for previous in ("", "the-secret-before-this-one-0123456789"):
        paths = _admin_paths(admin_tg_ids="1001", secrets_key=SECRETS_KEY, secrets_key_previous=previous)
        assert "/api/admin/v1/shops/{shop_id}/owner" in paths


def test_the_previous_server_secret_is_empty_by_default_and_not_shown_when_settings_are_printed() -> None:
    assert Settings().secrets_key_previous == ""
    previous = "the-secret-before-this-one-0123456789"
    assert previous not in repr(Settings(secrets_key=SECRETS_KEY, secrets_key_previous=previous))


def test_the_allow_list_is_read_as_numbers() -> None:
    assert Settings(admin_tg_ids=" 1001, 1002 ,,1001 ").admin_allow_list() == frozenset({1001, 1002})
    assert Settings(admin_tg_ids="").admin_allow_list() == frozenset()


def test_the_secrets_key_and_the_allow_list_are_not_shown_when_settings_are_printed() -> None:
    shown = repr(Settings(admin_tg_ids="1001", secrets_key=SECRETS_KEY))
    assert SECRETS_KEY not in shown
    assert "1001" not in shown
