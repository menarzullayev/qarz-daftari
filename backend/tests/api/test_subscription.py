"""The subscription as the owner sees it, and the daily review (REQ-052, REQ-053, REQ-057; BR-28, BR-29)."""

import asyncio
import itertools
from datetime import date, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import day as show_day
from qarz.application.chat_texts import money, say
from qarz.application.reminders import ReminderService
from qarz.application.scheduler import Scheduler
from qarz.application.subscription import SubscriptionService
from qarz.infrastructure.db import Database

from .conftest import World, as_user
from .test_chat import chat_of
from .test_customers_ledger import record, shop, today
from .test_reminders import at

pytestmark = pytest.mark.db

_days = itertools.count()


@pytest.fixture
def day() -> date:
    """A day of its own for each test, so that the periods the job records never meet another test's."""
    return date(2050, 1, 1) + timedelta(days=next(_days))


def review(app_database_url: str, now: datetime) -> None:
    """One tick of the worker's scheduler at the given moment."""

    async def scenario() -> None:
        database = Database(app_database_url)
        try:
            clock = lambda: now  # noqa: E731
            scheduler = Scheduler(
                database, ReminderService(database, clock), clock, SubscriptionService(database, clock)
            )
            await scheduler.tick()
        finally:
            await database.dispose()

    asyncio.run(scenario())


def set_subscription(owner: psycopg.Connection, world: World, state: str, **dates: date | None) -> None:
    owner.execute(
        "UPDATE subscription SET state = %s, trial_ends = %s, paid_through = %s, prior_state = NULL WHERE shop_id = %s",
        (state, dates.get("trial_ends"), dates.get("paid_through"), world.shop_a),
    )


def stored(owner: psycopg.Connection, world: World) -> Any:
    return owner.execute("SELECT state, prior_state FROM subscription WHERE shop_id = %s", (world.shop_a,)).fetchone()


def told(owner: psycopg.Connection, world: World) -> list[tuple[str, str]]:
    return [
        (str(row[0]), str(row[1]))
        for row in owner.execute(
            "SELECT recipient, payload->>'text' FROM outbox_message WHERE shop_id = %s AND dedupe_key LIKE 'sub:%%' "
            "ORDER BY created_at, id",
            (world.shop_a,),
        ).fetchall()
    ]


def owner_chat(owner: psycopg.Connection, world: World) -> str:
    row = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (world.owner_a,)).fetchone()
    assert row is not None
    return str(row[0])


# --- what the owner sees ---------------------------------------------------------------------------------


def test_the_owner_sees_the_state_the_price_and_where_to_pay(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    ends = today() + timedelta(days=30)
    set_subscription(owner, world, "trial", trial_ends=ends)
    body = client.get(f"{shop(world)}/subscription", headers=as_user(world.owner_a)).json()
    assert body == {
        "state": "trial",
        "trial_ends": ends.isoformat(),
        "paid_through": None,
        "ends_on": ends.isoformat(),
        "days_left": 30,
        "price_uzs": 100_000,  # the initial price (REQ-053)
        "card_number": None,
    }
    for other in (world.manager_a, world.seller_a):
        assert client.get(f"{shop(world)}/subscription", headers=as_user(other)).status_code == 403


def test_the_price_and_card_come_from_the_administrators_settings(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('price_uzs', '150000', 'test'), "
        "('card_number', '\"8600 0000 0000 0000\"', 'test') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
    )
    try:
        body = client.get(f"{shop(world)}/subscription", headers=as_user(world.owner_a)).json()
        assert (body["price_uzs"], body["card_number"]) == (150_000, "8600 0000 0000 0000")
        said = chat_of(client, owner, world.owner_a).say("/obuna").text
        assert say("uz", "sub_price", price=money("uz", 150_000)) in said
        assert say("uz", "sub_pay_to", card="8600 0000 0000 0000") in said
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key IN ('price_uzs', 'card_number')")


@pytest.mark.parametrize(
    ("state", "dates", "expected_state"),
    [
        ("active", {"paid_through": 5}, "active"),
        ("trial", {"trial_ends": -1}, "limited"),
        ("active", {"paid_through": -1}, "limited"),
        ("limited", {}, "limited"),
        ("suspended", {"paid_through": 5}, "suspended"),
    ],
)
def test_an_ended_period_reads_as_limited_whatever_is_stored(
    client: TestClient, world: World, owner: psycopg.Connection, state: str, dates: dict[str, int], expected_state: str
) -> None:
    set_subscription(owner, world, state, **{name: today() + timedelta(days=offset) for name, offset in dates.items()})
    body = client.get(f"{shop(world)}/subscription", headers=as_user(world.owner_a)).json()
    assert body["state"] == expected_state
    assert (body["ends_on"] is None) == (expected_state in ("limited", "suspended"))


def test_obuna_in_chat_is_for_the_owner(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    ends = today() + timedelta(days=12)
    set_subscription(owner, world, "active", paid_through=ends)
    said = chat_of(client, owner, world.owner_a).say("/obuna").text.split("\n")
    assert said == [
        say("uz", "sub_header", shop="Shop A"),
        say("uz", "sub_state_active", date=show_day(ends), days=12),
        say("uz", "sub_price", price=money("uz", 100_000)),
        say("uz", "sub_no_card"),
    ]
    assert chat_of(client, owner, world.seller_a).say("/obuna").text == say("uz", "forbidden")
    assert chat_of(client, owner, world.stranger).say("/obuna").text == say("uz", "no_shops")


# --- the daily review ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "column", "text_key"),
    [("trial", "trial_ends", "sub_trial_ending"), ("active", "paid_through", "sub_paid_ending")],
)
@pytest.mark.parametrize("days", [7, 1])
def test_the_owner_is_warned_before_the_period_ends(
    world: World,
    owner: psycopg.Connection,
    app_database_url: str,
    day: date,
    state: str,
    column: str,
    text_key: str,
    days: int,
) -> None:
    ends = day + timedelta(days=days)
    set_subscription(owner, world, state, **{column: ends})
    review(app_database_url, at(day, 9, 5))
    assert told(owner, world) == [
        (owner_chat(owner, world), say("uz", text_key, shop="Shop A", days=days, date=show_day(ends)))
    ]
    assert stored(owner, world) == (state, None), "a warning changes nothing"

    # The review runs once a day, and a second run would not warn twice anyway.
    review(app_database_url, at(day, 15))
    owner.execute("DELETE FROM job_run WHERE job = 'subscriptions' AND period = %s", (day.isoformat(),))
    review(app_database_url, at(day, 16))
    assert len(told(owner, world)) == 1


@pytest.mark.parametrize("days", [8, 6, 2, 0])
def test_no_warning_on_other_days(
    world: World, owner: psycopg.Connection, app_database_url: str, day: date, days: int
) -> None:
    set_subscription(owner, world, "trial", trial_ends=day + timedelta(days=days))
    review(app_database_url, at(day, 9, 5))
    assert told(owner, world) == []
    assert stored(owner, world) == ("trial", None)


@pytest.mark.parametrize(("state", "column"), [("trial", "trial_ends"), ("active", "paid_through")])
def test_the_day_after_a_period_ends_the_shop_becomes_limited_and_the_owner_is_told(
    world: World, owner: psycopg.Connection, app_database_url: str, day: date, state: str, column: str
) -> None:
    set_subscription(owner, world, state, **{column: day - timedelta(days=1)})
    review(app_database_url, at(day, 9, 5))
    assert stored(owner, world) == ("limited", state)
    assert told(owner, world) == [(owner_chat(owner, world), say("uz", "sub_limited", shop="Shop A"))]
    logged = owner.execute(
        "SELECT actor_kind, actor_id FROM activity WHERE shop_id = %s AND action = 'subscription.limited'",
        (world.shop_a,),
    ).fetchall()
    assert logged == [("system", None)]

    # Told once: the next day's review finds nothing to do for this shop.
    review(app_database_url, at(day + timedelta(days=400), 9, 5))
    assert len(told(owner, world)) == 1


def test_the_review_waits_for_nine_and_leaves_other_states_alone(
    world: World, owner: psycopg.Connection, app_database_url: str, day: date
) -> None:
    set_subscription(owner, world, "trial", trial_ends=day - timedelta(days=1))
    review(app_database_url, at(day, 8, 59))
    assert stored(owner, world) == ("trial", None)

    set_subscription(owner, world, "suspended", paid_through=day - timedelta(days=1))
    review(app_database_url, at(day, 9, 0))
    assert stored(owner, world) == ("suspended", None), "a suspended shop is not moved to limited"
    assert told(owner, world) == []


def test_a_running_period_is_never_limited_by_the_review(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str, day: date
) -> None:
    """The review trusts what it reads inside the shop, not the list it was given."""
    set_subscription(owner, world, "active", paid_through=day + timedelta(days=1))
    review(app_database_url, at(day, 9, 5))
    assert stored(owner, world) == ("active", None)
    # And with the real clock the shop still sells.
    set_subscription(owner, world, "active", paid_through=today())
    assert record(client, world, world.customer_a, "credit", 1000).status_code == 201


def test_the_review_is_done_once_a_day(
    world: World, owner: psycopg.Connection, app_database_url: str, day: date
) -> None:
    calls: list[datetime] = []

    class Counting(SubscriptionService):
        async def run_daily(self) -> int:
            calls.append(self._now())
            return await super().run_daily()

    async def scenario() -> None:
        database = Database(app_database_url)
        try:
            for moment in (at(day, 8, 30), at(day, 9, 1), at(day, 9, 2), at(day, 18, 0)):
                clock = lambda moment=moment: moment  # noqa: E731
                scheduler = Scheduler(database, ReminderService(database, clock), clock, Counting(database, clock))
                await scheduler.tick()
        finally:
            await database.dispose()

    asyncio.run(scenario())
    assert calls == [at(day, 9, 1)]
    assert owner.execute(
        "SELECT count(*) FROM job_run WHERE job = 'subscriptions' AND period = %s", (day.isoformat(),)
    ).fetchone() == (1,)
