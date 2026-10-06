"""Outbox dispatcher (ADR-007): delivers queued messages at least once, within the channel's rate limits.

Messages are claimed with a lease, sent outside any database transaction, and then marked. If the process
dies between sending and marking, the message is sent again after the lease: a rare duplicate is accepted,
a lost message is not.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from qarz.application.ports import OutboxMessage, RecipientBlocked, RetryLater, Sender, SendFailed, Storage

PER_RECIPIENT_INTERVAL = timedelta(seconds=1)  # at most one message a second per chat
GLOBAL_PER_SECOND = 25  # below Telegram's limit of about 30 a second (EVID-032)
LEASE_SECONDS = 60
GIVE_UP_AFTER = timedelta(hours=24)
MAX_BACKOFF = timedelta(hours=1)


def backoff(attempts_so_far: int) -> timedelta:
    """5 s, 10 s, 20 s ... capped at one hour."""
    return min(timedelta(seconds=5 * 2**attempts_so_far), MAX_BACKOFF)


@dataclass
class DispatchResult:
    sent: int = 0
    deferred: int = 0
    rescheduled: int = 0
    failed: int = 0
    blocked: int = 0


@dataclass
class Dispatcher:
    storage: Storage
    sender: Sender
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    _last_sent: dict[tuple[str, str], datetime] = field(default_factory=dict)
    _recent: deque[datetime] = field(default_factory=deque)

    async def run_once(self, limit: int = 100) -> DispatchResult:
        result = DispatchResult()
        async with self.storage.platform() as session:
            messages = await session.claim_due_messages(now=self.now(), lease_seconds=LEASE_SECONDS, limit=limit)
        blocked: set[tuple[str, str]] = set()
        for message in messages:
            key = (message.channel, message.recipient)
            if key in blocked:
                continue  # already failed together with the first message to this recipient
            await self._deliver(message, result, blocked)
        return result

    def _wait_needed(self, key: tuple[str, str], now: datetime) -> timedelta | None:
        last = self._last_sent.get(key)
        if last is not None and now - last < PER_RECIPIENT_INTERVAL:
            return PER_RECIPIENT_INTERVAL - (now - last)
        while self._recent and now - self._recent[0] >= timedelta(seconds=1):
            self._recent.popleft()
        if len(self._recent) >= GLOBAL_PER_SECOND:
            return timedelta(seconds=1) - (now - self._recent[0])
        return None

    async def _deliver(self, message: OutboxMessage, result: DispatchResult, blocked: set[tuple[str, str]]) -> None:
        now = self.now()
        key = (message.channel, message.recipient)

        wait = self._wait_needed(key, now)
        if wait is not None:
            async with self.storage.platform() as session:
                await session.reschedule(message.message_id, next_try_at=now + wait, count_attempt=False)
            result.deferred += 1
            return

        try:
            await self.sender.send(message.channel, message.recipient, message.payload)
        except RetryLater as wait_request:
            # The channel told us exactly how long to wait; this is not a failure of the message.
            async with self.storage.platform() as session:
                await session.reschedule(
                    message.message_id, next_try_at=now + timedelta(seconds=wait_request.seconds), count_attempt=False
                )
            result.rescheduled += 1
        except RecipientBlocked:
            async with self.storage.platform() as session:
                await session.mark_failed(message.message_id)
                await session.fail_pending_for(channel=message.channel, recipient=message.recipient)
                if message.channel == "telegram" and message.recipient.lstrip("-").isdigit():
                    await session.mark_recipient_unreachable(int(message.recipient))
            blocked.add(key)
            result.blocked += 1
        except SendFailed:
            async with self.storage.platform() as session:
                if now - message.created_at >= GIVE_UP_AFTER:
                    await session.mark_failed(message.message_id)
                    result.failed += 1
                else:
                    await session.reschedule(
                        message.message_id, next_try_at=now + backoff(message.attempts), count_attempt=True
                    )
                    result.rescheduled += 1
        else:
            self._last_sent[key] = now
            self._recent.append(now)
            async with self.storage.platform() as session:
                await session.mark_sent(message.message_id, now=now)
            result.sent += 1
