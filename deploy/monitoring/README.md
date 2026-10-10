# Monitoring

What the application gives a monitoring system, and what is still missing. Nothing here has been loaded
into a real monitoring system: there are no servers yet. Launch criterion 9 ("alerts triggered and
received") is open until the founder has run the test alert on the real machine and seen it arrive.

> **On the single host (the current deployment, DEC-070) there is no monitoring system, and the worker
> stands in for one** (the founder's decision of 2026-10-09, DEC-078). The rules of `alerts.yml` are
> loaded nowhere; the worker evaluates most of them itself every minute, with the same thresholds, and
> writes to the operators' Telegram chat. See "The worker's watch" below, and
> `deploy/production/SINGLE-HOST.md`, "What is watched, and what is not", for what the founder
> configures, what it cannot see (anything, when the machine or the worker is down: Cloudflare's tunnel
> notification is for that), and what is still not watched. The table "What is missing" further down is
> written for the two-server design; its rows about replication and "both servers" have no meaning on
> one machine.

## The worker's watch (the single host)

`backend/src/qarz/domain/ops_alerts.py` holds every condition and every threshold; the worker
(`application/ops_watch.py`) runs a round a minute, keeps what is firing in the database (`ops_alert`),
and sends first notice, a reminder every four hours, and "resolved" straight through the bot to
`QD_ALERT_CHAT_IDS`. Prove delivery with `python -m qarz.interface.alert_test` (on the single host:
`single-host.sh alert-test`); what to do when an alert arrives is runbook 16.

**The thresholds are kept in one place and compared with this directory.** Where a rule of `alerts.yml`
is mirrored, `backend/tests/test_ops_alert_rules.py` builds the expression the rule must contain from
the constants of `ops_alerts.py` and fails when either side changes alone, when a `for:` differs, or
when a rule is added here that the watch neither mirrors nor names as not watched.

| Rule of `alerts.yml` | In the worker's watch | Where the worker reads it |
|---|---|---|
| `ErrorRateHigh` | Yes | The API's `/metrics`, with the metrics token: `qd_requests_total`, compared across rounds (samples in `ops_sample`) |
| `RecordingSlow`, `ChatSlow`, `OverviewSlow` | **No**: a percentile over a histogram is not something to approximate by hand | |
| `OutboxOld` | Yes, by channel | `outbox_message` |
| `RemindersNotRunning` | Yes | `job_run` |
| `SmsRefused`, `SmsNotGoingOut` | Yes. "Refused" is "an SMS ended as failed within the last hour", which is what the rule's `delta(...[1h]) > 0` says | `outbox_message` |
| `ReceiptsWaiting` | Yes | `oldest_waiting_receipt()` |
| `CrossTenantAttempt`, `InvalidSignaturesRepeated`, `AdminSecondFactorRepeated`, `SupportAccessOpened`, `AdminWithoutSupportAccess`, `ShopOwnerReassigned` | Yes | The API's `/metrics`: `qd_security_events_total`, compared across rounds |
| `MetricsMissing` | Yes, as "the worker cannot read `/metrics`" | The API's `/metrics` |
| (none: a rule of the watch alone) `AdminSecondFactorOff` | Yes: a standing warning while the API runs with `QD_ADMIN_SECOND_FACTOR=off` | The API's `/metrics`: the gauge `qd_admin_second_factor_off`, 1 while it is off; absent where the API serves no administrators |
| `BackupFailed`, `BackupMissing`, `RestoreTestNotPassed` | Yes | The figure files of the backup jobs, mounted read-only into the worker |
| `WalArchiveStale` | Yes, with one difference: the rule wants the check of the repository younger than 5 minutes, which fits a check every minute; the single host checks every 5 minutes, so the watch allows 16 (three intervals and a minute), as the `backup` container's own health check does. The age of the archive itself is the rule's 5 minutes | The same files |

Conditions the watch has and `alerts.yml` does not: `RestoreTestFailed`, `FilesCopyStale`,
`DiskAlmostFull` (above 80%, the operations document's figure), `JobNotRunning` for every other
scheduled job, `LedgerMismatch` (`open_debt_mismatch_count()`, once a day), `StockMismatch`
(`stock_level_mismatch_count()` and `supplier_balance_mismatch_count()`, once a day while `stock_on` is on), `ApiDown` (`/healthz` inside
the Compose network), `TelegramRefusesBot`, `TelegramUnreachable`, `DispatcherFailing`, and the database
being out of the worker's reach.

Limits that come from reading counters by hand: the counters are in the API's memory, so an API
restart starts them from zero (the watch reads a fall as a restart and loses nothing but what happened
in the last minute before it); a window is the samples of the last 5, 10 or 15 minutes taken a minute
apart, not a continuous rate; and the worker's own readings of `/healthz` and `/metrics` are requests
too (two a minute, answered 200), which the error rate counts.

## What exists

- **Logs.** The API and the worker write one JSON object a line to standard error: time, level, logger,
  event, and identifiers only (`request_id`, `method`, `route`, `status`, `ms`, `user_id`, `shop_id`,
  `update_id`, `job`, `period`, `channel`, `count`, `kind`). Any other field given to a log call is
  dropped, and an exception is logged by its type and where it happened, never by its text.
- **Request identifier.** Every answer carries `X-Request-Id`; a failure nobody handled is answered with
  a JSON body that repeats it. An identifier sent by the proxy (8 to 64 characters of `A-Z a-z 0-9 _ -`)
  is kept, so the proxy's log and the application's can be joined.
- **Metrics.** `GET /metrics` in the Prometheus text format, served only when `QD_METRICS_TOKEN` is set
  and only to a request that sends it as a bearer token. The proxy must not publish this path, and the one in
  `deploy/production/` answers 404 for it; a monitoring system reads it inside the compose network.
  - `qd_requests_total{method,route,status}` and `qd_request_duration_ms` (histogram) by route template.
  - `qd_security_events_total{kind}`: `shop_not_member`, `bad_sign_in`, `bad_webhook_secret`, `bad_second_factor`,
    `support_access_opened`, `admin_without_support_access`.
  - `qd_outbox_oldest_due_seconds{channel}`, `qd_job_last_finished_seconds{job}` and
    `qd_receipts_oldest_waiting_seconds{status="submitted"}`, read from the database when the metrics
    are read. The last is absent while no subscription receipt awaits a decision.
  - `qd_sms_messages_last_day{status}`, read from the database in the same way: the SMS of the last 24
    hours that were `sent` (accepted by the provider), `failed` (refused for good, or given up after a
    day of attempts) or are `retrying` (waiting after an attempt that did not succeed). Counts only. The
    worker has no metrics of its own; what it does shows here and in its log (`sms_sent`, `sms_rejected`,
    `sms_retry`, with `kind` and `status`). Rules `SmsRefused` and `SmsNotGoingOut`; runbook 12, "When SMS
    fail".
- **Alert rules.** `alerts.yml`, written against those metrics with the thresholds of the operations
  document. Its last group, `qarz-backup`, reads host-level figures that the backup scripts write, not
  the application's `/metrics` (see the table below and `deploy/backup/README.md`).

Counters live in the memory of the process that served the requests and start from zero when it starts.

## What is missing

| Signal in the operations document | State |
|---|---|
| External check of `/healthz` from outside both servers | Not set up; needs a service chosen by the founder |
| Webhook backlog reported by Telegram | Not collected (needs a call to Telegram's `getWebhookInfo`) |
| Replication lag | Not collected; it belongs to the database hosts, which do not exist |
| Log archive age, backup | Written by the scripts of `deploy/backup/` on the standby for node_exporter's textfile collector: `qd_wal_archive_newest_age_seconds`, `qd_backup_last_success_timestamp_seconds{type}`, `qd_backup_last_run_success{type}`, `qd_backup_restore_test_last_success_timestamp_seconds`, `qd_backup_check_timestamp_seconds`. Rules `WalArchiveStale`, `BackupFailed`, `BackupMissing`, `RestoreTestNotPassed` (group `qarz-backup`), with their own tests in `alerts.test.yml` (`promtool test rules`). The figures were produced in the local proof only; no node_exporter reads them and nothing delivers the alerts, because the hosts do not exist |
| Disk, memory, connections, certificate | Disk: on the single host the worker's watch reads how full the Docker disk is (`DiskAlmostFull`). Memory, connections, certificate: not collected |
| Repeated failed administrator second factor | Counted as `qd_security_events_total{kind="bad_second_factor"}` with the rule `AdminSecondFactorRepeated`; nothing delivers the alert yet, like every rule here |
| Receipts awaiting decision | `qd_receipts_oldest_waiting_seconds` with the rule `ReceiptsWaiting` (older than 24 hours); nothing delivers the alert yet, like every rule here |
| Daily digest and dashboards | Not built |
| Delivery of alerts to the operator's Telegram chat and phone | On the single host: to the Telegram chat, by the worker's watch, once `QD_ALERT_CHAT_IDS` is set (never proven against real Telegram: see `SINGLE-HOST.md`, "Not proven"). Not to a phone. For the two-server design: not configured |
| Logs from the proxy, 30-day retention | The proxy of `deploy/production/` writes JSON lines with the request identifier and no query string; rotation is by size, nothing keeps 30 days, and there are no hosts yet |
