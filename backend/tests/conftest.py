"""Database fixtures: a fresh database per test session, built by the real migrations."""

import os
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


@pytest.fixture(scope="session")
def app_database_url(database_url: str) -> str:
    """Connection string for qd_app itself, so the API runs under the same restrictions as in production."""
    # The role is shared by every database of the server, so two test sessions running at once (two
    # agents, or two terminals) must set the same password or they lock each other out.
    password = os.environ.get("QD_TEST_APP_PASSWORD", "test-only-qd-app-password")
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(psycopg.sql.SQL("ALTER ROLE qd_app LOGIN PASSWORD {}").format(psycopg.sql.Literal(password)))
    parts = urlsplit(database_url)
    host = parts.hostname or "127.0.0.1"
    netloc = f"qd_app:{password}@{host}:{parts.port or 5432}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


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


@pytest.fixture
def as_app(database_url: str) -> AppSession:
    """Open a transaction as the application role qd_app, with the tenant set to the given shop.

    Leaving the block commits, so deferred constraints fire; an exception rolls back.
    """

    @contextmanager
    def _session(shop_id: uuid.UUID | None) -> Iterator[psycopg.Connection]:
        with psycopg.connect(database_url) as conn:
            conn.execute("SET ROLE qd_app")
            if shop_id is not None:
                conn.execute("SELECT set_config('qd.shop_id', %s, true)", (str(shop_id),))
            yield conn

    return _session


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
