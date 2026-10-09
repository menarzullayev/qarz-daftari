"""Reminders: settings, the hourly job, a manual reminder, channels and limits.

REQ-022 to REQ-025, REQ-042, REQ-043, REQ-N10; domain rules BR-13, BR-17 to BR-19.

The job is driven with a clock set to a day far in the future, a different day for each test, so that the
periods it records never meet those of another test in the same database.
"""

import asyncio
import itertools
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import money, say
from qarz.application.reminders import ReminderService
from qarz.application.scheduler import Scheduler
from qarz.domain.promise import TASHKENT, tashkent_date
from qarz.infrastructure.db import Database

from .conftest import World, as_user
from .test_customers_ledger import key, seed_customer, shop

pytestmark = pytest.mark.db

_days = itertools.count()


@pytest.fixture
def day() -> date:
    """A day of its own for each test."""
    return date(2040, 1, 1) + timedelta(days=next(_days))


def at(day: date, hour: int, minute: int = 30) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TASHKENT).astimezone(UTC)


def run_job(worker_database_url: str, now: datetime, call: Callable[[ReminderService, Scheduler], Any]) -> Any:
    async def scenario() -> Any:
        database = Database(worker_database_url)
        try:
            service = ReminderService(database, lambda: now)
            return await call(service, Scheduler(database, service, lambda: now))
        finally:
            await database.dispose()

    return asyncio.run(scenario())


def turn_on(owner: psycopg.Connection, shop_id: uuid.UUID, hour: int = 10, template: int = 1) -> None:
    owner.execute(
        "UPDATE shop SET reminders_on = true, reminder_hour = %s, reminder_tpl = %s WHERE id = %s",
        (hour, template, shop_id),
    )
    # The world's own debtor, Ali, is promised a week from the real today; seen from a day in 2040 that is
    # long overdue. These tests are about the customers they create, so Ali is taken out of the picture.
    owner.execute("UPDATE customer SET reminders_off = true WHERE shop_id = %s AND display_name = 'Ali'", (shop_id,))


def debtor(
    owner: psycopg.Connection,
    world: World,
    name: str,
    amount: int,
    promised: date,
    *,
    phone: str | None = None,
    sold: date | None = None,
) -> uuid.UUID:
    """A customer of shop A who owes `amount`, promised for the given day."""
    customer = seed_customer(owner, world.shop_a, name)
    if phone:
        owner.execute("UPDATE customer SET phone = %s WHERE id = %s", (phone, customer))
    add_debt(owner, world, customer, 1, amount, promised, sold=sold)
    return customer


def add_debt(
    owner: psycopg.Connection,
    world: World,
    customer: uuid.UUID,
    seq: int,
    amount: int,
    promised: date,
    *,
    sold: date | None = None,
) -> uuid.UUID:
    entry = uuid.uuid4()
    created = datetime.combine(sold or promised - timedelta(days=10), time(9, 0), tzinfo=TASHKENT)
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id, created_at) "
        "VALUES (%s, %s, %s, %s, 'credit', %s, %s, %s)",
        (entry, world.shop_a, customer, seq, amount, world.seller_a_membership, created),
    )
    owner.execute(
        "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor, created_at) "
        "VALUES (%s, %s, %s, %s, 'staff', %s)",
        (uuid.uuid4(), world.shop_a, entry, promised, created),
    )
    return entry


def link(owner: psycopg.Connection, world: World, customer: uuid.UUID, lang: str = "uz") -> int:
    """Link the customer to a new Telegram user; returns the chat identifier."""
    user, tg_id = uuid.uuid4(), uuid.uuid4().int % 10**15
    owner.execute("INSERT INTO app_user (id, tg_id, lang) VALUES (%s, %s, %s)", (user, tg_id, lang))
    owner.execute(
        "INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at) "
        "VALUES (%s, %s, %s, %s, 'active', 2, now())",
        (uuid.uuid4(), world.shop_a, customer, user),
    )
    return tg_id


def reminders(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT c.display_name, r.kind, r.channel, r.amount, r.sent_on FROM reminder r "
        "JOIN customer c ON c.id = r.customer_id WHERE r.shop_id = %s ORDER BY c.display_name, r.kind",
        (world.shop_a,),
    ).fetchall()


def messages(owner: psycopg.Connection, world: World) -> list[tuple[str, str, str]]:
    return [
        (str(row[0]), str(row[1]), str(row[2]))
        for row in owner.execute(
            "SELECT channel, recipient, payload->>'text' FROM outbox_message "
            "WHERE shop_id = %s AND dedupe_key LIKE 'reminder:%%' ORDER BY created_at, id",
            (world.shop_a,),
        ).fetchall()
    ]


# --- the hourly job -------------------------------------------------------------------------------------


def test_reminders_are_off_until_the_shop_turns_them_on(
    world: World, owner: psycopg.Connection, worker_database_url: str, day: date
) -> None:
    link(owner, world, debtor(owner, world, "Kechikkan", 70000, day - timedelta(days=3)))
    run_job(worker_database_url, at(day, 10), lambda service, _: service.run_hour(10))
    assert reminders(owner, world) == []

    turn_on(owner, world.shop_a)
    run_job(worker_database_url, at(day, 10), lambda service, _: service.run_hour(10))
    assert reminders(owner, world) == [("Kechikkan", "auto", "telegram", 70000, day)]


def test_a_reminder_states_the_shop_and_the_amount_in_the_customers_language(
    world: World, owner: psycopg.Connection, worker_database_url: str, day: date
) -> None:
    turn_on(owner, world.shop_a, template=1)
    due = link(owner, world, debtor(owner, world, "Bugun", 40000, day))
    late = link(owner, world, debtor(owner, world, "Kech", 70000, day - timedelta(days=2)), lang="ru")
    link(owner, world, debtor(owner, world, "Erta", 90000, day + timedelta(days=1)))  # not due yet

    run_job(worker_database_url, at(day, 10), lambda service, _: service.run_hour(10))
    assert sorted(messages(owner, world)) == sorted(
        [
            ("telegram", str(due), say("uz", "r1_due_today", shop="Shop A", name="Bugun", amount=money("uz", 40000))),
            ("telegram", str(late), say("ru", "r1_overdue", shop="Shop A", name="Kech", amount=money("ru", 70000))),
        ]
    )
    for _, _, text in messages(owner, world):
        assert "Erta" not in text and "90" not in text, "nothing about other customers (REQ-024)"

    # Running the same hour again sends nothing more.
    run_job(worker_database_url, at(day, 10, 45), lambda service, _: service.run_hour(10))
    assert len(messages(owner, world)) == 2
    assert len(reminders(owner, world)) == 2


@pytest.mark.parametrize("template", [1, 2, 3])
def test_the_shop_picks_the_wording(
    world: World, owner: psycopg.Connection, worker_database_url: str, day: date, template: int
) -> None:
    turn_on(owner, world.shop_a, template=template)
    link(owner, world, debtor(owner, world, "Kech", 70000, day - timedelta(days=2)))
    run_job(worker_database_url, at(day, 10), lambda service, _: service.run_hour(10))
    assert [text for _, _, text in messages(owner, world)] == [
        say("uz", f"r{template}_overdue", shop="Shop A", name="Kech", amount=money("uz", 70000))
    ]


def test_an_overdue_debt_is_reminded_of_again_only_after_seven_days(
    world: World, owner: psycopg.Connection, worker_database_url: str, day: date
) -> None:
    turn_on(owner, world.shop_a)
    customers = {}
    for name, days_ago in (("Olti", 6), ("Yetti", 7), ("Hech", None)):
        customers[name] = debtor(owner, world, name, 10000, day - timedelta(days=20))
        link(owner, world, customers[name])
        if days_ago is not None:
            owner.execute(
                "INSERT INTO reminder (id, shop_id, customer_id, kind, channel, amount, sent_on) "
                "VALUES (%s, %s, %s, 'auto', 'telegram', 10000, %s)",
                (uuid.uuid4(), world.shop_a, customers[name], day - timedelta(days=days_ago)),
            )
    run_job(worker_database_url, at(day, 10), lambda service, _: service.run_hour(10))
    sent_today = [row[0] for row in reminders(owner, world) if row[4] == day]
    assert sorted(sent_today) == ["Hech", "Yetti"]


def test_only_shops_whose_hour_it_is_are_served(
    world: World, owner: psycopg.Connection, worker_database_url: str, day: date
) -> None:
    turn_on(owner, world.shop_a, hour=14)
    link(owner, world, debtor(owner, world, "Kech", 70000, day - timedelta(days=2)))
    run_job(worker_database_url, at(day, 10), lambda service, _: service.run_hour(10))
    assert reminders(owner, world) == []
    run_job(worker_database_url, at(day, 14), lambda service, _: service.run_hour(14))
    assert len(reminders(owner, world)) == 1
    assert owner.execute("SELECT count(*) FROM reminder WHERE shop_id = %s", (world.shop_b,)).fetchone() == (0,)


def test_a_customer_with_reminders_off_and_a_suspended_shop_get_none(
    world: World, owner: psycopg.Connection, worker_database_url: str, day: date
) -> None:
    turn_on(owner, world.shop_a)
    quiet = debtor(owner, world, "Jim", 70000, day - timedelta(days=2))
    link(owner, world, quiet)
    owner.execute("UPDATE customer SET reminders_off = true WHERE id = %s", (quiet,))
    link(owner, world, debtor(owner, world, "Kech", 30000, day - timedelta(days=2)))

    owner.execute("UPDATE subscription SET state = 'suspended' WHERE shop_id = %s", (world.shop_a,))
    run_job(worker_database_url, at(day, 10), lambda service, _: service.run_hour(10))
    assert reminders(owner, world) == []

    # In limited mode reminders continue (BR-29).
    owner.execute("UPDATE subscription SET state = 'limited' WHERE shop_id = %s", (world.shop_a,))
    run_job(worker_database_url, at(day, 10), lambda service, _: service.run_hour(10))
    assert [row[0] for row in reminders(owner, world)] == ["Kech"]


def test_a_disputed_amount_is_left_out(
    world: World, owner: psycopg.Connection, worker_database_url: str, day: date
) -> None:
    turn_on(owner, world.shop_a)
    only_disputed = debtor(owner, world, "Faqat", 50000, day - timedelta(days=2))
    link(owner, world, only_disputed)
    mixed = debtor(owner, world, "Aralash", 20000, day - timedelta(days=2))
    link(owner, world, mixed)
    disputed_entry = add_debt(owner, world, mixed, 2, 50000, day - timedelta(days=1))
    for entry in (
        owner.execute("SELECT id FROM ledger_entry WHERE customer_id = %s", (only_disputed,)).fetchone()[0],  # type: ignore[index]
        disputed_entry,
    ):
        owner.execute(
            "INSERT INTO dispute (id, shop_id, entry_id, reason) VALUES (%s, %s, %s, 'Men olmaganman')",
            (uuid.uuid4(), world.shop_a, entry),
        )
    run_job(worker_database_url, at(day, 10), lambda service, _: service.run_hour(10))
    assert reminders(owner, world) == [("Aralash", "auto", "telegram", 20000, day)]


def test_a_paid_debt_is_not_reminded_of(
    world: World, owner: psycopg.Connection, worker_database_url: str, day: date
) -> None:
    turn_on(owner, world.shop_a)
    paid = debtor(owner, world, "Tolagan", 30000, day - timedelta(days=2))
    link(owner, world, paid)
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id, created_at) "
        "VALUES (%s, %s, %s, 2, 'payment', 30000, %s, %s)",
        (uuid.uuid4(), world.shop_a, paid, world.seller_a_membership, at(day, 8)),
    )
    run_job(worker_database_url, at(day, 10), lambda service, _: service.run_hour(10))
    assert reminders(owner, world) == []


# --- channels (BR-18, REQ-043) ---------------------------------------------------------------------------


@pytest.fixture
def sms(owner: psycopg.Connection) -> Any:
    """Switch SMS on for the platform with a quota of two a month, and off again afterwards."""
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('sms_on', 'true', 'test'), "
        "('sms_monthly_quota', '2', 'test') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
    )
    yield
    owner.execute("DELETE FROM platform_setting WHERE key IN ('sms_on', 'sms_monthly_quota')")


def test_without_telegram_and_with_sms_off_the_customer_is_listed_as_not_reachable(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str
) -> None:
    today = tashkent_date(datetime.now(UTC))
    turn_on(owner, world.shop_a)
    debtor(owner, world, "Telefonli", 70000, today - timedelta(days=2), phone="+998901112233")
    linked = debtor(owner, world, "Ulangan", 10000, today - timedelta(days=2))
    link(owner, world, linked)
    debtor(owner, world, "Hali erta", 10000, today + timedelta(days=5), phone="+998901112244")

    run_job(worker_database_url, datetime.now(UTC), lambda service, _: service.run_hour(10))
    assert [row[0] for row in reminders(owner, world)] == ["Ulangan"]
    listed = client.get(f"{shop(world)}/reminders/unreachable", headers=as_user(world.manager_a)).json()["items"]
    assert [(item["display_name"], item["phone"], item["amount"]) for item in listed] == [
        ("Telefonli", "+998901112233", 70000)
    ]


def test_sms_needs_the_platform_the_shop_a_phone_and_quota(
    world: World, owner: psycopg.Connection, worker_database_url: str, day: date, sms: Any
) -> None:
    turn_on(owner, world.shop_a)
    for index in range(3):
        debtor(owner, world, f"Sms {index}", 10000 + index, day - timedelta(days=2), phone=f"+99890111220{index}")
    debtor(owner, world, "Raqamsiz", 5000, day - timedelta(days=2))

    # The platform switch alone is not enough: the shop has not turned SMS on.
    run_job(worker_database_url, at(day, 10), lambda service, _: service.run_hour(10))
    assert reminders(owner, world) == []

    owner.execute("UPDATE shop SET sms_on = true WHERE id = %s", (world.shop_a,))
    run_job(worker_database_url, at(day, 10), lambda service, _: service.run_hour(10))
    sent = reminders(owner, world)
    assert [(row[1], row[2]) for row in sent] == [("auto", "sms")] * 2, "the monthly quota of two is not exceeded"
    queued = messages(owner, world)
    assert [channel for channel, _, _ in queued] == ["sms", "sms"]
    assert all(recipient.startswith("+9989011122") for _, recipient, _ in queued)
    name = sent[0][0]
    assert say("uz", "sms_overdue", shop="Shop A", name=name, amount=money("uz", sent[0][3])) in [
        t for _, _, t in queued
    ]

    # The quota is for the month: the next day nothing more goes out by SMS.
    run_job(worker_database_url, at(day + timedelta(days=0), 10, 50), lambda service, _: service.run_hour(10))
    assert len(reminders(owner, world)) == 2


# --- a manual reminder (REQ-025) -------------------------------------------------------------------------


def manual(client: TestClient, world: World, customer: Any, user: uuid.UUID | None = None) -> Any:
    return client.post(
        f"{shop(world)}/reminders/manual",
        json={"customer_id": str(customer)},
        headers={**as_user(user or world.manager_a), **key()},
    )


def test_a_manager_sends_one_reminder_a_day_by_hand(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    today = tashkent_date(datetime.now(UTC))
    turn_on(owner, world.shop_a, template=2)
    customer = debtor(owner, world, "Kech", 70000, today - timedelta(days=2))
    tg_id = link(owner, world, customer)

    sent = manual(client, world, customer)
    assert (sent.status_code, sent.json()) == (201, {"sent": True, "channel": "telegram", "amount": 70000})
    assert reminders(owner, world) == [("Kech", "manual", "telegram", 70000, today)]
    assert messages(owner, world) == [
        ("telegram", str(tg_id), say("uz", "r2_overdue", shop="Shop A", name="Kech", amount=money("uz", 70000)))
    ]
    logged = owner.execute(
        "SELECT actor_id FROM activity WHERE shop_id = %s AND action = 'reminder.sent_manually'", (world.shop_a,)
    ).fetchall()
    assert logged == [(world.manager_a_membership,)]

    again = manual(client, world, customer, world.owner_a)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "REMINDER_LIMIT_REACHED")
    assert len(reminders(owner, world)) == 1
    assert len(messages(owner, world)) == 1


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("shop_off", "REMINDERS_OFF"),
        ("customer_off", "REMINDERS_OFF"),
        ("not_due", "REMINDER_NOT_DUE"),
        ("unreachable", "CUSTOMER_UNREACHABLE"),
    ],
)
def test_a_manual_reminder_obeys_the_same_rules(
    client: TestClient, world: World, owner: psycopg.Connection, setup: str, code: str
) -> None:
    today = tashkent_date(datetime.now(UTC))
    turn_on(owner, world.shop_a)
    promised = today + timedelta(days=5) if setup == "not_due" else today - timedelta(days=2)
    customer = debtor(owner, world, "Mijoz", 70000, promised)
    if setup != "unreachable":
        link(owner, world, customer)
    if setup == "shop_off":
        owner.execute("UPDATE shop SET reminders_on = false WHERE id = %s", (world.shop_a,))
    if setup == "customer_off":
        owner.execute("UPDATE customer SET reminders_off = true WHERE id = %s", (customer,))

    response = manual(client, world, customer)
    assert (response.status_code, response.json()["error"]["code"]) == (409, code)
    assert reminders(owner, world) == []
    assert messages(owner, world) == []


def test_a_manual_reminder_for_nobody_or_another_shops_customer_is_not_found(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    turn_on(owner, world.shop_a)
    foreign = seed_customer(owner, world.shop_b, "Begona")
    for customer in (foreign, uuid.uuid4(), world.archived_customer_a):
        assert manual(client, world, customer).status_code == 404
    assert manual(client, world, "not-a-uuid").status_code == 422


# --- settings (REQ-022, REQ-042) -------------------------------------------------------------------------


def test_reminder_settings_start_off_and_show_the_wordings(client: TestClient, world: World) -> None:
    body = client.get(f"{shop(world)}/reminders", headers=as_user(world.manager_a)).json()
    assert (body["on"], body["hour"], body["template"], body["sms_on"], body["hours"]) == (False, 10, 1, False, [8, 20])
    assert [template["id"] for template in body["templates"]] == [1, 2, 3]
    for template in body["templates"]:
        for kind in ("due_today", "overdue"):
            for lang in ("uz", "ru"):
                assert "{shop}" in template[kind][lang] and "{amount}" in template[kind][lang]


def test_a_manager_turns_reminders_on_and_chooses_the_hour(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    def patch(body: dict[str, Any]) -> Any:
        return client.patch(f"{shop(world)}/reminders", json=body, headers={**as_user(world.manager_a), **key()})

    done = patch({"on": True, "hour": 8, "template": 3})
    assert done.status_code == 200, done.text
    assert (done.json()["on"], done.json()["hour"], done.json()["template"]) == (True, 8, 3)
    assert owner.execute(
        "SELECT reminders_on, reminder_hour, reminder_tpl, sms_on FROM shop WHERE id = %s", (world.shop_a,)
    ).fetchone() == (True, 8, 3, False)
    assert patch({"hour": 20}).json()["hour"] == 20
    assert patch({"on": False}).json()["on"] is False

    for bad in (
        {},
        {"hour": 7},
        {"hour": 21},
        {"hour": 10.5},
        {"hour": "10"},
        {"template": 0},
        {"template": 4},
        {"x": 1},
    ):
        assert patch(bad).status_code == 422, bad
    assert owner.execute(
        "SELECT reminders_on, reminder_hour, reminder_tpl FROM shop WHERE id = %s", (world.shop_a,)
    ).fetchone() == (
        False,
        20,
        3,
    )


# --- the schedule ----------------------------------------------------------------------------------------


def test_the_scheduler_serves_each_hour_once_and_catches_up(
    world: World, owner: psycopg.Connection, worker_database_url: str, day: date
) -> None:
    turn_on(owner, world.shop_a, hour=9)
    link(owner, world, debtor(owner, world, "Kech", 70000, day - timedelta(days=2)))

    # Before eight nothing is due.
    run_job(worker_database_url, at(day, 7, 59), lambda _, scheduler: scheduler.tick())
    assert reminders(owner, world) == []
    assert owner.execute("SELECT count(*) FROM job_run WHERE period LIKE %s", (f"{day}T%",)).fetchone() == (0,)

    # A worker that starts at 10:30 still serves the hours that have begun: 8, 9 and 10.
    run_job(worker_database_url, at(day, 10, 30), lambda _, scheduler: scheduler.tick())
    assert [row[0] for row in reminders(owner, world)] == ["Kech"]
    periods = owner.execute(
        "SELECT period FROM job_run WHERE job = 'reminders' AND period LIKE %s ORDER BY period", (f"{day}T%",)
    ).fetchall()
    assert periods == [(f"{day}T08",), (f"{day}T09",), (f"{day}T10",)]

    # A second tick in the same hour does nothing.
    owner.execute("DELETE FROM reminder WHERE shop_id = %s", (world.shop_a,))
    run_job(worker_database_url, at(day, 10, 31), lambda _, scheduler: scheduler.tick())
    assert reminders(owner, world) == [], "a finished hour is not run again"

    # Late in the evening the hours stop at twenty.
    run_job(worker_database_url, at(day, 23, 0), lambda _, scheduler: scheduler.tick())
    last = owner.execute(
        "SELECT max(period), count(*) FROM job_run WHERE job = 'reminders' AND period LIKE %s", (f"{day}T%",)
    ).fetchone()
    assert last == (f"{day}T20", 13)


def test_an_hour_that_failed_is_tried_again(
    world: World, owner: psycopg.Connection, worker_database_url: str, day: date
) -> None:
    turn_on(owner, world.shop_a, hour=8)
    link(owner, world, debtor(owner, world, "Kech", 70000, day - timedelta(days=2)))

    async def failing(service: ReminderService, scheduler: Scheduler) -> None:
        real = service.run_hour

        async def broken(hour: int) -> int:
            raise RuntimeError("the database went away")

        service.run_hour = broken  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            await scheduler.tick()
        service.run_hour = real  # type: ignore[method-assign]

    run_job(worker_database_url, at(day, 8, 5), failing)
    assert owner.execute("SELECT count(*) FROM job_run WHERE period = %s", (f"{day}T08",)).fetchone() == (0,)
    run_job(worker_database_url, at(day, 8, 6), lambda _, scheduler: scheduler.tick())
    assert [row[0] for row in reminders(owner, world)] == ["Kech"]


def test_a_shop_that_turned_reminders_off_meanwhile_is_not_served(
    world: World, owner: psycopg.Connection, worker_database_url: str, day: date
) -> None:
    """The list of shops is only where to look: each shop's own setting is read again inside it."""
    turn_on(owner, world.shop_a)
    link(owner, world, debtor(owner, world, "Kech", 70000, day - timedelta(days=2)))
    owner.execute("UPDATE shop SET reminders_on = false WHERE id = %s", (world.shop_a,))
    sent = run_job(worker_database_url, at(day, 10), lambda service, _: service._remind_shop(world.shop_a))
    assert sent == 0
    assert reminders(owner, world) == []


def test_the_platform_switch_alone_stops_every_sms(
    world: World, owner: psycopg.Connection, worker_database_url: str, day: date, sms: Any
) -> None:
    owner.execute("UPDATE platform_setting SET value = 'false' WHERE key = 'sms_on'")
    turn_on(owner, world.shop_a)
    owner.execute("UPDATE shop SET sms_on = true WHERE id = %s", (world.shop_a,))
    debtor(owner, world, "Telefonli", 70000, day - timedelta(days=2), phone="+998901112299")
    run_job(worker_database_url, at(day, 10), lambda service, _: service.run_hour(10))
    assert reminders(owner, world) == []
    assert messages(owner, world) == []


@pytest.mark.parametrize(
    ("plan_on", "state", "column", "sent"),
    [
        # BR-35: with the free plan on, SMS belongs to a paid period alone.
        (True, "active", "paid_through", 2),
        (True, "trial", "trial_ends", 0),
        (True, "limited", None, 0),
        # With the plan off nothing about SMS depends on the subscription, as before.
        (False, "trial", "trial_ends", 2),
        (False, "limited", None, 2),
    ],
)
def test_with_the_free_plan_on_sms_is_sent_for_shops_in_a_paid_period_only(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    worker_database_url: str,
    day: date,
    sms: Any,
    free_plan: Callable[[int], None],
    plan_on: bool,
    state: str,
    column: str | None,
    sent: int,
) -> None:
    if plan_on:
        free_plan(30)
    ends = datetime.now(UTC).date() + timedelta(days=365 * 40)  # runs on the test's day and on the real today
    owner.execute(
        "UPDATE subscription SET state = %s, trial_ends = %s, paid_through = %s WHERE shop_id = %s",
        (state, ends if column == "trial_ends" else None, ends if column == "paid_through" else None, world.shop_a),
    )
    turn_on(owner, world.shop_a)
    owner.execute("UPDATE shop SET sms_on = true WHERE id = %s", (world.shop_a,))
    for index in range(3):
        debtor(owner, world, f"Sms {index}", 10000 + index, day - timedelta(days=2), phone=f"+99890111230{index}")

    run_job(worker_database_url, at(day, 10), lambda service, _: service.run_hour(10))
    assert [(row[1], row[2]) for row in reminders(owner, world)] == [("auto", "sms")] * sent, "within the quota of two"
    assert [channel for channel, _, _ in messages(owner, world)] == ["sms"] * sent

    # By hand it is the same rule: a customer with a phone alone cannot be reached from a shop that does
    # not pay, and is listed for it as such.
    owner.execute("DELETE FROM reminder WHERE shop_id = %s", (world.shop_a,))
    today = tashkent_date(datetime.now(UTC))
    late = debtor(owner, world, "Qo'lda", 20000, today - timedelta(days=2), phone="+998901112309")
    by_hand = manual(client, world, late)
    listed = client.get(f"{shop(world)}/reminders/unreachable", headers=as_user(world.manager_a)).json()["items"]
    if sent:
        assert (by_hand.status_code, by_hand.json()["channel"]) == (201, "sms")
    else:
        assert (by_hand.status_code, by_hand.json()["error"]["code"]) == (409, "CUSTOMER_UNREACHABLE")
        assert "Qo'lda" in [item["display_name"] for item in listed]


@pytest.mark.parametrize("status", ["unreachable", "ended"])
def test_a_customer_whose_link_is_not_active_is_not_reminded_through_telegram(
    client: TestClient, world: World, owner: psycopg.Connection, worker_database_url: str, status: str
) -> None:
    today = tashkent_date(datetime.now(UTC))
    turn_on(owner, world.shop_a)
    customer = debtor(owner, world, "Bloklagan", 70000, today - timedelta(days=2))
    link(owner, world, customer)
    owner.execute("UPDATE customer_link SET status = %s WHERE customer_id = %s", (status, customer))

    run_job(worker_database_url, datetime.now(UTC), lambda service, _: service.run_hour(10))
    assert reminders(owner, world) == []
    assert messages(owner, world) == []
    listed = client.get(f"{shop(world)}/reminders/unreachable", headers=as_user(world.manager_a)).json()["items"]
    assert [item["display_name"] for item in listed] == ["Bloklagan"]
