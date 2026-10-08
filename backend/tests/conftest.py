"""Database fixtures: a fresh database per test session, built by the real migrations."""

import os
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from alembic import command
from alembic.config import Config

BACKEND = Path(__file__).resolve().parents[1]


def _admin_url() -> str:
    url = os.environ.get("QD_TEST_ADMIN_URL")
    if not url:
        # Fail loudly: silently skipping the database suite would let schema regressions through CI.
        pytest.fail("QD_TEST_ADMIN_URL is not set; start PostgreSQL (docker compose -f docker-compose.dev.yml up -d)")
    return url


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    admin_url = _admin_url()
    name = f"qd_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = admin_url.rsplit("/", 1)[0] + "/" + name

    previous = os.environ.get("QD_MIGRATION_URL")
    os.environ["QD_MIGRATION_URL"] = url
    try:
        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "migrations"))
        command.upgrade(config, "head")
        yield url
    finally:
        if previous is None:
            os.environ.pop("QD_MIGRATION_URL", None)
        else:
            os.environ["QD_MIGRATION_URL"] = previous
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _login_url(database_url: str, role: str) -> str:
    """Give one of the three application roles a login and return its connection string."""
    # A role is shared by every database of the server, so two test sessions running at once (two
    # agents, or two terminals) must set the same password or they lock each other out.
    password = os.environ.get("QD_TEST_APP_PASSWORD", "test-only-qd-app-password")
    statement = psycopg.sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
        psycopg.sql.Identifier(role), psycopg.sql.Literal(password)
    )
    with psycopg.connect(database_url, autocommit=True) as conn:
        for attempt in range(10):
            try:
                conn.execute(statement)
                break
            except psycopg.errors.InternalError_:
                # "tuple concurrently updated": another test session set the same role's password at the
                # same instant. Both set the same one, so trying again is all there is to do.
                if attempt == 9:
                    raise
                time.sleep(0.05 * (attempt + 1))
    parts = urlsplit(database_url)
    host = parts.hostname or "127.0.0.1"
    netloc = f"{role}:{password}@{host}:{parts.port or 5432}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


@pytest.fixture(scope="session")
def app_database_url(database_url: str) -> str:
    """Connection string for qd_app itself, so the API runs under the same restrictions as in production."""
    return _login_url(database_url, "qd_app")


@pytest.fixture(scope="session")
def admin_database_url(database_url: str) -> str:
    """Connection string for qd_admin, the role of the administrators' side."""
    return _login_url(database_url, "qd_admin")


@pytest.fixture(scope="session")
def worker_database_url(database_url: str) -> str:
    """Connection string for qd_worker, the role of the worker."""
    return _login_url(database_url, "qd_worker")


@pytest.fixture
def owner(database_url: str) -> Iterator[psycopg.Connection]:
    """Connection as the migration owner (bypasses row-level security); used only to seed and inspect."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        yield conn


@dataclass(frozen=True)
class Shop:
    shop_id: uuid.UUID
    user_id: uuid.UUID
    member_id: uuid.UUID
    customer_id: uuid.UUID


def _seed_shop(conn: psycopg.Connection, label: str) -> Shop:
    shop = Shop(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    conn.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (shop.user_id, uuid.uuid4().int % 10**15))
    conn.execute("INSERT INTO shop (id, name) VALUES (%s, %s)", (shop.shop_id, f"Shop {label}"))
    conn.execute(
        "INSERT INTO membership (id, shop_id, user_id, role) VALUES (%s, %s, %s, 'owner')",
        (shop.member_id, shop.shop_id, shop.user_id),
    )
    conn.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, %s, %s)",
        (shop.customer_id, shop.shop_id, f"Customer {label}", f"customer {label.lower()}"),
    )
    return shop


@pytest.fixture
def shop_a(owner: psycopg.Connection) -> Shop:
    return _seed_shop(owner, "A")


@pytest.fixture
def shop_b(owner: psycopg.Connection) -> Shop:
    return _seed_shop(owner, "B")


AppSession = Callable[[uuid.UUID | None], AbstractContextManager[psycopg.Connection]]


def _as_role(database_url: str, role: str) -> AppSession:
    @contextmanager
    def _session(shop_id: uuid.UUID | None) -> Iterator[psycopg.Connection]:
        with psycopg.connect(database_url) as conn:
            conn.execute(psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(role)))
            if shop_id is not None:
                conn.execute("SELECT set_config('qd.shop_id', %s, true)", (str(shop_id),))
            yield conn

    return _session


def refused(session: AppSession, statement: str, shop_id: uuid.UUID | None = None) -> None:
    """The role is refused the statement outright: it lacks the right, whatever the rows are."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege), session(shop_id) as conn:
        conn.execute(statement)


@pytest.fixture
def as_app(database_url: str) -> AppSession:
    """Open a transaction as the application role qd_app, with the tenant set to the given shop.

    Leaving the block commits, so deferred constraints fire; an exception rolls back.
    """
    return _as_role(database_url, "qd_app")


@pytest.fixture
def as_admin(database_url: str) -> AppSession:
    """The same as the role of the administrators' side, qd_admin."""
    return _as_role(database_url, "qd_admin")


@pytest.fixture
def as_worker(database_url: str) -> AppSession:
    """The same as the role of the worker, qd_worker."""
    return _as_role(database_url, "qd_worker")


def add_entry(
    conn: psycopg.Connection,
    shop: Shop,
    *,
    seq: int,
    amount: int,
    kind: str = "credit",
    reverses: uuid.UUID | None = None,
    created_at_sql: str = "now()",
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, reverses_id, author_id, created_at) "
        f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, {created_at_sql})",
        (entry_id, shop.shop_id, shop.customer_id, seq, kind, amount, reverses, shop.member_id),
    )
    return entry_id


def add_line(conn: psycopg.Connection, shop: Shop, entry_id: uuid.UUID, line_no: int, total: int) -> None:
    conn.execute(
        "INSERT INTO goods_line (id, shop_id, entry_id, line_no, name, qty, unit, unit_price, line_total) "
        "VALUES (%s, %s, %s, %s, %s, 1, 'dona', %s, %s)",
        (uuid.uuid4(), shop.shop_id, entry_id, line_no, f"Good {line_no}", total, total),
    )
