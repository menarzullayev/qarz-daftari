# Monitoring

What the application gives a monitoring system, and what is still missing. Nothing here has been loaded
into a real monitoring system: there are no servers yet, so no alert has been triggered or received
(launch criterion 9 is open).

> **On the single host (the current deployment, DEC-070) there is no monitoring system at all.** The
> rules of `alerts.yml` are loaded nowhere and nothing delivers an alert. What is and is not watched
> there, and the two free checks from outside the machine that the founder can set up (Cloudflare's
> tunnel notification, an uptime check of `/healthz`), is in `deploy/production/SINGLE-HOST.md`, "What is
> watched, and what is not". The figures the backup jobs write are the same names as below, kept as
> files in the `backup-state` volume, and the `backup` container's health in `docker ps` is the only
> thing that reads them. The table "What is missing" below is written for the two-server design; its
> rows about replication and "both servers" have no meaning on one machine.

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
| Disk, memory, connections, certificate | Not collected; host-level |
| Repeated failed administrator second factor | Counted as `qd_security_events_total{kind="bad_second_factor"}` with the rule `AdminSecondFactorRepeated`; nothing delivers the alert yet, like every rule here |
| Receipts awaiting decision | `qd_receipts_oldest_waiting_seconds` with the rule `ReceiptsWaiting` (older than 24 hours); nothing delivers the alert yet, like every rule here |
| Daily digest and dashboards | Not built |
| Delivery of alerts to the operator's Telegram chat and phone | Not configured |
| Logs from the proxy, 30-day retention | The proxy of `deploy/production/` writes JSON lines with the request identifier and no query string; rotation is by size, nothing keeps 30 days, and there are no hosts yet |
