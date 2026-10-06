"""Deleting a shop: the owner asks, may cancel, and after the waiting period the shop is erased (REQ-048, BR-25)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import day, say
from qarz.application.reminders import ReminderService
from qarz.application.scheduler import Scheduler
from qarz.application.shop_deletion import ShopDeletionService
from qarz.domain.promise import tashkent_date
from qarz.domain.shop_deletion import WAITING_PERIOD, erasure_due, name_confirms
from qarz.infrastructure.db import Database

from .conftest import World, as_user
from .test_customers_ledger import key, record, shop

pytestmark = pytest.mark.db


# --- the rule ------------------------------------------------------------------------------------------


def test_the_waiting_period_is_thirty_days() -> None:
    asked = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    assert erasure_due(asked) == datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    assert timedelta(days=30) == WAITING_PERIOD


@pytest.mark.parametrize("typed", ["Shop A", "shop a", "  SHOP   A ", "Shop\tA"])
def test_the_shops_name_confirms_whatever_its_case_and_spacing(typed: str) -> None:
    assert name_confirms(typed, "Shop A") is True


@pytest.mark.parametrize("typed", ["", "   ", "Shop", "Shop B", "ShopA", "Shop A!", "yes", "delete"])
def test_anything_else_does_not_confirm(typed: str) -> None:
    assert name_confirms(typed, "Shop A") is False


# --- asking and cancelling -------------------------------------------------------------------------------


def ask(client: TestClient, world: World, name: Any = "Shop A", user: uuid.UUID | None = None) -> Any:
    return client.post(
        f"{shop(world)}/deletion", json={"confirm_name": name}, headers={**as_user(user or world.owner_a), **key()}
    )


def cancel(client: TestClient, world: World) -> Any:
    return client.delete(f"{shop(world)}/deletion", headers={**as_user(world.owner_a), **key()})


def shop_row(owner: psycopg.Connection, world: World) -> Any:
    return owner.execute(
        "SELECT status, name, deletion_due - now() FROM shop WHERE id = %s", (world.shop_a,)
    ).fetchone()


def owner_messages(owner: psycopg.Connection, world: World) -> list[str]:
    tg = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (world.owner_a,)).fetchone()
    assert tg is not None
    return [
        str(row[0])
        for row in owner.execute(
            "SELECT payload->>'text' FROM outbox_message WHERE recipient = %s AND dedupe_key LIKE 'shop:%%' "
            "ORDER BY created_at, id",
            (str(tg[0]),),
        ).fetchall()
    ]


@pytest.mark.parametrize("name", ["", "Shop", "Shop B", "delete", None, 5])
def test_deletion_needs_the_shops_name(client: TestClient, world: World, owner: psycopg.Connection, name: Any) -> None:
    assert ask(client, world, name).status_code == 422
    assert shop_row(owner, world)[:2] == ("active", "Shop A")


def test_the_owner_asks_and_the_shop_waits_thirty_days(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    assert client.get(f"{shop(world)}/deletion", headers=as_user(world.owner_a)).json() == {
        "status": "active",
        "deletion_due": None,
    }
    asked = ask(client, world, "  shop   a ")
    assert asked.status_code == 201, asked.text
    assert asked.json()["status"] == "deletion_pending"
    status, _, remaining = shop_row(owner, world)
    assert status == "deletion_pending"
    assert timedelta(days=29, hours=23, minutes=58) < remaining <= timedelta(days=30)
    due = datetime.fromisoformat(asked.json()["deletion_due"])
    assert owner_messages(owner, world) == [
        say("uz", "shop_deletion_requested", shop="Shop A", date=day(tashkent_date(due)))
    ]
    logged = owner.execute(
        "SELECT actor_id FROM activity WHERE shop_id = %s AND action = 'shop.deletion_requested'", (world.shop_a,)
    ).fetchall()
    assert logged == [(world.owner_a_membership,)]
    assert client.get(f"{shop(world)}/deletion", headers=as_user(world.owner_a)).json() == asked.json()

    again = ask(client, world)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "DELETION_ALREADY_REQUESTED")

    # While it waits the shop goes on as before, so that cancelling leaves nothing to repair.
    assert record(client, world, world.customer_a, "credit", 1000).status_code == 201
    assert client.get(f"{shop(world)}/customers", headers=as_user(world.seller_a)).status_code == 200


def test_the_owner_cancels_and_nothing_is_lost(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    refused = cancel(client, world)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "DELETION_NOT_REQUESTED")

    ask(client, world)
    entries_before = owner.execute("SELECT count(*) FROM ledger_entry WHERE shop_id = %s", (world.shop_a,)).fetchone()
    done = cancel(client, world)
    assert (done.status_code, done.json()) == (200, {"status": "active", "deletion_due": None})
    assert shop_row(owner, world) == ("active", "Shop A", None)
    assert owner_messages(owner, world)[-1] == say("uz", "shop_deletion_cancelled", shop="Shop A")
    assert (
        owner.execute("SELECT count(*) FROM ledger_entry WHERE shop_id = %s", (world.shop_a,)).fetchone()
        == entries_before
    )
    assert cancel(client, world).status_code == 409
    # It can be asked for again, with a fresh waiting period.
    assert ask(client, world).status_code == 201


# --- erasure ---------------------------------------------------------------------------------------------


def run_erasure(app_database_url: str) -> int:
    async def scenario() -> int:
        database = Database(app_database_url)
        try:
            return await ShopDeletionService(database).erase_due()
        finally:
            await database.dispose()

    return asyncio.run(scenario())


def test_a_shop_is_erased_only_after_its_waiting_period(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    ask(client, world)
    run_erasure(app_database_url)
    assert shop_row(owner, world)[0] == "deletion_pending", "the waiting period is not over"
    assert owner.execute("SELECT count(*) FROM ledger_entry WHERE shop_id = %s", (world.shop_a,)).fetchone() == (1,)

    owner.execute("UPDATE shop SET deletion_due = now() - interval '1 second' WHERE id = %s", (world.shop_a,))
    owner_tg = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (world.owner_a,)).fetchone()
    run_erasure(app_database_url)

    assert shop_row(owner, world)[:2] == ("erased", "erased")
    for table in ("ledger_entry", "customer", "customer_link", "membership", "promise", "activity", "subscription"):
        assert owner.execute(f"SELECT count(*) FROM {table} WHERE shop_id = %s", (world.shop_a,)).fetchone() == (0,)
    assert owner.execute("SELECT count(*) FROM ledger_entry WHERE shop_id = %s", (world.shop_b,)).fetchone() is not None
    assert owner.execute("SELECT count(*) FROM membership WHERE shop_id = %s", (world.shop_b,)).fetchone() == (1,)

    # The owner is told once it is done; the message does not belong to the shop, which is gone.
    assert owner_tg is not None
    told = owner.execute(
        "SELECT recipient, shop_id, payload->>'text' FROM outbox_message WHERE dedupe_key = %s",
        (f"shop:erased:{world.shop_a}",),
    ).fetchall()
    assert told == [(str(owner_tg[0]), None, say("uz", "shop_erased", shop="Shop A"))]

    # Staff lose access, and the customer's link is gone (REQ-048).
    for user in (world.owner_a, world.manager_a, world.seller_a):
        assert client.get(f"{shop(world)}/customers", headers=as_user(user)).status_code == 404
        assert client.get("/api/v1/me/shops", headers=as_user(user)).json()["items"] == []
    assert client.get("/api/v1/me/accounts", headers=as_user(world.customer_of_a)).json() == {"items": []}
    # A second run finds nothing to do.
    run_erasure(app_database_url)
    again = owner.execute(
        "SELECT count(*) FROM outbox_message WHERE dedupe_key = %s", (f"shop:erased:{world.shop_a}",)
    ).fetchone()
    assert again == (1,)
    assert shop_row(owner, world)[:2] == ("erased", "erased")


def test_the_worker_erases_due_shops_once_an_hour(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    ask(client, world)
    owner.execute("UPDATE shop SET deletion_due = now() - interval '1 second' WHERE id = %s", (world.shop_a,))
    calls: list[int] = []

    class Counting(ShopDeletionService):
        async def erase_due(self) -> int:
            calls.append(1)
            return await super().erase_due()

    moment = datetime(2070, 5, 5, 6, 30, tzinfo=UTC)

    async def scenario() -> None:
        database = Database(app_database_url)
        try:
            for minutes in (0, 5, 61):
                clock = lambda minutes=minutes: moment + timedelta(minutes=minutes)  # noqa: E731
                await Scheduler(
                    database, ReminderService(database, clock), clock, None, Counting(database, clock)
                ).tick()
        finally:
            await database.dispose()

    asyncio.run(scenario())
    assert len(calls) == 2, "once in the first hour, once in the next"
    assert shop_row(owner, world)[0] == "erased"
