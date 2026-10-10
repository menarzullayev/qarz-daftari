"""The administrators' other ways in: a service key and a password (the owner's decision of 2026-10-10).

Each must lead to the session the Telegram sign-in gives and to nothing more: who is an administrator is
still the allow-list and the account. Here: a key works as a bearer token and never as a cookie, stops
when it is revoked, and is made for administrators only; a password is refused in one voice whatever is
wrong, locks after five wrong attempts, and every acceptance is audited and announced.
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.admin_access import AdminAccess
from qarz.application.admin_sign_in import KEY_PREFIX, AdminSignIn, NotAnAdministrator
from qarz.application.admin_sign_in_ports import ServiceKey
from qarz.application.auth import AuthService
from qarz.domain import admin_password as rules
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app

from .conftest import ADMIN_API, TEST_BOT_TOKEN, AdminEnv, World, make_admin

pytestmark = pytest.mark.db

ME = "/api/v1/me"
SIGN_IN = "/api/v1/auth/admin-password"
GOOD = "a long and private phrase 42"
LOGIN = "boss"


class Announced:
    def __init__(self) -> None:
        self.logins: list[str] = []

    async def __call__(self, login: str, at: datetime) -> None:
        self.logins.append(login)


@pytest.fixture
def announced() -> Announced:
    return Announced()


@pytest.fixture
def client(
    app_database_url: str, admin_database_url: str, admin_env: AdminEnv, announced: Announced
) -> Iterator[TestClient]:
    """The application with its real authenticator and the second factor off, as the owner's runs."""
    database, admin_database = Database(app_database_url), Database(admin_database_url)
    admin = AdminAccess(
        admin_database,
        allowed_tg_ids=admin_env.allowed,
        cipher=admin_env.box,
        now=admin_env.clock.now,
        second_factor_required=False,
    )
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        admin=admin,
        admin_storage=admin_database,
        admin_announce=announced,
        now=admin_env.clock.now,
    )
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]
        test_client.portal.call(admin_database.dispose)  # type: ignore[union-attr]


@pytest.fixture
def tg_id(world: World, owner: psycopg.Connection, admin_env: AdminEnv) -> int:
    make_admin(owner, admin_env, world.admin)
    row = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (world.admin,)).fetchone()
    assert row is not None
    return int(row[0])


class Tool:
    """What the server's command line works through, one call after another on a loop of its own."""

    def __init__(self, database: Database, service: AdminSignIn) -> None:
        self._database, self._service = database, service
        self._loop = asyncio.new_event_loop()

    def create_key(self, tg_id: int, label: str) -> str:
        return self._loop.run_until_complete(self._service.create_key(tg_id, label))

    def live(self) -> list[ServiceKey]:
        return self._loop.run_until_complete(self._service.keys())

    def revoke_key(self, label: str) -> bool:
        return self._loop.run_until_complete(self._service.revoke_key(label))

    def set_password(self, tg_id: int, login: str, password: str) -> None:
        self._loop.run_until_complete(self._service.set_password(tg_id, login, password))

    def close(self) -> None:
        self._loop.run_until_complete(self._database.dispose())
        self._loop.close()


@pytest.fixture
def tool(admin_database_url: str, admin_env: AdminEnv) -> Iterator[Tool]:
    """The administrators' role and the allow-list, as the command has them."""
    database = Database(admin_database_url)
    made = Tool(database, AdminSignIn(database, allowed_tg_ids=admin_env.allowed, now=admin_env.clock.now))
    yield made
    made.close()


@pytest.fixture(autouse=True)
def nothing_left_over(owner: psycopg.Connection, world: World) -> None:
    """Labels and logins are the platform's, not a shop's: what an earlier test made is taken away."""
    owner.execute("DELETE FROM user_session WHERE kind = 'service'")
    owner.execute("DELETE FROM admin_password")
    _NOW["admin"] = world.admin


_NOW: dict[str, uuid.UUID] = {}


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _audit(owner: psycopg.Connection, action: str) -> int:
    row = owner.execute(
        "SELECT count(*) FROM admin_audit WHERE action = %s AND admin_id = %s", (action, _NOW["admin"])
    ).fetchone()
    assert row is not None
    return int(row[0])


# --- the service key ------------------------------------------------------------------------------------


def test_a_key_is_a_bearer_token_for_its_administrator(
    client: TestClient, tool: Tool, tg_id: int, owner: psycopg.Connection
) -> None:
    key = tool.create_key(tg_id, "ci")
    assert key.startswith(KEY_PREFIX)
    assert client.get(ME, headers=_bearer(key)).status_code == 200
    door = client.get(f"{ADMIN_API}/auth", headers=_bearer(key))
    assert door.status_code == 200 and door.json()["elevated"] is True
    assert _audit(owner, "admin.service_key_created") == 1
    # Only the hash is kept.
    stored = owner.execute("SELECT token_hash, expires_at FROM user_session WHERE label = 'ci'").fetchone()
    assert stored is not None and key.encode() not in bytes(stored[0])


def test_a_key_is_never_a_cookie(client: TestClient, tool: Tool, tg_id: int) -> None:
    key = tool.create_key(tg_id, "ci")
    client.cookies.set("qd_session", key)
    assert client.get(ME).status_code == 401


def test_a_revoked_key_opens_nothing(client: TestClient, tool: Tool, tg_id: int, owner: psycopg.Connection) -> None:
    key = tool.create_key(tg_id, "ci")
    assert [k.label for k in tool.live()] == ["ci"]
    assert tool.revoke_key("ci") is True
    assert client.get(ME, headers=_bearer(key)).status_code == 401
    assert tool.live() == []
    assert tool.revoke_key("ci") is False
    assert _audit(owner, "admin.service_key_revoked") == 1


def test_a_label_names_one_live_key(tool: Tool, tg_id: int) -> None:
    tool.create_key(tg_id, "ci")
    with pytest.raises(ValueError, match="exists"):
        tool.create_key(tg_id, "ci")
    tool.revoke_key("ci")
    tool.create_key(tg_id, "ci")


def test_a_key_is_made_for_administrators_only(tool: Tool, admin_env: AdminEnv) -> None:
    outsider = 7_000_000_000 + uuid.uuid4().int % 1_000_000
    with pytest.raises(NotAnAdministrator):  # not on the allow-list
        tool.create_key(outsider, "ci")
    admin_env.allowed.add(outsider)
    with pytest.raises(NotAnAdministrator):  # on it, and never came in: no account
        tool.create_key(outsider, "ci")
    assert tool.live() == []


def test_a_key_of_someone_taken_off_the_allow_list_is_no_administrator(
    client: TestClient, tool: Tool, tg_id: int, admin_env: AdminEnv
) -> None:
    key = tool.create_key(tg_id, "ci")
    admin_env.allowed.discard(tg_id)
    assert client.get(f"{ADMIN_API}/auth", headers=_bearer(key)).status_code == 404


def test_the_database_wants_a_label_on_a_key_and_on_nothing_else(owner: psycopg.Connection, world: World) -> None:
    insert = (
        "INSERT INTO user_session (id, token_hash, user_id, kind, label, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, now() + interval '1 day')"
    )
    for kind, label in (("service", None), ("webapp", "ci"), ("service", "Not A Label")):
        with pytest.raises(psycopg.errors.CheckViolation), owner.transaction():
            owner.execute(insert, (uuid.uuid4(), uuid.uuid4().bytes * 2, world.admin, kind, label))


# --- the password ---------------------------------------------------------------------------------------


def test_a_password_gives_the_web_session(
    client: TestClient, tool: Tool, tg_id: int, owner: psycopg.Connection, announced: Announced
) -> None:
    tool.set_password(tg_id, LOGIN, GOOD)
    answer = client.post(SIGN_IN, json={"login": LOGIN, "password": GOOD})
    assert answer.status_code == 200 and answer.json()["csrf_token"]
    assert client.get(ME).status_code == 200
    assert client.get(f"{ADMIN_API}/auth").json()["elevated"] is True
    assert _audit(owner, "admin.password_set") == 1
    assert _audit(owner, "admin.signed_in_with_password") == 1
    assert announced.logins == [LOGIN]
    stored = owner.execute("SELECT salt, hash FROM admin_password").fetchone()
    assert stored is not None and GOOD.encode() not in bytes(stored[1])


def test_every_refusal_sounds_the_same(
    client: TestClient, tool: Tool, tg_id: int, announced: Announced, owner: psycopg.Connection
) -> None:
    tool.set_password(tg_id, LOGIN, GOOD)
    wrong = client.post(SIGN_IN, json={"login": LOGIN, "password": GOOD + "x"})
    unknown = client.post(SIGN_IN, json={"login": "nobody", "password": GOOD})
    malformed = client.post(SIGN_IN, json={"login": "No Such", "password": GOOD})
    assert wrong.status_code == unknown.status_code == malformed.status_code == 401
    assert wrong.json() == unknown.json() == malformed.json()
    assert announced.logins == []
    assert _audit(owner, "admin.signed_in_with_password") == 0
    assert client.get(ME).status_code == 401


def test_five_wrong_attempts_lock_the_login_for_a_quarter_of_an_hour(
    client: TestClient, tool: Tool, tg_id: int, admin_env: AdminEnv, owner: psycopg.Connection
) -> None:
    admin_env.clock.freeze()
    tool.set_password(tg_id, LOGIN, GOOD)
    for _ in range(rules.MAX_FAILURES - 1):
        assert client.post(SIGN_IN, json={"login": LOGIN, "password": "wrong wrong wrong"}).status_code == 401
    # Four wrong ones lock nothing: the right password still comes in, and the count starts again.
    assert client.post(SIGN_IN, json={"login": LOGIN, "password": GOOD}).status_code == 200
    for _ in range(rules.MAX_FAILURES):
        assert client.post(SIGN_IN, json={"login": LOGIN, "password": "wrong wrong wrong"}).status_code == 401
    assert _audit(owner, "admin.password_locked") == 1
    assert client.post(SIGN_IN, json={"login": LOGIN, "password": GOOD}).status_code == 401
    admin_env.clock.offset += rules.LOCK - timedelta(seconds=1)
    assert client.post(SIGN_IN, json={"login": LOGIN, "password": GOOD}).status_code == 401
    admin_env.clock.offset += timedelta(seconds=2)
    assert client.post(SIGN_IN, json={"login": LOGIN, "password": GOOD}).status_code == 200


def test_a_password_of_someone_taken_off_the_allow_list_opens_nothing(
    client: TestClient, tool: Tool, tg_id: int, admin_env: AdminEnv
) -> None:
    tool.set_password(tg_id, LOGIN, GOOD)
    admin_env.allowed.discard(tg_id)
    assert client.post(SIGN_IN, json={"login": LOGIN, "password": GOOD}).status_code == 401


def test_a_weak_password_or_a_bad_login_is_not_stored(tool: Tool, tg_id: int, owner: psycopg.Connection) -> None:
    for login, password in ((LOGIN, "short"), (LOGIN, "aaaaaaaaaaaaaaaaaaaa"), ("Bad Login", GOOD)):
        with pytest.raises(ValueError):
            tool.set_password(tg_id, login, password)
    row = owner.execute("SELECT count(*) FROM admin_password").fetchone()
    assert row is not None and row[0] == 0


def test_setting_a_password_again_replaces_the_old_one(client: TestClient, tool: Tool, tg_id: int) -> None:
    tool.set_password(tg_id, LOGIN, GOOD)
    tool.set_password(tg_id, LOGIN, GOOD + " and more")
    assert client.post(SIGN_IN, json={"login": LOGIN, "password": GOOD}).status_code == 401
    assert client.post(SIGN_IN, json={"login": LOGIN, "password": GOOD + " and more"}).status_code == 200
