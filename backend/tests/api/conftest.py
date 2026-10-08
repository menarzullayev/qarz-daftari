"""API fixtures: the real application wired to the real database as the restricted role qd_app.

Only authentication is replaced: until story S2.1 delivers Telegram sign-in, a test authenticator reads
the caller's user identifier from a header. It exists only in the test suite.
"""

import hashlib
import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from qarz.application.admin_access import AdminAccess
from qarz.application.auth import AuthService
from qarz.domain import totp
from qarz.domain.promise import tashkent_date
from qarz.infrastructure.db import Database
from qarz.infrastructure.file_store import FilesystemFileStore
from qarz.infrastructure.secret_box import SecretBox
from qarz.interface.http import create_app

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


@pytest.fixture
def client(
    app_database_url: str,
    admin_database_url: str,
    file_root: Path,
    telegram_files: FakeTelegramFiles,
    telegram_members: FakeChatMembers,
    admin_env: AdminEnv,
) -> Iterator[TestClient]:
    # As deployed: the ordinary side connects as qd_app and the administrators' side as qd_admin.
    database = Database(app_database_url)
    admin_database = Database(admin_database_url)
    auth = AuthService(database, TEST_BOT_TOKEN)
    admin = AdminAccess(admin_database, allowed_tg_ids=admin_env.allowed, cipher=admin_env.box, now=admin_env.clock.now)
    app = create_app(
        database.reachable,
        database,
        auth=auth,
        admin=admin,
        admin_storage=admin_database,
        authenticator=HeaderAuthenticator(),
        webhook_secret=WEBHOOK_SECRET,
        now=admin_env.clock.now,
        file_store=FilesystemFileStore(file_root),
        telegram_files=telegram_files,
        telegram_members=telegram_members,
        secrets_key=TEST_SECRETS_KEY,
    )
    with TestClient(app) as test_client:
        yield test_client
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]
        test_client.portal.call(admin_database.dispose)  # type: ignore[union-attr]


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


def seed_world(owner: psycopg.Connection) -> World:
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


@pytest.fixture
def world(owner: psycopg.Connection) -> World:
    """A world of the test's own: nothing else has seen it and nothing else will."""
    return seed_world(owner)


# --- a world handed to several tests in a row, for as long as nobody wrote anything ---------------------
#
# Seeding a world is some twenty statements. A test that only watches a call being refused writes nothing,
# so the next such test can be given the same world. That is safe only while the world is exactly what was
# seeded, and this is not left to the tests' good behaviour: the position of the database server's
# write-ahead log is read when the world is seeded and again before it is handed over. Every change to
# any table of any database of the server moves that position, also one that is rolled back, so an equal
# position means nothing has been written since the seeding: not by a test, a fixture, the application
# under test or a failed call. Then the world is handed over; otherwise a new one is seeded. A test that
# changes data therefore costs its successor a seeding and nothing else, and a defect that lets a refused
# call write is met by the following tests on a fresh world, as it was when every test seeded its own.
#
# What moves the position without touching the world (a checkpoint, another test session on the same
# server) only costs a seeding. The world also is not kept past a change of the day or for long, because
# its dates are counted from the moment of seeding.

KEPT_WORLD_SECONDS = 30.0


@dataclass(frozen=True)
class KeptWorld:
    world: World
    database: str
    wal_position: str
    seeded_at: float  # time.monotonic()
    days: tuple[date, date]  # the day in UTC (the server's) and in Tashkent (the service's)


def wal_position(owner: psycopg.Connection) -> str:
    row = owner.execute("SELECT pg_current_wal_insert_lsn()::text").fetchone()
    assert row is not None
    return str(row[0])


def days_at(moment: datetime) -> tuple[date, date]:
    return moment.astimezone(UTC).date(), tashkent_date(moment)


def _days() -> tuple[date, date]:
    return days_at(datetime.now(UTC))


def keep_world(owner: psycopg.Connection, database: str) -> KeptWorld:
    world = seed_world(owner)
    # Read after the last statement of the seeding: the position covers the whole world.
    return KeptWorld(world, database, wal_position(owner), time.monotonic(), _days())


def still_untouched(kept: KeptWorld, owner: psycopg.Connection, database: str) -> bool:
    """Nothing has been written to the server since the world was seeded, and it is recent and of today."""
    return (
        kept.database == database
        and time.monotonic() - kept.seeded_at <= KEPT_WORLD_SECONDS
        and kept.days == _days()
        and kept.wal_position == wal_position(owner)
    )


_kept_world: list[KeptWorld] = []
kept_world_counts = {"seeded": 0, "handed over again": 0}


def hand_over_world(owner: psycopg.Connection, database: str) -> World:
    """The kept world if it is still untouched, else a newly seeded one, which is then the kept one."""
    if _kept_world and still_untouched(_kept_world[0], owner, database):
        kept_world_counts["handed over again"] += 1
        return _kept_world[0].world
    _kept_world[:] = [keep_world(owner, database)]
    kept_world_counts["seeded"] += 1
    return _kept_world[0].world


@pytest.fixture
def untouched_world(owner: psycopg.Connection, database_url: str) -> World:
    """A world exactly as seeded, possibly one that earlier tests have looked at (see above).

    Only for a test whose calls are all refused. A test that changes data may ask for it too and gets a
    world nobody has changed, but gains nothing; `world` says what such a test means.
    """
    return hand_over_world(owner, database_url)


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if kept_world_counts["seeded"]:
        seeded, again = kept_world_counts["seeded"], kept_world_counts["handed over again"]
        terminalreporter.write_line(f"untouched_world: seeded {seeded} times, handed over again {again} times")


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
