"""The operations watch against the real database, connected as the worker's role (migration 0035):
its state survives as rows, its figures are ages and counts, and a message built from them holds nothing
of the rows they were counted from.

Other tests leave rows in the same tables, so every test here reads its own: keys and series with a
random part, moments far outside what any other test writes, and counts as differences.
"""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from qarz.application.ops_watch import AlertNotDelivered, OpsWatch
from qarz.application.reminders import ReminderService
from qarz.application.scheduler import LEDGER_CHECK, STOCK_CHECK, Scheduler
from qarz.domain import ops_alerts as rules
from qarz.domain.ops_alerts import Alert
from qarz.infrastructure.db import Database

from ..conftest import AppSession, Shop, add_entry
from ..db.test_stock_schema import item, move, supplier, supplier_entry

pytestmark = pytest.mark.db

# Long before anything another test writes: a message due since then is the oldest there is.
ANCIENT = datetime(2001, 1, 1, tzinfo=UTC)
# And a clock far ahead of them, for the counts that look back a day.
FAR_AHEAD = datetime(2090, 1, 1, tzinfo=UTC)
PHONE = "+998901112233"
NAME_IN_TEXT = "Alisher aka, qarzingiz 450 000 so'm"


def run[T](worker_database_url: str, scenario: Callable[[Database], Awaitable[T]]) -> T:
    async def wrapper() -> T:
        database = Database(worker_database_url)
        try:
            return await scenario(database)
        finally:
            await database.dispose()

    return asyncio.run(wrapper())


def queue(
    owner: psycopg.Connection, *, channel: str, status: str, next_try_at: datetime, recipient: str = PHONE
) -> uuid.UUID:
    message_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO outbox_message (id, channel, recipient, payload, dedupe_key, status, next_try_at, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            message_id,
            channel,
            recipient,
            json.dumps({"text": NAME_IN_TEXT}),
            message_id.hex,
            status,
            next_try_at,
            next_try_at,
        ),
    )
    return message_id


@pytest.fixture
def own_rows(owner: psycopg.Connection) -> Iterator[list[uuid.UUID]]:
    """Messages a test queued; they are taken away again so that no other test finds them due."""
    created: list[uuid.UUID] = []
    yield created
    owner.execute("DELETE FROM outbox_message WHERE id = ANY(%s)", (created,))


# --- the state is rows -----------------------------------------------------------------------------------


def test_an_alert_is_stored_read_changed_and_deleted_as_the_worker(worker_database_url: str) -> None:
    key = f"JobNotRunning:test-{uuid.uuid4().hex[:8]}"
    since = datetime(2026, 10, 7, 7, 0, tzinfo=UTC)
    first = Alert(key=key, since=since, value=4000.5)
    told = replace(
        first,
        firing_since=since + timedelta(minutes=5),
        notified_at=since + timedelta(minutes=6),
        attempts=2,
        last_attempt_at=since + timedelta(minutes=6),
        last_outcome="sent",
    )

    async def scenario(database: Database) -> list[Alert | None]:
        seen: list[Alert | None] = []

        async def mine() -> Alert | None:
            async with database.platform() as session:
                return next((alert for alert in await session.ops_alerts() if alert.key == key), None)

        async with database.platform() as session:
            await session.store_ops_alert(first)
        seen.append(await mine())
        async with database.platform() as session:
            await session.store_ops_alert(told)
        seen.append(await mine())
        async with database.platform() as session:
            await session.delete_ops_alert(key)
        seen.append(await mine())
        return seen

    assert run(worker_database_url, scenario) == [first, told, None]


def test_an_outcome_the_watch_does_not_know_is_refused_by_the_table(owner: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), owner.transaction():
        owner.execute(
            "INSERT INTO ops_alert (key, since, last_outcome) VALUES (%s, now(), 'Telegram said: chat not found')",
            (f"ApiDown:{uuid.uuid4().hex[:8]}",),
        )


# --- the figures -------------------------------------------------------------------------------------------


def test_the_oldest_due_message_is_an_age_by_channel(
    worker_database_url: str, owner: psycopg.Connection, own_rows: list[uuid.UUID]
) -> None:
    now = datetime(2026, 10, 7, 7, 0, tzinfo=UTC)
    own_rows.append(queue(owner, channel="sms", status="pending", next_try_at=ANCIENT))
    own_rows.append(queue(owner, channel="telegram", status="pending", next_try_at=ANCIENT + timedelta(hours=1)))
    # Neither a message that was sent nor one whose time has not come is waiting.
    own_rows.append(queue(owner, channel="sms", status="sent", next_try_at=ANCIENT - timedelta(days=9)))

    async def scenario(database: Database) -> rules.DatabaseFigures:
        async with database.platform() as session:
            return await session.ops_database_figures(now)

    figures = run(worker_database_url, scenario)
    assert figures.outbox_oldest_due["sms"] == (now - ANCIENT).total_seconds()
    assert figures.outbox_oldest_due["telegram"] == (now - ANCIENT).total_seconds() - 3600


def test_the_sms_counts_are_of_the_last_day_and_hour(
    worker_database_url: str, owner: psycopg.Connection, own_rows: list[uuid.UUID]
) -> None:
    async def scenario(database: Database) -> rules.DatabaseFigures:
        async with database.platform() as session:
            return await session.ops_database_figures(FAR_AHEAD)

    before = run(worker_database_url, scenario)
    assert (before.sms_retrying, before.sms_failed_last_hour) == (0, 0)  # nobody else writes that far ahead
    own_rows.append(queue(owner, channel="sms", status="pending", next_try_at=FAR_AHEAD + timedelta(minutes=5)))
    own_rows.append(queue(owner, channel="sms", status="pending", next_try_at=FAR_AHEAD + timedelta(minutes=9)))
    own_rows.append(queue(owner, channel="sms", status="failed", next_try_at=FAR_AHEAD - timedelta(minutes=59)))
    # Not counted: failed more than an hour ago, a Telegram message, one that was sent.
    own_rows.append(queue(owner, channel="sms", status="failed", next_try_at=FAR_AHEAD - timedelta(minutes=61)))
    own_rows.append(queue(owner, channel="telegram", status="failed", next_try_at=FAR_AHEAD - timedelta(minutes=5)))
    own_rows.append(queue(owner, channel="sms", status="sent", next_try_at=FAR_AHEAD - timedelta(minutes=5)))
    after = run(worker_database_url, scenario)
    assert (after.sms_retrying, after.sms_failed_last_hour) == (2, 1)


def test_a_jobs_age_is_since_its_last_finished_period(worker_database_url: str, owner: psycopg.Connection) -> None:
    now = datetime(2026, 10, 7, 7, 0, tzinfo=UTC)
    job = f"test-job-{uuid.uuid4().hex[:8]}"
    owner.execute(
        "INSERT INTO job_run (job, period, finished_at) VALUES (%s, 'a', %s), (%s, 'b', %s)",
        (job, now - timedelta(hours=5), job, now - timedelta(minutes=7)),
    )
    first_ever = f"test-first-{uuid.uuid4().hex[:8]}"
    owner.execute("INSERT INTO job_run (job, period, finished_at) VALUES (%s, 'a', %s)", (first_ever, ANCIENT))

    async def scenario(database: Database) -> rules.DatabaseFigures:
        async with database.platform() as session:
            return await session.ops_database_figures(now)

    try:
        figures = run(worker_database_url, scenario)
    finally:
        owner.execute("DELETE FROM job_run WHERE job IN (%s, %s)", (job, first_ever))
    assert figures.job_age[job] == 7 * 60
    assert figures.service_age == (now - ANCIENT).total_seconds()


def test_the_worker_may_ask_how_long_the_oldest_receipt_has_waited(worker_database_url: str) -> None:
    async def scenario(database: Database) -> rules.DatabaseFigures:
        async with database.platform() as session:
            return await session.ops_database_figures(datetime.now(UTC))

    waiting = run(worker_database_url, scenario).receipt_waiting
    assert waiting is None or waiting >= 0


def test_samples_are_kept_by_series_and_pruned_to_the_newest(worker_database_url: str) -> None:
    mine, other = f"test:{uuid.uuid4().hex[:8]}", f"test:{uuid.uuid4().hex[:8]}"
    now = datetime(2026, 10, 7, 7, 0, tzinfo=UTC)
    moments = [now - timedelta(minutes=minutes) for minutes in (50, 30, 10, 0)]

    async def scenario(database: Database) -> list[dict[str, list[tuple[datetime, float]]]]:
        seen = []
        async with database.platform() as session:
            for count, moment in enumerate(moments):
                await session.add_ops_samples(moment, {mine: float(count)})
            await session.add_ops_samples(moments[0], {other: 7.0})
            # The same series at the same moment again: kept once.
            await session.add_ops_samples(moments[0], {other: 8.0})
        async with database.platform() as session:
            seen.append(await session.ops_samples(now - timedelta(minutes=20)))
            await session.prune_ops_samples(now - timedelta(minutes=20))
            seen.append(await session.ops_samples(ANCIENT))
        return [{name: kept for name, kept in found.items() if name in (mine, other)} for found in seen]

    recent, after_pruning = run(worker_database_url, scenario)
    assert recent == {mine: [(moments[2], 2.0), (moments[3], 3.0)]}
    # What is older than the window goes; the newest of a series stays however old it is.
    assert after_pruning == {mine: [(moments[2], 2.0), (moments[3], 3.0)], other: [(moments[0], 7.0)]}


# --- the nightly check of the ledger -------------------------------------------------------------------------


def test_the_count_of_mismatches_grows_by_one_when_a_stored_debt_is_damaged(
    owner: psycopg.Connection, shop_a: Shop
) -> None:
    entry = add_entry(owner, shop_a, seq=1, amount=40_000)
    before = owner.execute("SELECT open_debt_mismatch_count()").fetchone()
    assert before is not None
    with owner.transaction(force_rollback=True):
        owner.execute("UPDATE open_debt SET remaining = 39999 WHERE entry_id = %s", (entry,))
        damaged = owner.execute("SELECT open_debt_mismatch_count()").fetchone()
        assert damaged is not None and damaged[0] == before[0] + 1
    again = owner.execute("SELECT open_debt_mismatch_count()").fetchone()
    assert again == before


def test_the_worker_gets_the_count_and_not_the_comparison(as_worker: AppSession, as_app: AppSession) -> None:
    with as_worker(None) as conn:
        row = conn.execute("SELECT open_debt_mismatch_count()").fetchone()
        assert row is not None and row[0] >= 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_worker(None) as conn:
        conn.execute("SELECT * FROM open_debt_mismatches(NULL)")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_app(None) as conn:
        conn.execute("SELECT open_debt_mismatch_count()")


def test_the_scheduler_checks_the_ledger_once_a_day_and_keeps_the_count(
    worker_database_url: str, owner: psycopg.Connection
) -> None:
    # A day no other test's clock is at; 05:30 in Tashkent, before the reminders' hours.
    day = datetime(2091, 3, 4, 0, 30, tzinfo=UTC) + timedelta(days=uuid.uuid4().int % 300)
    periods = [(day + timedelta(hours=5, days=more)).date().isoformat() for more in (0, 1)]
    current = {"now": day}

    async def scenario(database: Database) -> list[list[int]]:
        scheduler = Scheduler(
            database, ReminderService(database, lambda: current["now"]), lambda: current["now"], ledger_check=True
        )
        runs = []
        for moment in (day, day + timedelta(minutes=1), day + timedelta(minutes=20), day + timedelta(days=1)):
            current["now"] = moment
            await scheduler.tick()
            rows = owner.execute(
                "SELECT period, count(*) FROM job_run WHERE job = %s AND period = ANY(%s) GROUP BY period",
                (LEDGER_CHECK, periods),
            ).fetchall()
            found = dict(rows)
            runs.append([int(found.get(period, 0)) for period in periods])
        return runs

    try:
        # Once on the day, however many ticks follow; and again the day after.
        assert run(worker_database_url, scenario) == [[1, 0], [1, 0], [1, 0], [1, 1]]
        samples = owner.execute(
            "SELECT taken_at, value FROM ops_sample WHERE series = %s AND taken_at >= %s AND taken_at <= %s "
            "ORDER BY taken_at",
            (rules.LEDGER_SERIES, day, day + timedelta(days=1)),
        ).fetchall()
        assert [taken_at for taken_at, _ in samples] == [day, day + timedelta(days=1)]
        assert all(value >= 0 for _, value in samples)
    finally:
        owner.execute("DELETE FROM job_run WHERE job = %s AND period = ANY(%s)", (LEDGER_CHECK, periods))
        owner.execute(
            "DELETE FROM ops_sample WHERE series = %s AND taken_at >= %s AND taken_at <= %s",
            (rules.LEDGER_SERIES, day, day + timedelta(days=1)),
        )


def test_a_scheduler_that_is_not_asked_to_does_not_check_the_ledger(
    worker_database_url: str, owner: psycopg.Connection
) -> None:
    day = datetime(2092, 3, 4, 0, 30, tzinfo=UTC) + timedelta(days=uuid.uuid4().int % 300)
    period = (day + timedelta(hours=5)).date().isoformat()

    async def scenario(database: Database) -> None:
        await Scheduler(database, ReminderService(database, lambda: day), lambda: day).tick()

    run(worker_database_url, scenario)
    row = owner.execute(
        "SELECT count(*) FROM job_run WHERE job = %s AND period = %s", (LEDGER_CHECK, period)
    ).fetchone()
    assert row == (0,)


# --- the nightly check of the stock (migration 0046) ---------------------------------------------------------


def stock_counts(conn: psycopg.Connection) -> tuple[int, int]:
    row = conn.execute("SELECT stock_level_mismatch_count(), supplier_balance_mismatch_count()").fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


def test_each_stock_count_grows_by_one_when_its_kept_figure_is_damaged(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    sugar = item(owner, shop_a, f"Shakar {uuid.uuid4().hex[:8]}")
    who = supplier(owner, shop_a, f"Ulgurji {uuid.uuid4().hex[:8]}")
    with as_app(shop_a.shop_id) as app:
        move(app, shop_a, sugar, 1, "10", 100_000, "10", 100_000)
        supplier_entry(app, shop_a, who, 1, "purchase", 100_000)
    levels, owed = stock_counts(owner)
    with owner.transaction(force_rollback=True):
        owner.execute("UPDATE stock_level SET on_hand = 9 WHERE item_id = %s", (sugar,))
        assert stock_counts(owner) == (levels + 1, owed), "only the level's count moves"
    with owner.transaction(force_rollback=True):
        owner.execute("UPDATE supplier_balance SET balance = 99999 WHERE supplier_id = %s", (who,))
        assert stock_counts(owner) == (levels, owed + 1), "only the suppliers' count moves"
    assert stock_counts(owner) == (levels, owed)


def test_the_worker_gets_the_stock_counts_and_nobody_gets_the_comparisons(
    as_worker: AppSession, as_app: AppSession
) -> None:
    with as_worker(None) as conn:
        assert min(stock_counts(conn)) >= 0
    for comparison in ("stock_level_mismatches", "supplier_balance_mismatches"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), as_worker(None) as conn:
            conn.execute(f"SELECT * FROM {comparison}(NULL)")
    for count in ("stock_level_mismatch_count", "supplier_balance_mismatch_count"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), as_app(None) as conn:
            conn.execute(f"SELECT {count}()")


def stock_switch(owner: psycopg.Connection, on: bool | None) -> None:
    owner.execute("DELETE FROM platform_setting WHERE key = 'stock_on'")
    if on is not None:
        owner.execute(
            "INSERT INTO platform_setting (key, value, updated_by) VALUES ('stock_on', %s::jsonb, 'test')",
            (json.dumps(on),),
        )


def stock_samples(owner: psycopg.Connection, since: datetime, until: datetime) -> list[tuple[str, datetime, float]]:
    return owner.execute(
        "SELECT series, taken_at, value FROM ops_sample WHERE series = ANY(%s) AND taken_at >= %s AND taken_at <= %s "
        "ORDER BY taken_at, series",
        (list(rules.STOCK_SERIES.values()), since, until),
    ).fetchall()


def stock_days(owner: psycopg.Connection, periods: list[str]) -> list[int]:
    rows = owner.execute(
        "SELECT period, count(*) FROM job_run WHERE job = %s AND period = ANY(%s) GROUP BY period",
        (STOCK_CHECK, periods),
    ).fetchall()
    found = dict(rows)
    return [int(found.get(period, 0)) for period in periods]


def test_the_scheduler_checks_the_stock_once_a_day_while_it_is_on_and_keeps_both_counts(
    worker_database_url: str, owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    day = datetime(2093, 3, 4, 0, 30, tzinfo=UTC) + timedelta(days=uuid.uuid4().int % 300)
    periods = [(day + timedelta(hours=5, days=more)).date().isoformat() for more in (0, 1, 2)]
    current = {"now": day}
    sugar = item(owner, shop_a, f"Shakar {uuid.uuid4().hex[:8]}")
    with as_app(shop_a.shop_id) as app:
        move(app, shop_a, sugar, 1, "10", 100_000, "10", 100_000)
    before = stock_counts(owner)

    async def scenario(database: Database) -> tuple[list[list[int]], rules.DatabaseFigures]:
        scheduler = Scheduler(
            database, ReminderService(database, lambda: current["now"]), lambda: current["now"], stock_check=True
        )
        runs = []
        for moment in (day, day + timedelta(minutes=1), day + timedelta(days=1), day + timedelta(days=2)):
            current["now"] = moment
            if moment == day + timedelta(days=1):
                # Behind the ledger's back, as only the owner of the tables can: the next check finds it.
                owner.execute("UPDATE stock_level SET on_hand = 9 WHERE item_id = %s", (sugar,))
            if moment == day + timedelta(days=2):
                stock_switch(owner, False)
            await scheduler.tick()
            runs.append(stock_days(owner, periods))
        async with database.platform() as session:
            return runs, await session.ops_database_figures(current["now"])

    stock_switch(owner, True)
    try:
        runs, figures = run(worker_database_url, scenario)
        # Once on the day however many ticks follow, again the day after, and the day the stock is off is
        # finished too (or JobNotRunning would fire for a job that has nothing to do).
        assert runs == [[1, 0, 0], [1, 0, 0], [1, 1, 0], [1, 1, 1]]
        level_series, owed_series = rules.STOCK_SERIES["stock_level"], rules.STOCK_SERIES["supplier_balance"]
        # Two samples a day while it is on; none on the day it is off: its tables are not read then.
        assert sorted(stock_samples(owner, day, day + timedelta(days=2))) == sorted(
            [
                (level_series, day, float(before[0])),
                (owed_series, day, float(before[1])),
                (level_series, day + timedelta(days=1), float(before[0] + 1)),
                (owed_series, day + timedelta(days=1), float(before[1])),
            ]
        )
        # The watch reads the newest of each, which is the damaged day's: the alert holds, and says which.
        assert figures.stock_mismatches == {"stock_level": before[0] + 1, "supplier_balance": before[1]}
        judged = rules.evaluate(
            rules.Figures(now=current["now"], configured=frozenset({rules.DATABASE}), database=figures)
        )
        assert judged["StockMismatch:stock_level"].holds is True
        assert judged["StockMismatch:supplier_balance"].holds is (before[1] > 0)
    finally:
        stock_switch(owner, None)
        owner.execute("UPDATE stock_level SET on_hand = 10 WHERE item_id = %s", (sugar,))
        owner.execute("DELETE FROM job_run WHERE job = %s AND period = ANY(%s)", (STOCK_CHECK, periods))
        owner.execute(
            "DELETE FROM ops_sample WHERE series = ANY(%s) AND taken_at >= %s AND taken_at <= %s",
            (list(rules.STOCK_SERIES.values()), day, day + timedelta(days=2)),
        )


def test_a_scheduler_does_not_read_the_stock_while_it_is_off_or_when_it_is_not_asked_to(
    worker_database_url: str, owner: psycopg.Connection
) -> None:
    day = datetime(2094, 3, 4, 0, 30, tzinfo=UTC) + timedelta(days=uuid.uuid4().int % 300)
    period = (day + timedelta(hours=5)).date().isoformat()

    async def scenario(database: Database) -> list[list[int]]:
        seen = []
        # Not asked to: nothing of the job at all.
        await Scheduler(database, ReminderService(database, lambda: day), lambda: day).tick()
        seen.append(stock_days(owner, [period]))
        # Asked to, with the switch off (no row: off by default): the day is finished, nothing is sampled.
        await Scheduler(database, ReminderService(database, lambda: day), lambda: day, stock_check=True).tick()
        seen.append(stock_days(owner, [period]))
        return seen

    stock_switch(owner, None)
    try:
        assert run(worker_database_url, scenario) == [[0], [1]]
        assert stock_samples(owner, day, day) == []
    finally:
        owner.execute("DELETE FROM job_run WHERE job = %s AND period = %s", (STOCK_CHECK, period))


# --- a whole round against the database ----------------------------------------------------------------------


@dataclass
class Chat:
    sent: list[str] = field(default_factory=list)
    failure: str | None = None

    async def send(self, chat_id: int, text: str) -> None:
        if self.failure:
            raise AlertNotDelivered(self.failure)
        self.sent.append(text)

    async def state(self) -> str:
        return "ok"


@pytest.fixture
def quiet_outbox(owner: psycopg.Connection) -> Iterator[None]:
    """As the dispatcher's tests do: nothing of another test is pending while a round is judged, and the
    watch's own state starts and ends empty."""
    owner.execute("UPDATE outbox_message SET status = 'sent' WHERE status = 'pending'")
    owner.execute("DELETE FROM ops_alert")
    yield
    owner.execute("DELETE FROM ops_alert")


def test_a_stuck_outbox_is_told_and_taken_back_and_the_message_holds_nothing_of_the_stuck_messages(
    worker_database_url: str, owner: psycopg.Connection, quiet_outbox: None, own_rows: list[uuid.UUID]
) -> None:
    key = "OutboxOld:sms"
    chat = Chat()
    start = datetime.now(UTC) + timedelta(seconds=5)
    current = {"now": start}
    stuck = queue(owner, channel="sms", status="pending", next_try_at=start - timedelta(minutes=15))
    own_rows.append(stuck)

    def stored() -> tuple[object, ...] | None:
        return owner.execute(
            "SELECT firing_since IS NOT NULL, notified_at IS NOT NULL, resolved_at IS NOT NULL, attempts, "
            "last_outcome FROM ops_alert WHERE key = %s",
            (key,),
        ).fetchone()

    async def scenario(database: Database) -> list[tuple[object, ...] | None]:
        seen = []
        for minutes, failure, fixed in ((0, None, False), (2, "unreachable", False), (3, None, False), (4, None, True)):
            current["now"] = start + timedelta(minutes=minutes)
            chat.failure = failure
            if fixed:
                owner.execute("UPDATE outbox_message SET status = 'sent' WHERE id = %s", (stuck,))
            # A new watch at every round: a worker that restarted between any two of them.
            await OpsWatch(database, chat, chats=(700200,), now=lambda: current["now"]).run_once()
            seen.append(stored())
        return seen

    rounds = run(worker_database_url, scenario)
    assert rounds == [
        (False, False, False, 0, None),  # it holds; not for two minutes yet
        (True, False, False, 1, "unreachable"),  # firing; Telegram did not take it; owed
        (True, True, False, 0, "sent"),  # told at the next round
        None,  # stopped, taken back, forgotten
    ]
    # Other tests may have left something else for the watch to say; these are the messages about this key.
    mine = [text for text in chat.sent if key in text]
    assert len(mine) == 2 and "🔴" in mine[0] and "🟢" in mine[1]
    for text in chat.sent:
        # Nothing of the message that was stuck: not whom it was for, not a word of it, not its id.
        for private in (PHONE, PHONE[-7:], "Alisher", "450 000", "qarzingiz", str(stuck), stuck.hex):
            assert private not in text, private
    # And the alert itself was never queued.
    queued = owner.execute("SELECT count(*) FROM outbox_message WHERE payload::text LIKE %s", (f"%{key}%",)).fetchone()
    assert queued == (0,)


def test_the_watch_holds_no_right_but_on_its_own_two_tables(as_worker: AppSession) -> None:
    """The counterpart of the rights table: what the watch stores is the worker's to write, and the
    stored open debts it counts mismatches of stay closed to it."""
    with as_worker(None) as conn:
        conn.execute("INSERT INTO ops_sample (series, taken_at, value) VALUES ('test:rights', now(), 1)")
        conn.execute("DELETE FROM ops_sample WHERE series = 'test:rights'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_worker(None) as conn:
        conn.execute("UPDATE ops_sample SET value = 2")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_worker(None) as conn:
        conn.execute("SELECT * FROM open_debt")
