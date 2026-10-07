# Monitoring

What the application gives a monitoring system, and what is still missing. Nothing here has been loaded
into a real monitoring system: there are no servers yet, so no alert has been triggered or received
(launch criterion 9 is open).

## What exists

- **Logs.** The API and the worker write one JSON object a line to standard error: time, level, logger,
  event, and identifiers only (`request_id`, `method`, `route`, `status`, `ms`, `user_id`, `shop_id`,
  `update_id`, `job`, `period`, `channel`, `count`, `kind`). Any other field given to a log call is
  dropped, and an exception is logged by its type and where it happened, never by its text.
- **Request identifier.** Every answer carries `X-Request-Id`; a failure nobody handled is answered with
  a JSON body that repeats it. An identifier sent by the proxy (8 to 64 characters of `A-Z a-z 0-9 _ -`)
  is kept, so the proxy's log and the application's can be joined.
- **Metrics.** `GET /metrics` in the Prometheus text format, served only when `QD_METRICS_TOKEN` is set
  and only to a request that sends it as a bearer token. The proxy must not publish this path.
  - `qd_requests_total{method,route,status}` and `qd_request_duration_ms` (histogram) by route template.
  - `qd_security_events_total{kind}`: `shop_not_member`, `bad_sign_in`, `bad_webhook_secret`.
  - `qd_outbox_oldest_due_seconds{channel}` and `qd_job_last_finished_seconds{job}`, read from the
    database when the metrics are read.
- **Alert rules.** `alerts.yml`, written against those metrics with the thresholds of the operations
  document.

Counters live in the memory of the process that served the requests and start from zero when it starts.

## What is missing

| Signal in the operations document | State |
|---|---|
| External check of `/healthz` from outside both servers | Not set up; needs a service chosen by the founder |
| Webhook backlog reported by Telegram | Not collected (needs a call to Telegram's `getWebhookInfo`) |
| Replication lag, log archive age, backup | Not collected; they belong to the database hosts, which do not exist |
| Disk, memory, connections, certificate | Not collected; host-level |
| Repeated failed administrator second factor | Not counted; the administrator's side is not merged |
| Receipts awaiting decision | Not built (story S17.2) |
| Daily digest and dashboards | Not built |
| Delivery of alerts to the operator's Telegram chat and phone | Not configured |
| Logs from the proxy, 30-day retention | No proxy and no hosts yet |
