"""The operations watch: which conditions are worth telling the operators about, and when to tell them.

There is no monitoring system on the single host (DEC-070); the founder chose that the service's own
worker watches a small set of conditions and writes to the operators' Telegram chat (DEC-078). This
module is the whole of the judgement, with no input or output of its own:

- `evaluate` turns one round's figures into findings: for each condition, does it hold now;
- `advance` moves one alert between "not yet", "firing" and "stopped", honouring the time a condition
  must hold before anybody is told (`Rule.for_seconds`);
- `due`, `delivered` and `not_delivered` say which message an alert is owed and what becomes of it.

**Every threshold is in this file.** Where a rule of `deploy/monitoring/alerts.yml` exists, the figure
here is that rule's, and `tests/test_ops_alert_rules.py` fails when the two differ. The rules of that
file this watch does not evaluate are named in `NOT_WATCHED`, each with its reason.

Nothing here knows a shop or a person. A key is a rule's name and, for some rules, a label that the code
itself supplies (a channel, a job, a backup type, a path inside the container); a value is a number.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

MINUTE, HOUR, DAY = 60, 3600, 86400

# --- thresholds mirrored from deploy/monitoring/alerts.yml ----------------------------------------------
ERROR_SHARE = 0.02  # ErrorRateHigh: more than 2% of answers are server errors ...
ERROR_WINDOW_SECONDS = 5 * MINUTE  # ... over five minutes
ERROR_RATE_FLOOR = 0.001  # requests a second below which the share is taken of this rate (clamp_min)
OUTBOX_OLD_SECONDS = 600  # OutboxOld: a due message has waited ten minutes
REMINDERS_LATE_SECONDS = 3900  # RemindersNotRunning: no reminder run for an hour and five minutes ...
REMINDER_HOURS_UTC = (3, 15)  # ... between 08:00 and 20:00 Tashkent time
RECEIPT_WAITING_SECONDS = DAY  # ReceiptsWaiting
BACKUP_MAX_AGE_SECONDS = 93600  # BackupMissing: no backup of any type for 26 hours
FULL_BACKUP_MAX_AGE_SECONDS = 691200  # BackupMissing: no full backup for 8 days
WAL_MAX_AGE_SECONDS = 300  # WalArchiveStale: the newest archived segment is older than 5 minutes
RESTORE_TEST_MAX_AGE_SECONDS = 691200  # RestoreTestNotPassed: 8 days
# Security events: the rule, the kinds it reads, the window, and "more than" how many.
SECURITY_RULES: dict[str, tuple[tuple[str, ...], int, int]] = {
    "CrossTenantAttempt": (("shop_not_member",), 10 * MINUTE, 0),
    "InvalidSignaturesRepeated": (("bad_sign_in", "bad_webhook_secret"), 10 * MINUTE, 10),
    "AdminSecondFactorRepeated": (("bad_second_factor",), 15 * MINUTE, 5),
    "SupportAccessOpened": (("support_access_opened",), 10 * MINUTE, 0),
    "AdminWithoutSupportAccess": (("admin_without_support_access",), 10 * MINUTE, 0),
    "ShopOwnerReassigned": (("owner_reassigned",), 10 * MINUTE, 0),
}

# --- thresholds of this watch alone (alerts.yml has no rule for them) -----------------------------------
# alerts.yml asks that the check of the repository be younger than 5 minutes, which fits a check that
# runs every minute (the two-server design). On the single host it runs every 300 seconds, so the same
# bound would fire between any two checks: here it is what the `backup` container's own health check
# allows, three intervals and a minute.
BACKUP_CHECK_MAX_AGE_SECONDS = 3 * 300 + 60
# As the `files-backup` container's health check: three intervals of five minutes, and two minutes.
FILES_COPY_MAX_AGE_SECONDS = 3 * 300 + 120
DISK_USED_SHARE = 0.80  # operations document, Monitoring: "Disk above 80%"
HOURLY_JOB_LATE_SECONDS = 3900
DAILY_JOB_LATE_SECONDS = 93600
WEEKLY_JOB_LATE_SECONDS = 691200
DISPATCH_IDLE_SECONDS = 300  # the dispatcher has not finished one round for five minutes

# A condition that stays is said again after this long, and not before.
REPEAT_AFTER = timedelta(hours=4)
# A "resolved" that could not be delivered is tried at every round for this long, then dropped.
GIVE_UP_RESOLVED_AFTER = timedelta(hours=24)

CHANNELS = ("telegram", "sms")
BACKUP_TYPES = ("full", "diff")
# The scheduled jobs (application/scheduler.py) and how long each may go without finishing a period.
# `reminders` has its own rule, mirrored from alerts.yml.
JOB_LIMITS: dict[str, int] = {
    "erasure": HOURLY_JOB_LATE_SECONDS,
    "sign_in_cleanup": HOURLY_JOB_LATE_SECONDS,
    "receipts": HOURLY_JOB_LATE_SECONDS,
    "subscriptions": DAILY_JOB_LATE_SECONDS,
    "ledger_check": DAILY_JOB_LATE_SECONDS,
    "measure_week": WEEKLY_JOB_LATE_SECONDS,
}
REMINDERS_JOB = "reminders"

# Series kept in `ops_sample`.
REQUESTS_ALL, REQUESTS_FAILED = "requests:all", "requests:5xx"
LEDGER_SERIES = "ledger_mismatches"
# Samples are kept this long: the longest window, and room for a late round.
SAMPLES_KEPT = timedelta(minutes=20)
# A sample a little older than the window still opens it: rounds are a minute apart, never exactly.
_WINDOW_SLACK_SECONDS = 30

# Where a figure comes from. A source that is not configured in a deployment is not watched there, and
# an alert of it that was firing is dropped; a source that could not be read this round changes nothing.
BACKUP, FILES, DISK, DATABASE, API, METRICS, TELEGRAM, WORKER = (
    "backup",
    "files",
    "disk",
    "database",
    "api",
    "metrics",
    "telegram",
    "worker",
)


@dataclass(frozen=True)
class Rule:
    name: str
    source: str
    for_seconds: int = 0
    # How the figure of a message is written: "age" (seconds), "share" (0 to 1), "count", or "" for none.
    unit: str = ""


RULES: dict[str, Rule] = {
    rule.name: rule
    for rule in (
        # --- mirrored from alerts.yml ---
        Rule("ErrorRateHigh", METRICS, 5 * MINUTE, "share"),
        Rule("OutboxOld", DATABASE, 2 * MINUTE, "age"),
        Rule("RemindersNotRunning", DATABASE, 5 * MINUTE, "age"),
        Rule("SmsRefused", DATABASE, 0, "count"),
        Rule("SmsNotGoingOut", DATABASE, 30 * MINUTE, "count"),
        Rule("ReceiptsWaiting", DATABASE, 10 * MINUTE, "age"),
        Rule("CrossTenantAttempt", METRICS, 0, "count"),
        Rule("InvalidSignaturesRepeated", METRICS, 0, "count"),
        Rule("AdminSecondFactorRepeated", METRICS, 0, "count"),
        Rule("SupportAccessOpened", METRICS, 0, "count"),
        Rule("AdminWithoutSupportAccess", METRICS, 0, "count"),
        Rule("ShopOwnerReassigned", METRICS, 0, "count"),
        Rule("MetricsMissing", METRICS, 3 * MINUTE),
        Rule("BackupFailed", BACKUP, 5 * MINUTE),
        Rule("BackupMissing", BACKUP, 10 * MINUTE, "age"),
        Rule("WalArchiveStale", BACKUP, 1 * MINUTE, "age"),
        Rule("RestoreTestNotPassed", BACKUP, 10 * MINUTE, "age"),
        # --- of this watch alone ---
        Rule("RestoreTestFailed", BACKUP, 5 * MINUTE),
        Rule("FilesCopyStale", FILES, 5 * MINUTE, "age"),
        Rule("DiskAlmostFull", DISK, 10 * MINUTE, "share"),
        Rule("JobNotRunning", DATABASE, 5 * MINUTE, "age"),
        Rule("LedgerMismatch", DATABASE, 0, "count"),
        Rule("ApiDown", API, 2 * MINUTE),
        Rule("TelegramRefusesBot", TELEGRAM, 0),
        Rule("TelegramUnreachable", TELEGRAM, 5 * MINUTE),
        Rule("DispatcherFailing", WORKER, 0, "age"),
    )
}

# Rules of alerts.yml that this watch does not evaluate, and why. The test that compares the two files
# fails for a rule that is neither in RULES nor here, so a rule added there must be decided on.
NOT_WATCHED: dict[str, str] = {
    "RecordingSlow": "a 95th percentile over a histogram; needs a monitoring system to be worth trusting",
    "ChatSlow": "a 95th percentile over a histogram; needs a monitoring system to be worth trusting",
    "OverviewSlow": "a 95th percentile over a histogram; needs a monitoring system to be worth trusting",
}

# The figures the backup jobs write (deploy/backup/scripts, deploy/production/single-host/scripts).
F_BACKUP_SUCCESS = 'qd_backup_last_success_timestamp_seconds{{type="{type}"}}'
F_BACKUP_RUN = 'qd_backup_last_run_success{{type="{type}"}}'
F_WAL_AGE = "qd_wal_archive_newest_age_seconds"
F_BACKUP_CHECK = "qd_backup_check_timestamp_seconds"
F_RESTORE_SUCCESS = "qd_backup_restore_test_last_success_timestamp_seconds"
F_RESTORE_RUN = "qd_backup_restore_test_last_run_success"
F_FILES_SUCCESS = "sync.last-success"

_LABEL = re.compile(r"[A-Za-z0-9_./-]{1,60}")
_STATUS = re.compile(r'status="(\d{3})"')
_KIND = re.compile(r'kind="([a-z_]+)"')


@dataclass(frozen=True)
class DatabaseFigures:
    """What one round reads from the database: ages and counts, nothing of a shop or a person."""

    outbox_oldest_due: Mapping[str, float]  # channel -> seconds the oldest due message has waited
    job_age: Mapping[str, float]  # job -> seconds since it last finished a period
    service_age: float | None  # seconds since any job first finished; None on a database that never ran one
    sms_retrying: int
    sms_failed_last_hour: int
    receipt_waiting: float | None  # seconds the oldest undecided receipt has waited; None when none waits
    ledger_mismatches: float | None  # the last daily count; None when the check has never run


@dataclass(frozen=True)
class Figures:
    """One round. A part that is None could not be read this round, or is not configured (`configured`)."""

    now: datetime
    configured: frozenset[str]
    backup: Mapping[str, float] | None = None
    files: Mapping[str, float] | None = None
    disk_used: Mapping[str, float] | None = None  # path -> share of the filesystem in use
    database: DatabaseFigures | None = None
    api_healthy: bool | None = None
    metrics_readable: bool | None = None
    # (series, window in seconds) -> by how much the counter grew; absent while there are too few samples
    increases: Mapping[tuple[str, int], float] | None = None
    telegram: str | None = None  # "ok", "refused" or "unreachable"
    dispatch_idle: float | None = None  # seconds since the dispatcher last finished a round


@dataclass(frozen=True)
class Finding:
    holds: bool
    value: float | None = None


@dataclass(frozen=True)
class Alert:
    """A row of `ops_alert`: a condition that holds, or one that stopped and has not been said yet."""

    key: str
    since: datetime
    firing_since: datetime | None = None
    value: float | None = None
    notified_at: datetime | None = None
    resolved_at: datetime | None = None
    attempts: int = 0
    last_attempt_at: datetime | None = None
    last_outcome: str | None = None


def rule_of(key: str) -> Rule:
    return RULES[key.split(":", 1)[0]]


def label_of(key: str) -> str:
    return key.split(":", 1)[1] if ":" in key else ""


def key_of(rule: str, label: str = "") -> str:
    """The key of an alert. Refuses a rule it does not know and a label that is not a plain identifier:
    a key is written into messages, so nothing a person typed may ever become one."""
    if rule not in RULES:
        raise ValueError("unknown rule")
    if not label:
        return rule
    if not _LABEL.fullmatch(label):
        raise ValueError("a label is letters, digits and . _ / - only")
    return f"{rule}:{label}"


# --- reading figures ---------------------------------------------------------------------------------------


def parse_figures(text: str) -> dict[str, float]:
    """Lines `name{labels} value` of the Prometheus text format; comments and anything else are skipped."""
    figures: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, raw = line.rpartition(" ")
        try:
            figures[name.strip()] = float(raw)
        except ValueError:
            continue
    return figures


def counter_series(metrics: Mapping[str, float]) -> dict[str, float]:
    """The counters of the API's /metrics this watch follows: every request, the failed ones, and each
    kind of security event. Routes and methods are summed away."""
    series = {REQUESTS_ALL: 0.0, REQUESTS_FAILED: 0.0}
    for name, value in metrics.items():
        if name.startswith("qd_requests_total{"):
            status = _STATUS.search(name)
            series[REQUESTS_ALL] += value
            if status is not None and status.group(1).startswith("5"):
                series[REQUESTS_FAILED] += value
        elif name.startswith("qd_security_events_total{"):
            kind = _KIND.search(name)
            if kind is not None:
                series[f"security:{kind.group(1)}"] = value
    return series


def increase(samples: Sequence[tuple[datetime, float]], now: datetime, window_seconds: int) -> float | None:
    """By how much a counter grew within the window. A counter that fell was started again (the API
    restarted): what it shows now is then growth. None with fewer than two samples: nothing to compare."""
    edge = now - timedelta(seconds=window_seconds + _WINDOW_SLACK_SECONDS)
    inside = sorted(sample for sample in samples if edge <= sample[0] <= now)
    if len(inside) < 2:
        return None
    grown = 0.0
    previous = inside[0][1]
    for _, value in inside[1:]:
        grown += value - previous if value >= previous else value
        previous = value
    return grown


def windows() -> list[tuple[str, int]]:
    """Every (series, window) `evaluate` asks for."""
    wanted = [(REQUESTS_ALL, ERROR_WINDOW_SECONDS), (REQUESTS_FAILED, ERROR_WINDOW_SECONDS)]
    for kinds, window, _ in SECURITY_RULES.values():
        wanted += [(f"security:{kind}", window) for kind in kinds]
    return wanted


# --- the conditions ----------------------------------------------------------------------------------------


def _age(now: datetime, moment: float | None) -> float | None:
    """Seconds since an epoch moment; None when there is none (absent, or 0 for "never")."""
    if moment is None or moment <= 0:
        return None
    return now.timestamp() - moment


def _backup(now: datetime, figures: Mapping[str, float]) -> dict[str, Finding]:
    found: dict[str, Finding] = {}
    for kind in BACKUP_TYPES:
        # Absent until the first run of the type: nothing failed yet.
        found[key_of("BackupFailed", kind)] = Finding(figures.get(F_BACKUP_RUN.format(type=kind)) == 0)
    full = _age(now, figures.get(F_BACKUP_SUCCESS.format(type="full")))
    diff = _age(now, figures.get(F_BACKUP_SUCCESS.format(type="diff")))
    newest = min((age for age in (full, diff) if age is not None), default=None)
    found["BackupMissing"] = Finding(
        newest is None or newest > BACKUP_MAX_AGE_SECONDS or full is None or full > FULL_BACKUP_MAX_AGE_SECONDS,
        newest,
    )
    wal = figures.get(F_WAL_AGE)
    checked = _age(now, figures.get(F_BACKUP_CHECK))
    found["WalArchiveStale"] = Finding(
        wal is None
        or wal < 0
        or wal > WAL_MAX_AGE_SECONDS
        or checked is None
        or checked > BACKUP_CHECK_MAX_AGE_SECONDS,
        wal if wal is not None and wal >= 0 else None,
    )
    restored = _age(now, figures.get(F_RESTORE_SUCCESS))
    found["RestoreTestNotPassed"] = Finding(restored is None or restored > RESTORE_TEST_MAX_AGE_SECONDS, restored)
    found["RestoreTestFailed"] = Finding(figures.get(F_RESTORE_RUN) == 0)
    return found


def _database(now: datetime, figures: DatabaseFigures) -> dict[str, Finding]:
    found: dict[str, Finding] = {}
    for channel in CHANNELS:
        waited = figures.outbox_oldest_due.get(channel, 0.0)
        found[key_of("OutboxOld", channel)] = Finding(waited > OUTBOX_OLD_SECONDS, waited)

    def late(job: str, limit: int) -> Finding:
        # A job that never finished a period is late once the service itself is older than the limit: a
        # database a minute old owes nobody a daily job yet.
        age = figures.job_age.get(job, figures.service_age)
        return Finding(age is not None and age > limit, age)

    sending_hours = REMINDER_HOURS_UTC[0] <= now.astimezone(UTC).hour < REMINDER_HOURS_UTC[1]
    reminders = late(REMINDERS_JOB, REMINDERS_LATE_SECONDS)
    found["RemindersNotRunning"] = Finding(reminders.holds and sending_hours, reminders.value)
    for job, limit in JOB_LIMITS.items():
        found[key_of("JobNotRunning", job)] = late(job, limit)
    found["SmsRefused"] = Finding(figures.sms_failed_last_hour > 0, figures.sms_failed_last_hour)
    found["SmsNotGoingOut"] = Finding(figures.sms_retrying > 0, figures.sms_retrying)
    waiting = figures.receipt_waiting
    found["ReceiptsWaiting"] = Finding(waiting is not None and waiting > RECEIPT_WAITING_SECONDS, waiting)
    wrong = figures.ledger_mismatches
    found["LedgerMismatch"] = Finding(wrong is not None and wrong > 0, wrong)
    return found


def _metrics(readable: bool, increases: Mapping[tuple[str, int], float]) -> dict[str, Finding]:
    found = {"MetricsMissing": Finding(not readable)}
    failed = increases.get((REQUESTS_FAILED, ERROR_WINDOW_SECONDS))
    every = increases.get((REQUESTS_ALL, ERROR_WINDOW_SECONDS))
    if failed is not None and every is not None:
        share = failed / max(every, ERROR_RATE_FLOOR * ERROR_WINDOW_SECONDS)
        found["ErrorRateHigh"] = Finding(share > ERROR_SHARE, min(share, 1.0))
    for name, (kinds, window, more_than) in SECURITY_RULES.items():
        for kind in kinds:
            grown = increases.get((f"security:{kind}", window))
            if grown is not None:
                found[key_of(name, kind if len(kinds) > 1 else "")] = Finding(grown > more_than, grown)
    return found


def evaluate(figures: Figures) -> dict[str, Finding]:
    """Every condition that could be judged this round: its key, whether it holds, and its figure."""
    found: dict[str, Finding] = {}
    if figures.backup is not None:
        found |= _backup(figures.now, figures.backup)
    if figures.files is not None:
        copied = _age(figures.now, figures.files.get(F_FILES_SUCCESS))
        found["FilesCopyStale"] = Finding(copied is None or copied > FILES_COPY_MAX_AGE_SECONDS, copied)
    if figures.disk_used is not None:
        for path, share in figures.disk_used.items():
            found[key_of("DiskAlmostFull", path)] = Finding(share > DISK_USED_SHARE, share)
    if figures.database is not None:
        found |= _database(figures.now, figures.database)
    if figures.api_healthy is not None:
        found["ApiDown"] = Finding(not figures.api_healthy)
    if figures.metrics_readable is not None:
        found |= _metrics(figures.metrics_readable, figures.increases or {})
    if figures.telegram is not None:
        found["TelegramRefusesBot"] = Finding(figures.telegram == "refused")
        found["TelegramUnreachable"] = Finding(figures.telegram == "unreachable")
    if figures.dispatch_idle is not None:
        found["DispatcherFailing"] = Finding(figures.dispatch_idle > DISPATCH_IDLE_SECONDS, figures.dispatch_idle)
    return found


def sources_read(figures: Figures) -> frozenset[str]:
    """The sources this round did read. An alert of one of them without a finding no longer holds."""
    parts = {
        BACKUP: figures.backup,
        FILES: figures.files,
        DISK: figures.disk_used,
        DATABASE: figures.database,
        API: figures.api_healthy,
        METRICS: figures.metrics_readable,
        TELEGRAM: figures.telegram,
        WORKER: figures.dispatch_idle,
    }
    return frozenset(source for source, part in parts.items() if part is not None)


# --- one alert through time --------------------------------------------------------------------------------


def advance(current: Alert | None, finding: Finding | None, key: str, now: datetime) -> Alert | None:
    """The alert after this round's finding. None: there is nothing to remember about the key.

    `finding` None means the condition was not judged this round, and changes nothing.
    """
    if finding is None:
        return current
    rule = rule_of(key)
    if finding.holds:
        if current is None:
            current = Alert(key=key, since=now)
        firing_since = current.firing_since
        if firing_since is None and now - current.since >= timedelta(seconds=rule.for_seconds):
            firing_since = now
        # A condition that came back before its "resolved" was said goes on as the same alert.
        return replace(current, firing_since=firing_since, value=finding.value, resolved_at=None)
    if current is None or current.firing_since is None:
        return None  # it never fired: nobody was told, so there is nothing to take back
    if current.resolved_at is None:
        return replace(current, resolved_at=now)
    return current


FIRING, REMINDER, RESOLVED = "firing", "reminder", "resolved"


def due(alert: Alert, now: datetime, repeat_after: timedelta = REPEAT_AFTER) -> str | None:
    """Which message the alert is owed now: its first, a reminder, the "resolved", or none."""
    if alert.firing_since is None:
        return None
    if alert.resolved_at is not None:
        return RESOLVED
    if alert.notified_at is None:
        return FIRING
    if now - alert.notified_at >= repeat_after:
        return REMINDER
    return None


def delivered(alert: Alert, message: str, now: datetime) -> Alert | None:
    """The alert after its message was accepted. A resolved alert is forgotten."""
    if message == RESOLVED:
        return None
    return replace(alert, notified_at=now, attempts=0, last_attempt_at=now, last_outcome="sent")


def not_delivered(alert: Alert, message: str, outcome: str, now: datetime) -> Alert | None:
    """The alert after its message could not be sent: it stays owed and is tried at the next round.

    Nothing is queued: the row itself is what is owed, one for each condition. With no chat configured a
    "resolved" has nobody to reach and is forgotten at once; otherwise it is dropped after a day.
    """
    if message == RESOLVED and (
        outcome == "unconfigured"
        or (alert.resolved_at is not None and now - alert.resolved_at >= GIVE_UP_RESOLVED_AFTER)
    ):
        return None
    attempts = alert.attempts if outcome == "unconfigured" else alert.attempts + 1
    return replace(alert, attempts=attempts, last_attempt_at=now, last_outcome=outcome)


def dropped(alerts: Iterable[Alert], configured: frozenset[str]) -> list[str]:
    """Keys of alerts whose source this deployment no longer watches (or whose rule no longer exists)."""
    gone = []
    for alert in alerts:
        rule = RULES.get(alert.key.split(":", 1)[0])
        if rule is None or rule.source not in configured:
            gone.append(alert.key)
    return gone


def shown(key: str, value: float | None) -> str:
    """The figure of an alert as a message shows it: "26h", "12m", "87%", "3". Empty when there is none."""
    unit = rule_of(key).unit
    if value is None or not unit:
        return ""
    if unit == "share":
        return f"{round(value * 100)}%"
    if unit == "count":
        return str(round(value))
    seconds = max(0, round(value))
    if seconds >= 2 * DAY:
        return f"{seconds // DAY}d"
    if seconds >= 2 * HOUR:
        return f"{seconds // HOUR}h"
    if seconds >= 2 * MINUTE:
        return f"{seconds // MINUTE}m"
    return f"{seconds}s"
