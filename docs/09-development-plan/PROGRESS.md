# Implementation Progress

Tracks the build against `OUTPUT.md` in this directory. A story is listed as done only when its checks passed in CI on the main branch; the evidence column says where that can be seen. Updated at the end of each working session.

Last updated: 2026-10-06 (third update).

## Milestone M1: Platform foundation

| Story | State | Evidence | Notes |
|---|---|---|---|
| S1.1 Repository, layers, tooling, CI | Done | EVID-036; pull request 1 | Import contracts enforced, with negative tests |
| S1.2 Schema as migrations, roles, row-level security, schema checks as tests | Done | EVID-036; pull request 1 | Goods-line time limit tested on both sides of its boundary |
| S1.3 Tenant context, authorization framework, cross-tenant and cross-role suite | Done | EVID-037; pull request 5 | The suite is blocking: an unregistered route or an undescribed operation fails it |
| S1.4 Webhook, idempotency, outbox and dispatcher | Done | EVID-038; pull request 6 | Dispatcher is not yet run against the real Telegram API; the sender is tested with a fake bot |
| S1.5 Two servers, replication, archiving, file store, failover | Local rehearsal done; production part blocked on the founder | EVID-044; pull request 13 | Rehearsed in containers on one machine: failover in 20.5 seconds, no acknowledged row lost. Real servers need two providers in Uzbekistan chosen, ordered and paid for |
| S2.1 Sign-in from chat, Mini App, and web | Done | EVID-039; pull request 7 | Verified with generated signatures; not yet exercised from a real Telegram client, which needs a public HTTPS address |
| S2.2 Front-end shell, message catalogs, bundle budget | Done except the generated API client | EVID-040; pull request 8 | No API description is published yet, so no client is generated; screens are placeholders |

M1 exit condition:

| Part | State |
|---|---|
| A user signs in through chat, Mini App, and web | Met in CI with generated Telegram signatures; not yet from a real client |
| The cross-tenant test suite runs | Met; blocking in CI |
| Failover rehearsed once on empty data | Met locally in containers (EVID-044); not on real servers |

## Milestone M2: Shops, staff, core ledger

| Story | State | Evidence | Notes |
|---|---|---|---|
| S3.1 Create shop; settings | Done | EVID-041; pull request 9 | |
| S3.2 Invite, join, suspend, remove staff | Done | EVID-041; pull request 9 | |
| S3.3 Ownership transfer | Done | EVID-043; pull request 14 | Both people confirm; an offer lapses after 48 hours |
| S3.4 Several shops per person; active shop; owner totals | Done except the owner's combined totals | EVID-043; pull request 14 | Combined totals across shops (REQ-065) are still to build |
| S4.1 Customers | Done | EVID-045; pull request 15 | Search ignores case and Latin or Cyrillic spelling |
| S4.2 Chat fast-entry parser | Done | EVID-042; pull request 11 | Pure domain code; not yet connected to the chat |
| S4.3 Record credit and payment; balance and allocation | Done in the API; the chat reply is in progress | EVID-042, EVID-045; pull requests 12 and 15 | Two concurrent payments of a whole balance end with exactly one accepted |
| S4.4 Reversal | Done | EVID-045; pull request 15 | |
| S4.5 Overview and customer detail | Done in the API | EVID-045; pull request 15 | Lists and totals are computed in SQL and checked against the domain rules by a test |
| S5.1 Activity log view | Done | EVID-043; pull request 14 | |

M2 exit condition: a shop with staff records credit sales and payments, and the overview is correct. Met in the API in CI. Not met in the chat or on screens yet, and no notification reaches a customer yet (M4).

## Milestones M3 to M8

| Story | State | Notes |
|---|---|---|
| S6.1 Catalog management; learned items | In progress (helper agent) | Back-end API |
| S7.1 Promised date choices in chat and Mini App | In progress | Together with the chat side of S4.3 |
| S8.1 Staff workspace screens | In progress (helper agent) | Customers, entry, overview |

Everything else in M3 to M8 is not started.

## Decisions made by agents, awaiting the founder's review

Each is implemented. If one is rejected, the named story must be revisited.

| Record | Subject | Affects |
|---|---|---|
| DEC-021 | Role `qd_app` created without login; two cross-tenant SECURITY DEFINER functions; their owner must bypass row-level security | S1.2, S1.4, S3.2; deployment (S1.5) |
| DEC-022 | `user_session` table; one-hour limit on signed Telegram data; CSRF failure answers 401; session kinds not interchangeable | S2.1 |
| DEC-023 | 7-day staff invitations; a re-invited removed member keeps the same membership; invitation token handed out once; shop identifier derived from the idempotency key | S3.1, S3.2 |
| DEC-024 | Front-end shell choices: catalogs, plurals, fixed UTC+5 dates, hash router, not-found for sections a role may not open | S2.2 |
| DEC-025 | `ownership_transfer` table; offers lapse after 48 hours; `my_memberships` function; owner's combined totals deferred | S3.3, S3.4 |
| DEC-026 | Chat parser and ledger calculation choices where BR-9 and BR-13 are silent: disputed entries in allocation and indicator, due today is not overdue, end of week is Sunday, a comma is a decimal mark | S4.2, S4.3, S7.1, S12.2 |
| DEC-027 | Entry bounds of 100 to 100 000 000 UZS; refusal codes and statuses; a shop without a subscription record is limited; only the owner views a suspended shop; SQL totals; derived measurement references | S4.1, S4.3, S4.4, S4.5 |
| DEC-028 | Design of the local rehearsal: PostgreSQL 16 with pgBackRest, 60-second archive timeout, scripted failover with fencing | S1.5 |

## Deviations from the approved documents

| Item | Approved text | Implementation | Why |
|---|---|---|---|
| Creation of role `qd_app` | `LOGIN PASSWORD 'set-at-deploy'` in `schema.sql` | Created `NOLOGIN`; login and password set at deploy | No password literal in the repository (DEC-021) |
| Layer order for the import contract | Layers named without ordering infrastructure | interface, infrastructure, application, domain | A single order is needed for an automatic check |
| Dependency lock | "Pinned with hashes" | Hash-pinned Linux lock generated with uv | pip-tools did not finish |
| Tables and functions beyond `schema.sql` | Not present | `user_session`; `ownership_transfer`; `mark_recipient_unreachable`; `accept_staff_invitation`; `my_memberships` | DEC-021, DEC-022, DEC-025 |
| Ruff rule S608 in the storage module | All security rules on | Off for `infrastructure/db.py`, replaced by a stricter project test | The rule cannot tell a constant SQL fragment from user input (DEC-027) |
| Rate limits on API and sign-in | Specified | Not implemented yet | Planned for hardening (M8); dispatcher rate limits are implemented |

## Not yet proven, stated plainly

- Nothing has run on a real server. Every result is from a developer machine or CI.
- Telegram has never called the webhook, and no real Telegram client has signed in: there is no public HTTPS address.
- The Uzbek and Russian texts were written by agents and not reviewed by native speakers.
- No performance target (NFR-001 to NFR-013) has been measured except the bundle size in NFR-010.
- The failover and restore rehearsal ran in containers on one machine. It proves the script, not the servers.
- No customer has been notified of anything: linking and notifications are M4.
- The overview queries have been run on tens of accounts, not on a shop with thousands of customers.

## Launch criteria (docs/10-operations/OUTPUT.md)

| No. | Criterion | State |
|---|---|---|
| 1 | Interviews and pDaftar test | Open; founder |
| 2 | Legal review | Open; founder |
| 3 | Registration if required | Open; founder |
| 4 | M1 to M8 complete, acceptance criteria pass in CI | In progress: 14 of 49 stories done, 3 partly done, 3 in progress |
| 5 | Authorization and tenant suite covers every operation | In place and blocking; grows with each story |
| 6 | Load test | Not started |
| 7 | Security review | Not started |
| 8 | Failover and restore rehearsals | Rehearsed locally in containers (EVID-044); not on real servers |
| 9 | Alerts triggered and received | Not started |
| 10 | Runbooks executed | Not started |
| 11 | Usability sessions | Not started; needs real sellers |
| 12 | Two servers in use | Open; founder |
| 13 | Second operator named | Open; founder |
| 14 | Both languages reviewed | Not started; needs native speakers |
| 15 | Payment process run end to end with a test shop | Not started |
| 16 | Launch approval | Open; founder |

## What the founder is needed for

1. A public HTTPS address for the development bot (a tunnel or a server), so that the webhook, the Mini App and Telegram Login can be exercised from a real Telegram client. Put it in `QD_PUBLIC_BASE_URL`.
2. Two hosting providers or facilities in Uzbekistan (S1.5).
3. The shopkeeper interviews and the pDaftar test (launch criterion 1). By DEC-020 they no longer block the build.
4. Review of the pending decisions listed above.
