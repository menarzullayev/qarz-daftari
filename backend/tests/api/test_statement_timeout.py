"""The application's statement timeout (S19.1 load test; technical specification, "Abuse").

In the load test a handful of slow queries from one large shop held every database core and stopped the
service for all shops. The application therefore tells the database, on its own connections, how long a
statement may run. These tests make a request wait on a row another transaction has locked: the database
cancels it, the caller gets an ordinary error answer, nothing is saved, and the next request is served.
"""

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.auth import AuthService
from qarz.application.errors import StorageTimeout
from qarz.infrastructure.db import Database
from qarz.infrastructure.settings import Settings
from qarz.interface.http import create_app

from .conftest import TEST_BOT_TOKEN, WEBHOOK_SECRET, HeaderAuthenticator, World, as_user

pytestmark = pytest.mark.db

TIMEOUT_MS = 300


def _client(app_database_url: str, statement_timeout_ms: int) -> Iterator[TestClient]:
    database = Database(app_database_url, statement_timeout_ms=statement_timeout_ms)
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        authenticator=HeaderAuthenticator(),
        webhook_secret=WEBHOOK_SECRET,
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]


@pytest.fixture
def limited(app_database_url: str) -> Iterator[TestClient]:
    yield from _client(app_database_url, TIMEOUT_MS)


def _record(client: TestClient, world: World, key: str) -> Any:
    return client.post(
        f"/api/v1/shops/{world.shop_a}/customers/{world.customer_a}/entries",
        json={"kind": "credit", "amount": 7000},
        headers={**as_user(world.seller_a), "Idempotency-Key": key},
    )


def _entries(owner: psycopg.Connection, world: World) -> int:
    row = owner.execute("SELECT count(*) FROM ledger_entry WHERE customer_id = %s", (world.customer_a,)).fetchone()
    assert row is not None
    return int(row[0])


def test_a_statement_over_the_limit_is_cancelled_and_answered_as_an_error(
    limited: TestClient, world: World, owner: psycopg.Connection, database_url: str
) -> None:
    before = _entries(owner, world)
    key = f"timeout-{uuid.uuid4().hex}"
    with psycopg.connect(database_url) as blocker:
        # Another transaction holds the customer's row; recording an entry locks that row first.
        blocker.execute("SELECT 1 FROM customer WHERE id = %s FOR UPDATE", (world.customer_a,))
        response = _record(limited, world, key)
        assert response.status_code == 503, response.text
        body = response.json()
        assert body["error"]["code"] == "TIMEOUT"
        assert "saqlanmadi" in body["error"]["message"]
        # Nothing of the cancelled request was kept, while the row is still locked.
        assert _entries(owner, world) == before
        # The service is not down: what does not need the locked row is answered meanwhile.
        assert limited.get("/healthz").json() == {"status": "ok"}
        listed = limited.get(f"/api/v1/shops/{world.shop_a}/customers", headers=as_user(world.seller_a))
        assert listed.status_code == 200
        blocker.rollback()

    # Once the row is free the very same request, with the same key, is carried out, on the same pool.
    again = _record(limited, world, key)
    assert again.status_code == 201, again.text
    assert _entries(owner, world) == before + 1


def test_many_cancelled_requests_leave_the_pool_usable(
    limited: TestClient, world: World, owner: psycopg.Connection, database_url: str
) -> None:
    """More cancelled requests than the pool has connections: none of them may be lost to it."""
    with psycopg.connect(database_url) as blocker:
        blocker.execute("SELECT 1 FROM customer WHERE id = %s FOR UPDATE", (world.customer_a,))
        for _ in range(12):
            response = _record(limited, world, f"timeout-{uuid.uuid4().hex}")
            assert response.status_code == 503
        blocker.rollback()
    assert _record(limited, world, f"timeout-{uuid.uuid4().hex}").status_code == 201


def test_normal_requests_pass_under_the_limit(limited: TestClient, world: World, owner: psycopg.Connection) -> None:
    before = _entries(owner, world)
    assert _record(limited, world, f"timeout-{uuid.uuid4().hex}").status_code == 201
    for path in ("customers", "overview", "overview/debtors", f"customers/{world.customer_a}"):
        response = limited.get(f"/api/v1/shops/{world.shop_a}/{path}", headers=as_user(world.seller_a))
        assert response.status_code == 200, (path, response.text)
    assert _entries(owner, world) == before + 1


def test_without_a_limit_the_same_wait_is_simply_waited_out(
    app_database_url: str, world: World, database_url: str
) -> None:
    """The cancellation above comes from the limit and from nothing else: with no limit set, a request
    that waits longer than that limit for the same lock is carried out when the lock is released."""

    async def scenario() -> tuple[str, int]:
        database = Database(app_database_url)  # the default: no limit, as every other test runs
        try:
            async with database.platform() as session:
                shown = (await session._conn.exec_driver_sql("SHOW statement_timeout")).scalar_one()
            # A plain blocking connection: these calls return at once, and psycopg's async form does
            # not run on every event loop.
            blocker = psycopg.connect(database_url)
            blocker.execute("SELECT 1 FROM customer WHERE id = %s FOR UPDATE", (world.customer_a,))

            async def locked() -> int:
                async with database.tenant(world.shop_a) as tenant:
                    customer = await tenant.get_customer(world.customer_a, for_update=True)
                    return 0 if customer is None else 1

            waiting = asyncio.create_task(locked())
            await asyncio.sleep(TIMEOUT_MS * 2 / 1000)
            assert not waiting.done()
            blocker.rollback()
            blocker.close()
            return str(shown), await waiting
        finally:
            await database.dispose()

    assert asyncio.run(scenario()) == ("0", 1)


def test_the_limit_is_a_setting_of_the_applications_connections_only(
    app_database_url: str, world: World, database_url: str
) -> None:
    async def scenario() -> str:
        database = Database(app_database_url, statement_timeout_ms=TIMEOUT_MS)
        try:
            async with database.platform() as session:
                shown = str((await session._conn.exec_driver_sql("SHOW statement_timeout")).scalar_one())
            with pytest.raises(StorageTimeout):
                async with database.tenant(world.shop_a) as tenant:
                    await tenant._conn.exec_driver_sql("SELECT pg_sleep(2)")
            # The cancelled connection went back to the pool in working order.
            assert await database.user_language(world.seller_a) == "uz"
            return shown
        finally:
            await database.dispose()

    assert asyncio.run(scenario()) == f"{TIMEOUT_MS}ms"
    # Nothing was written to the role: a fresh connection as qd_app, made any other way, has no limit.
    with psycopg.connect(app_database_url) as conn:
        assert conn.execute("SHOW statement_timeout").fetchone() == ("0",)
        assert conn.execute("SELECT rolconfig FROM pg_roles WHERE rolname = 'qd_app'").fetchone() == (None,)


def test_a_negative_limit_is_refused() -> None:
    with pytest.raises(ValueError):
        Database("postgresql://qd_app:unused@127.0.0.1:1/unused", statement_timeout_ms=-1)


def test_the_defaults_and_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings()
    assert (settings.statement_timeout_ms, settings.worker_statement_timeout_ms) == (5000, 60000)
    monkeypatch.setenv("QD_STATEMENT_TIMEOUT_MS", "1500")
    monkeypatch.setenv("QD_WORKER_STATEMENT_TIMEOUT_MS", "0")
    settings = Settings()
    assert (settings.statement_timeout_ms, settings.worker_statement_timeout_ms) == (1500, 0)


def test_the_api_and_the_worker_each_connect_with_their_own_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from qarz.interface import asgi, worker

    seen: dict[str, int] = {}

    class Recorded(Exception):
        pass

    def record(name: str) -> Any:
        def database(url: str, *, statement_timeout_ms: int = 0) -> Database:
            seen[name] = statement_timeout_ms
            raise Recorded()

        return database

    settings = Settings(
        database_url="postgresql://qd_app:unused@127.0.0.1:1/unused",
        bot_token="123:test",
        statement_timeout_ms=1234,
        worker_statement_timeout_ms=45678,
    )
    monkeypatch.setattr(asgi, "Database", record("api"))
    monkeypatch.setattr(worker, "Database", record("worker"))
    with pytest.raises(Recorded):
        asgi.build(settings)
    with pytest.raises(Recorded):
        asyncio.run(worker.run(settings, asyncio.Event()))
    assert seen == {"api": 1234, "worker": 45678}
