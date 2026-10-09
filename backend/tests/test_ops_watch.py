"""The operations watch, round by round (qarz.application.ops_watch), with the database, Telegram and
the clock replaced: an alert is told once, repeated after the interval, taken back once, kept quiet with
no chat configured, and survives Telegram failing. Nothing here touches a database or a network.
"""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from qarz.application.ops_watch import (
    DATABASE_DOWN_FOR,
    MAX_LINES,
    AlertNotDelivered,
    OpsWatch,
    Round,
    Sources,
    WorkerHealth,
    clock,
    render,
    send_test_alert,
    trial_text,
)
from qarz.domain import ops_alerts as rules
from qarz.domain.ops_alerts import Alert, DatabaseFigures

START = datetime(2026, 10, 7, 7, 0, tzinfo=UTC)  # 12:00 in Tashkent
CHAT, GROUP = 700100, -1001234567890
QUIET = DatabaseFigures(
    outbox_oldest_due={},
    job_age=dict.fromkeys((*rules.JOB_LIMITS, rules.REMINDERS_JOB), 60.0),
    service_age=86400.0 * 30,
    sms_retrying=0,
    sms_failed_last_hour=0,
    receipt_waiting=None,
    ledger_mismatches=0.0,
)
STUCK = replace(QUIET, outbox_oldest_due={"telegram": 900.0})  # OutboxOld:telegram, which waits two minutes
REFUSED_SMS = replace(QUIET, sms_failed_last_hour=2)  # SmsRefused, which fires at once
KEY = "OutboxOld:telegram"


@dataclass
class Clock:
    current: datetime = START

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


@dataclass
class FakeStore:
    """Stands in for the database: the alert rows, the samples, and the figures a round reads."""

    figures: DatabaseFigures = QUIET
    alerts: dict[str, Alert] = field(default_factory=dict)
    samples: dict[str, list[tuple[datetime, float]]] = field(default_factory=dict)
    langs: dict[int, str] = field(default_factory=dict)
    down: bool = False
    writes: int = 0

    @asynccontextmanager
    async def platform(self) -> AsyncIterator["FakeStore"]:
        if self.down:
            raise ConnectionError("the database is not there")
        yield self

    def tenant(self, shop_id: object) -> Any:
        raise AssertionError("the watch opens no shop")

    async def user_language(self, user_id: object) -> str | None:
        raise AssertionError("the watch asks for no user")

    async def ops_alerts(self) -> list[Alert]:
        return list(self.alerts.values())

    async def store_ops_alert(self, alert: Alert) -> None:
        self.writes += 1
        self.alerts[alert.key] = alert

    async def delete_ops_alert(self, key: str) -> None:
        self.writes += 1
        del self.alerts[key]

    async def ops_database_figures(self, now: datetime) -> DatabaseFigures:
        return self.figures

    async def language_of_telegram_user(self, tg_id: int) -> str | None:
        return self.langs.get(tg_id)

    async def add_ops_samples(self, taken_at: datetime, values: Mapping[str, float]) -> None:
        for series, value in values.items():
            self.samples.setdefault(series, []).append((taken_at, value))

    async def ops_samples(self, since: datetime) -> dict[str, list[tuple[datetime, float]]]:
        return {name: [s for s in kept if s[0] >= since] for name, kept in self.samples.items()}

    async def prune_ops_samples(self, before: datetime) -> None:
        for name, kept in self.samples.items():
            self.samples[name] = [s for s in kept if s[0] >= before or s == kept[-1]]


@dataclass
class FakeTelegram:
    sent: list[tuple[int, str]] = field(default_factory=list)
    asked: int = 0
    # What `send` raises, for every chat or for the chats named; None: accepted.
    failure: str | None = None
    failing_chats: set[int] = field(default_factory=set)
    answer: str = "ok"
    attempts: int = 0

    async def send(self, chat_id: int, text: str) -> None:
        self.attempts += 1
        if self.failure is not None and (not self.failing_chats or chat_id in self.failing_chats):
            raise AlertNotDelivered(self.failure)
        self.sent.append((chat_id, text))

    async def state(self) -> str:
        self.asked += 1
        return self.answer


@dataclass
class FakeApi:
    up: bool = True
    metrics: dict[str, float] | None = None

    async def healthy(self) -> bool:
        return self.up

    async def counters(self) -> dict[str, float] | None:
        return self.metrics


@dataclass
class Stage:
    store: FakeStore = field(default_factory=FakeStore)
    telegram: FakeTelegram = field(default_factory=FakeTelegram)
    time: Clock = field(default_factory=Clock)
    chats: tuple[int, ...] = (CHAT,)
    sources: Sources = field(default_factory=Sources)
    _watch: OpsWatch | None = None

    @property
    def watch(self) -> OpsWatch:
        if self._watch is None:
            self._watch = OpsWatch(  # type: ignore[arg-type]
                self.store, self.telegram, chats=self.chats, sources=self.sources, now=self.time.now
            )
        return self._watch

    def round(self, **later: float) -> Round:
        """One round, after the clock moved on by `later`."""
        if later:
            self.time.advance(**later)
        return asyncio.run(self.watch.run_once())

    def restart(self) -> None:
        """A new worker process: nothing of the old one's memory, the same database."""
        self._watch = None

    def texts(self) -> list[str]:
        return [text for _, text in self.telegram.sent]


def firing(stage: Stage) -> None:
    """Bring OutboxOld:telegram to firing and told: it holds, and two minutes later it still does."""
    stage.store.figures = STUCK
    stage.round()
    stage.round(minutes=2)
    assert len(stage.telegram.sent) == 1 and stage.store.alerts[KEY].notified_at is not None


# --- fires once ------------------------------------------------------------------------------------------


def test_nothing_wrong_sends_nothing_and_stores_nothing() -> None:
    stage = Stage()
    for _ in range(3):
        result = stage.round(minutes=1)
        assert (result.owed, result.outcome, result.firing) == (0, None, [])
    assert stage.telegram.sent == [] and stage.store.alerts == {} and stage.store.writes == 0


def test_a_condition_is_told_once_when_it_starts_to_fire_and_not_at_every_round() -> None:
    stage = Stage()
    stage.store.figures = STUCK
    first = stage.round()
    # It holds, but has not held for the rule's two minutes: remembered, and nobody is told.
    assert (first.firing, first.owed) == ([], 0) and stage.telegram.sent == []
    assert stage.store.alerts[KEY].firing_since is None
    second = stage.round(minutes=2)
    assert second.firing == [KEY] and second.told == [KEY] and second.outcome == "sent"
    assert len(stage.telegram.sent) == 1
    chat, text = stage.telegram.sent[0]
    assert chat == CHAT and "OutboxOld:telegram" in text and "[15m]" in text and "🔴" in text
    for _ in range(30):
        again = stage.round(minutes=1)
        assert again.owed == 0
    assert len(stage.telegram.sent) == 1


def test_a_rule_without_a_waiting_time_is_told_at_the_first_round() -> None:
    stage = Stage()
    stage.store.figures = REFUSED_SMS
    assert stage.round().told == ["SmsRefused"]
    assert "SmsRefused" in stage.texts()[0] and "[2]" in stage.texts()[0]


def test_a_restart_of_the_worker_neither_repeats_nor_forgets() -> None:
    """The state is in the database: a new process finds the alert firing and told."""
    stage = Stage()
    firing(stage)
    stage.restart()
    assert stage.round(minutes=1).owed == 0
    assert len(stage.telegram.sent) == 1
    # And a condition that was only waiting goes on waiting from when it began, not from the restart.
    other = Stage()
    other.store.figures = STUCK
    other.round()
    other.restart()
    assert other.round(minutes=2).told == [KEY]


# --- repeats after the interval ----------------------------------------------------------------------------


def test_a_condition_that_stays_is_said_again_after_the_interval_and_not_before() -> None:
    stage = Stage()
    firing(stage)
    stage.round(hours=3, minutes=59)
    assert len(stage.telegram.sent) == 1
    reminded = stage.round(minutes=1)
    assert reminded.told == [KEY] and len(stage.telegram.sent) == 2
    assert "🟠" in stage.texts()[1] and "🔴" not in stage.texts()[1]
    stage.round(hours=3, minutes=59)
    assert len(stage.telegram.sent) == 2
    stage.round(minutes=1)
    assert len(stage.telegram.sent) == 3


# --- resolves once -----------------------------------------------------------------------------------------


def test_a_condition_that_stops_is_taken_back_once_and_then_forgotten() -> None:
    stage = Stage()
    firing(stage)
    stage.store.figures = QUIET
    stopped = stage.round(minutes=10)
    assert stopped.resolved == [KEY] and stopped.told == [KEY]
    assert len(stage.telegram.sent) == 2
    text = stage.texts()[1]
    assert "🟢" in text and "OutboxOld:telegram" in text and "🔴" not in text
    # From when it fired until when it stopped, in Tashkent time.
    assert "07.10 12:02 — 12:12" in text.replace("07.10 12:12", "12:12")
    assert stage.store.alerts == {}
    for _ in range(5):
        assert stage.round(minutes=1).owed == 0
    assert len(stage.telegram.sent) == 2


def test_a_condition_that_stops_before_it_fired_is_never_mentioned() -> None:
    stage = Stage()
    stage.store.figures = STUCK
    stage.round()
    stage.store.figures = QUIET
    assert stage.round(minutes=1).owed == 0
    assert stage.telegram.sent == [] and stage.store.alerts == {}


def test_a_condition_that_fires_again_later_is_a_new_alert() -> None:
    stage = Stage()
    firing(stage)
    stage.store.figures = QUIET
    stage.round(minutes=5)
    stage.store.figures = STUCK
    stage.round(minutes=5)
    assert stage.round(minutes=2).told == [KEY]
    assert [("🔴" in t, "🟢" in t) for t in stage.texts()] == [(True, False), (False, True), (True, False)]


# --- does not notify while unconfigured ------------------------------------------------------------------


def test_with_no_chat_configured_the_watch_records_and_tells_nobody() -> None:
    stage = Stage(chats=())
    stage.store.figures = STUCK
    stage.round()
    result = stage.round(minutes=2)
    assert result.firing == [KEY] and result.outcome == "unconfigured" and result.told == []
    # Not one call to Telegram, not even to ask whether the bot is accepted.
    assert stage.telegram.attempts == 0 and stage.telegram.asked == 0
    stored = stage.store.alerts[KEY]
    assert stored.firing_since is not None and stored.notified_at is None and stored.last_outcome == "unconfigured"
    for _ in range(5):
        stage.round(minutes=1)
    assert stage.telegram.attempts == 0
    # It stops: with nobody to tell there is nothing owed, and the row goes.
    stage.store.figures = QUIET
    stage.round(minutes=1)
    assert stage.store.alerts == {} and stage.telegram.attempts == 0


def test_an_alert_that_fired_while_unconfigured_is_told_once_a_chat_is_named() -> None:
    stage = Stage(chats=())
    stage.store.figures = STUCK
    stage.round()
    stage.round(minutes=2)
    configured = Stage(store=stage.store, time=stage.time, chats=(CHAT,))
    assert configured.round(minutes=1).told == [KEY]
    assert len(configured.telegram.sent) == 1
    assert configured.round(minutes=1).owed == 0


# --- survives Telegram failing -----------------------------------------------------------------------------


@pytest.mark.parametrize("failure", ["unreachable", "refused", "rejected"])
def test_when_telegram_does_not_take_the_alert_it_stays_owed_and_is_tried_at_every_round(failure: str) -> None:
    stage = Stage()
    stage.telegram.failure = failure
    stage.store.figures = STUCK
    stage.round()
    result = stage.round(minutes=2)
    assert result.outcome == failure and result.told == []
    stored = stage.store.alerts[KEY]
    assert (stored.notified_at, stored.attempts, stored.last_outcome) == (None, 1, failure)
    assert stored.last_attempt_at == stage.time.current
    stage.round(minutes=1)
    stage.round(minutes=1)
    assert stage.store.alerts[KEY].attempts == 3 and stage.telegram.attempts == 3
    # One row is all that is owed: nothing piled up while Telegram was away.
    assert list(stage.store.alerts) == [KEY]
    stage.telegram.failure = None
    assert stage.round(minutes=1).told == [KEY]
    assert len(stage.telegram.sent) == 1 and stage.store.alerts[KEY].attempts == 0
    assert stage.round(minutes=1).owed == 0


def test_a_resolved_that_could_not_be_delivered_is_tried_again_and_given_up_after_a_day() -> None:
    stage = Stage()
    firing(stage)
    stage.telegram.failure = "unreachable"
    stage.store.figures = QUIET
    stage.round(minutes=5)
    assert stage.store.alerts[KEY].resolved_at is not None
    stage.round(hours=23)
    assert KEY in stage.store.alerts
    stage.round(hours=2)
    assert stage.store.alerts == {} and len(stage.telegram.sent) == 1


def test_an_error_nobody_expected_while_sending_does_not_lose_the_round() -> None:
    class Broken(FakeTelegram):
        async def send(self, chat_id: int, text: str) -> None:
            raise RuntimeError("anything at all")

    stage = Stage(telegram=Broken())
    stage.store.figures = REFUSED_SMS
    result = stage.round()
    assert result.outcome == "unreachable"
    assert stage.store.alerts["SmsRefused"].attempts == 1


def test_one_chat_that_refuses_does_not_make_the_other_hear_everything_again() -> None:
    stage = Stage(chats=(CHAT, GROUP))
    stage.telegram.failure = "rejected"
    stage.telegram.failing_chats = {GROUP}
    stage.store.figures = REFUSED_SMS
    assert stage.round().outcome == "sent"
    for _ in range(5):
        stage.round(minutes=1)
    assert [chat for chat, _ in stage.telegram.sent] == [CHAT]


def test_the_alert_about_the_outbox_does_not_go_through_the_outbox() -> None:
    """The store the watch is given has no way to queue a message; the alert reached the chat anyway."""
    stage = Stage()
    firing(stage)
    assert not hasattr(stage.store, "enqueue") and len(stage.telegram.sent) == 1


# --- one message, and what is in it ------------------------------------------------------------------------


def test_everything_due_in_a_round_goes_in_one_message() -> None:
    stage = Stage()
    stage.store.figures = replace(QUIET, sms_failed_last_hour=1, ledger_mismatches=4.0)
    result = stage.round()
    assert sorted(result.told) == ["LedgerMismatch", "SmsRefused"]
    assert len(stage.telegram.sent) == 1
    text = stage.texts()[0]
    assert text.count("•") == 2 and "LedgerMismatch" in text and "[4]" in text and "runbook 16" in text


def test_more_than_a_message_holds_waits_for_the_next_round() -> None:
    stage = Stage(sources=Sources(disk=lambda: {f"/data/{n:02d}": 0.99 for n in range(MAX_LINES + 3)}))
    stage.round()
    first = stage.round(minutes=10)
    assert first.owed == MAX_LINES + 3 and len(first.told) == MAX_LINES
    assert stage.texts()[0].count("•") == MAX_LINES and "3" in stage.texts()[0].splitlines()[-3]
    second = stage.round(minutes=1)
    assert len(second.told) == 3 and stage.texts()[1].count("•") == 3
    assert stage.round(minutes=1).owed == 0


def test_a_person_is_written_to_in_their_language_and_a_group_in_uzbek() -> None:
    stage = Stage(chats=(CHAT, GROUP))
    stage.store.langs = {CHAT: "ru"}
    stage.store.figures = REFUSED_SMS
    stage.round()
    by_chat = dict(stage.telegram.sent)
    assert "контроль системы" in by_chat[CHAT] and "отклонено провайдером" in by_chat[CHAT]
    assert "tizim nazorati" in by_chat[GROUP] and "rad etildi" in by_chat[GROUP]


def test_a_message_is_made_of_the_rules_name_its_fixed_text_a_figure_and_moments() -> None:
    alert = Alert(key="BackupMissing", since=START, firing_since=START + timedelta(minutes=10), value=97200.0)
    text = render("uz", [(alert, rules.FIRING)])
    assert text.splitlines() == [
        "⚠️ Qarz Daftari: tizim nazorati",
        "",
        "🔴 Boshlandi:",
        "• BackupMissing: omborda yangi zaxira nusxa yo'q (26 soatda birorta ham, yoki 8 kunda to'liq nusxa)"
        " [27h] (07.10 12:10 dan beri)",
        "",
        "Nima qilish kerak: runbook 16 (docs/10-operations/runbooks.md).",
    ]


def test_an_alert_whose_key_is_not_a_rule_and_a_plain_label_cannot_be_rendered() -> None:
    """Whatever were to end up in a key, a message is not built from it."""
    for key in ("OutboxOld:Ali Valiyev +998901234567", "Shop «Baraka»", "OutboxOld:45 000 so'm"):
        with pytest.raises((ValueError, KeyError)):
            render("uz", [(Alert(key=key, since=START, firing_since=START), rules.FIRING)])


# --- sources ----------------------------------------------------------------------------------------------


def test_what_a_deployment_does_not_configure_is_not_watched() -> None:
    stage = Stage()
    stage.round()
    assert Sources().configured() == frozenset({rules.DATABASE})
    # No backup figures are configured, so "no backup at all" is not an alert here.
    assert stage.round(minutes=30).owed == 0


def test_stale_backup_figures_fire_and_fresh_ones_resolve() -> None:
    figures = {"value": {"qd_wal_archive_newest_age_seconds": 9999.0}}
    stage = Stage(sources=Sources(backup=lambda: figures["value"]))
    stage.round()
    told = stage.round(minutes=12).told
    assert told == ["BackupMissing", "RestoreTestNotPassed", "WalArchiveStale"]
    now = stage.time.current.timestamp() + 60
    figures["value"] = {
        'qd_backup_last_success_timestamp_seconds{type="full"}': now - 100,
        "qd_wal_archive_newest_age_seconds": 20.0,
        "qd_backup_check_timestamp_seconds": now - 30,
        "qd_backup_restore_test_last_success_timestamp_seconds": now - 50,
    }
    assert sorted(stage.round(minutes=1).resolved) == told
    assert stage.store.alerts == {}


def test_a_source_that_could_not_be_read_changes_nothing() -> None:
    answers: list[Any] = [{"/data": 0.95}, {"/data": 0.95}]

    def disk() -> dict[str, float]:
        answer = answers.pop(0) if answers else OSError("gone for a moment")
        if isinstance(answer, Exception):
            raise answer
        return answer  # type: ignore[no-any-return]

    stage = Stage(sources=Sources(disk=disk))
    stage.round()
    assert stage.round(minutes=10).told == ["DiskAlmostFull:/data"]
    assert stage.round(minutes=1).resolved == []  # unreadable: neither fired again nor taken back
    assert "DiskAlmostFull:/data" in stage.store.alerts


def test_an_alert_of_a_source_that_is_no_longer_configured_is_dropped_without_a_message() -> None:
    stage = Stage(sources=Sources(disk=lambda: {"/data": 0.95}))
    stage.round()
    stage.round(minutes=10)
    assert len(stage.telegram.sent) == 1
    without = Stage(store=stage.store, telegram=stage.telegram, time=stage.time)
    without.round(minutes=1)
    assert stage.store.alerts == {} and len(stage.telegram.sent) == 1


def test_a_path_that_is_no_longer_watched_resolves_its_alert() -> None:
    paths = {"/a": 0.95, "/b": 0.95}
    stage = Stage(sources=Sources(disk=lambda: dict(paths)))
    stage.round()
    stage.round(minutes=10)
    del paths["/b"]
    assert stage.round(minutes=1).resolved == ["DiskAlmostFull:/b"]


def test_the_api_not_answering_fires_after_two_minutes() -> None:
    api = FakeApi(up=False)
    stage = Stage(sources=Sources(api=api))
    stage.round()
    assert stage.round(minutes=1).owed == 0
    assert stage.round(minutes=1).told == ["ApiDown"]
    api.up = True
    assert stage.round(minutes=1).resolved == ["ApiDown"]


def test_security_events_and_errors_are_read_from_the_apis_counters_across_rounds() -> None:
    def metrics(ok: int, failed: int, cross: int) -> dict[str, float]:
        return {
            'qd_requests_total{method="GET",route="/api/v1/me",status="200"}': float(ok),
            'qd_requests_total{method="GET",route="/api/v1/me",status="500"}': float(failed),
            'qd_security_events_total{kind="shop_not_member"}': float(cross),
        }

    api = FakeApi(metrics=metrics(100, 0, 0))
    stage = Stage(sources=Sources(api=api, metrics=True))
    assert stage.round().owed == 0  # one sample: nothing to compare yet
    api.metrics = metrics(200, 0, 1)
    assert stage.round(minutes=1).told == ["CrossTenantAttempt"]
    # The error rate must hold five minutes: 30 failures among 130 answers in the window, and it goes on.
    api.metrics = metrics(300, 30, 1)
    assert stage.round(minutes=1).owed == 0
    for minute in range(1, 5):
        api.metrics = metrics(300 + 100 * minute, 30 + 30 * minute, 1)
        assert stage.round(minutes=1).told == []
    api.metrics = metrics(800, 180, 1)
    assert stage.round(minutes=1).told == ["ErrorRateHigh"]
    # Ten minutes after the one cross-tenant attempt its window has passed.
    assert "CrossTenantAttempt" in stage.store.alerts
    for minute in range(6):
        api.metrics = metrics(900 + 100 * minute, 180, 1)
        stage.round(minutes=1)
    assert "CrossTenantAttempt" not in stage.store.alerts
    # Samples older than the longest window are not kept.
    oldest = min(sample[0] for kept in stage.store.samples.values() for sample in kept)
    assert stage.time.current - oldest <= rules.SAMPLES_KEPT


def test_unreadable_metrics_fire_their_own_rule_and_judge_no_counter() -> None:
    api = FakeApi(metrics=None)
    stage = Stage(sources=Sources(api=api, metrics=True))
    stage.round()
    assert stage.round(minutes=3).told == ["MetricsMissing"]
    assert stage.store.samples == {}


def test_with_no_chat_configured_a_refused_bot_is_not_looked_for() -> None:
    stage = Stage(chats=())
    stage.telegram.answer = "refused"
    assert stage.round().firing == [] and stage.telegram.asked == 0 and stage.store.alerts == {}
    # And an alert about Telegram left from when a chat was configured is dropped, not kept for ever.
    stage.store.alerts["TelegramRefusesBot"] = Alert(key="TelegramRefusesBot", since=START, firing_since=START)
    stage.round(minutes=1)
    assert stage.store.alerts == {}


def test_telegram_refusing_the_bot_is_recorded_even_though_it_cannot_be_said() -> None:
    stage = Stage()
    stage.telegram.answer = "refused"
    stage.telegram.failure = "refused"
    result = stage.round()
    assert result.firing == ["TelegramRefusesBot"] and result.outcome == "refused"
    assert stage.store.alerts["TelegramRefusesBot"].last_outcome == "refused"
    # Once the token is right again the operators learn of it, from start to end.
    stage.telegram.answer, stage.telegram.failure = "ok", None
    assert stage.round(minutes=7).told == ["TelegramRefusesBot"]
    assert "🟢" in stage.texts()[0] and "12:00 — " in stage.texts()[0]


def test_a_dispatcher_that_finishes_no_round_fires() -> None:
    health = WorkerHealth(started_at=START)
    stage = Stage(sources=Sources(health=health))
    assert stage.round(minutes=5).owed == 0
    assert stage.round(seconds=1).told == ["DispatcherFailing"]
    health.dispatched(stage.time.current)
    assert stage.round(minutes=1).resolved == ["DispatcherFailing"]


# --- the database out of reach -----------------------------------------------------------------------------


def test_a_database_that_cannot_be_reached_is_told_from_memory_and_taken_back_when_it_returns() -> None:
    stage = Stage()
    stage.store.down = True
    first = stage.round()
    assert first.database_reachable is False and stage.telegram.sent == []  # a restart is not an outage
    stage.round(seconds=DATABASE_DOWN_FOR.total_seconds())
    assert len(stage.telegram.sent) == 1 and "ma'lumotlar bazasiga ulana olmayapti" in stage.texts()[0]
    for _ in range(10):
        stage.round(minutes=1)
    assert len(stage.telegram.sent) == 1
    stage.round(hours=4)
    assert len(stage.telegram.sent) == 2  # said again after the interval
    stage.store.down = False
    assert stage.round(minutes=1).database_reachable is True
    assert len(stage.telegram.sent) == 3 and "yana ishlayapti" in stage.texts()[2]
    stage.round(minutes=1)
    assert len(stage.telegram.sent) == 3


def test_a_database_that_was_away_for_a_moment_is_not_mentioned() -> None:
    stage = Stage()
    stage.store.down = True
    stage.round()
    stage.store.down = False
    stage.round(seconds=30)
    stage.round(minutes=5)
    assert stage.telegram.sent == []


# --- the test alert ----------------------------------------------------------------------------------------


def test_the_test_alert_is_marked_as_a_test_in_both_languages_and_reports_each_chat() -> None:
    telegram = FakeTelegram(failure="rejected", failing_chats={GROUP})
    outcomes = asyncio.run(send_test_alert(telegram, (CHAT, GROUP), START))
    assert outcomes == {CHAT: "sent", GROUP: "rejected"}
    assert telegram.sent == [(CHAT, trial_text(START))]
    assert "SINOV" in trial_text(START) and "ПРОБНОЕ" in trial_text(START) and clock(START) == "07.10 12:00"


def test_the_test_alert_with_no_chat_sends_nothing() -> None:
    telegram = FakeTelegram()
    assert asyncio.run(send_test_alert(telegram, (), START)) == {}
    assert telegram.attempts == 0
