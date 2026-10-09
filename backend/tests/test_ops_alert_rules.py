"""The operations watch's judgement (qarz.domain.ops_alerts): every condition fires when it should and
not otherwise, an alert moves through "not yet", "firing" and "stopped" as described, and the thresholds
are those of deploy/monitoring/alerts.yml.

Pure: no database, no clock but the one given.
"""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qarz.application.chat_texts import RU, UZ
from qarz.domain import ops_alerts as rules
from qarz.domain.ops_alerts import Alert, DatabaseFigures, Figures, Finding

REPO = Path(__file__).resolve().parents[2]
ALERTS_YML = REPO / "deploy" / "monitoring" / "alerts.yml"

# A Wednesday, 12:00 in Tashkent (07:00 UTC): inside the reminder hours.
NOW = datetime(2026, 10, 7, 7, 0, tzinfo=UTC)
T = NOW.timestamp()
EVERYTHING = frozenset(
    {
        rules.BACKUP,
        rules.FILES,
        rules.DISK,
        rules.DATABASE,
        rules.API,
        rules.METRICS,
        rules.TELEGRAM,
        rules.WORKER,
    }
)

HEALTHY_BACKUP = {
    'qd_backup_last_success_timestamp_seconds{type="full"}': T - 3 * 86400,
    'qd_backup_last_success_timestamp_seconds{type="diff"}': T - 3600,
    'qd_backup_last_run_success{type="full"}': 1.0,
    'qd_backup_last_run_success{type="diff"}': 1.0,
    "qd_wal_archive_newest_age_seconds": 40.0,
    "qd_backup_check_timestamp_seconds": T - 100,
    "qd_backup_restore_test_last_success_timestamp_seconds": T - 86400,
    "qd_backup_restore_test_last_run_success": 1.0,
}
HEALTHY_DATABASE = DatabaseFigures(
    outbox_oldest_due={"telegram": 3.0},
    job_age=dict.fromkeys((*rules.JOB_LIMITS, rules.REMINDERS_JOB), 120.0),
    service_age=30 * 86400.0,
    sms_retrying=0,
    sms_failed_last_hour=0,
    receipt_waiting=None,
    ledger_mismatches=0.0,
)
QUIET_COUNTERS = dict.fromkeys(rules.windows(), 0.0) | {(rules.REQUESTS_ALL, 300): 500.0}
HEALTHY = Figures(
    now=NOW,
    configured=EVERYTHING,
    backup=HEALTHY_BACKUP,
    files={"sync.last-success": T - 200},
    disk_used={"/var/lib/qarz/files": 0.41},
    database=HEALTHY_DATABASE,
    api_healthy=True,
    metrics_readable=True,
    increases=QUIET_COUNTERS,
    telegram="ok",
    dispatch_idle=1.0,
)


def backup(**changed: float | None) -> Figures:
    figures = dict(HEALTHY_BACKUP)
    names = {
        "full": 'qd_backup_last_success_timestamp_seconds{type="full"}',
        "diff": 'qd_backup_last_success_timestamp_seconds{type="diff"}',
        "full_run": 'qd_backup_last_run_success{type="full"}',
        "diff_run": 'qd_backup_last_run_success{type="diff"}',
        "wal": "qd_wal_archive_newest_age_seconds",
        "checked": "qd_backup_check_timestamp_seconds",
        "restored": "qd_backup_restore_test_last_success_timestamp_seconds",
        "restore_run": "qd_backup_restore_test_last_run_success",
    }
    for short, value in changed.items():
        if value is None:
            del figures[names[short]]
        else:
            figures[names[short]] = value
    return replace(HEALTHY, backup=figures)


def database(**changed: object) -> Figures:
    return replace(HEALTHY, database=replace(HEALTHY_DATABASE, **changed))  # type: ignore[arg-type]


def jobs(**ages: float | None) -> Figures:
    age = dict(HEALTHY_DATABASE.job_age)
    for job, value in ages.items():
        if value is None:
            del age[job]
        else:
            age[job] = value
    return database(job_age=age)


def counters(**grown: float) -> Figures:
    names = {
        "failed": (rules.REQUESTS_FAILED, 300),
        "every": (rules.REQUESTS_ALL, 300),
        "cross": ("security:shop_not_member", 600),
        "sign_in": ("security:bad_sign_in", 600),
        "webhook": ("security:bad_webhook_secret", 600),
        "second_factor": ("security:bad_second_factor", 900),
        "support": ("security:support_access_opened", 600),
        "no_support": ("security:admin_without_support_access", 600),
        "owner": ("security:owner_reassigned", 600),
    }
    return replace(HEALTHY, increases=QUIET_COUNTERS | {names[short]: value for short, value in grown.items()})


# Every condition: its key, figures under which it holds, and figures just short of it under which it
# does not. The second column changes one thing in a healthy round; the third is the nearest healthy case.
CONDITIONS: list[tuple[str, Figures, Figures]] = [
    ("BackupFailed:full", backup(full_run=0.0), backup(full_run=1.0)),
    ("BackupFailed:diff", backup(diff_run=0.0), backup(diff_run=None)),  # never run yet: nothing failed
    ("BackupMissing", backup(full=T - 93601, diff=T - 93601), backup(full=T - 93599, diff=T - 93599)),
    ("BackupMissing", backup(full=T - 691201), backup(full=T - 691199)),  # a fresh diff does not replace a full
    (
        "BackupMissing",
        backup(full=None, diff=None),
        backup(full=T - 3600, diff=None),
    ),  # no figure at all / a fresh full is enough
    ("BackupMissing", backup(full=0.0), backup(full=T - 10)),  # "0" is how the check writes "none"
    ("WalArchiveStale", backup(wal=301.0), backup(wal=300.0)),
    ("WalArchiveStale", backup(wal=-1.0), backup(wal=0.0)),  # -1: no segment in the repository
    ("WalArchiveStale", backup(wal=None), backup(wal=12.0)),
    ("WalArchiveStale", backup(checked=T - 961), backup(checked=T - 959)),  # the check itself stopped
    ("WalArchiveStale", backup(checked=None), backup(checked=T - 1)),
    ("RestoreTestNotPassed", backup(restored=T - 691201), backup(restored=T - 691199)),
    ("RestoreTestNotPassed", backup(restored=None), backup(restored=T - 5)),
    ("RestoreTestNotPassed", backup(restored=0.0), backup(restored=T - 5)),  # "0": it never passed
    ("RestoreTestFailed", backup(restore_run=0.0), backup(restore_run=None)),
    (
        "FilesCopyStale",
        replace(HEALTHY, files={"sync.last-success": T - 1021}),
        replace(HEALTHY, files={"sync.last-success": T - 1019}),
    ),
    ("FilesCopyStale", replace(HEALTHY, files={}), HEALTHY),  # never copied
    (
        "DiskAlmostFull:/var/lib/qarz/files",
        replace(HEALTHY, disk_used={"/var/lib/qarz/files": 0.81}),
        replace(HEALTHY, disk_used={"/var/lib/qarz/files": 0.80}),
    ),
    (
        "OutboxOld:telegram",
        database(outbox_oldest_due={"telegram": 601.0}),
        database(outbox_oldest_due={"telegram": 600.0}),
    ),
    ("OutboxOld:sms", database(outbox_oldest_due={"sms": 601.0}), database(outbox_oldest_due={"telegram": 601.0})),
    ("RemindersNotRunning", jobs(reminders=3901.0), jobs(reminders=3900.0)),
    # Outside 08:00 to 20:00 Tashkent nothing is due, however long ago the last run was.
    ("RemindersNotRunning", jobs(reminders=40_000.0), replace(jobs(reminders=40_000.0), now=NOW.replace(hour=16))),
    ("JobNotRunning:erasure", jobs(erasure=3901.0), jobs(erasure=3900.0)),
    ("JobNotRunning:sign_in_cleanup", jobs(sign_in_cleanup=3901.0), jobs(sign_in_cleanup=3900.0)),
    ("JobNotRunning:receipts", jobs(receipts=3901.0), jobs(receipts=3900.0)),
    ("JobNotRunning:subscriptions", jobs(subscriptions=93601.0), jobs(subscriptions=93600.0)),
    ("JobNotRunning:ledger_check", jobs(ledger_check=93601.0), jobs(ledger_check=93600.0)),
    ("JobNotRunning:stock_check", jobs(stock_check=93601.0), jobs(stock_check=93600.0)),
    ("JobNotRunning:measure_week", jobs(measure_week=691201.0), jobs(measure_week=691200.0)),
    # A job that never finished a period: late once the service is older than the job's limit.
    (
        "JobNotRunning:erasure",
        jobs(erasure=None),
        replace(jobs(erasure=None), database=replace(jobs(erasure=None).database, service_age=60.0)),
    ),  # type: ignore[arg-type]
    ("SmsRefused", database(sms_failed_last_hour=1), database(sms_failed_last_hour=0)),
    ("SmsNotGoingOut", database(sms_retrying=1), database(sms_retrying=0)),
    ("ReceiptsWaiting", database(receipt_waiting=86401.0), database(receipt_waiting=86400.0)),
    ("LedgerMismatch", database(ledger_mismatches=1.0), database(ledger_mismatches=None)),  # never checked yet
    # Each of the stock's two kept figures by itself; one that was never checked (the stock is off) is quiet.
    (
        "StockMismatch:stock_level",
        database(stock_mismatches={"stock_level": 1.0, "supplier_balance": 0.0}),
        database(stock_mismatches={"stock_level": 0.0, "supplier_balance": 0.0}),
    ),
    (
        "StockMismatch:supplier_balance",
        database(stock_mismatches={"stock_level": 0.0, "supplier_balance": 2.0}),
        database(stock_mismatches={}),
    ),
    ("ApiDown", replace(HEALTHY, api_healthy=False), HEALTHY),
    ("MetricsMissing", replace(HEALTHY, metrics_readable=False, increases=None), HEALTHY),
    ("ErrorRateHigh", counters(failed=11.0, every=500.0), counters(failed=10.0, every=500.0)),  # 2.2% / 2.0%
    # A lone failure among next to no requests: the share is taken of at least 0.3 requests (0.001 a second).
    ("ErrorRateHigh", counters(failed=1.0, every=1.0), counters(failed=0.0, every=0.0)),
    ("CrossTenantAttempt", counters(cross=1.0), counters(cross=0.0)),
    ("InvalidSignaturesRepeated:bad_sign_in", counters(sign_in=11.0), counters(sign_in=10.0)),
    ("InvalidSignaturesRepeated:bad_webhook_secret", counters(webhook=11.0), counters(sign_in=11.0)),
    ("AdminSecondFactorRepeated", counters(second_factor=6.0), counters(second_factor=5.0)),
    ("SupportAccessOpened", counters(support=1.0), counters(support=0.0)),
    ("AdminWithoutSupportAccess", counters(no_support=1.0), counters(no_support=0.0)),
    ("ShopOwnerReassigned", counters(owner=1.0), counters(owner=0.0)),
    ("TelegramRefusesBot", replace(HEALTHY, telegram="refused"), replace(HEALTHY, telegram="unreachable")),
    ("TelegramUnreachable", replace(HEALTHY, telegram="unreachable"), replace(HEALTHY, telegram="refused")),
    ("DispatcherFailing", replace(HEALTHY, dispatch_idle=301.0), replace(HEALTHY, dispatch_idle=300.0)),
]


def holding(figures: Figures) -> set[str]:
    return {key for key, finding in rules.evaluate(figures).items() if finding.holds}


def test_a_healthy_round_holds_nothing() -> None:
    found = rules.evaluate(HEALTHY)
    assert holding(HEALTHY) == set()
    # And it was all judged: a condition that is never looked at would pass this test too.
    assert {rules.rule_of(key).name for key in found} == set(rules.RULES)


@pytest.mark.parametrize(("key", "firing", "quiet"), CONDITIONS, ids=[f"{i}-{c[0]}" for i, c in enumerate(CONDITIONS)])
def test_each_condition_holds_when_it_should(key: str, firing: Figures, quiet: Figures) -> None:
    assert rules.evaluate(firing)[key].holds is True


@pytest.mark.parametrize(("key", "firing", "quiet"), CONDITIONS, ids=[f"{i}-{c[0]}" for i, c in enumerate(CONDITIONS)])
def test_each_condition_does_not_hold_just_short_of_it(key: str, firing: Figures, quiet: Figures) -> None:
    assert rules.evaluate(quiet)[key].holds is False


def test_a_stock_figure_never_checked_is_judged_and_quiet_and_no_other_label_is_invented() -> None:
    found = rules.evaluate(HEALTHY)
    assert {key for key in found if key.startswith("StockMismatch")} == {
        "StockMismatch:stock_level",
        "StockMismatch:supplier_balance",
    }
    assert not found["StockMismatch:stock_level"].holds
    assert found["StockMismatch:stock_level"].value is None
    # A sample of a series the rules do not know changes nothing: only the two labels are read.
    assert holding(database(stock_mismatches={"something_else": 9.0})) == set()


def test_every_rule_has_a_firing_and_a_quiet_case() -> None:
    assert {rules.rule_of(key).name for key, _, _ in CONDITIONS} == set(rules.RULES)


def test_one_thing_wrong_fires_one_condition() -> None:
    """A condition must not hold because another does: each case of the table changes one thing."""
    for key, held in [(key, holding(firing)) for key, firing, _ in CONDITIONS]:
        assert key in held
        assert len(held) == 1, (key, held)


def test_what_was_not_read_is_not_judged() -> None:
    nothing = Figures(now=NOW, configured=EVERYTHING)
    assert rules.evaluate(nothing) == {}
    assert rules.sources_read(nothing) == frozenset()
    assert rules.sources_read(HEALTHY) == EVERYTHING
    only_database = Figures(now=NOW, configured=EVERYTHING, database=HEALTHY_DATABASE)
    assert {rules.rule_of(key).source for key in rules.evaluate(only_database)} == {rules.DATABASE}


def test_counters_with_too_few_samples_are_not_judged() -> None:
    found = rules.evaluate(replace(HEALTHY, increases={}))
    assert "ErrorRateHigh" not in found and "CrossTenantAttempt" not in found
    assert found["MetricsMissing"].holds is False


# --- the figures ---------------------------------------------------------------------------------------


def test_the_figures_of_the_backup_jobs_are_read_as_written() -> None:
    text = (
        "# HELP qd_backup_last_success_timestamp_seconds When the newest backup of this type ended.\n"
        "# TYPE qd_backup_last_success_timestamp_seconds gauge\n"
        'qd_backup_last_success_timestamp_seconds{type="full"} 1790000000\n'
        "qd_wal_archive_newest_age_seconds -1\n"
        "\n"
        "not a figure\n"
        "qd_backup_last_run_duration_seconds 12.50\n"
    )
    assert rules.parse_figures(text) == {
        'qd_backup_last_success_timestamp_seconds{type="full"}': 1790000000.0,
        "qd_wal_archive_newest_age_seconds": -1.0,
        "qd_backup_last_run_duration_seconds": 12.5,
    }


def test_the_apis_counters_are_summed_by_what_the_rules_read() -> None:
    metrics = rules.parse_figures(
        'qd_requests_total{method="GET",route="/api/v1/me",status="200"} 90\n'
        'qd_requests_total{method="POST",route="/api/v1/shops/{shop_id}/customers",status="500"} 3\n'
        'qd_requests_total{method="GET",route="/healthz",status="503"} 2\n'
        'qd_requests_total{method="GET",route="unmatched",status="404"} 5\n'
        'qd_request_duration_ms_bucket{method="GET",route="/api/v1/me",le="25"} 80\n'
        'qd_security_events_total{kind="shop_not_member"} 4\n'
        'qd_security_events_total{kind="bad_sign_in"} 0\n'
    )
    assert rules.counter_series(metrics) == {
        rules.REQUESTS_ALL: 100.0,
        rules.REQUESTS_FAILED: 5.0,
        "security:shop_not_member": 4.0,
        "security:bad_sign_in": 0.0,
    }


def at(seconds_ago: float, value: float) -> tuple[datetime, float]:
    return NOW - timedelta(seconds=seconds_ago), value


def test_a_counter_grew_by_the_difference_within_the_window() -> None:
    samples = [at(900, 1.0), at(540, 4.0), at(300, 6.0), at(60, 9.0), at(0, 10.0)]
    assert rules.increase(samples, NOW, 600) == 6.0  # since the sample of 540 seconds ago
    assert rules.increase(samples, NOW, 300) == 4.0
    assert rules.increase(samples, NOW, 60) == 1.0


def test_a_counter_that_fell_was_started_again() -> None:
    """The API restarted: its counters begin at zero, and what they show since is growth."""
    assert rules.increase([at(120, 50.0), at(60, 2.0), at(0, 3.0)], NOW, 600) == 3.0


def test_one_sample_is_nothing_to_compare() -> None:
    assert rules.increase([at(0, 7.0)], NOW, 600) is None
    assert rules.increase([], NOW, 600) is None
    assert rules.increase([at(5000, 1.0), at(0, 7.0)], NOW, 600) is None  # the older one is outside the window


def test_a_figure_is_shown_short() -> None:
    assert rules.shown("BackupMissing", 26 * 3600 + 5) == "26h"
    assert rules.shown("BackupMissing", 9 * 86400) == "9d"
    assert rules.shown("OutboxOld:telegram", 720) == "12m"
    assert rules.shown("WalArchiveStale", 95) == "95s"
    assert rules.shown("DiskAlmostFull:/var/lib/qarz/files", 0.874) == "87%"
    assert rules.shown("SmsRefused", 3.0) == "3"
    assert rules.shown("ApiDown", 1.0) == ""  # a rule without a figure
    assert rules.shown("BackupMissing", None) == ""


@pytest.mark.parametrize("label", ["Ali Valiyev", "+998901234567", "a:b", "x" * 61, "do'kon", "45 000", "a\nb"])
def test_a_label_that_is_not_a_plain_identifier_is_refused(label: str) -> None:
    """A key is written into messages; nothing a person typed may become one."""
    with pytest.raises(ValueError, match="label"):
        rules.key_of("OutboxOld", label)


def test_a_key_of_an_unknown_rule_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown rule"):
        rules.key_of("ShopNameHere")
    assert rules.key_of("OutboxOld", "telegram") == "OutboxOld:telegram"
    assert rules.key_of("DiskAlmostFull", "/var/lib/qarz/files") == "DiskAlmostFull:/var/lib/qarz/files"
    assert rules.label_of("DiskAlmostFull:/var/lib/qarz/files") == "/var/lib/qarz/files"
    assert rules.label_of("BackupMissing") == ""


# --- one alert through time ----------------------------------------------------------------------------

HOLDS, GONE = Finding(True, 700.0), Finding(False, 3.0)
KEY = "OutboxOld:telegram"  # must hold two minutes before it fires


def later(**delta: float) -> datetime:
    return NOW + timedelta(**delta)


def test_a_condition_fires_only_after_it_has_held_for_the_rules_time() -> None:
    first = rules.advance(None, HOLDS, KEY, NOW)
    assert first == Alert(key=KEY, since=NOW, firing_since=None, value=700.0)
    assert rules.due(first, NOW) is None
    still = rules.advance(first, HOLDS, KEY, later(seconds=119))
    assert still is not None and still.firing_since is None and rules.due(still, later(seconds=119)) is None
    fired = rules.advance(still, HOLDS, KEY, later(seconds=120))
    assert fired is not None and fired.firing_since == later(seconds=120) and fired.since == NOW
    assert rules.due(fired, later(seconds=120)) == rules.FIRING


def test_a_rule_without_a_waiting_time_fires_at_once() -> None:
    fired = rules.advance(None, Finding(True, 2.0), "SmsRefused", NOW)
    assert fired is not None and fired.firing_since == NOW


def test_a_condition_that_stops_before_it_fired_leaves_nothing() -> None:
    first = rules.advance(None, HOLDS, KEY, NOW)
    assert rules.advance(first, GONE, KEY, later(seconds=60)) is None
    assert rules.advance(None, GONE, KEY, NOW) is None


def firing_alert() -> Alert:
    return Alert(key=KEY, since=NOW, firing_since=later(seconds=120), value=700.0)


def test_a_firing_alert_is_told_once_then_reminded_after_the_interval_and_not_before() -> None:
    alert = firing_alert()
    told_at = later(seconds=120)
    told = rules.delivered(alert, rules.FIRING, told_at)
    assert told is not None and told.notified_at == told_at and told.last_outcome == "sent"
    assert rules.due(told, told_at + timedelta(minutes=1)) is None
    assert rules.due(told, told_at + rules.REPEAT_AFTER - timedelta(seconds=1)) is None
    assert rules.due(told, told_at + rules.REPEAT_AFTER) == rules.REMINDER
    reminded = rules.delivered(told, rules.REMINDER, told_at + rules.REPEAT_AFTER)
    assert reminded is not None and rules.due(reminded, told_at + rules.REPEAT_AFTER + timedelta(minutes=1)) is None


def test_a_firing_alert_that_stops_is_owed_its_resolved_and_then_forgotten() -> None:
    told = rules.delivered(firing_alert(), rules.FIRING, later(seconds=120))
    stopped = rules.advance(told, GONE, KEY, later(minutes=30))
    assert stopped is not None and stopped.resolved_at == later(minutes=30)
    assert rules.due(stopped, later(minutes=30)) == rules.RESOLVED
    # Still stopped at the next round: the moment it stopped is kept.
    again = rules.advance(stopped, GONE, KEY, later(minutes=31))
    assert again == stopped
    assert rules.delivered(stopped, rules.RESOLVED, later(minutes=31)) is None


def test_a_condition_that_comes_back_before_its_resolved_was_said_is_the_same_alert() -> None:
    told = rules.delivered(firing_alert(), rules.FIRING, later(seconds=120))
    stopped = rules.advance(told, GONE, KEY, later(minutes=30))
    back = rules.advance(stopped, HOLDS, KEY, later(minutes=31))
    assert back is not None and back.resolved_at is None and back.firing_since == later(seconds=120)
    assert rules.due(back, later(minutes=31)) is None  # told already, and the interval has not passed


def test_a_finding_that_is_missing_changes_nothing() -> None:
    alert = firing_alert()
    assert rules.advance(alert, None, KEY, later(hours=5)) == alert
    assert rules.advance(None, None, KEY, NOW) is None


def test_an_alert_that_could_not_be_delivered_stays_owed() -> None:
    alert = firing_alert()
    failed = rules.not_delivered(alert, rules.FIRING, "unreachable", later(minutes=3))
    assert failed is not None and failed.notified_at is None
    assert (failed.attempts, failed.last_outcome, failed.last_attempt_at) == (1, "unreachable", later(minutes=3))
    assert rules.due(failed, later(minutes=4)) == rules.FIRING
    twice = rules.not_delivered(failed, rules.FIRING, "unreachable", later(minutes=4))
    assert twice is not None and twice.attempts == 2
    told = rules.delivered(twice, rules.FIRING, later(minutes=5))
    assert told is not None and (told.attempts, told.last_outcome) == (0, "sent")


def test_with_no_chat_configured_a_firing_alert_stays_and_a_resolved_one_goes() -> None:
    alert = firing_alert()
    kept = rules.not_delivered(alert, rules.FIRING, "unconfigured", later(minutes=3))
    assert kept is not None and kept.notified_at is None and kept.attempts == 0
    assert kept.last_outcome == "unconfigured"
    stopped = replace(alert, resolved_at=later(minutes=9))
    assert rules.not_delivered(stopped, rules.RESOLVED, "unconfigured", later(minutes=9)) is None


def test_an_undelivered_resolved_is_tried_for_a_day_and_then_dropped() -> None:
    stopped = replace(firing_alert(), resolved_at=later(minutes=9))
    soon = later(minutes=9) + rules.GIVE_UP_RESOLVED_AFTER - timedelta(seconds=1)
    assert rules.not_delivered(stopped, rules.RESOLVED, "unreachable", soon) is not None
    assert rules.not_delivered(stopped, rules.RESOLVED, "unreachable", soon + timedelta(seconds=1)) is None


def test_alerts_of_a_source_that_is_no_longer_watched_are_dropped() -> None:
    alerts = [firing_alert(), Alert(key="BackupMissing", since=NOW), Alert(key="NoSuchRuleAnyMore", since=NOW)]
    assert rules.dropped(alerts, frozenset({rules.DATABASE, rules.BACKUP})) == ["NoSuchRuleAnyMore"]
    assert rules.dropped(alerts, frozenset({rules.DATABASE})) == ["BackupMissing", "NoSuchRuleAnyMore"]


# --- the thresholds are those of deploy/monitoring/alerts.yml ---------------------------------------------


def yml_rules(text: str) -> dict[str, tuple[str, int]]:
    """Each rule of alerts.yml: its expression on one line, and its `for` in seconds (0 without one)."""
    found: dict[str, tuple[str, int]] = {}
    for block in re.split(r"^\s+- alert: ", text, flags=re.MULTILINE)[1:]:
        name = block.split("\n", 1)[0].strip()
        body = "\n".join(line for line in block.splitlines()[1:] if not line.strip().startswith("#"))
        expression = re.search(r"expr:\s*\|?\s*\n?(.*?)\n\s+(?:for|labels):", body, flags=re.DOTALL)
        assert expression is not None, name
        wait = re.search(r"^\s+for: (\d+)m$", body, flags=re.MULTILINE)
        found[name] = (" ".join(expression.group(1).split()), int(wait.group(1)) * 60 if wait else 0)
    return found


def expected_expressions() -> dict[str, list[str]]:
    """For each rule this watch mirrors: what its expression in alerts.yml must contain, written from the
    thresholds of qarz.domain.ops_alerts. A figure changed on either side alone no longer matches."""
    hours = rules.REMINDER_HOURS_UTC
    expected = {
        "ErrorRateHigh": [
            f"[{rules.ERROR_WINDOW_SECONDS // 60}m]",
            f"{rules.ERROR_RATE_FLOOR}) > {rules.ERROR_SHARE}",
        ],
        "OutboxOld": [f"qd_outbox_oldest_due_seconds > {rules.OUTBOX_OLD_SECONDS}"],
        "RemindersNotRunning": [
            f'qd_job_last_finished_seconds{{job="reminders"}} > {rules.REMINDERS_LATE_SECONDS}',
            f"hour() >= {hours[0]} and hour() < {hours[1]}",
        ],
        "SmsRefused": ['delta(qd_sms_messages_last_day{status="failed"}[1h]) > 0'],
        "SmsNotGoingOut": ['qd_sms_messages_last_day{status="retrying"} > 0'],
        "ReceiptsWaiting": [f"qd_receipts_oldest_waiting_seconds > {rules.RECEIPT_WAITING_SECONDS}"],
        "MetricsMissing": ["absent(qd_requests_total)"],
        "BackupFailed": ["qd_backup_last_run_success == 0"],
        "BackupMissing": [
            f"max(qd_backup_last_success_timestamp_seconds) > {rules.BACKUP_MAX_AGE_SECONDS}",
            f'qd_backup_last_success_timestamp_seconds{{type="full"}} > {rules.FULL_BACKUP_MAX_AGE_SECONDS}',
            "absent(qd_backup_last_success_timestamp_seconds)",
        ],
        "WalArchiveStale": [
            f"qd_wal_archive_newest_age_seconds > {rules.WAL_MAX_AGE_SECONDS}",
            "qd_wal_archive_newest_age_seconds < 0",
            "absent(qd_wal_archive_newest_age_seconds)",
        ],
        "RestoreTestNotPassed": [
            f"qd_backup_restore_test_last_success_timestamp_seconds > {rules.RESTORE_TEST_MAX_AGE_SECONDS}",
            "absent(qd_backup_restore_test_last_success_timestamp_seconds)",
        ],
    }
    for name, (kinds, window, more_than) in rules.SECURITY_RULES.items():
        selector = f'kind="{kinds[0]}"' if len(kinds) == 1 else f'kind=~"{"|".join(kinds)}"'
        expected[name] = [f"increase(qd_security_events_total{{{selector}}}[{window // 60}m]) > {more_than}"]
    return expected


def says(expression: str, part: str) -> bool:
    """The expression holds this part, and the part's last number ends there: "> 5" is not in "> 50"."""
    return re.search(re.escape(part) + r"(?![\d.])", expression) is not None


def drift(yml_text: str) -> list[str]:
    """Where alerts.yml and the watch's thresholds differ; empty when they agree."""
    in_yml = yml_rules(yml_text)
    expected = expected_expressions()
    problems = [
        f"{name}: neither mirrored nor named as not watched"
        for name in in_yml
        if name not in expected and name not in rules.NOT_WATCHED
    ]
    problems += [f"{name}: mirrored here, gone from alerts.yml" for name in expected if name not in in_yml]
    problems += [f"{name}: not watched here, gone from alerts.yml" for name in rules.NOT_WATCHED if name not in in_yml]
    for name, parts in expected.items():
        if name not in in_yml:
            continue
        expression, wait = in_yml[name]
        problems += [f"{name}: the expression has no {part!r}" for part in parts if not says(expression, part)]
        if wait != rules.RULES[name].for_seconds:
            problems.append(f"{name}: for {wait} s in alerts.yml, {rules.RULES[name].for_seconds} s here")
    return problems


def test_the_thresholds_are_those_of_the_alert_rules() -> None:
    assert drift(ALERTS_YML.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("old", "new", "noticed"),
    [
        ("qd_outbox_oldest_due_seconds > 600", "qd_outbox_oldest_due_seconds > 900", "OutboxOld"),
        ("qd_wal_archive_newest_age_seconds > 300", "qd_wal_archive_newest_age_seconds > 600", "WalArchiveStale"),
        (
            "max(qd_backup_last_success_timestamp_seconds) > 93600",
            "max(qd_backup_last_success_timestamp_seconds) > 180000",
            "BackupMissing",
        ),
        ('{kind="bad_second_factor"}[15m]) > 5', '{kind="bad_second_factor"}[15m]) > 50', "AdminSecondFactorRepeated"),
        ('{kind="shop_not_member"}[10m]', '{kind="shop_not_member"}[1h]', "CrossTenantAttempt"),
        ("0.001) > 0.02", "0.001) > 0.2", "ErrorRateHigh"),
        ("hour() >= 3 and hour() < 15", "hour() >= 0 and hour() < 24", "RemindersNotRunning"),
        (
            "        expr: qd_outbox_oldest_due_seconds > 600\n        for: 2m",
            "        expr: qd_outbox_oldest_due_seconds > 600\n        for: 20m",
            "OutboxOld",
        ),
        ("      - alert: ReceiptsWaiting", "      - alert: ReceiptsLate", "ReceiptsWaiting"),
        ("      - alert: ChatSlow", "      - alert: BotSlow", "BotSlow"),
    ],
)
def test_a_figure_changed_in_the_alert_rules_alone_is_noticed(old: str, new: str, noticed: str) -> None:
    text = ALERTS_YML.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    problems = drift(text.replace(old, new))
    assert problems and any(problem.startswith(f"{noticed}:") for problem in problems), problems


def test_a_threshold_changed_here_alone_is_noticed(monkeypatch: pytest.MonkeyPatch) -> None:
    text = ALERTS_YML.read_text(encoding="utf-8")
    monkeypatch.setattr(rules, "WAL_MAX_AGE_SECONDS", 900)
    assert [problem.split(":")[0] for problem in drift(text)] == ["WalArchiveStale"]
    monkeypatch.undo()
    monkeypatch.setitem(rules.RULES, "BackupMissing", replace(rules.RULES["BackupMissing"], for_seconds=0))
    assert [problem.split(":")[0] for problem in drift(text)] == ["BackupMissing"]


def test_every_rule_of_the_file_was_decided_on() -> None:
    in_yml = set(yml_rules(ALERTS_YML.read_text(encoding="utf-8")))
    assert in_yml == set(expected_expressions()) | set(rules.NOT_WATCHED)
    assert set(expected_expressions()) <= set(rules.RULES)
    assert not set(rules.NOT_WATCHED) & set(rules.RULES)


# --- the texts ---------------------------------------------------------------------------------------------


def test_every_rule_has_its_text_in_both_languages() -> None:
    for name in rules.RULES:
        assert UZ[f"ops_rule_{name}"].strip() and RU[f"ops_rule_{name}"].strip(), name
    spoken = {key.removeprefix("ops_rule_") for key in UZ if key.startswith("ops_rule_")}
    assert spoken == set(rules.RULES)


def test_no_rule_text_has_a_place_for_anything_to_be_put_in() -> None:
    """A rule's text is fixed words: there is nowhere in it for a name, a number or an amount."""
    for catalog in (UZ, RU):
        for key, text in catalog.items():
            if key.startswith("ops_rule_"):
                assert "{" not in text and "}" not in text, key
