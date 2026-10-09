"""The tenant setting must never outlive its transaction, even on a pooled connection (ADR-016)."""

import asyncio
import uuid

import psycopg
import pytest
from sqlalchemy import text

from qarz.infrastructure.db import Database

from ..conftest import Shop

pytestmark = pytest.mark.db


def test_tenant_setting_does_not_leak_to_the_next_use_of_the_connection(
    app_database_url: str, shop_a: Shop, shop_b: Shop
) -> None:
    async def scenario() -> tuple[list[uuid.UUID], int, list[uuid.UUID], int]:
        # A pool of exactly one connection forces every step onto the same physical connection.
        database = Database(app_database_url, pool_size=1, max_overflow=0)
        try:
            async with database.tenant(shop_a.shop_id) as session:
                seen_a = [s.shop_id for s in [await session.shop_settings()] if s]
            async with database._engine.begin() as conn:
                after_a = (await conn.execute(text("SELECT count(*) FROM shop"))).scalar_one()
            async with database.tenant(shop_b.shop_id) as session:
                seen_b = [s.shop_id for s in [await session.shop_settings()] if s]
            async with database._engine.begin() as conn:
                after_b = (await conn.execute(text("SELECT count(*) FROM shop"))).scalar_one()
            return seen_a, after_a, seen_b, after_b
        finally:
            await database.dispose()

    seen_a, after_a, seen_b, after_b = asyncio.run(scenario())
    assert seen_a == [shop_a.shop_id]
    assert after_a == 0, "the previous tenant leaked into a transaction that set none"
    assert seen_b == [shop_b.shop_id]
    assert after_b == 0


def test_a_failed_tenant_transaction_rolls_back_and_does_not_leak(
    app_database_url: str, shop_a: Shop, owner: psycopg.Connection
) -> None:
    async def scenario() -> int:
        database = Database(app_database_url)
        try:
            with pytest.raises(RuntimeError, match="boom"):
                async with database.tenant(shop_a.shop_id) as session:
                    await session.update_shop_settings(name="Changed", lang=None, default_promise_days=None)
                    raise RuntimeError("boom")
            async with database._engine.begin() as conn:
                return int((await conn.execute(text("SELECT count(*) FROM shop"))).scalar_one())
        finally:
            await database.dispose()

    assert asyncio.run(scenario()) == 0
    assert owner.execute("SELECT name FROM shop WHERE id = %s", (shop_a.shop_id,)).fetchone() == ("Shop A",)


def test_a_tenant_session_cannot_see_or_change_another_shop(
    app_database_url: str, shop_a: Shop, shop_b: Shop, owner: psycopg.Connection
) -> None:
    async def scenario() -> tuple[object, int]:
        database = Database(app_database_url)
        try:
            async with database.tenant(shop_a.shop_id) as session:
                membership_of_b_owner = await session.active_membership(shop_b.user_id)
                changed = await session._conn.execute(  # type: ignore[attr-defined]
                    text("UPDATE shop SET name = 'hacked' WHERE id = :id"), {"id": shop_b.shop_id}
                )
                return membership_of_b_owner, changed.rowcount
        finally:
            await database.dispose()

    membership, rowcount = asyncio.run(scenario())
    assert membership is None
    assert rowcount == 0
    assert owner.execute("SELECT name FROM shop WHERE id = %s", (shop_b.shop_id,)).fetchone() == ("Shop B",)


def test_counting_the_active_customers_of_several_shops_leaves_the_transaction_without_a_tenant(
    admin_database_url: str, shop_a: Shop, shop_b: Shop, owner: psycopg.Connection
) -> None:
    """The administrators' side counts what the free plan counts, shop by shop, inside a transaction
    that has no tenant. Each shop's count is its own, and afterwards no shop's rows are in sight."""
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm, status) VALUES "
        "(%s, %s, 'Second', 'second', 'active'), (%s, %s, 'Gone', 'gone', 'archived')",
        (uuid.uuid4(), shop_a.shop_id, uuid.uuid4(), shop_a.shop_id),
    )
    unknown = uuid.uuid4()

    async def scenario() -> tuple[dict[uuid.UUID, int], int, int, dict[uuid.UUID, int]]:
        database = Database(admin_database_url, pool_size=1, max_overflow=0)
        try:
            async with database.platform() as session:
                counts = await session.admin_active_customers([shop_a.shop_id, shop_b.shop_id, unknown])
                inside = (await session._conn.execute(text("SELECT count(*) FROM customer"))).scalar_one()
                nothing = await session.admin_active_customers([])
            async with database._engine.begin() as conn:
                after = (await conn.execute(text("SELECT count(*) FROM customer"))).scalar_one()
            return counts, inside, after, nothing
        finally:
            await database.dispose()

    counts, inside, after, nothing = asyncio.run(scenario())
    assert counts == {shop_a.shop_id: 2, shop_b.shop_id: 1, unknown: 0}, "active customers only, each shop its own"
    assert inside == 0, "the last shop counted stayed the tenant of the transaction"
    assert after == 0
    assert nothing == {}
