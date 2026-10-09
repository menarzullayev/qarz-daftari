# Quality & Operations

Version 2. Status: rewritten for release 1 as a production service; approved by the founder on 2026-10-06 (DEC-019 / APR-019) with no changes to the rules the agent decided. The stage stays in REVIEW by the founder's earlier decision until the launch criteria are met. Prepared 2026-10-06.
Version 1 (ten-shop pilot, DEC-011) is superseded and remains in version history.

This document defines how release 1 is tested, released, watched, and recovered. It does not claim readiness: nothing has been built and no launch criterion is met.

Scale assumed: one operator, two servers in Uzbekistan, design capacity of 5,000 shops (REQ-N13).

**Amended on 2026-10-08 (changed by the founder on 2026-10-08, DEC-058/DEC-070).** For lack of budget there are no servers. The service runs on **one computer** the founder already has, reached through a Cloudflare Tunnel, with encrypted backups in Cloudflare R2, and the proxy is nginx. Wherever this document says "both servers", "the standby", "the primary", "failover" or "replication", it describes the two-server design, which is kept for when there are two servers and is **not what runs now**. What runs now is in the passages marked "one host" below, in `deploy/production/SINGLE-HOST.md`, and in runbooks 14 and 15. Text marked "approved by the founder on 2026-10-09, DEC-078" is a proposal and not yet a rule.

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

Stated gaps: payment and SMS adapters cannot be exercised in production while their switches are off, and the SMS sender (Eskiz) has only ever met a fake transport; no test covers a compromised administrator; real-world behavior of Telegram delivery to servers in Uzbekistan is unknown until tried.

Verified so far: the release 1 schema was executed in PostgreSQL 16 and ten constraint, trigger, permission, and isolation checks behaved as intended (Technical Specification).

## CI/CD

| Step | Detail |
|---|---|
| Trigger | Every pull request and every push to main. A pull request that changes documentation only skips every job but the documents check (the rule is an allow-list, `backend/scripts/ci_scope.py`); a push to main runs everything. A new push to a pull request cancels its older run; a run on main is never cancelled |
| Backend checks | Format, lint, types, import rules between modules, unit and integration tests with PostgreSQL (four parallel jobs, each test in exactly one, which CI proves from the collections on every run), tenant suite, migration applied to empty and to previous schema, dependency scan |
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
| SMS | One failed for good within the hour; any waiting after a failed attempt for 30 minutes |
| Replication lag | Above 2 minutes |
| Log archive age | Older than 5 minutes |
| Backup | Failed or missing |
| Disk, memory, connections on both servers | Disk above 80%; memory above 90% for 10 minutes; connections above 80% of the limit |
| Certificate | Under 14 days |
| Security events | Any cross-tenant attempt by an authenticated session; repeated invalid signatures; repeated failed administrator second factor |
| Receipts awaiting decision | Older than 24 hours |
| Scheduler | No reminder run recorded in an hour during sending hours |

A daily digest reports counts, so that silence can be told from health. Dashboards are kept to one page per area: traffic, ledger, messaging, database, subscription.

**Monitoring on one host (changed by the founder on 2026-10-08, DEC-070).** There is no monitoring system. Stated plainly, signal by signal:

| Signal | On one host |
|---|---|
| External check of `/healthz` | Not set up. It is the only way to learn that the machine is off or offline, because nothing on the machine can say so. The founder can add one at no cost (any uptime service that notifies a phone) and Cloudflare's "Tunnel Health Alert" notification; both are described in `deploy/production/SINGLE-HOST.md` and created by nobody but him |
| Error rate, latency, outbox age, SMS, security events, receipts, scheduler | The API serves the figures at `/metrics` inside the Compose network and the rules exist (`deploy/monitoring/alerts.yml`); nothing reads the one or evaluates the other |
| Webhook backlog reported by Telegram | Not collected |
| Replication lag | Does not exist: there is no replica |
| Log archive age; backup failed or missing; restore test | Checked every five minutes by the `backup` container, which turns **unhealthy** in `docker ps` when the newest archived WAL segment is older than 5 minutes, the newest backup older than 26 hours, or the newest full backup older than 8 days. Every job writes a line to that container's log, and the figures are files in a volume. **Nobody is told**; a person has to look (`single-host.sh status`) |
| Disk, memory, connections | Shown by `single-host.sh status` (disk) and `docker stats`. Nobody is told |
| Certificate | Not the service's: Cloudflare holds and renews the public certificate |
| A process that died | Restarted by Docker. A process that runs but is unhealthy is not restarted and nobody is told |

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

**Alerting on one host (changed by the founder on 2026-10-08, DEC-070).** No alert of the table above is delivered to anybody: there is nothing to evaluate the rules and nothing to send. What can exist without a monitoring system is the two outside checks named under Monitoring, which cover "service down" only. "Archive stale", "backup failed" and "disk" reach the operator only when he looks. Launch criterion 9 cannot be met in this state.

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

**Backup on one host (changed by the founder on 2026-10-08, DEC-070).** Implemented in `deploy/production/compose.single-host.yml`; the detail, and the comparison with the table above line by line, is in `deploy/production/SINGLE-HOST.md`, "Backups".

| Item | One host |
|---|---|
| Database | No replica. Write-ahead log archived to Cloudflare R2 within about a minute of a write (`archive_timeout` 60 seconds and a heartbeat); weekly full and daily differential backups with pgBackRest, to the same bucket |
| Files | Kept in a volume of the machine; copied to the bucket every five minutes; a file removed here is kept there 30 more days. Not included in a weekly archive |
| Encryption | Everything is encrypted on the machine before it is sent: the database and its log by pgBackRest (AES-256), the files and the monthly dumps by rclone's crypt, file names included, with one passphrase. A working copy of the passphrase is on the machine, because the machine encrypts with it; **the two copies that count are off the machine** |
| Location | **One: the R2 bucket, outside Uzbekistan.** No standby and no third location. Losing the machine and the bucket, or the machine and the passphrase, loses everything |
| Retention | Unchanged: point-in-time recovery for 14 to 21 days; weekly backups for 8 weeks; monthly dumps for 12 months |
| Verification | Automated restore test of the latest backup weekly, into a throwaway instance on the same machine, and by command. A timed restore onto a **second machine** has never been done and is what the proposed launch criterion 8 asks for |
| Schedule | A scheduler container, because the host has no systemd; a backup missed while the machine was off is taken when it starts |
| Not backed up | Logs; images (rebuilt); the env file (the founder's password manager, which must hold a copy of it) |

## Disaster recovery

Targets (NFR-004): service restored within 1 hour; at most 5 minutes of entries lost.

**Disaster recovery on one host (changed by the founder on 2026-10-08, DEC-070).** The two targets above belong to the two-server design and are not met. What one machine gives is proposed in the Technical Specification under NFR-003 and NFR-004 (approved by the founder on 2026-10-09, DEC-078): no availability percentage; no recovery time until one has been measured; at most 5 minutes of entries lost with the machine's disk while the archive to R2 is healthy, and no bound while the machine cannot reach R2. The scenarios, for one host:

| Scenario | Recovery on one host |
|---|---|
| Process crash | Docker restarts it; Telegram redelivers; the outbox resumes |
| The computer was off, lost power, or Windows restarted | Nothing is lost. The service is down until the machine is up and Docker runs; then everything starts by itself, the tunnel reconnects and a missed backup is taken. Runbook 14 |
| Internet link down | The service is unreachable although it runs; nothing is lost; entries cannot be recorded meanwhile. The write-ahead log waits on the disk (up to 4 GiB, then the oldest is dropped so that the database keeps running) and is sent when the link returns |
| Bad release | Redeploy the previous images; no data change needed |
| Operator or software damages data | A point-in-time copy from R2 beside the live database (`single-host.sh pitr`), never over it; affected shops told which minutes to re-enter. Runbook 3 |
| The machine or its disk is lost | Another machine, the env file from the password manager, a restore from R2, start. The tunnel's token and the public name are unchanged, so nothing changes in Cloudflare or Telegram. What had not reached R2 is lost. Runbook 15 |
| Docker's data is wiped (a prune with volumes, a factory reset, a reinstall) | The same as a lost disk: the database is a Docker volume |
| The R2 bucket or the Cloudflare account is lost | The service keeps running without backups until a new bucket is set up; if the tunnel's account is lost, the service is unreachable until a new way in exists. The machine is then the only copy |
| The machine **and** the bucket are lost | The data is gone |
| Backup passphrase lost | The backups are unusable; prevention only: two copies off the machine |
| Tunnel token leaked | Whoever has it can receive the service's traffic: delete the tunnel in Cloudflare, create a new one, put the new token in the env file. Runbook 4 |
| Bot token or session secret leaked; administrator account compromised | As in the table above |
| Operator unavailable | The service runs until something stops; then it is down until he is back. Nobody else can act unless a second person has the machine's sign-in, the env file and runbooks 14 and 15 |

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
14. "The computer was off": power loss, a restart, Docker not running (changed by the founder on 2026-10-08, DEC-070).
15. Move the service to another machine from R2 alone (changed by the founder on 2026-10-08, DEC-070).

Runbooks 1 to 5 have a part for one host (changed by the founder on 2026-10-08, DEC-070); on one host runbook 2 is not a failover but points at 14 and 15.

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
| 8, one host (changed by the founder on 2026-10-08, DEC-070; approved by the founder on 2026-10-09, DEC-078) | *Approved text:* A restore of the whole service from R2 alone onto a **second machine**, by runbook 15, done once by the founder with a clock running, the data checked against the first machine and the time recorded; a point-in-time copy to a chosen minute (runbook 3) done once on the real machine, time recorded; "the computer was off" (runbook 14) done once by cutting the power, time until the service answers recorded. There is no failover to rehearse and no target to be within: the recorded times become the stated recovery times | Not started. The mechanism is proven in CI with a stand-in for R2; nothing was done on the real machine |
| 9 | Every alert triggered once and received | Not started |
| 10 | Runbooks 1 to 11 each executed once | Not started |
| 11 | Usability sessions show recording is not slower than the notebook for the sellers tested | Not started |
| 12 | Two servers at two providers or facilities in Uzbekistan in use; backup key stored off both | Not chosen |
| 12, one host (changed by the founder on 2026-10-08, DEC-070; approved by the founder on 2026-10-09, DEC-078) | *Approved text:* The service runs on the one machine as `deploy/production/SINGLE-HOST.md` describes, with its preparation done (starts after a power cut, never sleeps, no restart for updates in shop hours, disk encrypted, on a UPS); backups and the write-ahead log arrive in the R2 bucket and the restore test has passed on the real bucket; the backup passphrase and a copy of the env file are kept in **two places that are not that machine**, and the founder has read the passphrase back from each; an outside check of `/healthz` notifies his phone; and the founder has accepted in writing that there is no failover and that backups and traffic leave Uzbekistan, or the legal review (criterion 2) has answered that they may | Not started |
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
| DEC-019 | Version 2 operations definition: test strategy with a blocking tenant suite, staged releases with backward-compatible migrations, monitoring and alerting, replication with point-in-time recovery, recovery targets of 1 hour and 5 minutes, runbooks, and sixteen launch criteria | Approved 2026-10-06 (APR-019) |
| DEC-058, DEC-070 | nginx as the proxy; one host behind a Cloudflare Tunnel with encrypted backups to Cloudflare R2, in place of two servers | Decided by the founder (2026-10-07, 2026-10-08); this document amended 2026-10-08. The one-host text of launch criteria 8 and 12, and the recovery objectives (Technical Specification, NFR-003 and NFR-004), are approved by the founder on 2026-10-09, DEC-078 |
| Production launch approval | Launch criterion 16 | Not requested; cannot be given until criteria 1 to 15 are met |
