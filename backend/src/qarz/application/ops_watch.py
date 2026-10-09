"""The operations watch (DEC-078): one round reads the figures, judges them, and tells the operators.

The worker runs a round every minute, beside the outbox and the schedule. What is firing, since when and
when somebody was last told is kept in the database (`ops_alert`), so a restart neither repeats every
alert nor forgets one. The judgement is `qarz.domain.ops_alerts`; this module is the order of the work:

1. read the state and the database's figures; read what the backup jobs wrote, the disk, the API;
2. judge, and move every alert on;
3. send ONE message with whatever became due (first notice, reminder, resolved), straight through the
   bot and never through the outbox: an alert about the outbox cannot wait in the outbox;
4. record what was said and what could not be.

Nothing is queued. An alert that could not be delivered stays a row that is owed its message, and the
next round tries again; there is one row for each condition, so there is nothing to grow.

**The one thing kept in memory** is the database being unreachable: the state lives there, so that
failure cannot be recorded in it. It is told from memory, at most once every `REPEAT_AFTER`; a worker
that restarts during an outage says it once more.

This watch runs on the machine it watches. When the machine, Docker or the worker is down it says
nothing; that is what the notification from outside is for (deploy/production/SINGLE-HOST.md).
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from qarz.application.chat_texts import say
from qarz.application.ports import Storage
from qarz.domain import ops_alerts as rules
from qarz.domain.ops_alerts import Alert, Figures, Finding
from qarz.domain.promise import TASHKENT

log = logging.getLogger("qarz.ops")

# Lines in one message; what is over stays owed and goes with the next round's message.
MAX_LINES = 20
DEFAULT_LANG = "uz"
# The database must have been out of reach this long before anybody is told: a restart is not an outage.
DATABASE_DOWN_FOR = timedelta(minutes=2)
SENT, UNCONFIGURED, UNREACHABLE, REFUSED, REJECTED = "sent", "unconfigured", "unreachable", "refused", "rejected"
_ORDER = (rules.FIRING, rules.REMINDER, rules.RESOLVED)


class AlertNotDelivered(Exception):
    """Telegram did not take the message. `outcome` says why, in one fixed word and never in Telegram's:
    `unreachable` (no answer, or asked to wait), `refused` (the bot's token), `rejected` (this chat)."""

    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome


class AlertChannel(Protocol):
    async def send(self, chat_id: int, text: str) -> None:
        """Deliver now, with a short timeout, or raise AlertNotDelivered."""
        ...

    async def state(self) -> str:
        """Whether Telegram accepts the bot: "ok", "refused" or "unreachable"."""
        ...


class ApiReader(Protocol):
    async def healthy(self) -> bool: ...

    async def counters(self) -> dict[str, float] | None:
        """The API's /metrics as name -> value; None when it could not be read."""
        ...


@dataclass
class WorkerHealth:
    """What the worker's own loop reports to the watch: when the dispatcher last finished a round."""

    started_at: datetime
    dispatched_at: datetime | None = None

    def dispatched(self, now: datetime) -> None:
        self.dispatched_at = now

    def idle_seconds(self, now: datetime) -> float:
        return (now - (self.dispatched_at or self.started_at)).total_seconds()


FigureReader = Callable[[], Mapping[str, float]]


@dataclass(frozen=True)
class Sources:
    """Where the figures outside the database come from. What is None is not watched in this deployment."""

    backup: FigureReader | None = None  # what the backup jobs wrote
    files: FigureReader | None = None  # what the copy of the stored files wrote
    disk: FigureReader | None = None  # path -> share of its filesystem in use
    api: ApiReader | None = None
    metrics: bool = False  # the API's counters can be read (a metrics token is set)
    health: WorkerHealth | None = None

    def configured(self) -> frozenset[str]:
        parts = {
            rules.BACKUP: self.backup is not None,
            rules.FILES: self.files is not None,
            rules.DISK: self.disk is not None,
            rules.API: self.api is not None,
            rules.METRICS: self.api is not None and self.metrics,
            rules.WORKER: self.health is not None,
        }
        return frozenset({rules.DATABASE} | {name for name, there in parts.items() if there})


@dataclass
class Round:
    """What one round did; for the log and for tests."""

    firing: list[str] = field(default_factory=list)  # keys that began to fire this round
    resolved: list[str] = field(default_factory=list)  # keys that stopped this round
    owed: int = 0  # messages due
    told: list[str] = field(default_factory=list)  # keys whose message was accepted
    outcome: str | None = None  # of the send, when something was due
    database_reachable: bool = True


def clock(moment: datetime) -> str:
    """A moment as a message shows it: day, month and time in Tashkent."""
    return moment.astimezone(TASHKENT).strftime("%d.%m %H:%M")


def render(lang: str, batch: Sequence[tuple[Alert, str]], *, more: int = 0) -> str:
    """One message for everything that became due. It is made of the title, a heading for each kind, and
    one line an alert: the rule's name and label, the rule's fixed text, a figure and one or two moments.
    Nothing else can get in: there is no field of a shop or of a person to take it from."""
    lines = [say(lang, "ops_title")]
    for kind in _ORDER:
        chosen = [alert for alert, message in batch if message == kind]
        if not chosen:
            continue
        lines += ["", say(lang, f"ops_{kind}")]
        for alert in chosen:
            name = rules.key_of(rules.rule_of(alert.key).name, rules.label_of(alert.key))
            text = say(lang, f"ops_rule_{rules.rule_of(alert.key).name}")
            since = clock(alert.firing_since or alert.since)
            if kind == rules.RESOLVED:
                until = clock(alert.resolved_at or alert.since)
                lines.append(say(lang, "ops_line_resolved", name=name, text=text, since=since, until=until))
            else:
                figure = rules.shown(alert.key, alert.value)
                value = f" [{figure}]" if figure else ""
                lines.append(say(lang, "ops_line", name=name, text=text, value=value, since=since))
    if more > 0:
        lines += ["", say(lang, "ops_more", count=more)]
    lines += ["", say(lang, "ops_footer")]
    return "\n".join(lines)


def trial_text(now: datetime) -> str:
    """The clearly marked test alert, in both languages: the command that sends it knows no chat's."""
    return "\n\n".join(say(lang, "ops_test", at=clock(now)) for lang in ("uz", "ru"))


async def send_test_alert(channel: AlertChannel, chats: Sequence[int], now: datetime) -> dict[int, str]:
    """Send the test alert to every configured chat. For each: `sent`, or why Telegram did not take it."""
    outcomes: dict[int, str] = {}
    for chat in chats:
        try:
            await channel.send(chat, trial_text(now))
        except AlertNotDelivered as failure:
            outcomes[chat] = failure.outcome
        else:
            outcomes[chat] = SENT
    return outcomes


class OpsWatch:
    def __init__(
        self,
        storage: Storage,
        channel: AlertChannel,
        *,
        chats: Sequence[int] = (),
        sources: Sources | None = None,
        now: Callable[[], datetime] | None = None,
        repeat_after: timedelta = rules.REPEAT_AFTER,
    ) -> None:
        self._storage = storage
        self._channel = channel
        self._chats = tuple(chats)
        self._sources = sources or Sources()
        self._now = now or (lambda: datetime.now(UTC))
        self._repeat_after = repeat_after
        self._langs: dict[int, str] = {}
        # The database out of reach: the one state that cannot be kept in the database.
        self._down_since: datetime | None = None
        self._down_told_at: datetime | None = None

    async def run_once(self) -> Round:
        now = self._now()
        try:
            async with self._storage.platform() as session:
                stored = {alert.key: alert for alert in await session.ops_alerts()}
                database = await session.ops_database_figures(now)
                for chat in self._chats:
                    # A person's chat is written to in their language; a group in Uzbek, as the review
                    # group is.
                    known = await session.language_of_telegram_user(chat) if chat > 0 else None
                    self._langs[chat] = known or DEFAULT_LANG
        except Exception:
            log.exception("ops_database_unreachable")
            await self._database_down(now)
            return Round(database_reachable=False)
        if self._down_since is not None:
            await self._database_back(now)

        figures = await self._figures(now, database)
        findings = rules.evaluate(figures)
        read = rules.sources_read(figures)
        result = Round()
        gone = set(rules.dropped(stored.values(), figures.configured))
        for key in gone:
            log.info("ops_alert_dropped", extra={"kind": key})
        current: dict[str, Alert] = {}
        for key in sorted((set(stored) | set(findings)) - gone):
            finding = findings.get(key)
            if finding is None and rules.rule_of(key).source in read:
                # Its source was read and no longer speaks of it (a path or a job that is gone).
                finding = Finding(False)
            before = stored.get(key)
            after = rules.advance(before, finding, key, now)
            if after is None:
                continue
            current[key] = after
            if after.firing_since == now:
                result.firing.append(key)
                log.warning("ops_alert_firing", extra={"kind": key})
            if after.resolved_at == now:
                result.resolved.append(key)
                log.info("ops_alert_resolved", extra={"kind": key})

        owed = [
            (alert, message) for alert in current.values() if (message := rules.due(alert, now, self._repeat_after))
        ]
        owed.sort(key=lambda item: (_ORDER.index(item[1]), item[0].key))
        result.owed = len(owed)
        batch = owed[:MAX_LINES]
        if batch:
            result.outcome = await self._tell(lambda lang: render(lang, batch, more=len(owed) - len(batch)))
            for alert, message in batch:
                if result.outcome == SENT:
                    told = rules.delivered(alert, message, now)
                    result.told.append(alert.key)
                else:
                    told = rules.not_delivered(alert, message, result.outcome, now)
                if told is None:
                    del current[alert.key]
                else:
                    current[alert.key] = told
            if result.outcome == SENT:
                log.info("ops_alert_sent", extra={"count": len(batch)})
            elif result.outcome != UNCONFIGURED:
                log.warning("ops_alert_not_sent", extra={"kind": result.outcome, "count": len(batch)})

        async with self._storage.platform() as session:
            for key in sorted(set(stored) - set(current)):
                await session.delete_ops_alert(key)
            for key, alert in current.items():
                if stored.get(key) != alert:
                    await session.store_ops_alert(alert)
        return result

    async def _figures(self, now: datetime, database: rules.DatabaseFigures) -> Figures:
        sources = self._sources
        healthy: bool | None = None
        readable: bool | None = None
        increases: dict[tuple[str, int], float] | None = None
        if sources.api is not None:
            healthy = await sources.api.healthy()
            if sources.metrics:
                counters = await sources.api.counters()
                readable = counters is not None
                if counters is not None:
                    try:
                        increases = await self._increases(now, rules.counter_series(counters))
                    except Exception:
                        # The rules that compare counters are not judged this round; the others are.
                        log.exception("ops_samples_failed")
        # With no chat configured the watch calls Telegram for nothing at all, not even to ask whether the
        # bot is accepted: a deployment that tells nobody sends nothing.
        watched = sources.configured() | ({rules.TELEGRAM} if self._chats else frozenset())
        return Figures(
            now=now,
            configured=watched,
            backup=_read(sources.backup),
            files=_read(sources.files),
            disk_used=_read(sources.disk),
            database=database,
            api_healthy=healthy,
            metrics_readable=readable,
            increases=increases,
            telegram=await self._channel.state() if self._chats else None,
            dispatch_idle=None if sources.health is None else sources.health.idle_seconds(now),
        )

    async def _increases(self, now: datetime, series: Mapping[str, float]) -> dict[tuple[str, int], float]:
        """Store this round's counters and say by how much each grew within the windows the rules read."""
        async with self._storage.platform() as session:
            await session.add_ops_samples(now, series)
            samples = await session.ops_samples(now - rules.SAMPLES_KEPT)
            await session.prune_ops_samples(now - rules.SAMPLES_KEPT)
        grown: dict[tuple[str, int], float] = {}
        for name, window in rules.windows():
            amount = rules.increase(samples.get(name, []), now, window)
            if amount is not None:
                grown[(name, window)] = amount
        return grown

    async def _tell(self, text_in: Callable[[str], str]) -> str:
        """Send to every configured chat. `sent` when at least one took it: a chat that is wrong for good
        must not make the others hear everything again at every round."""
        if not self._chats:
            return UNCONFIGURED
        failures: list[str] = []
        accepted = 0
        for chat in self._chats:
            try:
                await self._channel.send(chat, text_in(self._langs.get(chat, DEFAULT_LANG)))
            except AlertNotDelivered as failure:
                failures.append(failure.outcome)
            except Exception:
                # Whatever it is, it is not a reason to lose the round's state.
                log.exception("ops_alert_send_failed")
                failures.append(UNREACHABLE)
            else:
                accepted += 1
        if accepted:
            return SENT
        return failures[0]

    async def _database_down(self, now: datetime) -> None:
        if self._down_since is None:
            self._down_since = now
        if now - self._down_since < DATABASE_DOWN_FOR:
            return
        if self._down_told_at is not None and now - self._down_told_at < self._repeat_after:
            return
        since = clock(self._down_since)
        if await self._tell(lambda lang: say(lang, "ops_db_down", since=since)) == SENT:
            self._down_told_at = now

    async def _database_back(self, now: datetime) -> None:
        since = self._down_since
        if since is None:
            return
        lasted = now - since >= DATABASE_DOWN_FOR
        log.info("ops_database_back")
        if lasted:
            # Said even when the outage itself could not be: the operators then learn of it afterwards.
            outcome = await self._tell(lambda lang: say(lang, "ops_db_up", since=clock(since), until=clock(now)))
            if outcome not in (SENT, UNCONFIGURED) and now - since < rules.GIVE_UP_RESOLVED_AFTER:
                return  # owed still; the next round that reaches the database says it
        self._down_since = None
        self._down_told_at = None


def _read(reader: FigureReader | None) -> Mapping[str, float] | None:
    """A source's figures; None when it is not configured, or could not be read this round."""
    if reader is None:
        return None
    try:
        return reader()
    except Exception:
        log.exception("ops_figures_unreadable")
        return None
