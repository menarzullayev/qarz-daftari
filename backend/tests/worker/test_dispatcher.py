"""Outbox and dispatcher (ADR-007) against the real database, with a fake channel and a controlled clock."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from qarz.application.dispatch import GIVE_UP_AFTER, GLOBAL_PER_SECOND, LEASE_SECONDS, Dispatcher, backoff
from qarz.application.ports import RecipientBlocked, RetryLater, SendFailed
from qarz.infrastructure.db import Database

pytestmark = pytest.mark.db


@dataclass
class Clock:
    current: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(seconds=2))

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


@dataclass
class FakeSender:
    sent: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    # recipient -> exceptions to raise on successive attempts, before succeeding
    failures: dict[str, list[Exception]] = field(default_factory=dict)

    async def send(self, channel: str, recipient: str, payload: dict[str, Any]) -> None:
        queue = self.failures.get(recipient)
        if queue:
            raise queue.pop(0)
        self.sent.append((channel, recipient, payload))


@pytest.fixture(autouse=True)
def empty_outbox(owner: psycopg.Connection) -> None:
    """Other tests queue messages too; start each dispatcher test with nothing pending."""
    owner.execute("UPDATE outbox_message SET status = 'sent' WHERE status = 'pending'")


def run[T](worker_database_url: str, scenario: Callable[[Database], Awaitable[T]]) -> T:
    async def wrapper() -> T:
        database = Database(worker_database_url)
        try:
            return await scenario(database)
        finally:
            await database.dispose()

    return asyncio.run(wrapper())


async def enqueue(database: Database, recipient: str, text: str = "hello", key: str | None = None) -> bool:
    async with database.platform() as session:
        return await session.enqueue(
            channel="telegram", recipient=recipient, payload={"text": text}, dedupe_key=key or uuid.uuid4().hex
        )


def status_of(owner: psycopg.Connection, recipient: str) -> list[tuple[str, int]]:
    return owner.execute(
        "SELECT status, attempts FROM outbox_message WHERE recipient = %s ORDER BY created_at, id", (recipient,)
    ).fetchall()


# --- the outbox is transactional -----------------------------------------------------------------------


def test_a_queued_message_is_sent_and_marked(worker_database_url: str, owner: psycopg.Connection) -> None:
    sender, clock = FakeSender(), Clock()

    async def scenario(database: Database) -> Any:
        await enqueue(database, "111", "salom")
        return await Dispatcher(database, sender, clock.now).run_once()

    result = run(worker_database_url, scenario)
    assert result.sent == 1
    assert sender.sent == [("telegram", "111", {"text": "salom"})]
    assert status_of(owner, "111") == [("sent", 0)]


def test_a_message_queued_in_a_failed_transaction_never_exists(
    worker_database_url: str, owner: psycopg.Connection
) -> None:
    sender, clock = FakeSender(), Clock()

    async def scenario(database: Database) -> Any:
        with pytest.raises(RuntimeError):
            async with database.platform() as session:
                await session.enqueue(channel="telegram", recipient="222", payload={"text": "x"}, dedupe_key="rolled")
                raise RuntimeError("the business change failed")
        return await Dispatcher(database, sender, clock.now).run_once()

    result = run(worker_database_url, scenario)
    assert result.sent == 0
    assert sender.sent == []
    assert status_of(owner, "222") == []


def test_the_same_dedupe_key_is_queued_once(worker_database_url: str, owner: psycopg.Connection) -> None:
    async def scenario(database: Database) -> tuple[bool, bool]:
        key = f"dedupe-{uuid.uuid4().hex}"
        return await enqueue(database, "333", key=key), await enqueue(database, "333", key=key)

    assert run(worker_database_url, scenario) == (True, False)
    assert len(status_of(owner, "333")) == 1


# --- failures ------------------------------------------------------------------------------------------


def test_retry_after_waits_exactly_that_long_and_is_not_a_failed_attempt(
    worker_database_url: str, owner: psycopg.Connection
) -> None:
    sender, clock = FakeSender(failures={"444": [RetryLater(30)]}), Clock()

    async def scenario(database: Database) -> list[Any]:
        await enqueue(database, "444")
        dispatcher = Dispatcher(database, sender, clock.now)
        first = await dispatcher.run_once()
        clock.advance(seconds=29)
        too_early = await dispatcher.run_once()
        clock.advance(seconds=2)
        later = await dispatcher.run_once()
        return [first, too_early, later]

    first, too_early, later = run(worker_database_url, scenario)
    assert (first.rescheduled, first.sent) == (1, 0)
    assert (too_early.sent, too_early.rescheduled) == (0, 0)
    assert later.sent == 1
    assert status_of(owner, "444") == [("sent", 0)]


def test_other_failures_back_off_and_count_attempts(worker_database_url: str, owner: psycopg.Connection) -> None:
    sender = FakeSender(failures={"555": [SendFailed("net"), SendFailed("net"), SendFailed("net")]})
    clock = Clock()

    async def scenario(database: Database) -> list[int]:
        await enqueue(database, "555")
        dispatcher = Dispatcher(database, sender, clock.now)
        sent_per_run = []
        for wait in (0, 4, 2, 9, 2, 19, 2):  # just before and just after 5 s, 10 s, 20 s
            clock.advance(seconds=wait)
            sent_per_run.append((await dispatcher.run_once()).sent)
        return sent_per_run

    assert run(worker_database_url, scenario) == [0, 0, 0, 0, 0, 0, 1]
    assert status_of(owner, "555") == [("sent", 3)]


def test_backoff_doubles_and_is_capped() -> None:
    assert [backoff(n).total_seconds() for n in range(5)] == [5, 10, 20, 40, 80]
    assert backoff(30) == timedelta(hours=1)


def test_a_message_that_keeps_failing_for_a_day_is_given_up(
    worker_database_url: str, owner: psycopg.Connection
) -> None:
    sender, clock = FakeSender(failures={"666": [SendFailed("net")] * 3}), Clock()

    async def scenario(database: Database) -> list[Any]:
        await enqueue(database, "666")
        owner.execute(
            "UPDATE outbox_message SET created_at = created_at - %s WHERE recipient = '666'",
            (GIVE_UP_AFTER - timedelta(minutes=1),),
        )
        dispatcher = Dispatcher(database, sender, clock.now)
        still_trying = await dispatcher.run_once()
        clock.advance(minutes=2)
        given_up = await dispatcher.run_once()
        return [still_trying, given_up]

    still_trying, given_up = run(worker_database_url, scenario)
    assert (still_trying.rescheduled, still_trying.failed) == (1, 0)
    assert given_up.failed == 1
    assert status_of(owner, "666") == [("failed", 1)]


def test_a_blocked_recipient_fails_all_their_messages_and_their_links_become_unreachable(
    worker_database_url: str, owner: psycopg.Connection
) -> None:
    blocked_tg, other_tg = 777_000_001, 777_000_002
    users = {}
    for tg in (blocked_tg, other_tg):
        users[tg] = uuid.uuid4()
        owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (users[tg], tg))
    links = {}
    for label, tg in (("blocked in shop 1", blocked_tg), ("blocked in shop 2", blocked_tg), ("other", other_tg)):
        shop, customer, link = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        owner.execute("INSERT INTO shop (id, name) VALUES (%s, %s)", (shop, label))
        owner.execute(
            "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'C', 'c')", (customer, shop)
        )
        owner.execute(
            "INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at) "
            "VALUES (%s, %s, %s, %s, 'active', 2, now())",
            (link, shop, customer, users[tg]),
        )
        links[label] = link

    sender, clock = FakeSender(failures={str(blocked_tg): [RecipientBlocked()]}), Clock()

    async def scenario(database: Database) -> Any:
        for _ in range(3):
            await enqueue(database, str(blocked_tg))
        await enqueue(database, str(other_tg))
        return await Dispatcher(database, sender, clock.now).run_once()

    result = run(worker_database_url, scenario)
    assert (result.blocked, result.sent) == (1, 1)
    assert [status for status, _ in status_of(owner, str(blocked_tg))] == ["failed", "failed", "failed"]
    assert status_of(owner, str(other_tg)) == [("sent", 0)]
    assert sender.sent == [("telegram", str(other_tg), {"text": "hello"})]
    state = {
        label: owner.execute("SELECT status FROM customer_link WHERE id = %s", (link,)).fetchone()[0]  # type: ignore[index]
        for label, link in links.items()
    }
    assert state == {"blocked in shop 1": "unreachable", "blocked in shop 2": "unreachable", "other": "active"}


def test_only_the_narrow_function_can_cross_tenants(worker_database_url: str, owner: psycopg.Connection) -> None:
    """The worker's role with no tenant cannot touch customer_link directly; the function is the worker's
    alone (migration 0031): neither everyone nor the ordinary application may call it."""
    with psycopg.connect(worker_database_url, autocommit=True) as conn:
        assert conn.execute("UPDATE customer_link SET status = 'unreachable'").rowcount == 0
    row = owner.execute(
        "SELECT has_function_privilege('public', 'mark_recipient_unreachable(bigint)', 'EXECUTE'), "
        "       has_function_privilege('qd_worker', 'mark_recipient_unreachable(bigint)', 'EXECUTE'), "
        "       (SELECT prosecdef FROM pg_proc WHERE proname = 'mark_recipient_unreachable'), "
        "       has_function_privilege('qd_app', 'mark_recipient_unreachable(bigint)', 'EXECUTE')"
    ).fetchone()
    assert row == (False, True, True, False)


# --- rate limits ---------------------------------------------------------------------------------------


def test_one_message_a_second_per_recipient(worker_database_url: str, owner: psycopg.Connection) -> None:
    sender, clock = FakeSender(), Clock()

    async def scenario(database: Database) -> list[Any]:
        for n in range(3):
            await enqueue(database, "888", f"m{n}")
        dispatcher = Dispatcher(database, sender, clock.now)
        results = [await dispatcher.run_once()]
        for _ in range(2):
            clock.advance(seconds=1)
            results.append(await dispatcher.run_once())
        return results

    results = run(worker_database_url, scenario)
    assert [(r.sent, r.deferred) for r in results] == [(1, 2), (1, 1), (1, 0)]
    assert [payload["text"] for _, _, payload in sender.sent] == ["m0", "m1", "m2"]
    assert status_of(owner, "888") == [("sent", 0)] * 3  # deferral is not a failed attempt


def test_the_global_limit_holds_across_recipients(worker_database_url: str) -> None:
    sender, clock = FakeSender(), Clock()
    extra = 5

    async def scenario(database: Database) -> list[Any]:
        for n in range(GLOBAL_PER_SECOND + extra):
            await enqueue(database, str(900_000 + n))
        dispatcher = Dispatcher(database, sender, clock.now)
        first = await dispatcher.run_once()
        clock.advance(seconds=1)
        second = await dispatcher.run_once()
        return [first, second]

    first, second = run(worker_database_url, scenario)
    assert (first.sent, first.deferred) == (GLOBAL_PER_SECOND, extra)
    assert second.sent == extra
    assert len({recipient for _, recipient, _ in sender.sent}) == GLOBAL_PER_SECOND + extra


# --- at-least-once, never twice at the same time -------------------------------------------------------


def test_two_dispatchers_never_send_the_same_message(worker_database_url: str) -> None:
    sender, clock = FakeSender(), Clock()

    async def scenario(database: Database) -> list[Any]:
        for n in range(20):
            await enqueue(database, str(910_000 + n))
        one, two = Dispatcher(database, sender, clock.now), Dispatcher(database, sender, clock.now)
        return list(await asyncio.gather(one.run_once(), two.run_once()))

    results = run(worker_database_url, scenario)
    assert sum(r.sent for r in results) == 20
    recipients = [recipient for _, recipient, _ in sender.sent]
    assert len(recipients) == len(set(recipients)) == 20


def test_a_message_claimed_by_a_dispatcher_that_died_is_sent_after_the_lease(
    worker_database_url: str, owner: psycopg.Connection
) -> None:
    sender, clock = FakeSender(), Clock()

    async def scenario(database: Database) -> list[int]:
        await enqueue(database, "999")
        # a dispatcher claims the message and then dies before sending or marking it
        async with database.platform() as session:
            claimed = await session.claim_due_messages(now=clock.now(), lease_seconds=LEASE_SECONDS, limit=10)
        assert len(claimed) == 1
        survivor = Dispatcher(database, sender, clock.now)
        during_lease = (await survivor.run_once()).sent
        clock.advance(seconds=LEASE_SECONDS + 1)
        after_lease = (await survivor.run_once()).sent
        return [during_lease, after_lease]

    assert run(worker_database_url, scenario) == [0, 1]
    assert status_of(owner, "999") == [("sent", 0)]
