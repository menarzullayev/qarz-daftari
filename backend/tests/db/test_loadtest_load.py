"""The load test's loader and driver against a real database (S19.1, REQ-N13).

A small generated set is copied into a database of its own, built by the migrations, so the rest of the
suite never sees these shops. The loader switches one trigger off while it copies; these tests show that
it is on again afterwards, that the loaded data passes the checks that stand in for it, and that those
checks notice data that breaks a rule. The driver is then run for a moment against the real application,
connected as the restricted role, to show that every request it sends is one the application accepts.
"""

import asyncio
import os
import random
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import psycopg
import pytest
from loadtest import drive, report
from loadtest.dataset import TABLES, TINY, generate
from loadtest.invariants import database_problems
from loadtest.load import app_url, copy_shops, drop_database, fill, read_manifest
from psycopg import errors

from qarz.application.auth import AuthService
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app

pytestmark = pytest.mark.db

SEED = 5


@dataclass(frozen=True)
class Loaded:
    admin_url: str
    name: str
    url: str
    manifest: dict[str, Any]


@pytest.fixture(scope="module")
def loaded() -> Iterator[Loaded]:
    admin_url = os.environ.get("QD_TEST_ADMIN_URL")
    if not admin_url:
        pytest.fail("QD_TEST_ADMIN_URL is not set; start PostgreSQL (docker compose -f docker-compose.dev.yml up -d)")
    name = f"qd_load_t{uuid.uuid4().hex[:12]}"
    try:
        manifest = fill(admin_url, name, TINY, SEED, datetime.now(UTC), drop_existing=False)
        yield Loaded(admin_url, name, admin_url.rsplit("/", 1)[0] + "/" + name, manifest)
    finally:
        drop_database(admin_url, name)


@pytest.fixture
def conn(loaded: Loaded) -> Iterator[psycopg.Connection[Any]]:
    """As the migration owner. Whatever a test writes is rolled back."""
    with psycopg.connect(loaded.url) as connection:
        yield connection
        connection.rollback()


def test_every_generated_row_is_in_the_database(loaded: Loaded, conn: psycopg.Connection[Any]) -> None:
    expected = dict.fromkeys(TABLES, 0)
    for shop in generate(TINY, SEED, datetime.fromisoformat(loaded.manifest["now"])):
        for table in TABLES:
            expected[table] += len(shop.rows[table])
    assert loaded.manifest["rows"] == expected
    for table in TABLES:
        row = conn.execute(psycopg.sql.SQL("SELECT count(*) FROM {}").format(psycopg.sql.Identifier(table))).fetchone()
        assert row == (expected[table],), table
    assert read_manifest(conn)["seed"] == SEED


def test_the_loaded_data_breaks_no_rule(conn: psycopg.Connection[Any]) -> None:
    assert database_problems(conn) == []


def test_the_goods_line_trigger_is_on_again_after_the_load(conn: psycopg.Connection[Any]) -> None:
    state = conn.execute("SELECT tgenabled FROM pg_trigger WHERE tgname = 'goods_line_guard'").fetchone()
    assert state == ("O",)
    # And it refuses what it is there to refuse: a second batch of goods lines on an old sale.
    entry_id, shop_id = conn.execute("SELECT entry_id, shop_id FROM goods_line LIMIT 1").fetchone() or (None, None)
    with pytest.raises(errors.RaiseException):
        conn.execute(
            "INSERT INTO goods_line (id, shop_id, entry_id, line_no, name, qty, unit, unit_price, line_total) "
            "VALUES (%s, %s, %s, 99, 'Non', 1, 'dona', 4000, 4000)",
            (uuid.uuid4(), shop_id, entry_id),
        )


def test_a_failed_load_leaves_the_trigger_on(conn: psycopg.Connection[Any]) -> None:
    shops = list(generate(TINY, SEED + 1, datetime.now(UTC)))
    # The same shops a second time: the first COPY fails on a primary key.
    with pytest.raises(errors.UniqueViolation):
        copy_shops(conn, shops + shops)
    state = conn.execute("SELECT tgenabled FROM pg_trigger WHERE tgname = 'goods_line_guard'").fetchone()
    assert state == ("O",)
    assert conn.execute("SELECT count(*) FROM shop").fetchone() == (TINY.shops,)


def test_the_application_role_sees_one_shop_at_a_time(loaded: Loaded) -> None:
    with psycopg.connect(loaded.url) as connection:
        shops = [row[0] for row in connection.execute("SELECT id FROM shop ORDER BY id")]
        per_shop = dict(connection.execute("SELECT shop_id, count(*) FROM ledger_entry GROUP BY shop_id").fetchall())
        connection.execute("SET ROLE qd_app")
        assert connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (0,)
        for shop_id in shops:
            connection.execute("SELECT set_config('qd.shop_id', %s, true)", (str(shop_id),))
            assert connection.execute("SELECT count(*) FROM ledger_entry").fetchone() == (per_shop[shop_id],)
        connection.rollback()


# --- the database checks notice each broken rule ----------------------------------------------------------

_CUSTOMER = "(SELECT customer_id FROM ledger_entry WHERE kind = 'payment' LIMIT 1)"
_ENTRY_COLUMNS = "(id, shop_id, customer_id, seq, kind, amount, reverses_id, author_id, created_at)"
_NEXT = (
    f"INSERT INTO ledger_entry {_ENTRY_COLUMNS} "
    "SELECT gen_random_uuid(), e.shop_id, e.customer_id, max(e.seq) + {step}, '{kind}', {amount}, {reverses}, "
    "min(e.author_id::text)::uuid, {at} FROM ledger_entry e "
    f"WHERE e.customer_id = {_CUSTOMER} GROUP BY e.shop_id, e.customer_id"
)
_BREAKAGES = {
    "seq is not contiguous": _NEXT.format(step=5, kind="payment", amount=100, reverses="NULL", at="now()"),
    "created_at goes backwards": _NEXT.format(
        step=1, kind="payment", amount=100, reverses="NULL", at="now() - interval '10 years'"
    ),
    "a running balance is negative": _NEXT.format(step=1, kind="payment", amount=99000000, reverses="NULL", at="now()"),
    "a reversal does not undo": _NEXT.format(
        step=1,
        kind="reversal",
        amount=1,
        reverses=f"(SELECT id FROM ledger_entry WHERE customer_id <> {_CUSTOMER} AND kind = 'payment' "
        "AND id NOT IN (SELECT reverses_id FROM ledger_entry WHERE reverses_id IS NOT NULL) LIMIT 1)",
        at="now()",
    ),
    "has no promise": _NEXT.format(step=1, kind="credit", amount=5000, reverses="NULL", at="now()"),
    "a payment or reversal has a promise": (
        "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor) "
        "SELECT gen_random_uuid(), shop_id, id, current_date, 'staff' FROM ledger_entry WHERE kind = 'payment' LIMIT 1"
    ),
    "an entry is in another shop than its customer": (
        f"INSERT INTO ledger_entry {_ENTRY_COLUMNS} "
        "SELECT gen_random_uuid(), (SELECT id FROM shop WHERE id <> c.shop_id LIMIT 1), c.id, 5000, 'credit', 1000, "
        "NULL, (SELECT id FROM membership WHERE shop_id = c.shop_id LIMIT 1), now() FROM customer c LIMIT 1"
    ),
    "goods lines do not sum": (
        "INSERT INTO goods_line (id, shop_id, entry_id, line_no, name, qty, unit, unit_price, line_total, batch_at) "
        "SELECT gen_random_uuid(), shop_id, entry_id, 99, 'Non', 1, 'dona', 4000, 4000, batch_at "
        "FROM goods_line LIMIT 1"
    ),
    "more than one batch": (
        "INSERT INTO goods_line (id, shop_id, entry_id, line_no, name, qty, unit, unit_price, line_total, batch_at) "
        "SELECT gen_random_uuid(), shop_id, entry_id, 99, 'Non', 1, 'dona', 4000, 4000, batch_at + interval '1 hour' "
        "FROM goods_line LIMIT 1"
    ),
    "after the end of the day after the sale": (
        "INSERT INTO goods_line (id, shop_id, entry_id, line_no, name, qty, unit, unit_price, line_total, batch_at) "
        "SELECT gen_random_uuid(), e.shop_id, e.id, 1, 'Non', 1, 'dona', e.amount, e.amount, "
        "e.created_at + interval '3 days' "
        "FROM ledger_entry e WHERE e.kind = 'credit' AND e.created_at < now() - interval '10 days' "
        "AND NOT EXISTS (SELECT 1 FROM goods_line g WHERE g.entry_id = e.id) LIMIT 1"
    ),
    "not a credit sale": (
        "INSERT INTO goods_line (id, shop_id, entry_id, line_no, name, qty, unit, unit_price, line_total, batch_at) "
        "SELECT gen_random_uuid(), shop_id, id, 1, 'Non', 1, 'dona', amount, amount, created_at "
        "FROM ledger_entry WHERE kind = 'payment' LIMIT 1"
    ),
    "an archived customer owes": (
        f"INSERT INTO ledger_entry {_ENTRY_COLUMNS} "
        "SELECT gen_random_uuid(), e.shop_id, e.customer_id, max(e.seq) + 1, 'opening', 7000, NULL, "
        "min(e.author_id::text)::uuid, now() FROM ledger_entry e JOIN customer c ON c.id = e.customer_id "
        "WHERE c.status = 'archived' GROUP BY e.shop_id, e.customer_id LIMIT 1"
    ),
    "exactly one active owner": "UPDATE membership SET status = 'removed' WHERE role = 'owner'",
}


@pytest.mark.parametrize("rule", sorted(_BREAKAGES))
def test_the_database_checks_notice_a_broken_rule(conn: psycopg.Connection[Any], rule: str) -> None:
    # What the loader does while it copies; the transaction is rolled back, and with it this change.
    conn.execute("ALTER TABLE goods_line DISABLE TRIGGER USER")
    changed = conn.execute(_BREAKAGES[rule])
    assert changed.rowcount >= 1, "the breakage found nothing to break"
    found = database_problems(conn)
    assert any(rule in problem for problem in found), found


# --- the driver against the real application ---------------------------------------------------------------


def test_the_driver_sends_only_requests_the_application_accepts(loaded: Loaded) -> None:
    async def run() -> tuple[drive.Driver, dict[str, int]]:
        with psycopg.connect(loaded.url, autocommit=True) as connection:
            world = drive.load_world(connection, SEED, TINY.large_shops)
        database = Database(app_url(loaded.admin_url, loaded.name))
        # The production wiring: sessions, not a test authenticator, and the restricted role.
        app = create_app(
            database.reachable,
            database,
            auth=AuthService(database, drive.BOT_TOKEN),
            webhook_secret=drive.WEBHOOK_SECRET,
        )
        started = datetime.now(UTC)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://loadtest") as client:
                driver = drive.Driver([client], world, random.Random(3), warmup=0)  # noqa: S311
                await driver.run(
                    duration=4, rate=25, large_write_rate=25, read_rate=25, large_read_rate=25, report_every=0.25
                )
        finally:
            await database.dispose()
        with psycopg.connect(loaded.url, autocommit=True) as connection:
            return driver, drive._counts(connection, started)

    driver, recorded = asyncio.run(run())
    results = driver.results
    assert world_is_covered(results), sorted(results)
    for key, samples in results.items():
        assert not samples.errors, (key, samples.errors)
        assert not samples.refused, (key, samples.refused)
        assert samples.ok_ms, key
    # Every write the server acknowledged is in the ledger, the chat messages included.
    checks = drive.verify(results, recorded)
    assert checks["chat_messages_answered_200"] > 0
    assert checks["chat_entries_in_database"] == checks["chat_messages_answered_200"]
    assert checks["entries_in_database"] == sum(
        len(samples.ok_ms) for (name, _), samples in results.items() if name.startswith(("api_", "chat_"))
    )
    with psycopg.connect(loaded.url) as connection:
        assert database_problems(connection) == []


def world_is_covered(results: dict[tuple[str, str], report.Samples]) -> bool:
    """Every reported operation was exercised, and both the large shop and the others were driven."""
    return {name for name, _ in results} == set(report.OPERATIONS) and {scope for _, scope in results} == set(
        report.SCOPES
    )
