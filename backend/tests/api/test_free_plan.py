"""The free plan behind the platform switch `free_plan_on` (BR-33 to BR-35).

A shop without a trial or paid period that has no more active customers than the plan holds is free and
works in full; one customer more needs a trial or paid period; a shop over the number becomes limited as
before. With the switch off nothing of this exists.
"""

import itertools
import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import day as show_day
from qarz.application.chat_texts import money, say

from .conftest import World, as_user
from .test_chat import Chat, chat_of, entries, named
from .test_customers_ledger import another_client, key, new_customer, record, shop, today, write
from .test_reminders import at
from .test_subscription import owner_chat, review, set_subscription, stored, told

pytestmark = pytest.mark.db

_days = itertools.count()
_tg_ids = itertools.count(7_300_000_000)

FreePlan = Callable[[int], None]
PERIOD_BODY = {"state", "trial_ends", "paid_through", "ends_on", "days_left", "price_uzs", "card_number", "cards"}
NO_SMS = {"offered": False, "included": False, "quota": 0, "left": 0}


@pytest.fixture
def day() -> date:
    """A day of its own for each test, away from the days the subscription tests use."""
    return date(2060, 1, 1) + timedelta(days=next(_days))


@pytest.fixture
def sms(owner: psycopg.Connection) -> Iterator[None]:
    """Switch SMS on for the platform with a quota of two a month, and off again afterwards."""
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('sms_on', 'true', 'test'), "
        "('sms_monthly_quota', '2', 'test') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
    )
    yield
    owner.execute("DELETE FROM platform_setting WHERE key IN ('sms_on', 'sms_monthly_quota')")


def active(owner: psycopg.Connection, shop_id: uuid.UUID) -> int:
    """The shop's customers that count for the plan: those whose status is active."""
    row = owner.execute("SELECT count(*) FROM customer WHERE shop_id = %s AND status = 'active'", (shop_id,)).fetchone()
    assert row is not None
    return int(row[0])


def subscription(client: TestClient, world: World) -> dict[str, Any]:
    response = client.get(f"{shop(world)}/subscription", headers=as_user(world.owner_a))
    assert response.status_code == 200, response.text
    return dict(response.json())


def obuna(client: TestClient, owner: psycopg.Connection, world: World) -> list[str]:
    return chat_of(client, owner, world.owner_a).say("/obuna").text.split("\n")


def add(client: TestClient, world: World, name: str) -> Any:
    return write(client, world.seller_a, "POST", f"{shop(world)}/customers", {"display_name": name})


def refusal(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def logged(owner: psycopg.Connection, world: World) -> list[str]:
    rows = owner.execute(
        "SELECT action FROM activity WHERE shop_id = %s AND action LIKE 'subscription.%%' ORDER BY at, id",
        (world.shop_a,),
    ).fetchall()
    return [str(action) for (action,) in rows]


# --- the switch off: what was true before the plan ---------------------------------------------------------


@pytest.mark.parametrize("switch", ["false", None, '"true"', "1"])
def test_with_the_switch_off_the_review_and_adding_a_customer_are_what_they_were(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    worker_database_url: str,
    day: date,
    switch: str | None,
) -> None:
    """A number of customers stored without the switch, and a switch that is anything but a stored true,
    change nothing: the shop the plan would have held becomes limited, and a customer is added to it."""
    owner.execute("INSERT INTO platform_setting (key, value, updated_by) VALUES ('free_plan_customers', '1', 'test')")
    if switch is not None:
        owner.execute(
            "INSERT INTO platform_setting (key, value, updated_by) VALUES ('free_plan_on', %s::jsonb, 'test')",
            (switch,),
        )
    try:
        # The daily review: the day after the trial ended the shop is limited and the owner told so.
        set_subscription(owner, world, "trial", trial_ends=day - timedelta(days=1))
        review(worker_database_url, at(day, 9, 5))
        assert stored(owner, world) == ("limited", "trial")
        assert told(owner, world) == [(owner_chat(owner, world), say("uz", "sub_limited", shop="Shop A"))]
        assert logged(owner, world) == ["subscription.limited"]

        # What the owner reads has the fields it had, and no others.
        body = subscription(client, world)
        assert set(body) == PERIOD_BODY
        assert (body["state"], body["ends_on"]) == ("limited", None)
        assert obuna(client, owner, world) == [
            say("uz", "sub_header", shop="Shop A"),
            say("uz", "sub_state_limited"),
            say("uz", "sub_price", price=money("uz", 100_000)),
            say("uz", "sub_no_card"),
        ]

        # A customer is added to a limited shop however many it has; a new credit sale is refused.
        assert active(owner, world.shop_a) > 1
        customer = new_customer(client, world, "Cheklovsiz")
        assert refusal(record(client, world, customer, "credit", 1000)) == (402, "SUBSCRIPTION_LIMITED")
        archived = f"{shop(world)}/customers/{world.archived_customer_a}/unarchive"
        assert write(client, world.manager_a, "POST", archived).status_code == 200
    finally:
        owner.execute("DELETE FROM platform_setting WHERE updated_by = 'test'")


def test_with_the_switch_off_a_warning_says_what_it_said(
    world: World, owner: psycopg.Connection, worker_database_url: str, day: date
) -> None:
    ends = day + timedelta(days=7)
    set_subscription(owner, world, "trial", trial_ends=ends)
    review(worker_database_url, at(day, 9, 5))
    assert told(owner, world) == [
        (owner_chat(owner, world), say("uz", "sub_trial_ending", shop="Shop A", days=7, date=show_day(ends)))
    ]


# --- free: a shop the plan holds works in full ---------------------------------------------------------------


def test_a_shop_without_a_period_that_the_plan_holds_is_free_and_one_customer_over_is_limited(
    client: TestClient, world: World, owner: psycopg.Connection, free_plan: FreePlan
) -> None:
    set_subscription(owner, world, "limited")
    customers = active(owner, world.shop_a)
    free_plan(customers)  # exactly as many as the plan holds

    body = subscription(client, world)
    assert set(body) == PERIOD_BODY | {"plan"}
    assert (body["state"], body["ends_on"], body["days_left"]) == ("free", None, None)
    assert body["plan"] == {"free_customers": customers, "customers": customers, "after_period": None, "sms": NO_SMS}
    assert record(client, world, world.customer_a, "credit", 5000).status_code == 201
    assert stored(owner, world) == ("limited", None), "nothing is stored for a free shop"

    # The same shop when the plan holds one customer fewer: limited, exactly as without the plan.
    free_plan(customers - 1)
    assert subscription(client, world)["state"] == "limited"
    assert refusal(record(client, world, world.customer_a, "credit", 5000)) == (402, "SUBSCRIPTION_LIMITED")
    assert record(client, world, world.customer_a, "payment", 1000).status_code == 201, "BR-29 still holds"


def test_archived_customers_do_not_count(
    client: TestClient, world: World, owner: psycopg.Connection, free_plan: FreePlan
) -> None:
    set_subscription(owner, world, "limited")
    everyone = owner.execute("SELECT count(*) FROM customer WHERE shop_id = %s", (world.shop_a,)).fetchone()
    assert everyone is not None and everyone[0] > active(owner, world.shop_a), "the shop has an archived customer"
    free_plan(active(owner, world.shop_a))
    assert subscription(client, world)["plan"]["customers"] == active(owner, world.shop_a)
    assert subscription(client, world)["state"] == "free"


def test_a_suspended_shop_stays_suspended_whatever_the_plan_holds(
    client: TestClient, world: World, owner: psycopg.Connection, free_plan: FreePlan
) -> None:
    free_plan(1000)
    set_subscription(owner, world, "suspended")
    assert subscription(client, world)["state"] == "suspended"
    assert refusal(add(client, world, "Yangi")) == (403, "SHOP_SUSPENDED")
    assert refusal(record(client, world, world.customer_a, "payment", 1000)) == (403, "SHOP_SUSPENDED")
    assert obuna(client, owner, world) == [
        say("uz", "sub_header", shop="Shop A"),
        say("uz", "sub_state_suspended"),
        say("uz", "sub_price", price=money("uz", 100_000)),
        say("uz", "sub_no_card"),
    ]


# --- one customer more than the plan holds -----------------------------------------------------------------


def test_the_customer_after_the_last_free_place_is_refused_unless_a_period_runs(
    client: TestClient, world: World, owner: psycopg.Connection, free_plan: FreePlan
) -> None:
    set_subscription(owner, world, "limited")
    limit = active(owner, world.shop_a) + 1
    free_plan(limit)
    assert add(client, world, "Oxirgi joy").status_code == 201

    refused = add(client, world, "Ortiqcha")
    assert refusal(refused) == (402, "FREE_PLAN_FULL")
    error = refused.json()["error"]
    assert error["fields"] == {"limit": str(limit)}
    assert str(limit) in error["message"] and "/obuna" in error["message"]
    assert named(owner, world, "Ortiqcha") == (0,)
    assert active(owner, world.shop_a) == limit
    # Everything else goes on: the shop is free, not limited.
    assert record(client, world, world.customer_a, "credit", 5000).status_code == 201

    # A trial or a paid period goes beyond what the plan holds, to its last day.
    set_subscription(owner, world, "trial", trial_ends=today())
    assert add(client, world, "Sinovda").status_code == 201
    set_subscription(owner, world, "active", paid_through=today())
    assert add(client, world, "To'langan").status_code == 201
    # The day after it, the place is asked for again; and the shop, now over the number, is limited.
    set_subscription(owner, world, "active", paid_through=today() - timedelta(days=1))
    assert refusal(add(client, world, "Kech")) == (402, "FREE_PLAN_FULL")
    assert refusal(record(client, world, world.customer_a, "credit", 5000)) == (402, "SUBSCRIPTION_LIMITED")


def test_the_bot_says_how_many_the_plan_holds_and_how_to_subscribe(
    client: TestClient, world: World, owner: psycopg.Connection, free_plan: FreePlan
) -> None:
    set_subscription(owner, world, "limited")
    limit = active(owner, world.shop_a)
    free_plan(limit)
    seller = chat_of(client, owner, world.seller_a)
    before = entries(owner, world.shop_a)
    asked = seller.say("Dilshod 45000 un")
    done = seller.press(asked.button("➕ Qo'shish"), seller.last_message_id)
    assert done.text == say("uz", "FREE_PLAN_FULL", limit=limit)
    assert str(limit) in done.text and "/obuna" in done.text
    assert named(owner, world, "Dilshod") == (0,)
    assert entries(owner, world.shop_a) == before, "the sale is not written without its customer"
    # A sale to a customer the shop has is written as ever.
    assert seller.say("Ali 30000").text.startswith("✅ Shop A")


def test_a_customer_taken_out_of_the_archive_needs_a_place(
    client: TestClient, world: World, owner: psycopg.Connection, free_plan: FreePlan
) -> None:
    set_subscription(owner, world, "limited")
    free_plan(active(owner, world.shop_a))
    path = f"{shop(world)}/customers"
    refused = write(client, world.manager_a, "POST", f"{path}/{world.archived_customer_a}/unarchive")
    assert refusal(refused) == (402, "FREE_PLAN_FULL")
    status = owner.execute("SELECT status FROM customer WHERE id = %s", (world.archived_customer_a,)).fetchone()
    assert status == ("archived",)

    # Archiving a customer who owes nothing frees a place.
    assert write(client, world.manager_a, "POST", f"{path}/{world.settled_customer_a}/archive").status_code == 200
    restored = write(client, world.manager_a, "POST", f"{path}/{world.archived_customer_a}/unarchive")
    assert (restored.status_code, restored.json()["status"]) == (200, "active")


def test_two_requests_at_once_cannot_both_take_the_last_free_place(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str, free_plan: FreePlan
) -> None:
    set_subscription(owner, world, "limited")
    with another_client(app_database_url) as second:
        for attempt in range(5):
            limit = active(owner, world.shop_a) + 1
            free_plan(limit)
            barrier = threading.Barrier(2)

            def create(which: TestClient, attempt: int = attempt, barrier: threading.Barrier = barrier) -> int:
                barrier.wait(timeout=10)
                body = {"display_name": f"Poyga {attempt} {uuid.uuid4().hex[:6]}"}
                headers = {**as_user(world.seller_a), **key()}
                return int(which.post(f"{shop(world)}/customers", json=body, headers=headers).status_code)

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = sorted(pool.map(create, (client, second)))
            assert statuses == [201, 402]
            assert active(owner, world.shop_a) == limit


# --- the daily review ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("state", "column"), [("trial", "trial_ends"), ("active", "paid_through")])
def test_a_shop_the_plan_holds_becomes_free_when_its_period_ends_and_the_owner_is_told_so(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    worker_database_url: str,
    day: date,
    free_plan: FreePlan,
    state: str,
    column: str,
) -> None:
    limit = active(owner, world.shop_a)
    free_plan(limit)
    set_subscription(owner, world, state, **{column: day - timedelta(days=1)})
    review(worker_database_url, at(day, 9, 5))
    assert stored(owner, world) == ("limited", state), "the row says that no period runs"
    assert told(owner, world) == [(owner_chat(owner, world), say("uz", "sub_free_now", shop="Shop A", limit=limit))]
    assert logged(owner, world) == ["subscription.free"]
    assert subscription(client, world)["state"] == "free"
    assert record(client, world, world.customer_a, "credit", 5000).status_code == 201

    # Told once.
    review(worker_database_url, at(day + timedelta(days=400), 9, 5))
    assert len(told(owner, world)) == 1


@pytest.mark.parametrize(("state", "column"), [("trial", "trial_ends"), ("active", "paid_through")])
def test_a_shop_over_the_number_becomes_limited_when_its_period_ends_as_before(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    worker_database_url: str,
    day: date,
    free_plan: FreePlan,
    state: str,
    column: str,
) -> None:
    free_plan(active(owner, world.shop_a) - 1)
    set_subscription(owner, world, state, **{column: day - timedelta(days=1)})
    review(worker_database_url, at(day, 9, 5))
    assert stored(owner, world) == ("limited", state)
    assert told(owner, world) == [(owner_chat(owner, world), say("uz", "sub_limited", shop="Shop A"))]
    assert logged(owner, world) == ["subscription.limited"]
    assert subscription(client, world)["state"] == "limited"
    assert refusal(record(client, world, world.customer_a, "credit", 5000)) == (402, "SUBSCRIPTION_LIMITED")
    assert refusal(add(client, world, "Yana biri")) == (402, "FREE_PLAN_FULL")


@pytest.mark.parametrize(
    ("state", "column", "text_key"),
    [("trial", "trial_ends", "sub_trial_ending"), ("active", "paid_through", "sub_paid_ending")],
)
@pytest.mark.parametrize(("spare", "after"), [(0, "free"), (-1, "limited")])
def test_a_warning_says_what_follows_the_period(
    world: World,
    owner: psycopg.Connection,
    worker_database_url: str,
    day: date,
    free_plan: FreePlan,
    state: str,
    column: str,
    text_key: str,
    spare: int,
    after: str,
) -> None:
    customers = active(owner, world.shop_a)
    limit = customers + spare
    free_plan(limit)
    ends = day + timedelta(days=7)
    set_subscription(owner, world, state, **{column: ends})
    review(worker_database_url, at(day, 9, 5))
    warning = say("uz", text_key, shop="Shop A", days=7, date=show_day(ends))
    follows = say("uz", f"sub_then_{after}", used=customers, limit=limit)
    assert told(owner, world) == [(owner_chat(owner, world), f"{warning} {follows}")]
    assert stored(owner, world) == (state, None), "a warning changes nothing"


# --- what the owner sees -------------------------------------------------------------------------------------


def test_obuna_names_the_plan_the_customers_used_and_what_paying_adds(
    client: TestClient, world: World, owner: psycopg.Connection, free_plan: FreePlan
) -> None:
    customers = active(owner, world.shop_a)
    free_plan(customers)
    used = say("uz", "sub_customers", used=customers, limit=customers)
    tail = [say("uz", "sub_price", price=money("uz", 100_000)), say("uz", "sub_no_card")]

    set_subscription(owner, world, "limited")
    assert obuna(client, owner, world) == [
        say("uz", "sub_header", shop="Shop A"),
        say("uz", "sub_state_free"),
        used,
        say("uz", "sub_paying_adds"),
        *tail,
    ]

    ends = today() + timedelta(days=12)
    set_subscription(owner, world, "trial", trial_ends=ends)
    assert obuna(client, owner, world) == [
        say("uz", "sub_header", shop="Shop A"),
        say("uz", "sub_state_trial", date=show_day(ends), days=12),
        used,
        say("uz", "sub_then_free", used=customers, limit=customers),
        say("uz", "sub_paying_adds"),
        *tail,
    ]
    assert subscription(client, world)["plan"]["after_period"] == "free"

    # A paying shop over the number: what follows an unpaid period is limited mode, and it is said.
    free_plan(customers - 1)
    set_subscription(owner, world, "active", paid_through=ends)
    assert obuna(client, owner, world) == [
        say("uz", "sub_header", shop="Shop A"),
        say("uz", "sub_state_active", date=show_day(ends), days=12),
        say("uz", "sub_customers", used=customers, limit=customers - 1),
        say("uz", "sub_then_limited", used=customers, limit=customers - 1),
        *tail,
    ]
    assert subscription(client, world)["plan"]["after_period"] == "limited"

    # In Russian too.
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.owner_a,))
    assert say("ru", "sub_then_limited", used=customers, limit=customers - 1) in obuna(client, owner, world)


def test_the_owner_sees_whether_sms_is_included_and_how_many_are_left(
    client: TestClient, world: World, owner: psycopg.Connection, free_plan: FreePlan, sms: None
) -> None:
    customers = active(owner, world.shop_a)
    free_plan(customers)
    ends = today() + timedelta(days=12)

    # Free and trial: SMS is what paying adds, not what the shop has.
    for state, dates in (("limited", {}), ("trial", {"trial_ends": ends})):
        set_subscription(owner, world, state, **dates)
        assert subscription(client, world)["plan"]["sms"] == {"offered": True, "included": False, "quota": 2, "left": 0}
        said = obuna(client, owner, world)
        assert say("uz", "sub_paying_adds_quota", quota=2) in said
        assert say("uz", "sub_quota_left", left=0, quota=2) not in said

    set_subscription(owner, world, "active", paid_through=ends)
    assert subscription(client, world)["plan"]["sms"] == {"offered": True, "included": True, "quota": 2, "left": 2}
    owner.execute(
        "INSERT INTO reminder (id, shop_id, customer_id, kind, channel, amount, sent_on) "
        "VALUES (%s, %s, %s, 'auto', 'sms', 1000, %s)",
        (uuid.uuid4(), world.shop_a, world.customer_a, today()),
    )
    assert subscription(client, world)["plan"]["sms"]["left"] == 1
    said = obuna(client, owner, world)
    assert say("uz", "sub_quota_left", left=1, quota=2) in said
    assert say("uz", "sub_paying_adds_quota", quota=2) not in said


def test_the_plan_reads_the_number_as_the_panel_shows_it(
    client: TestClient, world: World, owner: psycopg.Connection, free_plan: FreePlan
) -> None:
    """A stored number outside the allowed range does not apply: the plan then holds thirty."""
    set_subscription(owner, world, "limited")
    free_plan(0)
    assert owner.execute("SELECT value FROM platform_setting WHERE key = 'free_plan_customers'").fetchone() == (0,)
    assert subscription(client, world)["plan"]["free_customers"] == 30


# --- a person's later shops ------------------------------------------------------------------------------------


def test_a_later_shop_starts_free_and_the_chat_says_so(
    client: TestClient, owner: psycopg.Connection, free_plan: FreePlan
) -> None:
    free_plan(30)
    person = Chat(client, owner, next(_tg_ids), language="ru")
    new_shop = person.say("/start").button("🏪")

    def open_shop(name: str) -> str:
        assert person.press(new_shop).text == say("ru", "ask_shop_name")
        return person.say(name).text

    assert open_shop("Первый") == say("ru", "shop_created", shop="Первый")
    assert open_shop("Второй") == say("ru", "shop_created_free", shop="Второй")
    # It can record a credit sale at once: the first message creates the customer and the entry.
    asked = person.say("Али 45000")
    assert person.press(asked.button("➕"), person.last_message_id).text.startswith("✅ Второй")
    states = owner.execute(
        "SELECT sub.state FROM subscription sub JOIN membership m ON m.shop_id = sub.shop_id "
        "JOIN app_user u ON u.id = m.user_id WHERE u.tg_id = %s ORDER BY sub.state",
        (person.tg_id,),
    ).fetchall()
    assert states == [("limited",), ("trial",)], "nothing new is stored: the later shop has no period"


def test_the_api_names_a_later_shop_free(client: TestClient, owner: psycopg.Connection, free_plan: FreePlan) -> None:
    free_plan(30)
    person = uuid.uuid4()
    owner.execute("INSERT INTO app_user (id, tg_id, lang) VALUES (%s, %s, 'uz')", (person, next(_tg_ids)))

    def open_shop(name: str) -> str:
        headers = {**as_user(person), **key()}
        response = client.post("/api/v1/shops", json={"name": name, "lang": "uz"}, headers=headers)
        assert response.status_code == 201, response.text
        return str(response.json()["subscription_state"])

    assert (open_shop("Bir"), open_shop("Ikki")) == ("trial", "free")
    assert open_shop("Uch") == "free", "and every shop after it"
