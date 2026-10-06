# Evidence Record

- Evidence ID: EVID-038
- CLAIM: Story S1.4 is implemented and verified in CI: Telegram webhook with constant-time secret check and at-most-once processing, idempotent API writes, transactional outbox, and a dispatcher with lease-based claiming, retry-after handling, backoff and rate limits. 178 tests passed. Five mutation checks each made tests fail. A test caught a real defect: the webhook accepted a boolean as an update identifier.
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/6 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37500207188
- DATE: 2026-10-06
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
