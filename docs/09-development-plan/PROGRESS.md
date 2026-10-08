# Implementation Progress

Tracks the build against `OUTPUT.md` in this directory. A story is listed as done only when its checks passed in CI on the main branch; the evidence column says where that can be seen. Updated at the end of each working session.

Last updated: 2026-10-08.

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
| S9.1 Counter code, waiting list, personal link, consent | Done | EVID-049, EVID-053; pull requests 21 and 31 | Consent texts are agent drafts without legal review; screens tested against a fake server only |
| S9.2 Customer notifications for entries, payments, reversals | Done | EVID-049; pull request 21 | Queued, never sent through the real Telegram API |
| S9.3 Customer debt page; isolation; disconnect; removal and anonymization | Done | EVID-049, EVID-053; pull requests 22 and 31 | The customer's page was tested against a fake server only |
| S10.1 Disputes | Done | EVID-050, EVID-053; pull requests 25 and 31 | |
| S10.2 Payment notices with receipt upload | Done | EVID-065, EVID-067; pull requests 36 and 50 | Brings the file store; receipts are served through five-minute signed links. The S3 adapter never ran against a real bucket, nor the Telegram file download against the real Bot API. Screens tested against a fake server only |
| S10.3 Date change requests; promise history | Done | EVID-058, EVID-064; pull requests 38 and 48 | Screens tested against a fake server only; concurrent requests not tested as races |

## Milestone M5: Reminders and credit control

| Story | State | Evidence | Notes |
|---|---|---|---|
| S11.1 Reminder settings and wording | Done | EVID-052, EVID-056; pull requests 29 and 34 | Wordings are agent drafts |
| S11.2 Hourly reminder job with catch-up | Done | EVID-052; pull request 29 | Not measured on a shop with thousands of debtors; nothing sent through a real channel |
| S11.3 Manual reminders, channels and limits | Done | EVID-052, EVID-056; pull requests 29 and 34 | No SMS provider is chosen; the SMS switch is off |
| S12.1 Credit limits | Done | EVID-053, EVID-056; pull requests 30 and 34 | |
| S12.2 Payment history indicator | Done | EVID-056; pull request 34 | |

## Milestone M6: Reports, import, panel, Russian

| Story | State | Evidence | Notes |
|---|---|---|---|
| S13.1 Period report and overdue debt by age | Done | EVID-057, EVID-064; pull requests 37 and 48 | A past period's report can change when one of its sales is reversed later (DEC-040) |
| S13.2 Exports as jobs with signed downloads | Done | EVID-068, EVID-069; pull requests 52 and 55 | One workbook for the whole shop. Opened in one copy of Excel only; never run against a real object store |
| S14.1 Import | Done | EVID-069, EVID-071; pull requests 53 and 58 | The worker checks, applies and undoes, as the architecture says. Screens tested against a fake server only. The template was not opened in a spreadsheet program, nor a file written by one read |
| S15.1 Web panel | Done | EVID-059; pull request 40 | Tested against a fake server and a stub of the Telegram widget. The widget needs the bot's domain set with BotFather, which is the founder's to do |
| S16.1 Russian catalogs, reviewed by a native speaker | Catalogs written; review blocked on the founder | - | Every Russian text is an agent's draft |

## Milestone M7: Subscription and administration

| Story | State | Evidence | Notes |
|---|---|---|---|
| S17.1 Subscription states, warnings, limited mode | Done | EVID-054, EVID-056; pull requests 32 and 34 | |
| S17.2 Pay by card transfer with receipts | Done | EVID-069, EVID-071; pull requests 54 and 58 | Administrators decide in the panel or by buttons in their chat and in the review group (DEC-051). Screens tested against a fake server only. Never run with a real bot, group or card: launch criterion 15 is the founder's |
| S17.3 Click and Payme adapters behind the switch | Done, switched off | EVID-060; pull request 44 | Never run against either provider's test environment; must be checked against the providers' current documentation before the switch is ever turned on |
| S18.1 Administrator sign-in and panel API | Done | EVID-066, EVID-067; pull requests 42 and 50 | Never used with a real authenticator application; screens tested against a fake server only |
| S18.2 Support access; admin audit | Done | EVID-067, EVID-069; pull requests 51 and 55 | Read-only: customers and their entries. Every look is audited and shown to the owner |
| S18.3 Measurement | Done | EVID-056; pull request 35 | Figures are totals over all shops; the export is an operator's command |
| S18.4 Shop deletion | Done | EVID-055, EVID-059, EVID-065; pull requests 33, 40 and 36 | Stored file objects are deleted before the shop's rows |

## Milestone M8: Hardening

| Story | State | Evidence | Notes |
|---|---|---|---|
| S19.1 Load test | Tooling done; first measurement recorded; the slow overview cured by stored open debts | EVID-063, EVID-070; pull requests 43 and 56; `docs/10-operations/load-test.md` | A developer machine, not the servers. With open debts stored the overview's totals for a shop of 200 298 entries went from 361 ms to 2.3 ms in single statements; the 30-minute run was not repeated |
| S19.2 Security review | Agent's review done; a person's review still needed | EVID-059, EVID-061; pull requests 41 and 45 | No critical or high finding in what was read. Findings 1 to 8, 10 and 12 fixed; 9, 11 and 13 fixed in part, what is left of each is in the table at the top of the review. Deployment, the administrator's side and most of the storage module were not read |
| S19.3 Failover and restore rehearsals, timed | Rehearsed locally only | EVID-044 | Real servers are blocked on the founder |
| S19.4 Runbooks, alerts, usability sessions, copy review | Partly: logs, metrics, alert rules, the thirteen runbooks, deployment files and the backup schedule are written | EVID-062, EVID-072; pull requests 47, 60 and 61; `docs/10-operations/runbooks.md`, `deploy/production/`, `deploy/backup/` | Proven only in containers on one machine. No runbook was executed and no alert ever triggered. Usability sessions and copy review need people |

API rate limits per user and per shop, listed before as not implemented, are in place (EVID-059; pull request 39).

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
| DEC-035 | Reminders: a new due date is reminded of regardless of an older reminder; `job_run` table; one SMS quota setting; fixed SMS wording | S11.1 to S11.3 |
| DEC-036 | Credit limits: range of a limit; all staff read the settings; the limit is not shown to the customer | S12.1 |
| DEC-037 | Subscription: the owner is told once when a period ends; the day of payment is the first paid day | S17.1 |
| DEC-038 | Shop deletion: what works during the 30 days; tombstone row; the database counts the wait | S18.4 |
| DEC-039 | Measurement: Monday-to-Monday weeks; totals over all shops; export by command | S18.3 |
| DEC-040 | Reports: reversals cancel out wherever they fall; opening balances shown apart; age bands | S13.1 |
| DEC-041 | Date change requests: the wait counted from the decline; what a direct change does to an open request; `decline_reason` column | S10.3 |
| DEC-042 | API rate limits: the numbers; per user; in one process's memory; a stranger is never told a shop is busy | Hardening |
| DEC-043 | Web panel: new screens in the panel only; CSRF token in memory, so a reload signs out | S15.1 |
| DEC-044 | Online payment: `online_payment` table; an order first; no cancelling a performed payment through Payme | S17.3 |
| DEC-045 | Security fixes: one mebibyte body limit; five minutes of clock allowance; deletion still allowed in a suspended shop | S19.2 |
| DEC-046 | Monitoring: what counts as a cross-tenant attempt; metrics behind a token; exception text never logged | S19.4 |
| DEC-047 | Load test and statement timeout: traffic mix; 5 and 60 second limits; the large shop's overview left to the founder | S19.1 |
| DEC-048 | Payment notices and files: 14 days; three open notices; how a receipt is judged safe; where a duplicate is looked for | S10.2 |
| DEC-049 | Administrator access: allow-list and account; lock-out; recovery by an operator; new tables and a new dependency | S18.1 |
| DEC-050 | Screens for date requests and reports: arrangement choices | S10.3, S13.1 |
| DEC-054 | Support access: opened by an administrator with a reason for at most 24 hours; reads customers and entries only | S18.2 |
| DEC-055 | Exports: one workbook for the whole shop; limits and retention; `stored_file.purpose` gained `export` | S13.2 |
| DEC-056 | Subscription receipts: stated months; duplicates looked for across shops; three years' retention; `stated_months` column | S17.2 |
| DEC-057 | Import: formats and limits; ambiguous rows block; undo is all or nothing | S14.1 |
| DEC-059 | Proxy and deployment: rate and body limits, headers, images built on the host | Deployment |
| DEC-060 | Backups: where timers run; how each retention figure is met; what is not covered | Deployment |
| DEC-061 | At most five shops and one trial a person; later shops start limited; narrowed database rights | S19.2 |
| DEC-062 | Reassigning a shop's owner; the former owner becomes a suspended manager; rotating the server secret | S18.1 |
| DEC-063 | Mini App session token in sessionStorage; panel sign-in by redirect, accepted only from our own site | S15.1, S19.2 |
| DEC-064 | Telegram administrators of the review group decide a receipt (the founder's; replaces the reading in DEC-051) | Subscription |
| DEC-065 | No limit on shops a person (the founder's; changes DEC-061 and DEC-062) | Shops |
| DEC-066 | A customer sees their own payment history indicator (the founder's; changes DEC-033) | Customers |
| DEC-067 | Earlier choices the founder reviewed and kept | Review |
| DEC-069 | SMS in release 1 through Eskiz (the founder's) | Reminders |
| DEC-070 | One host, Cloudflare Tunnel, backups to R2 (the founder's; replaces two servers) | Deployment |
| DEC-071 | Support access stays as built: a reason, read-only, visible to the owner | Administration |
| DEC-072 | The repository is public (decided by the founder) | Development |
| DEC-073 | Eskiz sender: what counts as sent, failed and retried | Reminders |
| DEC-074 | Generated API types: six read operations typed, the rest open | Development |
| DEC-075 | Single host: data leaves the country, one passphrase, no failover figure, no alerts | Deployment |
| DEC-076 | CI in four test jobs; documents-only runs; superseded runs cancelled | Development |
| DEC-068 | Three database roles, one for each part of the application; what each is granted; sign out everywhere | S19.2 |

Decided by the founder on 2026-10-07, and so not awaiting review: DEC-058 (nginx and Docker Compose, where the architecture document names Caddy; what to prepare before production); DEC-051 (buttons in the review group, honoured for platform administrators only, as the agent understood him), DEC-052 (no fresh code to decide a receipt), DEC-053 (store open debts).

## Deviations from the approved documents

| Item | Approved text | Implementation | Why |
|---|---|---|---|
| Creation of role `qd_app` | `LOGIN PASSWORD 'set-at-deploy'` in `schema.sql` | Created `NOLOGIN`; login and password set at deploy | No password literal in the repository (DEC-021) |
| Layer order for the import contract | Layers named without ordering infrastructure | interface, infrastructure, application, domain | A single order is needed for an automatic check |
| Dependency lock | "Pinned with hashes" | Hash-pinned Linux lock generated with uv | pip-tools did not finish |
| Tables, columns and functions beyond `schema.sql` | Not present | Tables `user_session`, `ownership_transfer`, `chat_pending`; `job_run`, `measure.weekly`, `online_payment`, `admin_audit`, `admin_session`, `admin_request_key`, `export_job`, `open_debt`, `signin_replay`; columns `app_user.trial_used_at`, `catalog_item.merged_into`, `customer_link.waiting_name`, `date_change_request.decline_reason`; functions `mark_recipient_unreachable`, `accept_staff_invitation`, `my_memberships`, `customer_token_info`, `link_customer`, `my_accounts`, `my_link`, `end_my_link`, `mark_recipient_reachable`, `forget_user_if_unused`, `shops_due_for_reminders`, `subscriptions_to_review`, `shops_to_erase`, `erase_shop`, `online_payment_shop`, `online_payment_shop_by_txn`, `payme_statement`, `shops_with_receipt_work`, `admin_shop_search`, `admin_shop_receipts`, `admin_lock_subscription`, `admin_store_subscription`, `admin_set_platform_setting`, `purge_expired_sign_ins`, `claim_owned_shop`, `admin_notice_recipients`; roles `qd_admin`, `qd_worker`; trigger `shop_deletion_guard`; indexes `ledger_reversal_shop`, `ledger_shop_customer` | DEC-021, DEC-022, DEC-025, DEC-029, DEC-030, DEC-032, DEC-033, DEC-035, DEC-038, DEC-039, DEC-041, DEC-044, DEC-045, DEC-047, DEC-048, DEC-049, DEC-053, DEC-055, DEC-056, DEC-057, DEC-068 |
| Order of consent and the waiting entry | BR-16 allows the waiting entry before consent | Consent first; a constraint refuses a waiting entry without it | Stricter, and simpler to explain to a customer (DEC-032) |
| Ruff rule S608 in the storage module | All security rules on | Off for `infrastructure/db.py`, replaced by a stricter project test | The rule cannot tell a constant SQL fragment from user input (DEC-027) |
| Rate limits on sign-in | Specified | The API limits signed-in callers per user and per shop; requests that are not signed in are left to the proxy, which does not exist yet | Limits by network address belong in front of the application (DEC-042) |

## Not yet proven, stated plainly

- Nothing has run on a real server. Every result is from a developer machine or CI.
- Telegram has never called the webhook, and no real Telegram client has signed in: there is no public HTTPS address.
- The Uzbek and Russian texts were written by agents and not reviewed by native speakers.
- No performance target (NFR-001 to NFR-013) has been measured except the bundle size in NFR-010.
- The failover and restore rehearsal ran in containers on one machine. It proves the script, not the servers.
- No customer has been notified of anything through Telegram: notifications are queued and tested, never sent.
- The overview queries have been run on tens of accounts, not on a shop with thousands of customers.
- The main branch was red for one commit (pull request 21) between 19:00 and 24:00 UTC because a test used the server's date instead of the Tashkent date. The application was right; the test was fixed in the next commit (EVID-049).
- Whether removal may wait for a debt to be settled (BR-32), and the consent texts, have had no legal review.
- The Payme and Click adapters have never been run against either provider's test environment. They are switched off.
- The web panel has never used the real Telegram sign-in widget.
- The security review was done by an agent, not a person, and lists what it did not read.
- The load test ran on a developer machine only. The two targets missed there for a very large shop were cured by storing open debts, measured in single statements; the full run was not repeated. It is not launch criterion 6.
- Subscription receipts were never sent to a real bot or decided in a real group; exports and imports were never opened in or produced by the spreadsheet programs shop owners use, beyond one copy of Excel for the export.
- No runbook has been executed.
- The front end and the back end run together only in the end-to-end suite, with signed stand-in data: no real Telegram client and no real Login widget has been used (EVID-076).
- Deployment and backups were proven in containers on one machine with a self-signed certificate. Nothing ran on a server, under systemd, or between two hosts; whether the proxy sees clients' real addresses is unknown until then (EVID-072).
- There is no monitoring system: the logs, metrics and alert rules exist, and no alert was ever triggered or received.
- The API, the administrators' side and the worker connect as three database roles (pull request 68, DEC-068), which was proven in tests and in the end-to-end stack only. The administrators' side is still served by the API's process, which holds two of the three connections. What is left of security findings 9, 11 and 13 is in the table at the top of `docs/10-operations/security-review.md`.
- The file store's S3 adapter never ran against a real bucket; receipts were sanitized on hand-built samples, not on photographs from real phones.
- No administrator has signed in with a real authenticator application.

## Launch criteria (docs/10-operations/OUTPUT.md)

| No. | Criterion | State |
|---|---|---|
| 1 | Interviews and pDaftar test | Open; founder |
| 2 | Legal review | Open; founder |
| 3 | Registration if required | Open; founder |
| 4 | M1 to M8 complete, acceptance criteria pass in CI | 42 of 49 stories done in code and CI; S1.5, S16.1, S19.1, S19.2, S19.3 and S19.4 are partly done and each waits on something only people or real servers can give; S2.2's generated API client was never built. Every story's screens exist; fourteen end-to-end tests run the front end and back end together with signed stand-in data (EVID-076) |
| 5 | Authorization and tenant suite covers every operation | In place and blocking; grows with each story |
| 6 | Load test | Not met: measured on a developer machine only; the slow overview was cured and re-measured in single statements, not in a full run (EVID-063, EVID-070) |
| 7 | Security review | An agent's review is done; findings 1 to 8 and 10 to 12 are fixed and 9 and 13 in part (EVID-059, EVID-061, EVID-074, EVID-078); the administrator side still shares the API process; a person's review remains |
| 8 | Failover and restore rehearsals | Rehearsed locally in containers (EVID-044); a weekly automatic restore test is written and proven in containers (EVID-072); not on real servers |
| 9 | Alerts triggered and received | Not started: rules are written (EVID-062), no monitoring system exists |
| 10 | Runbooks executed | Not started: the thirteen runbooks are written (`docs/10-operations/runbooks.md`), none was executed |
| 11 | Usability sessions | Not started; needs real sellers |
| 12 | Two servers in use | Not met and no longer planned: by DEC-070 the service runs on one host; the criterion has to be rewritten by the founder |
| 13 | Second operator named | Open; founder |
| 14 | Both languages reviewed | Not started; needs native speakers |
| 15 | Payment process run end to end with a test shop | Not started |
| 16 | Launch approval | Open; founder |

## What the founder is needed for

1. A public HTTPS address for the development bot (a tunnel or a server), so that the webhook, the Mini App and Telegram Login can be exercised from a real Telegram client. Put it in `QD_PUBLIC_BASE_URL`.
2. Two hosting providers or facilities in Uzbekistan (S1.5).
3. The shopkeeper interviews and the pDaftar test (launch criterion 1). By DEC-020 they no longer block the build.
4. Review of the pending decisions listed above.
