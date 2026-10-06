# Quality & Operations

> **Stale since 2026-10-06.** This document was written for PRD version 1 (pilot MVP). The founder changed direction to a full production-grade product (DEC-012 / APR-012) and the PRD is now version 2. This document has not yet been revised and must not be relied on where it conflicts with `docs/04-prd/OUTPUT.md`.

Status: operations definition approved by the founder on 2026-10-06 (DEC-011 / APR-011). By the founder's decision the stage stays in REVIEW and is not passed until the launch criteria below are met. This document defines how the MVP is tested, released, watched, and recovered. It does **not** claim the product is ready to launch: nothing has been built, and most launch criteria below are unmet by design. Prepared 2026-10-06.
Upstream: Development Plan (DEC-010), Technical Specification (DEC-009), decision records ADR-001 to ADR-010 (DEC-008).

Scale assumed throughout: one operator (the founder), one server, about ten pilot shops.

## Test strategy

| Level | What is tested | How | Gate |
|---|---|---|---|
| Domain unit tests | Balance, allocation, overdue calculation, default due date, lifecycle transitions | Fast tests with no database; property-based tests for the invariants: balance never negative, a reversal restores the previous balance, shop total equals the sum of balances | CI, every change |
| Parser tests | Entry grammar | Table of cases: every example in the specification, amount formats, Cyrillic input, malformed and hostile input | CI |
| Application tests | Each command with its preconditions and error codes | Against a real PostgreSQL instance started for the test run | CI |
| Database tests | Constraints and role permissions | Negative tests: a second reversal, a zero amount, a credit without a due date, a second shop per owner must fail; role `qd_app` must be refused update and delete on entries (REQ-011, REQ-N07) | CI |
| Authorization tests | Isolation between shops and between customers | For every command and callback, attempt it as another owner, another customer, and a stranger; all must get the generic refusal (REQ-020, REQ-N11) | CI |
| Conversation tests | Owner and customer flows end to end | Simulated Telegram updates through the webhook handler, with Telegram calls faked; covers every acceptance criterion in the PRD | CI |
| Idempotency tests | Duplicate updates and repeated button presses | Replay the same update; exactly one effect (ADR-006) | CI |
| Outbox tests | Delivery, retry, rate limiting, blocked-bot handling | Faked Telegram returning 429, 403, and timeouts (ADR-007) | CI |
| Reminder tests | Eligibility and limits | Time-controlled tests: due today, overdue, zero balance, opted out, fully disputed, weekly limit, daily manual limit (REQ-023, REQ-025, REQ-N10) | CI |
| Privacy tests | No identifying data outside the allowed tables; anonymization leaves none | Schema inspection test and an anonymization test (NFR-008, REQ-029) | CI |
| Language test | Every message key has an Uzbek string (NFR-007) | Build fails otherwise | CI |
| Manual smoke test | Real Telegram, real server | Checklist run by the operator after every deployment: create shop, record, pay, reverse, link, confirm, dispute, remind, export | Each release |
| Usability test | Recording speed with real owners (REQ-N02) | Timed sessions with two or three owners | Before pilot |
| Restore rehearsal | Recovery targets (NFR-004) | Full restore onto a fresh server, timed | Before pilot, then monthly |
| Load check | NFR-001, NFR-005, NFR-006 | Scripted updates against a staging copy with 500 customers and 20,000 entries | Before pilot |

What is not tested, stated so it is not assumed: behavior under Telegram outages longer than a day; concurrent use by more than a handful of shops; security against a compromised operator account.

Already verified during specification: the schema was executed in PostgreSQL 16 and six of the database negative tests above behaved as intended (see the Technical Specification).

## CI/CD

| Step | Detail |
|---|---|
| Trigger | Every push and pull request |
| Checks | Formatter, linter, type checker, all automated tests with a PostgreSQL service, dependency vulnerability scan, migration applied to an empty database and to a copy of the previous schema |
| Merge rule | Main branch accepts only changes with green checks |
| Build | Container image tagged with the commit and, for releases, a version tag |
| Deployment | Manual, by the operator, with one script: take a pre-release backup, pull the tagged image, run migrations, restart, run the smoke checklist. Outside shop hours (REQ-N09). |
| Secrets | Never in the repository or in CI logs; the server's environment file is the only copy besides the operator's password manager |

Deployment stays manual on purpose: with one operator and real money records, a person should be present for every release.

## Monitoring

| Signal | Source | Threshold |
|---|---|---|
| Bot reachable | External check on `/healthz` every minute | Down for 3 minutes |
| Webhook backlog | Telegram's pending update count, polled every 5 minutes | Above 20 |
| Outbox delay | Oldest pending message | Older than 10 minutes |
| Outbox failures | Messages marked failed | Any |
| Errors | Unhandled exceptions | Any |
| Handling time | 95th percentile per hour (NFR-001) | Above 500 ms for two consecutive hours |
| Backup | Completion time and size | Not completed by 04:00, or size shrinks by more than 20% |
| Disk and memory | Host | Disk above 80%; memory above 90% for 10 minutes |
| Certificate | Days to expiry | Fewer than 14 |

A daily digest at 21:00 gives the operator the counts for the day, so that silence is distinguishable from health.

Pilot metrics (METRIC-001 to METRIC-004) are product measurements, not operational monitoring; they are exported weekly as described in the Technical Specification.

## Logging

- Structured JSON lines, one per handled update, job run, and outbound message.
- Fields: time, level, event name, update identifier, internal identifiers, duration, outcome, error code.
- Never logged: message text, names, phone numbers, Telegram identities, tokens, amounts together with a customer identifier (REQ-N05).
- Kept 30 days on the server, rotated daily, not shipped anywhere else.
- Security events (authorization refusals, invalid webhook secrets, invalid tokens) carry their own event names so they can be counted.

## Alerting

| Alert | Channel | Expected response |
|---|---|---|
| Bot unreachable | External service to the operator's phone, independent of the server | Within 30 minutes during shop hours |
| Unhandled exception, outbox failure or delay, backlog | Operator's Telegram chat through the bot | Same day |
| Backup missing or shrunk | Operator's Telegram chat | Before the next night |
| Disk, memory, certificate | Operator's Telegram chat | Within two days |

Each alert is triggered deliberately once before the pilot to prove it arrives. There is no on-call rotation; one person receives everything. Outside shop hours nothing is expected to be answered.

## Backup

Implements the backup decision record (ADR-009).

| Item | Specification |
|---|---|
| What | Full database dump in PostgreSQL custom format |
| When | Daily at 02:00 Tashkent time; additionally before every release |
| Protection | Encrypted on the server with a public key; the private key is held only by the operator, off the server, in two places |
| Where | A second provider or facility inside Uzbekistan (REQ-N04); not yet chosen |
| Retention | 14 daily and 8 weekly copies |
| Verification | Size and completion checked daily; a full restore rehearsed monthly |
| Not backed up | Logs; container images (rebuilt from the repository); the environment file (kept in the operator's password manager) |

## Disaster recovery

Targets (NFR-004): service restored within 4 hours; at most 24 hours of entries lost.

| Scenario | Recovery |
|---|---|
| Application crash | Container restarts automatically; pending updates are redelivered by Telegram; outbox resumes |
| Bad release | Redeploy the previous image; if a migration must be undone, restore the pre-release backup |
| Database corruption or accidental damage | Restore the latest good backup; tell every pilot owner which period must be re-entered from their notebook |
| Server lost | New server at the same or another provider in Uzbekistan; install from the repository; restore backup; point the webhook at the new address |
| Provider outage | Wait if short; otherwise as "server lost" at another provider |
| Backup key lost | Backups are unrecoverable. Prevention only: two copies of the key, checked at each monthly rehearsal. |
| Bot token leaked | Revoke through BotFather, set the new token and webhook; no data is exposed by the token alone, but an attacker could have impersonated the bot |
| Operator unavailable | No one else can act. Pilot owners are told in advance to continue in their notebook if the bot stops. |

The last row is the weakest point of the whole design and is accepted for the pilot.

## Runbooks

To be written as short checklists in the code repository during milestone M5; each must be executed once before the pilot.

1. Deploy a release.
2. Roll back a release.
3. Restore from backup onto a fresh server.
4. Rotate the bot token and webhook secret.
5. Respond to "bot is not answering".
6. Respond to "an entry is wrong" (always a reversal through the application; never a manual database change).
7. Owner lost their Telegram account: verify identity in person or by phone known from onboarding, then reassign the shop by a reviewed, logged operator command.
8. Customer removal request received outside the bot.
9. Suspected data exposure.
10. Onboard a pilot shop; offboard a pilot shop with a final export.

## Incident readiness

- **Severity.** High: owners cannot record, or data may be lost or exposed. Medium: notifications or reminders delayed or failing. Low: cosmetic or single-user issues.
- **Communication.** A Telegram group with pilot owners, separate from the bot, for outage notices; high-severity incidents are announced there within an hour of detection during shop hours.
- **Record.** Every high or medium incident gets a short written note: what happened, impact, cause, fix, what changes.
- **Data exposure.** Treated as high severity; affected owners and customers are told what was exposed. Whether a regulator must be notified, and by when, is a question for the legal review.

## Security / compliance readiness

| Item | State |
|---|---|
| Controls specified (transport, webhook secret, tokens, roles, logs, backups, host) | Specified in the Technical Specification; not yet implemented |
| Database immutability | Specified and verified against PostgreSQL 16 at schema level |
| Data stored in Uzbekistan (REQ-N04, EVID-027) | Designed (ADR-008); provider not chosen |
| Registration of the personal data base | Requirement known (EVID-027); whether and how it applies to this pilot is not established |
| Consent text | Agent draft; not legally reviewed |
| Recording a customer before consent | Open legal question |
| Delaying removal while a balance is owed | Open legal question |
| Notifications, including the customer's name by founder choice, passing through Telegram abroad (EVID-033) | Open legal question |
| Breach notification duties | Not researched |
| Dependency and host patching | Process defined above; not yet running |

Compliance readiness today: **not ready**. Five of the items above depend on a legal review that has not happened.

## Release / rollback plan

Stages are those in the Development Plan: development, internal alpha, friendly test, pilot.

Each release: tagged commit with green CI → pre-release backup → deploy outside shop hours → smoke checklist → watch alerts for one hour. If the smoke checklist fails, roll back immediately.

Rollback: redeploy the previous image. Migrations are forward-only and written to be compatible with the previous image wherever possible; when they are not, the release note says so and rollback includes restoring the pre-release backup, which loses entries made since the release. For that reason incompatible migrations are released only at the start of a quiet period and never during the pilot's shop hours.

## Production launch criteria

"Launch" here means giving the bot to pilot shops with real customer data. All must be true.

| No. | Criterion | State on 2026-10-06 |
|---|---|---|
| 1 | Interviews and the pDaftar test are done and no stop condition from Market Research is met | Not done |
| 2 | Legal review has answered the four questions and approved or corrected the consent text | Not done |
| 3 | Any registration the law requires before processing is complete | Not established |
| 4 | Milestones M1 to M5 are complete and every PRD acceptance criterion passes in CI | Not started |
| 5 | A full restore has been rehearsed and met the recovery targets, with the time recorded | Not started |
| 6 | Every alert has been triggered once and received | Not started |
| 7 | Runbooks 1 to 7 have each been executed once | Not started |
| 8 | Usability test shows recording is not slower than the notebook for the owners tested | Not started |
| 9 | Hosting and backup providers in Uzbekistan are chosen and in use | Not chosen |
| 10 | Pilot owners have been told in plain words: this is a test, keep your notebook, up to a day of entries could be lost, and who to contact | Not started |
| 11 | The founder records an explicit launch approval after reviewing items 1 to 10 | Not given |

None of the eleven is met. That is the expected state for a documentation pipeline that ends before the build begins.

## Evidence

- Schema and immutability checks executed in PostgreSQL 16 on 2026-10-06 (recorded in the Technical Specification).
- Hosting availability and price range in Uzbekistan (EVID-031).
- Telegram rate limits that the dispatcher and alert thresholds respect (EVID-032).
- Legal basis for the hosting and consent requirements (EVID-027) and Telegram message storage (EVID-033).

No operational evidence exists yet: no test run, no deployment, no restore, no alert has been exercised, because there is no system.

## Assumptions

- One operator is acceptable for an eight-week pilot in which shops keep their notebooks.
- A free or low-cost external uptime service can reach a server in Uzbekistan and notify the operator's phone.
- Monthly restore rehearsals are frequent enough at pilot scale.
- Pilot owners will join a Telegram group for notices.

## Open questions

1. Who can act when the operator cannot? Even a trusted person able to post an outage notice would help.
2. Which providers for hosting, backup storage, and the external uptime check?
3. What are the breach notification duties under the personal data law?
4. Should the monthly restore rehearsal be automated once the pilot starts?

## Approvals

| Record | Subject | Status |
|---|---|---|
| DEC-010 / APR-010 | Development plan and gates | Approved 2026-10-06 |
| DEC-011 | Operations definition in this document: test strategy, manual gated releases, monitoring and alerting, daily encrypted backups with recovery targets of 4 hours and 24 hours, single-operator incident handling, and the eleven launch criteria | Approved 2026-10-06 |
| Production launch approval | Launch criterion 11 | Not requested; cannot be given until criteria 1 to 10 are met |
