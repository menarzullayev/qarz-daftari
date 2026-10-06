# Quality & Operations

Version 2. Status: rewritten for release 1 as a production service; awaiting the founder's end-of-sequence review (DEC-019). The stage stays in REVIEW by the founder's earlier decision until the launch criteria are met. Prepared 2026-10-06.
Version 1 (ten-shop pilot, DEC-011) is superseded and remains in version history.

This document defines how release 1 is tested, released, watched, and recovered. It does not claim readiness: nothing has been built and no launch criterion is met.

Scale assumed: one operator, two servers in Uzbekistan, design capacity of 5,000 shops (REQ-N13).

## Test strategy

| Level | What | How | Gate |
|---|---|---|---|
| Domain unit tests | Balance, allocation, overdue, payment history indicator, line rounding, promise rules, lifecycles, subscription transitions | Pure tests; property-based tests for invariants: balance never negative, reversal restores the previous balance, lines sum to totals, shop total equals sum of balances | CI |
| Parser tests | Chat grammar in Uzbek and Russian, Latin and Cyrillic, amount formats, hostile input | Case tables | CI |
| Application tests | Every command with preconditions, role, subscription mode, and error codes | Real PostgreSQL per run | CI |
| Database tests | Constraints, triggers, insert-only tables, row-level security | The checks in `docs/08-technical-spec/tests/schema_checks.sql` ported to the suite, plus a clock-controlled test of the goods-line time limit | CI |
| Authorization and tenant suite | Every API operation and chat action attempted as each role, as a member of another shop, as an unrelated customer, and as an administrator without support access (NFR-013) | Generated from the API description so a new operation cannot be left out | CI; blocking |
| API contract tests | Responses match the published description; idempotency keys; pagination; error shape | Automated | CI |
| Front-end tests | Component tests; end-to-end flows in a browser for staff, customer, and admin in both languages and at phone and desktop widths | Headless browser against a test backend | CI |
| Conversation tests | Chat flows end to end with simulated updates | Faked Telegram | CI |
| Outbox and reminder tests | Delivery, retry, limits, channel choice, SMS quota, dispute exclusion | Faked Telegram and SMS; controlled clock | CI |
| Subscription tests | Trial, warnings, limited and suspended modes, receipt approval and rejection, duplicate receipt detection, switches | Controlled clock | CI |
| Adapter tests | Click and Payme request and signature handling against recorded examples and sandboxes where available | Cannot be proven against production while switched off; stated as a gap | CI |
| Privacy tests | No identifying data outside allowed tables (NFR-008); anonymization and shop erasure leave none | Schema inspection and data tests | CI |
| Language test | Every key in both languages (NFR-007) | Build fails otherwise | CI |
| Load test | NFR-001, NFR-005, NFR-006, NFR-009, NFR-011 with generated data: 5,000 shops, 500,000 customers | Staging, before launch and after any schema change to hot tables | Before launch |
| Security review | Session handling, signature validation, file upload, admin second factor, headers, dependency audit; a manual attempt to break tenant isolation | Checklist and manual testing; an outside reviewer if one can be afforded | Before launch |
| Failover and restore rehearsals | NFR-004: promote the standby; point-in-time restore to a chosen minute | Timed, on staging data at production scale | Before launch, then quarterly |
| Usability sessions | Recording speed for amount-only and itemized entry (REQ-N02) on low-end phones | Timed sessions with real sellers | Before launch |
| Smoke test | Core flows on production after each release | Scripted where possible, checklist otherwise | Each release |

Stated gaps: payment and SMS adapters cannot be exercised in production while their switches are off; no test covers a compromised administrator; real-world behavior of Telegram delivery to servers in Uzbekistan is unknown until tried.

Verified so far: the release 1 schema was executed in PostgreSQL 16 and ten constraint, trigger, permission, and isolation checks behaved as intended (Technical Specification).

## CI/CD

| Step | Detail |
|---|---|
| Trigger | Every push and pull request |
| Backend checks | Format, lint, types, import rules between modules, unit and integration tests with PostgreSQL, tenant suite, migration applied to empty and to previous schema, dependency scan |
| Front-end checks | Lint, types, tests, build, bundle-size budget (NFR-010), generated API client is current |
| Merge rule | Main accepts only green changes |
| Artifacts | Backend image and front-end static bundle, tagged by commit and by version |
| Staging | Every merge to main deploys to staging automatically |
| Production | A tagged release is deployed by the operator with one script, outside shop hours: confirm replication is healthy, mark a restore point, run migrations, replace images on the primary, run the smoke test, then update the standby's images |
| Migrations | Forward-only and compatible with the previous release, so rollback is a redeploy; destructive changes are split across two releases |
| Secrets | Never in the repository or CI logs |

## Monitoring

| Signal | Threshold |
|---|---|
| External check of `/healthz` every minute | Down 3 minutes |
| Error rate per route | Above 2% for 5 minutes |
| Latency 95th percentile on recording routes | Above the NFR targets for 15 minutes |
| Webhook backlog reported by Telegram | Above 50 |
| Outbox age, per channel | Older than 10 minutes |
| Replication lag | Above 2 minutes |
| Log archive age | Older than 5 minutes |
| Backup | Failed or missing |
| Disk, memory, connections on both servers | Disk above 80%; memory above 90% for 10 minutes; connections above 80% of the limit |
| Certificate | Under 14 days |
| Security events | Any cross-tenant attempt by an authenticated session; repeated invalid signatures; repeated failed administrator second factor |
| Receipts awaiting decision | Older than 24 hours |
| Scheduler | No reminder run recorded in an hour during sending hours |

A daily digest reports counts, so that silence can be told from health. Dashboards are kept to one page per area: traffic, ledger, messaging, database, subscription.

## Logging

- Structured JSON from proxy, API, and worker with a shared request identifier.
- Never logged: message text, names, phone numbers, card numbers, goods bought by a named customer, tokens, session identifiers.
- Kept 30 days on the servers; not shipped outside Uzbekistan.
- Shop activity and administrator audit are data, not logs: insert-only tables kept with the shop and the platform respectively.

## Alerting

| Alert | Channel | Expected response |
|---|---|---|
| Service down, replication broken, archive stale | External service to the operator's phone, independent of both servers | Within 30 minutes during shop hours |
| Errors, latency, outbox, scheduler, security events | Operator's Telegram chat | Same day; security events on sight |
| Backup, disk, certificate, pending receipts | Operator's Telegram chat | Before the next day |

Every alert is triggered deliberately once before launch. There is one operator and no rotation; outside shop hours no response is promised. A second person able to act must be named before launch (Development Plan, open question 2).

## Backup

Implements the archiving decision (ADR-015) and the file store decision (ADR-020).

| Item | Specification |
|---|---|
| Database | Streaming replica on the standby; write-ahead log archived every minute; weekly full and daily differential backups with pgBackRest |
| Files | Object store replicated to the standby continuously; included in weekly backup |
| Encryption | Backups encrypted; the key is held off both servers in two places |
| Location | Standby server, plus a third location in Uzbekistan if one can be found (REQ-N04) |
| Retention | Point-in-time recovery for 14 days; weekly backups for 8 weeks; monthly for 12 months |
| Verification | Automated restore test of the latest backup into staging weekly; full timed rehearsal quarterly |
| Not backed up | Logs; images (rebuilt); environment files (operator's password manager) |

## Disaster recovery

Targets (NFR-004): service restored within 1 hour; at most 5 minutes of entries lost.

| Scenario | Recovery |
|---|---|
| Process crash | Automatic restart; Telegram redelivers; outbox resumes |
| Bad release | Redeploy previous images; no data change needed because migrations are backward compatible |
| Operator or software damages data | Point-in-time restore to just before the damage, into a new database; affected shops told which minutes to re-enter |
| Primary server lost | Promote the standby, start API and worker there, repoint DNS and webhook; then build a new standby |
| Standby lost | Service unaffected; rebuild the standby; until then there is no failover and backups go to the third location if it exists |
| Both servers lost | Restore from the third location if it exists; otherwise the data is gone. This is why a third location matters. |
| Link between servers lost | Primary continues; replication and archive alerts fire; no failover while the primary is healthy |
| Backup key lost | Backups unusable; prevention only |
| Bot token or session secret leaked | Rotate; invalidate sessions; review audit for misuse |
| Administrator account compromised | Disable in the allow-list from the server; rotate second-factor secret; review admin audit; re-verify recent receipt approvals and setting changes |
| Operator unavailable | Named second person follows the failover runbook and posts notices; cannot change code |

## Runbooks

Written during M8, each executed once before launch:

1. Deploy and roll back a release.
2. Fail over to the standby; rebuild a standby.
3. Point-in-time restore.
4. Rotate bot token, webhook secret, session secret, database passwords, backup key.
5. "The bot or panel is not answering."
6. "An entry is wrong": always a reversal through the product; never a database change.
7. Owner lost their Telegram account: identity check, then a logged administrator action reassigning ownership.
8. Review and decide subscription receipts; handling a suspected forged or reused receipt.
9. Open and close support access.
10. Customer removal request received outside the product; shop deletion request.
11. Suspected data exposure.
12. Switch on SMS or online payment for one shop, then for all.
13. Onboard a shop, including importing its paper ledger.

## Incident readiness

- **Severity.** High: recording unavailable, data loss or exposure, wrong balances. Medium: notifications, reminders, panel, or subscription handling degraded. Low: cosmetic or single-user.
- **Communication.** A public status channel in Telegram, separate from the bot; high-severity incidents announced within an hour during shop hours.
- **Record.** Every high or medium incident gets a written note: what happened, impact, cause, fix, change.
- **Data exposure.** Treated as high; affected shops and customers told what was exposed. Duties to notify a regulator are not established and belong to the legal review.
- **Support.** Shops reach the operator through the bot and the status channel; response times are stated at onboarding and must be ones a single person can keep.

## Security / compliance readiness

| Item | State |
|---|---|
| Security controls | Specified; not implemented |
| Tenant isolation and ledger immutability | Specified; verified at schema level in PostgreSQL 16 |
| Data in Uzbekistan (EVID-027) | Designed; providers not chosen |
| Registration of the personal data base | Requirement known; applicability not established |
| Consent text in two languages | Uzbek draft by the agent; Russian not written; neither reviewed |
| Recording before consent; deferred removal; in-shop reliability indicator | Open legal questions |
| Notifications with names and goods, and card receipts, passing through Telegram abroad (EVID-033) | Open legal question |
| Subscription payments to a personal card without a registered entity | Founder states it is lawful (EVID-035); not verified |
| Selling a subscription through a bot outside Telegram's payment mechanism (EVID-028) | Risk accepted by decision; consequence unknown |
| Receipt and file retention periods | Chosen by the agent; no legal basis researched |
| Breach notification duties | Not researched |

Compliance readiness today: **not ready**. Most items depend on a legal review that has not happened.

## Release / rollback plan

Stages are those in the Development Plan: development, staging, internal acceptance, launch. There is no pilot stage by founder decision; at launch, shops are onboarded one at a time for two weeks.

Each production release: tagged commit with green CI and a staging soak → restore point → deploy outside shop hours → smoke test → watch for one hour. Rollback is a redeploy of the previous images. If data was damaged, point-in-time restore applies. Platform switches (ADR-018) allow a faulty feature to be turned off without a release where the feature has one.

## Production launch criteria

"Launch" means the first real shop with real customer data. All must be true.

| No. | Criterion | State on 2026-10-06 |
|---|---|---|
| 1 | Interviews and the pDaftar test are done and no stop condition is met | Not done |
| 2 | Legal review has answered the open questions and approved the consent text in both languages | Not done |
| 3 | Any registration required before processing personal data is complete | Not established |
| 4 | Milestones M1 to M8 complete; every acceptance criterion passes in CI | Not started |
| 5 | The authorization and tenant suite covers every operation and passes | Not started |
| 6 | Load test meets the performance targets at design capacity, or the capacity claim is lowered to what was measured | Not started |
| 7 | Security review done and its findings fixed or accepted in writing | Not started |
| 8 | Failover and point-in-time restore rehearsed within the targets, times recorded | Not started |
| 9 | Every alert triggered once and received | Not started |
| 10 | Runbooks 1 to 11 each executed once | Not started |
| 11 | Usability sessions show recording is not slower than the notebook for the sellers tested | Not started |
| 12 | Two servers at two providers or facilities in Uzbekistan in use; backup key stored off both | Not chosen |
| 13 | A second person able to fail over and post notices is named and has done it once | Not named |
| 14 | Both languages reviewed by native speakers | Not started |
| 15 | Before the first payment is accepted: the card-transfer process, review group, and receipt handling have been run end to end with a test shop | Not started |
| 16 | The founder records an explicit launch approval after reviewing items 1 to 15 | Not given |

None of the sixteen is met.

## Evidence

- Release 1 schema executed in PostgreSQL 16 on 2026-10-06 with ten checks passing (Technical Specification; `docs/08-technical-spec/tests/schema_checks.sql`).
- Hosting availability and price range in Uzbekistan (EVID-031); Telegram rate limits (EVID-032); localization requirement (EVID-027); Telegram message storage (EVID-033); Telegram payment rule (EVID-028); the founder's statement on card payments (EVID-035).

No operational evidence exists: no test run, deployment, failover, restore, or alert has been exercised, because there is no system.

## Assumptions

- One operator plus a named backup person can meet a 99.5% target in shop hours. Unproven; the target may have to be lowered after the first months.
- Two independent facilities in Uzbekistan are available to an individual.
- An external monitoring service can reach servers in Uzbekistan and notify by phone.
- Quarterly rehearsals are frequent enough.

## Open questions

1. Who is the second person?
2. Which providers, and is there a third location for backups?
3. Is an outside security review affordable before launch?
4. What response times will be promised to paying shops, given one operator?
5. Breach notification duties under the personal data law.

## Approvals

| Record | Subject | Status |
|---|---|---|
| DEC-011 / APR-011 | Version 1 operations definition | Superseded |
| DEC-019 | Version 2 operations definition: test strategy with a blocking tenant suite, staged releases with backward-compatible migrations, monitoring and alerting, replication with point-in-time recovery, recovery targets of 1 hour and 5 minutes, runbooks, and sixteen launch criteria | Pending end-of-sequence review |
| Production launch approval | Launch criterion 16 | Not requested; cannot be given until criteria 1 to 15 are met |
