"""Identity-free measures of usage, repayment, disputes and speed, and their weekly figures (REQ-030, ADR-010)."""

import asyncio
import itertools
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.measurement import MeasurementService, week_bounds, week_start_of
from qarz.application.reminders import ReminderService
from qarz.application.scheduler import Scheduler
from qarz.domain import ledger
from qarz.domain.ledger import Entry, EntryKind
from qarz.domain.promise import TASHKENT
from qarz.infrastructure.db import Database

from .conftest import World
from .test_chat import chat_of
from .test_customers_ledger import record, reverse, seed_customer, seed_entry, today

pytestmark = pytest.mark.db

_weeks = itertools.count()


@pytest.fixture
def week() -> date:
    """A Monday far in the future, a different one for each test: its events are only the test's own."""
    # Three weeks apart, because a test may put an event just outside its own week on either side.
    return date(2080, 1, 1) + timedelta(days=21 * next(_weeks))  # 1 January 2080 is a Monday


def known_events(owner: psycopg.Connection) -> set[uuid.UUID]:
    return {row[0] for row in owner.execute("SELECT id FROM measure.event").fetchall()}


def events(owner: psycopg.Connection, known: set[uuid.UUID]) -> list[tuple[Any, ...]]:
    """The rows written since `known` was taken. Not "since a moment": other tests here date rows years
    ahead (the `week` fixture), and those are later than any moment of today."""
    rows = owner.execute(
        "SELECT id, kind, amount, promised, handle_ms IS NOT NULL FROM measure.event ORDER BY at, kind"
    ).fetchall()
    return [row[1:] for row in rows if row[0] not in known]


def add_event(owner: psycopg.Connection, at: datetime, kind: str, amount: int, **more: Any) -> None:
    owner.execute(
        "INSERT INTO measure.event (id, at, shop_ref, entry_ref, kind, amount, handle_ms) "
        "VALUES (gen_random_uuid(), %s, %s, gen_random_uuid(), %s, %s, %s)",
        (at, more.get("shop", uuid.UUID(int=1)), kind, amount, more.get("handle_ms")),
    )


def compute(worker_database_url: str, week_start: date) -> dict[str, float | None]:
    async def scenario() -> dict[str, float | None]:
        database = Database(worker_database_url)
        try:
            return await MeasurementService(database).compute_week(week_start)
        finally:
            await database.dispose()

    return asyncio.run(scenario())


# --- what is recorded -----------------------------------------------------------------------------------


def test_a_payment_is_measured_as_repaid_in_time_or_late(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer = seed_customer(owner, world.shop_a, "Olchov")
    seed_entry(owner, world, customer, 1, "credit", 30_000, promised=today() - timedelta(days=3), days_ago=10)
    seed_entry(owner, world, customer, 2, "credit", 20_000, promised=today() + timedelta(days=5), days_ago=1)
    since = known_events(owner)

    # 40 000 paid today: 30 000 goes to the debt promised three days ago (late), 10 000 to the one not yet due.
    assert record(client, world, customer, "payment", 40_000).status_code == 201
    assert sorted(row[:2] for row in events(owner, since)) == [
        ("payment", 40_000),
        ("repaid_in_time", 10_000),
        ("repaid_late", 30_000),
    ]


def test_a_credit_sale_and_a_reversal_record_no_repayment(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    since = known_events(owner)
    sale = record(client, world, world.customer_a, "credit", 5_000).json()["entry"]["id"]
    reverse(client, world, sale)
    assert [row[0] for row in events(owner, since)] == ["credit", "reversal"]


def test_the_handling_time_of_a_sale_is_recorded_from_the_api_and_from_chat(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    known = known_events(owner)
    record(client, world, world.customer_a, "credit", 5_000)
    chat_of(client, owner, world.seller_a).say("Ali 6000")
    recorded = [
        row[1:]
        for row in owner.execute(
            "SELECT id, amount, handle_ms FROM measure.event WHERE kind = 'credit' ORDER BY amount"
        ).fetchall()
        if row[0] not in known
    ]
    assert [row[0] for row in recorded] == [5_000, 6_000]
    assert all(row[1] is not None and 0 <= row[1] < 60_000 for row in recorded)


def test_a_measure_names_nobody(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    known = known_events(owner)
    entry = record(client, world, world.customer_a, "payment", 1_000).json()["entry"]["id"]
    rows = [
        row[1:]
        for row in owner.execute("SELECT id, row_to_json(e)::text FROM measure.event e").fetchall()
        if row[0] not in known
    ]
    assert len(rows) == 2
    for (text,) in rows:
        for secret in (str(world.shop_a), str(world.customer_a), entry, "Ali", str(world.seller_a_membership)):
            assert secret not in text
    columns = owner.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'measure' ORDER BY column_name"
    ).fetchall()
    assert {c[0] for c in columns} == {
        "id", "at", "shop_ref", "entry_ref", "kind", "amount", "promised", "handle_ms",
        "week_start", "metric", "value", "computed_at",
    }  # fmt: skip


# --- the split of one payment (domain) --------------------------------------------------------------------


def _entry(seq: int, kind: EntryKind, amount: int, at: datetime, promised: date | None = None) -> Entry:
    return Entry(id=uuid.UUID(int=seq), seq=seq, kind=kind, amount=amount, created_at=at, promised_date=promised)


def test_a_payment_on_the_promised_day_is_in_time_and_the_parts_add_up() -> None:
    day = date(2026, 10, 7)
    morning = datetime.combine(day, time(9, 0), tzinfo=TASHKENT)
    account = [
        _entry(1, EntryKind.CREDIT, 100, morning - timedelta(days=9), promised=day - timedelta(days=1)),
        _entry(2, EntryKind.CREDIT, 100, morning - timedelta(days=5), promised=day),
        _entry(3, EntryKind.CREDIT, 100, morning - timedelta(days=1), promised=day + timedelta(days=9)),
        _entry(4, EntryKind.PAYMENT, 250, datetime.combine(day, time(23, 59), tzinfo=TASHKENT)),
    ]
    in_time, late = ledger.payment_timeliness(account, uuid.UUID(int=4))
    assert (in_time, late) == (150, 100)
    assert in_time + late == 250
    assert ledger.payment_timeliness(account, uuid.UUID(int=1)) == (0, 0), "a sale is not a payment"
    assert ledger.payment_timeliness(account, uuid.UUID(int=99)) == (0, 0)


def test_only_the_named_payment_is_split() -> None:
    day = date(2026, 10, 7)
    noon = datetime.combine(day, time(12, 0), tzinfo=TASHKENT)
    account = [
        _entry(1, EntryKind.CREDIT, 100, noon - timedelta(days=9), promised=day - timedelta(days=3)),
        _entry(2, EntryKind.PAYMENT, 60, noon - timedelta(days=5)),  # in time
        _entry(3, EntryKind.PAYMENT, 40, noon),  # late
    ]
    assert ledger.payment_timeliness(account, uuid.UUID(int=2)) == (60, 0)
    assert ledger.payment_timeliness(account, uuid.UUID(int=3)) == (0, 40)


# --- weekly figures ---------------------------------------------------------------------------------------


def test_a_week_runs_from_monday_to_monday_in_tashkent() -> None:
    assert week_start_of(date(2026, 10, 7)) == date(2026, 10, 5)
    assert week_start_of(date(2026, 10, 5)) == date(2026, 10, 5)
    assert week_start_of(date(2026, 10, 11)) == date(2026, 10, 5)
    start, end = week_bounds(date(2026, 10, 5))
    assert start == datetime(2026, 10, 4, 19, 0, tzinfo=UTC)  # Monday 00:00 in Tashkent
    assert end - start == timedelta(days=7)
    with pytest.raises(ValueError, match="Monday"):
        week_bounds(date(2026, 10, 6))


def test_the_weekly_figures_are_totals_of_that_week_only(
    owner: psycopg.Connection, worker_database_url: str, week: date
) -> None:
    start, end = week_bounds(week)
    shop_one, shop_two = uuid.uuid4(), uuid.uuid4()
    inside = start + timedelta(days=2)
    add_event(owner, start, "credit", 100_000, shop=shop_one, handle_ms=40)  # the first instant counts
    add_event(owner, inside, "credit", 50_000, shop=shop_one, handle_ms=60)
    add_event(owner, inside, "credit", 30_000, shop=shop_two, handle_ms=500)
    add_event(owner, inside, "credit", 20_000, shop=shop_two)  # no handling time: left out of the median
    # More was paid than was split (a part repaid a debt of an earlier week's measure), and a payment has a
    # handling time too: neither may leak into the share or into the median of sales.
    add_event(owner, inside, "payment", 90_000, shop=shop_one, handle_ms=10_000)
    add_event(owner, inside, "repaid_in_time", 60_000, shop=shop_one)
    add_event(owner, inside, "repaid_late", 20_000, shop=shop_one)
    add_event(owner, inside, "reversal", 5_000, shop=shop_two)
    add_event(owner, inside, "dispute_opened", 30_000, shop=shop_two)
    # Just outside the week on either side.
    add_event(owner, start - timedelta(seconds=1), "credit", 999_000, shop=uuid.uuid4())
    add_event(owner, end, "credit", 999_000, shop=uuid.uuid4())

    assert compute(worker_database_url, week) == {
        "active_shops": 2,
        "credit_count": 4,
        "credit_sum": 200_000,
        "payment_count": 1,
        "payment_sum": 90_000,
        "reversal_count": 1,
        "disputes_opened": 1,
        "dispute_rate": 0.25,
        "repaid_in_time_sum": 60_000,
        "repaid_late_sum": 20_000,
        "in_time_share": 0.75,
        "median_credit_handle_ms": 60,
    }
    stored = owner.execute("SELECT metric, value FROM measure.weekly WHERE week_start = %s", (week,)).fetchall()
    assert len(stored) == 12 and dict(stored)["in_time_share"] == pytest.approx(0.75)

    # Computing again replaces the figures rather than adding to them.
    add_event(owner, inside, "payment", 20_000, shop=shop_one)
    assert compute(worker_database_url, week)["payment_sum"] == 110_000
    again = owner.execute(
        "SELECT value FROM measure.weekly WHERE week_start = %s AND metric = 'payment_sum'", (week,)
    ).fetchone()
    assert again == (110_000,), "the stored figure is the new one"
    assert owner.execute("SELECT count(*) FROM measure.weekly WHERE week_start = %s", (week,)).fetchone() == (12,)


def test_an_empty_week_has_zeros_and_no_shares(owner: psycopg.Connection, worker_database_url: str, week: date) -> None:
    figures = compute(worker_database_url, week)
    assert figures["active_shops"] == 0 and figures["credit_sum"] == 0
    assert figures["dispute_rate"] is None, "no sales: a rate would be a guess"
    assert figures["in_time_share"] is None
    assert figures["median_credit_handle_ms"] is None


def test_the_export_is_csv_of_totals(owner: psycopg.Connection, worker_database_url: str, week: date) -> None:
    start, _ = week_bounds(week)
    add_event(owner, start, "credit", 45_000, handle_ms=30)
    compute(worker_database_url, week)

    async def scenario() -> str:
        database = Database(worker_database_url)
        try:
            return await MeasurementService(database).export_csv(weeks=1)
        finally:
            await database.dispose()

    lines = asyncio.run(scenario()).splitlines()
    assert lines[0] == "week_start,metric,value"
    assert f"{week.isoformat()},credit_sum,45000" in lines
    assert f"{week.isoformat()},in_time_share," in lines, "an unknown share is an empty cell, not a zero"
    assert f"{week.isoformat()},dispute_rate,0" in lines
    assert len(lines) == 13


def test_the_worker_computes_the_finished_week_once(
    owner: psycopg.Connection, worker_database_url: str, week: date
) -> None:
    start, _ = week_bounds(week)
    add_event(owner, start + timedelta(hours=1), "credit", 45_000)
    calls: list[date] = []

    class Counting(MeasurementService):
        async def compute_week(self, week_start: date) -> dict[str, float | None]:
            calls.append(week_start)
            return await super().compute_week(week_start)

    # Tuesday of the following week, at 08:00, 09:30 and 15:00 Tashkent time.
    tuesday = week + timedelta(days=8)

    async def scenario() -> None:
        database = Database(worker_database_url)
        try:
            for hour, minute in ((8, 0), (9, 30), (15, 0)):
                moment = datetime.combine(tuesday, time(hour, minute), tzinfo=TASHKENT)
                clock = lambda moment=moment: moment  # noqa: E731
                scheduler = Scheduler(
                    database, ReminderService(database, clock), clock, measurement=Counting(database, clock)
                )
                await scheduler.tick()
        finally:
            await database.dispose()

    asyncio.run(scenario())
    assert calls == [week]
    assert owner.execute(
        "SELECT value FROM measure.weekly WHERE week_start = %s AND metric = 'credit_sum'", (week,)
    ).fetchone() == (45_000,)
