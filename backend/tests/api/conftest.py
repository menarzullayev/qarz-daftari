"""API fixtures: the real application wired to the real database as the restricted role qd_app.

Only authentication is replaced: until story S2.1 delivers Telegram sign-in, a test authenticator reads
the caller's user identifier from a header. It exists only in the test suite.
"""

import hashlib
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from qarz.application.admin_access import AdminAccess
from qarz.application.auth import AuthService
from qarz.domain import totp
from qarz.domain.promise import tashkent_date
from qarz.infrastructure.db import Database
from qarz.infrastructure.file_store import FilesystemFileStore
from qarz.infrastructure.secret_box import SecretBox
from qarz.interface.http import create_app
from qarz.interface.observability import Metrics

TEST_USER_HEADER = "X-Test-User"
WEBHOOK_SECRET = "test-webhook-secret-0123456789"
TEST_BOT_TOKEN = "1234567890:TEST-ONLY-token-not-a-real-bot"
TEST_SECRETS_KEY = "test-only-server-secret-0123456789"
ADMIN_API = "/api/admin/v1"
ADMIN_COOKIE = "qd_admin"


class HeaderAuthenticator:
    async def user_id(self, request: Request) -> uuid.UUID | None:
        raw = request.headers.get(TEST_USER_HEADER)
        if raw is None:
            return None
        try:
            return uuid.UUID(raw)
        except ValueError:
            return None


class MovableClock:
    """The real time plus an offset a test may move; `freeze` stops it, for limits measured to the instant."""

    def __init__(self) -> None:
        self.offset = timedelta(0)
        self._frozen: datetime | None = None

    def freeze(self) -> None:
        self._frozen = datetime.now(UTC)

    def now(self) -> datetime:
        return (self._frozen or datetime.now(UTC)) + self.offset


@dataclass
class AdminEnv:
    """What makes someone an administrator in the test application, and its clock.

    `allowed` is the allow-list the application holds: a test adds a Telegram identifier to it instead of
    setting an environment variable. The clock is the real time until a test moves it.
    """

    allowed: set[int]
    clock: MovableClock
    box: SecretBox


@pytest.fixture(scope="session")
def _settings_cleaner(database_url: str) -> Iterator[psycopg.Connection]:
    """One connection for the whole session: opening one per test would cost more than the tests."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        yield conn


@pytest.fixture
def admin_env(_settings_cleaner: psycopg.Connection) -> Iterator[AdminEnv]:
    yield AdminEnv(set(), MovableClock(), SecretBox(TEST_SECRETS_KEY))
    # Platform settings are global and the test database lives for the whole session: what an
    # administrator changed in one test must not reach the next. The API signs a change with the
    # administrator's user identifier.
    _settings_cleaner.execute("DELETE FROM platform_setting WHERE updated_by ~ '^[0-9a-f]{8}-[0-9a-f-]{27}$'")


class FakeTelegramFiles:
    """Stands in for the Bot API's getFile: a test puts content under a file identifier."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.asked: list[str] = []

    async def fetch(self, file_id: str, max_bytes: int) -> bytes | None:
        self.asked.append(file_id)
        content = self.files.get(file_id)
        return content if content is not None and len(content) <= max_bytes else None


class FakeChatMembers:
    """Stands in for the Bot API's getChatMember: a test says what a person is in a chat. Someone it was
    told nothing about has "left", which is what Telegram answers for a person who is not in the chat."""

    def __init__(self) -> None:
        self.statuses: dict[tuple[int, int], str | None] = {}
        self.asked: list[tuple[int, int]] = []
        self.unreachable = False  # Telegram cannot be asked: every question gets no answer

    async def status(self, chat_id: int, user_id: int) -> str | None:
        self.asked.append((chat_id, user_id))
        return None if self.unreachable else self.statuses.get((chat_id, user_id), "left")


# Where the file store of the running test keeps its objects, for code that seeds a file without a fixture.
_file_root: list[Path] = []


def current_file_root() -> Path:
    return _file_root[-1]


@pytest.fixture
def file_root(tmp_path: Path) -> Iterator[Path]:
    """The directory of the test file store; empty until a file is kept."""
    root = tmp_path / "file-store"
    _file_root.append(root)
    yield root
    _file_root.remove(root)


@pytest.fixture
def telegram_files() -> FakeTelegramFiles:
    return FakeTelegramFiles()


@pytest.fixture
def telegram_members() -> FakeChatMembers:
    return FakeChatMembers()


def stored_objects(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


# --- one application for the whole session, with every test's own things behind it -------------------------
#
# Building the application costs more than anything else a test does before its first request: FastAPI
# works out the parameters of some 130 routes again each time (about 80 ms of the 90 ms the `client`
# fixture took). The routes do not differ from test to test; only what the application works with does:
# the two database connections, the clock, the allow-list, the cipher, the file store and the two Telegram
# fakes. So the application is built once, around stand-ins for those seven things, and each test puts its
# own behind the stand-ins before its first request and takes them away after its last.
#
# What makes this safe, and what holds it to that (test_shared_app.py):
#
# - nothing of one test is shared with the next but the application object itself. The database engines,
#   their connections and the event loop they belong to are made for each test and ended with it, as
#   before, and so is the `TestClient` with its cookies;
# - a stand-in resolves its target when it is called, never when it is asked for a method, so a method
#   the application took hold of while it was built (`storage.user_language`) still reaches the running
#   test's database. The application is built with nothing behind the stand-ins: anything that called
#   through one at that moment would fail there;
# - a stand-in hands out the methods of its kind and nothing else: reading a plain attribute through it is
#   an error, not a value of some other test;
# - the application object keeps one store of its own between requests, the request counters of
#   `Metrics`; they are set back before each test. test_shared_app.py walks everything the application
#   holds and fails for any other object that could change, so a cache added to a service is noticed;
# - `pytest --app-per-test` builds the application for each test, in the same way, as it was before.


class NothingBehind(RuntimeError):
    """A stand-in was called while no test had put anything behind it."""


class StandIn:
    """What the shared application holds in the place of one object that every test has of its own."""

    def __init__(self, what: str, kind: type) -> None:
        self._what = what
        self._kind = kind
        self._target: Any = None

    def _behind(self) -> Any:
        if self._target is None:
            raise NothingBehind(f"no test has put its {self._what} behind the application")
        return self._target

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or not callable(getattr(self._kind, name, None)):
            raise AttributeError(f"the stand-in for the {self._what} hands out methods only, not {name!r}")

        def forward(*args: Any, **kwargs: Any) -> Any:
            return getattr(self._behind(), name)(*args, **kwargs)

        forward.__name__ = name
        return forward

    def __repr__(self) -> str:
        return f"<stand-in for the {self._what}>"


class AllowListStandIn:
    """The allow-list of the running test: the application only ever asks whether somebody is on it."""

    def __init__(self, stage: "Stage") -> None:
        self._stage = stage

    def __contains__(self, tg_id: object) -> bool:
        return tg_id in self._stage.admin_env().allowed


class Stage:
    """Everything one test puts behind the application, and takes away again."""

    def __init__(self) -> None:
        self.database = StandIn("database connection", Database)
        self.admin_database = StandIn("administrators' database connection", Database)
        self.file_store = StandIn("file store", FilesystemFileStore)
        self.telegram_files = StandIn("Telegram files fake", FakeTelegramFiles)
        self.telegram_members = StandIn("Telegram members fake", FakeChatMembers)
        self.cipher = StandIn("cipher", SecretBox)
        self.allowed = AllowListStandIn(self)
        self._admin_env: AdminEnv | None = None

    def admin_env(self) -> AdminEnv:
        if self._admin_env is None:
            raise NothingBehind("no test has put its clock and allow-list behind the application")
        return self._admin_env

    def now(self) -> datetime:
        return self.admin_env().clock.now()

    def _stand_ins(self) -> tuple[StandIn, ...]:
        return (
            self.database,
            self.admin_database,
            self.file_store,
            self.telegram_files,
            self.telegram_members,
            self.cipher,
        )

    def empty(self) -> bool:
        return self._admin_env is None and all(stand_in._target is None for stand_in in self._stand_ins())

    def put(
        self,
        *,
        database: Database,
        admin_database: Database,
        file_store: FilesystemFileStore,
        telegram_files: FakeTelegramFiles,
        telegram_members: FakeChatMembers,
        admin_env: AdminEnv,
    ) -> None:
        if not self.empty():
            raise RuntimeError("another test's things are still behind the application")
        targets = (database, admin_database, file_store, telegram_files, telegram_members, admin_env.box)
        for stand_in, target in zip(self._stand_ins(), targets, strict=True):
            if not isinstance(target, stand_in._kind):
                raise TypeError(f"the {stand_in._what} must be a {stand_in._kind.__name__}")
            stand_in._target = target
        self._admin_env = admin_env

    def clear(self) -> None:
        for stand_in in self._stand_ins():
            stand_in._target = None
        self._admin_env = None


def build_app(stage: Stage) -> FastAPI:
    """The application as the `client` fixture always built it, around the stand-ins of the stage."""
    if not stage.empty():
        raise RuntimeError("the application is built with nothing behind the stand-ins")
    # As deployed: the ordinary side connects as qd_app and the administrators' side as qd_admin.
    auth = AuthService(stage.database, TEST_BOT_TOKEN)
    admin = AdminAccess(stage.admin_database, allowed_tg_ids=stage.allowed, cipher=stage.cipher, now=stage.now)
    return create_app(
        stage.database.reachable,
        stage.database,
        auth=auth,
        admin=admin,
        admin_storage=stage.admin_database,
        authenticator=HeaderAuthenticator(),
        webhook_secret=WEBHOOK_SECRET,
        now=stage.now,
        file_store=stage.file_store,
        telegram_files=stage.telegram_files,
        telegram_members=stage.telegram_members,
        secrets_key=TEST_SECRETS_KEY,
    )


def metrics_of(app: FastAPI) -> Metrics:
    """The one store the application object keeps between requests: its request counters."""
    found = [m.kwargs["metrics"] for m in app.user_middleware if isinstance(m.kwargs.get("metrics"), Metrics)]
    if len(found) != 1:
        raise RuntimeError(f"expected the application to have one Metrics, found {len(found)}")
    return found[0]


def as_new(app: FastAPI) -> None:
    """Set back what the application object itself remembers from earlier requests."""
    metrics = metrics_of(app)
    vars(metrics).clear()
    vars(metrics).update(vars(Metrics()))


@dataclass(frozen=True)
class SharedApp:
    app: FastAPI
    stage: Stage


@pytest.fixture(scope="session")
def shared_app() -> SharedApp:
    stage = Stage()
    return SharedApp(build_app(stage), stage)


@pytest.fixture
def client(
    request: pytest.FixtureRequest,
    app_database_url: str,
    admin_database_url: str,
    file_root: Path,
    telegram_files: FakeTelegramFiles,
    telegram_members: FakeChatMembers,
    admin_env: AdminEnv,
) -> Iterator[TestClient]:
    if request.config.getoption("--app-per-test"):
        stage = Stage()
        app = build_app(stage)
    else:
        shared: SharedApp = request.getfixturevalue("shared_app")
        app, stage = shared.app, shared.stage
        as_new(app)
    database = Database(app_database_url)
    admin_database = Database(admin_database_url)
    stage.put(
        database=database,
        admin_database=admin_database,
        file_store=FilesystemFileStore(file_root),
        telegram_files=telegram_files,
        telegram_members=telegram_members,
        admin_env=admin_env,
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
            test_client.portal.call(database.dispose)  # type: ignore[union-attr]
            test_client.portal.call(admin_database.dispose)  # type: ignore[union-attr]
    finally:
        stage.clear()


@dataclass(frozen=True)
class World:
    """Two shops and every kind of caller the authorization suite needs."""

    shop_a: uuid.UUID
    shop_b: uuid.UUID
    owner_a: uuid.UUID
    manager_a: uuid.UUID
    seller_a: uuid.UUID
    suspended_a: uuid.UUID
    owner_b: uuid.UUID
    customer_of_a: uuid.UUID
    admin: uuid.UUID
    stranger: uuid.UUID
    seller_a_membership: uuid.UUID
    manager_a_membership: uuid.UUID
    owner_a_membership: uuid.UUID
    invitation_a: str  # identifier (hex of the token hash) of an issued staff invitation in shop A
    invitation_a_token: str
    customer_a: uuid.UUID  # owes 50 000 UZS through entry_a; linked to the user customer_of_a
    settled_customer_a: uuid.UUID  # owes nothing
    archived_customer_a: uuid.UUID
    entry_a: uuid.UUID  # a credit sale of 50 000 UZS to customer_a, promised a week from today
    waiter: uuid.UUID  # a person who agreed at shop A's counter code and waits to be attached
    waiting_a: uuid.UUID  # that person's waiting link
    catalog_item_a: uuid.UUID  # "Non", 4 000 UZS a piece: shown and reviewed
    learned_item_a: uuid.UUID  # "Qatiq", 9 000 UZS: learned from a typed line, not yet reviewed


def _user(conn: psycopg.Connection, lang: str = "uz") -> uuid.UUID:
    user_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO app_user (id, tg_id, lang) VALUES (%s, %s, %s)", (user_id, uuid.uuid4().int % 10**15, lang)
    )
    return user_id


def _member(conn: psycopg.Connection, shop: uuid.UUID, user: uuid.UUID, role: str, status: str = "active") -> uuid.UUID:
    membership_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO membership (id, shop_id, user_id, role, status) VALUES (%s, %s, %s, %s, %s)",
        (membership_id, shop, user, role, status),
    )
    return membership_id


@pytest.fixture
def world(owner: psycopg.Connection) -> World:
    shop_a, shop_b = uuid.uuid4(), uuid.uuid4()
    owner.execute("INSERT INTO shop (id, name) VALUES (%s, 'Shop A'), (%s, 'Shop B')", (shop_a, shop_b))
    users = {name: _user(owner) for name in ("owner_a", "manager_a", "seller_a", "suspended_a", "owner_b")}
    users |= {name: _user(owner) for name in ("customer_of_a", "admin", "stranger")}
    owner_membership = _member(owner, shop_a, users["owner_a"], "owner")
    manager_membership = _member(owner, shop_a, users["manager_a"], "manager")
    seller_membership = _member(owner, shop_a, users["seller_a"], "seller")
    _member(owner, shop_a, users["suspended_a"], "manager", status="suspended")
    _member(owner, shop_b, users["owner_b"], "owner")

    # A customer of shop A with an active, consented link: a real user of the service, but not staff.
    customer_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Ali', 'ali')",
        (customer_id, shop_a),
    )
    owner.execute(
        "INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at) "
        "VALUES (%s, %s, %s, %s, 'active', 2, now())",
        (uuid.uuid4(), shop_a, customer_id, users["customer_of_a"]),
    )
    settled_customer, archived_customer, entry_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm, status) VALUES "
        "(%s, %s, 'Vali', 'vali', 'active'), (%s, %s, 'Sobir', 'sobir', 'archived')",
        (settled_customer, shop_a, archived_customer, shop_a),
    )
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
        "VALUES (%s, %s, %s, 1, 'credit', 50000, %s)",
        (entry_id, shop_a, customer_id, seller_membership),
    )
    owner.execute(
        "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor) VALUES (%s, %s, %s, %s, 'default')",
        # The service's day is Tashkent's, five hours ahead of the database server's: "a week from today"
        # counted from the server's current_date is a day short from 19:00 to 24:00 UTC.
        (uuid.uuid4(), shop_a, entry_id, tashkent_date(datetime.now(UTC)) + timedelta(days=7)),
    )
    owner.execute(
        "INSERT INTO subscription (shop_id, state, trial_ends) VALUES "
        "(%s, 'trial', current_date + 30), (%s, 'trial', current_date + 30)",
        (shop_a, shop_b),
    )
    catalog_item, learned_item = uuid.uuid4(), uuid.uuid4()
    owner.execute(
        "INSERT INTO catalog_item (id, shop_id, name, name_norm, unit, price, learned) VALUES "
        "(%s, %s, 'Non', 'non', 'dona', 4000, false), (%s, %s, 'Qatiq', 'qatiq', 'dona', 9000, true)",
        (catalog_item, shop_a, learned_item, shop_a),
    )
    waiter, waiting_link = _user(owner), uuid.uuid4()
    owner.execute(
        "INSERT INTO customer_link (id, shop_id, user_id, status, consent_text_v, consent_at, waiting_name) "
        "VALUES (%s, %s, %s, 'waiting', 2, now(), 'Kutuvchi Karim')",
        (waiting_link, shop_a, waiter),
    )
    # A platform administrator with no support access to any shop.
    owner.execute("INSERT INTO admin_account (user_id, totp_secret) VALUES (%s, %s)", (users["admin"], b"test-only"))
    token = f"world-invitation-{uuid.uuid4().hex}"
    digest = hashlib.sha256(token.encode()).digest()
    owner.execute(
        "INSERT INTO invitation (token_hash, shop_id, kind, role, expires_at) "
        "VALUES (%s, %s, 'staff', 'seller', now() + interval '7 days')",
        (digest, shop_a),
    )
    return World(
        shop_a=shop_a,
        shop_b=shop_b,
        seller_a_membership=seller_membership,
        manager_a_membership=manager_membership,
        owner_a_membership=owner_membership,
        invitation_a=digest.hex(),
        invitation_a_token=token,
        customer_a=customer_id,
        settled_customer_a=settled_customer,
        archived_customer_a=archived_customer,
        entry_a=entry_id,
        waiter=waiter,
        waiting_a=waiting_link,
        catalog_item_a=catalog_item,
        learned_item_a=learned_item,
        **users,
    )


def as_user(user_id: uuid.UUID) -> dict[str, str]:
    return {TEST_USER_HEADER: str(user_id)}


def allow_list(owner: psycopg.Connection, env: AdminEnv, user_id: uuid.UUID) -> int:
    """Put the user's Telegram identifier on the allow-list."""
    row = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (user_id,)).fetchone()
    assert row is not None
    env.allowed.add(int(row[0]))
    return int(row[0])


def make_admin(owner: psycopg.Connection, env: AdminEnv, user_id: uuid.UUID, *, confirmed: bool = True) -> bytes:
    """Make the user an administrator with a second factor of their own; returns its secret."""
    allow_list(owner, env, user_id)
    secret = os.urandom(totp.SECRET_BYTES)
    owner.execute(
        "INSERT INTO admin_account (user_id, totp_secret, confirmed_at) VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET totp_secret = EXCLUDED.totp_secret, status = 'active', "
        "confirmed_at = EXCLUDED.confirmed_at, failed_codes = 0, locked_until = NULL, last_step = NULL",
        (user_id, env.box.encrypt(secret, user_id.bytes), env.clock.now() if confirmed else None),
    )
    return secret


def fresh_code(env: AdminEnv, secret: bytes) -> str:
    """Move the clock to the next time step and return its code: a code is accepted only once."""
    env.clock.offset += timedelta(seconds=totp.STEP_SECONDS)
    return totp.code_at(secret, env.clock.now())


def admin_cookie(response: Any) -> str:
    return str(response.headers["set-cookie"]).split(f"{ADMIN_COOKIE}=")[1].split(";")[0]


def elevate(client: TestClient, env: AdminEnv, user_id: uuid.UUID, secret: bytes) -> dict[str, str]:
    """Pass the second factor; returns the headers of a signed-in administrator holding an admin session."""
    response = client.post(
        f"{ADMIN_API}/auth/session", json={"code": fresh_code(env, secret)}, headers=as_user(user_id)
    )
    assert response.status_code == 201, response.text
    return {**as_user(user_id), "Cookie": f"{ADMIN_COOKIE}={admin_cookie(response)}"}


@dataclass
class SessionClient:
    http: TestClient
    clock: MovableClock


@pytest.fixture
def session_client(app_database_url: str) -> Iterator[SessionClient]:
    """The application exactly as deployed: Telegram-backed sessions, no test authenticator."""
    database = Database(app_database_url)
    clock = MovableClock()
    auth = AuthService(database, TEST_BOT_TOKEN, clock.now)
    app = create_app(database.reachable, database, auth=auth, webhook_secret=WEBHOOK_SECRET)
    with TestClient(app) as test_client:
        yield SessionClient(test_client, clock)
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]
