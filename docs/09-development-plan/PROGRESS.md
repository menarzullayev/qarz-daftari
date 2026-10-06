# Implementation Progress

Tracks the build against `OUTPUT.md` in this directory. A story is listed as done only when its checks passed in CI on the main branch; the evidence column says where that can be seen. Updated at the end of each working session.

Last updated: 2026-10-07.

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
| S3.4 Several shops per person; active shop; owner totals | Done | EVID-043, EVID-049; pull requests 14 and 22 | |
| S4.1 Customers | Done | EVID-045; pull request 15 | Search ignores case and Latin or Cyrillic spelling |
| S4.2 Chat fast-entry parser | Done | EVID-042, EVID-046; pull requests 11 and 19 | |
| S4.3 Record credit and payment; balance and allocation | Done | EVID-042, EVID-045, EVID-046; pull requests 12, 15 and 19 | Two concurrent payments of a whole balance end with exactly one accepted |
| S4.4 Reversal | Done | EVID-045; pull request 15 | |
| S4.5 Overview and customer detail | Done | EVID-045, EVID-048; pull requests 15 and 17 | Lists and totals are computed in SQL and checked against the domain rules by a test |
| S5.1 Activity log view | Done | EVID-043; pull request 14 | |

M2 exit condition: a shop with staff records credit sales and payments, and the overview is correct. Met in CI through the API, the chat and the screens. Not exercised against the real Telegram API or by a person.

## Milestone M3: Goods, promised dates, staff workspace

| Story | State | Evidence | Notes |
|---|---|---|---|
| S6.1 Catalog management; learned items and their review | Done | EVID-047; pull request 20 | A column `merged_into` was added to the approved schema (DEC-030) |
| S6.2 Itemized entry screen and API; line rounding; totals | Done | EVID-047, EVID-048; pull requests 26 and 23 | The screen was built against a fake server before the API was merged; the two have not been run together |
| S6.3 Add lines later to an amount-only entry | Done | EVID-047, EVID-048; pull requests 26 and 23 | |
| S7.1 Promised date choices in chat and Mini App; shop default | Done | EVID-046, EVID-048; pull requests 19 and 23 | |
| S8.1 Staff workspace screens | Done | EVID-048; pull requests 17 and 23 | Exercised only against a fake server and a local render |

M3 exit condition: an itemized sale of five catalog goods can be entered in the Mini App. Met in front-end tests by taps (five goods, five taps and save). Not measured with a person against the 45 seconds of REQ-N02, and not run inside Telegram.

## Milestone M4: Customers

| Story | State | Evidence | Notes |
|---|---|---|---|
| S9.1 Counter code, waiting list, personal link, consent | Done in the API and chat | EVID-049; pull request 21 | Consent texts are agent drafts without legal review; screens not built yet |
| S9.2 Customer notifications for entries, payments, reversals | Done | EVID-049; pull request 21 | Queued, never sent through the real Telegram API |
| S9.3 Customer debt page; isolation; disconnect; removal and anonymization | Done in the API and chat | EVID-049; pull request 22 | The customer's Mini App page is not built yet |
| S10.1 Disputes | Done in the API and chat | EVID-050; pull request 25 | Screens not built yet |
| S10.2 Payment notices with receipt upload | Not started | - | Needs a file store, which the application does not have yet |
| S10.3 Date change requests; promise history | Not started | - | |

## Milestones M5 to M8

Not started.

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
| DEC-029 | Staff chat: `chat_pending` table; button data holds identifiers only; promise rows record who set them; one-tap date choice open once for 24 hours; invitation link format | S4.3, S7.1 |
| DEC-030 | Catalog and goods lines: `merged_into` column; unique normalized names; typed price never rewrites the catalog; 1 to 50 lines; quantity with three decimals | S6.1, S6.2, S6.3 |
| DEC-031 | Staff screens: what each role sees; integer arithmetic for quantities; no new colours for errors | S8.1 |
| DEC-032 | Customer linking: consent before the waiting entry; `waiting_name` column; counter code revealed once; 7-day personal links; five cross-tenant functions; draft Russian consent text | S9.1, S9.2 |
| DEC-033 | Customer page and removal: fields left out of the page; anonymous label; forgetting a person's Telegram identity; removal completed by the settling entry | S9.3 |
| DEC-034 | Disputes: 30 days from the later of entry and link; reasons of 3 to 300 characters; withdrawal on the page only | S10.1 |

## Deviations from the approved documents

| Item | Approved text | Implementation | Why |
|---|---|---|---|
| Creation of role `qd_app` | `LOGIN PASSWORD 'set-at-deploy'` in `schema.sql` | Created `NOLOGIN`; login and password set at deploy | No password literal in the repository (DEC-021) |
| Layer order for the import contract | Layers named without ordering infrastructure | interface, infrastructure, application, domain | A single order is needed for an automatic check |
| Dependency lock | "Pinned with hashes" | Hash-pinned Linux lock generated with uv | pip-tools did not finish |
| Tables, columns and functions beyond `schema.sql` | Not present | Tables `user_session`, `ownership_transfer`, `chat_pending`; columns `catalog_item.merged_into`, `customer_link.waiting_name`; functions `mark_recipient_unreachable`, `accept_staff_invitation`, `my_memberships`, `customer_token_info`, `link_customer`, `my_accounts`, `my_link`, `end_my_link`, `mark_recipient_reachable`, `forget_user_if_unused` | DEC-021, DEC-022, DEC-025, DEC-029, DEC-030, DEC-032, DEC-033 |
| Order of consent and the waiting entry | BR-16 allows the waiting entry before consent | Consent first; a constraint refuses a waiting entry without it | Stricter, and simpler to explain to a customer (DEC-032) |
| Ruff rule S608 in the storage module | All security rules on | Off for `infrastructure/db.py`, replaced by a stricter project test | The rule cannot tell a constant SQL fragment from user input (DEC-027) |
| Rate limits on API and sign-in | Specified | Not implemented yet | Planned for hardening (M8); dispatcher rate limits are implemented |

## Not yet proven, stated plainly

- Nothing has run on a real server. Every result is from a developer machine or CI.
- Telegram has never called the webhook, and no real Telegram client has signed in: there is no public HTTPS address.
- The Uzbek and Russian texts were written by agents and not reviewed by native speakers.
- No performance target (NFR-001 to NFR-013) has been measured except the bundle size in NFR-010.
- The failover and restore rehearsal ran in containers on one machine. It proves the script, not the servers.
- No customer has been notified of anything through Telegram: notifications are queued and tested, never sent.
- The overview queries have been run on tens of accounts, not on a shop with thousands of customers.
- The front end and the back end have never been run together. Every screen was tested against a fake server.
- The main branch was red for one commit (pull request 21) between 19:00 and 24:00 UTC because a test used the server's date instead of the Tashkent date. The application was right; the test was fixed in the next commit (EVID-049).
- Whether removal may wait for a debt to be settled (BR-32), and the consent texts, have had no legal review.

## Launch criteria (docs/10-operations/OUTPUT.md)

| No. | Criterion | State |
|---|---|---|
| 1 | Interviews and pDaftar test | Open; founder |
| 2 | Legal review | Open; founder |
| 3 | Registration if required | Open; founder |
| 4 | M1 to M8 complete, acceptance criteria pass in CI | In progress: 24 of 49 stories done, 1 partly done (S1.5); M5 to M8 not started |
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
