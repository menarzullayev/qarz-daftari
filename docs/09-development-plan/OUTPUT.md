# Development Plan

Version 2. Status: rewritten for release 1 as defined in PRD version 2 (DEC-013 / APR-013); awaiting the founder's end-of-sequence review (DEC-018). Prepared 2026-10-06.
Version 1 (pilot MVP, 18 to 31 days, DEC-010) is superseded and remains in version history.

**How to read the estimates.** Focused working days for one founder working full time with AI agents. They are agent estimates with no historical data from this project behind them, given as ranges. They are not commitments. The total is about five times the version 1 plan, because the scope is.

**What the founder decided about sequencing.** Everything in release 1 is built before any shop uses it (2026-10-06). The plan follows that. The agent's advice, a trial with two or three shops after the customer side is built, is recorded as declined; the plan marks the point where that trial would fit, so the choice can be revisited without replanning.

## Milestones

| ID | Milestone | Outcome | Exit condition |
|---|---|---|---|
| M0 | Validation and legal track | Open checks answered | See below; runs in parallel; gates as stated |
| M1 | Platform foundation | Repository, CI, schema with tenant isolation, authentication for all clients, empty API and front end deployed to two servers | A user signs in through chat, Mini App, and web; cross-tenant test suite runs; failover rehearsed once on empty data |
| M2 | Shops, staff, core ledger | Staff with roles record amount-only sales and payments in chat and Mini App, reverse, and see balances; several shops per person | FEAT-001 to FEAT-004, FEAT-009, FEAT-010, FEAT-014, FEAT-023, FEAT-026 pass acceptance |
| M3 | Catalog and itemized entry | Catalog, learned goods, lines, promised dates | FEAT-016, FEAT-003 in full, FEAT-017 for staff |
| M4 | Customer side | Linking with consent, notifications, debt page, disputes, payment notices, date requests | FEAT-005, FEAT-006, FEAT-007, FEAT-024, FEAT-027 |
| M5 | Reminders and credit control | Reminders on Telegram, SMS adapter behind its switch, limits, payment history indicator | FEAT-008, FEAT-013, FEAT-018 |
| M6 | Reports, export, import, web panel, Russian | Owner reports, exports, ledger import, desktop layouts, full second language | FEAT-019, FEAT-011, FEAT-025, FEAT-020, FEAT-022 |
| M7 | Subscription and administration | Trial, limited mode, receipts and approval, admin panel, switches, support access, payment adapters off | FEAT-015, FEAT-021, FEAT-012 |
| M8 | Hardening and launch readiness | Load test, security review, failover and restore rehearsals, runbooks, language and usability review | Launch criteria in Stage 10 met except the founder's approval |
| M9 | Launch | First shops onboarded on the production service | Founder's launch approval recorded |

**Validation and legal track (M0).** No code. The first two gates were approved in DEC-010 and have not been withdrawn.

| Check | Gates | Must finish before |
|---|---|---|
| 15 to 20 shopkeeper interviews; the founder's own answers (EVID-034) say what to listen for | Stop conditions from Market Research; nearly all of release 1 rests on EVID-034 | M4 |
| Hands-on test of pDaftar | Whether the incumbent already covers the scope | M4 |
| Legal review of the open questions in the Technical Specification, including payments to a personal card (EVID-035) and the consent text | Any real customer data; any payment | M9; much better before M4 |
| Two hosting providers or facilities in Uzbekistan chosen and reachable from Telegram | ADR-014 | End of M1 |
| Review group and administrator procedures agreed | REQ-055 | M7 |

## Epics

| ID | Epic | Milestone |
|---|---|---|
| E1 | Foundation: repository, CI, schema, tenant isolation, sessions, deployment on two servers | M1 |
| E2 | Identity and clients: chat, Mini App, web sign-in, language | M1 |
| E3 | Shops and staff: memberships, roles, invitations, ownership, active shop | M2 |
| E4 | Customers and core ledger: customers, entries, payments, reversals, balances, overview | M2 |
| E5 | Activity log | M2 |
| E6 | Catalog and goods lines | M3 |
| E7 | Promised dates | M3 |
| E8 | Staff workspace screens | M3 |
| E9 | Customer linking, notifications, debt page | M4 |
| E10 | Disputes, payment notices, date requests | M4 |
| E11 | Reminders and SMS adapter | M5 |
| E12 | Credit limits and payment history | M5 |
| E13 | Reports and export | M6 |
| E14 | Import | M6 |
| E15 | Web panel layouts | M6 |
| E16 | Russian language | M6 |
| E17 | Subscription, receipts, limited mode, payment adapters | M7 |
| E18 | Administration panel and measurement | M7 |
| E19 | Hardening: load, security, recovery, runbooks | M8 |

## Stories

| ID | Story | Requirements | Epic |
|---|---|---|---|
| S1.1 | Repository, layered modules with import rules, linting, typing, tests, CI for backend and front end | REQ-N07 | E1 |
| S1.2 | Schema from `schema.sql` as migrations; roles; row-level security; the schema checks as automated tests | REQ-011, REQ-N06, REQ-N07, REQ-N12 | E1 |
| S1.3 | Tenant context and authorization framework; cross-tenant and cross-role test harness | REQ-N11, REQ-N12 | E1 |
| S1.4 | Webhook, idempotency for updates and API writes, outbox and dispatcher | REQ-015 | E1 |
| S1.5 | Two servers: proxy, Compose, replication, archiving, file store, scripted failover | REQ-N04, REQ-N08, REQ-N09 | E1 |
| S2.1 | Sign-in from chat, Mini App launch data, and Telegram Login; sessions | REQ-050, REQ-N11 | E2 |
| S2.2 | Front-end shell with three entry points, generated API client, message catalogs | REQ-049, REQ-051, REQ-N15 | E2 |
| S3.1 | Create shop; settings | REQ-001 | E3 |
| S3.2 | Invite, join, suspend, remove staff; role checks everywhere | REQ-031, REQ-032, REQ-033, REQ-034 | E3 |
| S3.3 | Ownership transfer | REQ-036 | E3 |
| S3.4 | Several shops per person; active shop; owner combined totals | REQ-064, REQ-065 | E3 |
| S4.1 | Customers: add, search with transliteration, edit, archive | REQ-003, REQ-004, REQ-005 | E4 |
| S4.2 | Chat fast entry parser in two languages | REQ-006, REQ-N02, REQ-N03 | E4 |
| S4.3 | Record credit and payment; balance and allocation; reply with balance | REQ-007, REQ-009, REQ-010, REQ-N06 | E4 |
| S4.4 | Reversal by managers and owners | REQ-011, REQ-012 | E4 |
| S4.5 | Overview: totals, balances, overdue, filters; customer detail | REQ-026, REQ-027 | E4 |
| S5.1 | Activity records on every change; owner's activity view | REQ-035, REQ-047 | E5 |
| S6.1 | Catalog management; learned items and their review | REQ-039, REQ-040, REQ-041 | E6 |
| S6.2 | Itemized entry screen and API; line rounding; totals | REQ-037, REQ-N02 | E6 |
| S6.3 | Add lines later to an amount-only entry | REQ-038 | E6 |
| S7.1 | Promised date choices in chat and Mini App; shop default | REQ-008 | E7 |
| S8.1 | Staff workspace screens: customers, entry, overview, settings by role | REQ-049 | E8 |
| S9.1 | Counter code, waiting list, personal link, consent | REQ-013, REQ-014 | E9 |
| S9.2 | Customer notifications for entries, payments, reversals, with goods and dates | REQ-015 | E9 |
| S9.3 | Customer debt page; isolation; disconnect; removal request and anonymization | REQ-019, REQ-020, REQ-021, REQ-029 | E9 |
| S10.1 | Disputes: open, decline, withdraw, close on reversal | REQ-016, REQ-017 | E10 |
| S10.2 | Payment notices with receipt upload; accept and decline | REQ-060, REQ-061 | E10 |
| S10.3 | Date change requests; promise history | REQ-066, REQ-067 | E10 |
| S11.1 | Reminder settings, templates per language, hour | REQ-022, REQ-024, REQ-042 | E11 |
| S11.2 | Hourly job, eligibility, limits, dispute exclusion; manual reminder | REQ-023, REQ-025, REQ-N10 | E11 |
| S11.3 | SMS adapter, quota, switch; not-reachable list | REQ-043 | E11 |
| S12.1 | Credit limits and warnings by role | REQ-044 | E12 |
| S12.2 | Payment history indicator | REQ-045 | E12 |
| S13.1 | Period reports | REQ-046 | E13 |
| S13.2 | Exports as jobs with signed downloads | REQ-028 | E13 |
| S14.1 | Import: template, validation, preview, apply, undo | REQ-062, REQ-063 | E14 |
| S15.1 | Desktop layouts for the web panel; staff, activity, subscription screens | REQ-050, REQ-N15 | E15 |
| S16.1 | Russian catalogs for chat, notifications, and screens; reviewed by a native speaker | REQ-051, REQ-N01 | E16 |
| S17.1 | Trial, paid-through, warnings, limited and suspended modes | REQ-052, REQ-053, REQ-057 | E17 |
| S17.2 | Pay by card transfer: card display, receipt upload, forwarding, approve and reject | REQ-054, REQ-055 | E17 |
| S17.3 | Click and Payme adapters behind the switch | REQ-056 | E17 |
| S18.1 | Admin sign-in with allow-list and second factor; shops, receipts, settings, suspension | REQ-058, REQ-N14 | E18 |
| S18.2 | Support access with owner visibility; admin audit | REQ-059 | E18 |
| S18.3 | Measurement records and weekly export | REQ-030 | E18 |
| S18.4 | Shop deletion with waiting period and erasure | REQ-048 | E18 |
| S19.1 | Load test with generated data against the performance targets | REQ-N13 | E19 |
| S19.2 | Security review: authorization matrix, tenant suite, file handling, session handling | REQ-N11, REQ-N12 | E19 |
| S19.3 | Failover and point-in-time restore rehearsals, timed | REQ-N08, REQ-N09 | E19 |
| S19.4 | Runbooks, alerts proven, usability sessions, copy review | REQ-N02, REQ-N05 | E19 |

## Tasks

Each story is broken into tasks when its milestone starts. The first milestone is broken down now because it carries the most risk:

- **S1.1** Monorepo with `backend` and `frontend`; module layout mirroring the architecture; import-rule check; formatter, linter, type checker, unit and integration test runners; CI with a PostgreSQL service; dependency scans.
- **S1.2** Convert `schema.sql` to migrations run by a separate owner role; port `tests/schema_checks.sql` to the test suite; add a clock-controlled test for the goods-line time limit.
- **S1.3** Request context carrying user, shop, role; transaction wrapper that sets the tenant; authorization decorator; harness that calls every registered operation as each role and as members of another shop.
- **S1.4** Webhook handler; processed-update table; idempotency-key middleware; outbox repository and dispatcher with per-channel limits and retry.
- **S1.5** Order two servers; private tunnel; Compose files; streaming replica; pgBackRest; file store with replication; failover script; staging on the standby.
- **S2.1** Three sign-in flows with signature validation and age checks; session store; language preference.
- **S2.2** Front-end build with three entry points; API client generation; catalogs; a first screen in each.

## Estimates

| Milestone | Low | High | Main uncertainty |
|---|---|---|---|
| M1 Platform foundation | 12 | 18 | Two-server setup in Uzbekistan; row-level security with pooled connections; three sign-in flows |
| M2 Shops, staff, core ledger | 12 | 18 | Role checks across every operation; chat parser in two languages |
| M3 Catalog and itemized entry | 10 | 15 | Entry screen speed on low-end phones |
| M4 Customer side | 12 | 18 | Linking at the counter; three request types with their limits |
| M5 Reminders and credit control | 8 | 12 | SMS adapter without a provider to test against |
| M6 Reports, export, import, web panel, Russian | 16 | 24 | Report correctness; import validation; a whole second language |
| M7 Subscription and administration | 12 | 18 | Payment adapters without merchant accounts; admin security |
| M8 Hardening and launch readiness | 10 | 15 | Findings from load and security work send work back |
| **Build total** | **92** | **138** | |

At five focused days a week this is roughly 18 to 28 weeks before the first shop uses the product, not counting the validation track, illness, or rework. Estimates of this size made in advance are usually low.

Recurring cost during build and after: two servers at about 125,000 to 250,000 UZS a month each (EVID-031); a domain; backup storage if a third location is found; a legal consultation; SMS and payment provider costs only once those are switched on.

## Dependencies

| Item | Depends on |
|---|---|
| Everything | M1 |
| M3, M4 | M2 |
| M4 customer notifications with goods | M3 |
| M5 reminders | M4 links; M3 promises |
| M6 reports | M2 to M5 data; import depends only on M2 |
| M7 limited mode | M2 commands, which must already consult subscription state |
| M8 | M1 to M7 |
| M9 | M8; legal review; launch approval |
| External | Two hosting providers in Uzbekistan; a domain and certificates; Telegram Bot API, Mini App, and Login; a native Russian reviewer |
| Blocked until a registered entity exists | Production use of SMS and of Click and Payme |

## Critical path

M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8 → M9.

Work off the critical path: import (S14.1) and activity view (S5.1) any time after M2; Russian catalogs can be filled as each milestone ends instead of all in M6; the administration panel shell can start after M1.

Decision points on the path:

1. **After M4**, about 46 to 69 days in: the product can record itemized credit, link customers, and handle disputes and requests. This is the earliest point at which real shops could try it. The founder has decided not to; the interviews and the pDaftar test are due here regardless.
2. **Before M9**: legal review complete.

## Risks

| Risk | Likelihood | Impact | Response |
|---|---|---|---|
| The product is built for five months on untested assumptions and shops do not want it, or want something different | High | Most of the build wasted | Interviews before M4; the optional trial after M4 remains available |
| A stop condition is met when the validation track finally runs | Medium | Project ends late and expensively | Run M0 now, in parallel with M1 |
| Estimates are low | High | Launch slips by months | Re-estimate at every milestone; cut scope by decision, not by drift |
| Shops will not pay 100,000 UZS against a free incumbent tier (EVID-021) | High | No revenue | Price is a setting (ADR-018); ask in interviews |
| Payments to a personal card, or selling through a bot outside Telegram's mechanism, turn out not to be permissible (EVID-035, EVID-028) | Medium | Revenue blocked; bot restricted | Legal review; adapters ready for when an entity exists |
| Legal review rejects recording before consent, the reliability indicator, or Telegram notifications with goods | Medium | Redesign | Seek advice before M4 |
| One person builds and operates everything | High | Any absence stops the project; after launch, outages go unattended | Runbooks; a second person able to fail over, named before launch |
| Row-level security or session handling is implemented wrongly | Medium | Data of one shop exposed to another | Test harness from M1; security review in M8 |
| Two-server operation in Uzbekistan is unreliable | Medium | Availability target missed | Rehearse failover in M1 and M8; measure before promising |
| SMS and payment adapters are wrong when first switched on | Medium | Failures at the moment of first revenue | Switch on for one shop first; keep manual receipts as fallback |
| Scope grows again | High | Further delay | Every addition is a recorded decision with its estimate |

## Release plan

| Stage | Who | Entry condition | Data |
|---|---|---|---|
| Development | Founder with test bots | M1 | Invented |
| Staging | Founder, on the standby server | M1 onward, every milestone | Invented, generated at scale for load tests |
| Internal acceptance | Founder acting in every role across two test shops | End of each milestone | Invented |
| Launch | First real shops | M8 complete; legal review; founder's launch approval | Real, with consent |

By the founder's decision there is no pilot stage between internal acceptance and launch. To limit the damage of a fault found only in real use, launch onboards shops one at a time for the first two weeks; this is an operational safeguard the agent added and the founder may remove.

Release mechanics: continuous integration on every change; tagged releases deployed by script outside shop hours; forward-only, backward-compatible migrations so that rollback is a redeploy of the previous images; point-in-time recovery available for mistakes (ADR-015).

## Traceability to requirements

| Requirements | Stories |
|---|---|
| REQ-001 | S3.1 |
| REQ-003, REQ-004, REQ-005 | S4.1 |
| REQ-006 | S4.2 |
| REQ-007, REQ-009, REQ-010 | S4.3 |
| REQ-008 | S7.1 |
| REQ-011, REQ-012 | S1.2, S4.4 |
| REQ-013, REQ-014 | S9.1 |
| REQ-015 | S1.4, S9.2 |
| REQ-016, REQ-017 | S10.1 |
| REQ-019, REQ-020, REQ-021, REQ-029 | S9.3 |
| REQ-022, REQ-024, REQ-042 | S11.1 |
| REQ-023, REQ-025 | S11.2 |
| REQ-026, REQ-027 | S4.5 |
| REQ-028 | S13.2 |
| REQ-030 | S18.3 |
| REQ-031, REQ-032, REQ-033, REQ-034 | S3.2 |
| REQ-035, REQ-047 | S5.1 |
| REQ-036 | S3.3 |
| REQ-037 | S6.2 |
| REQ-038 | S6.3 |
| REQ-039, REQ-040, REQ-041 | S6.1 |
| REQ-043 | S11.3 |
| REQ-044 | S12.1 |
| REQ-045 | S12.2 |
| REQ-046 | S13.1 |
| REQ-048 | S18.4 |
| REQ-049 | S2.2, S8.1 |
| REQ-050 | S2.1, S15.1 |
| REQ-051 | S2.2, S16.1 |
| REQ-052, REQ-053, REQ-057 | S17.1 |
| REQ-054, REQ-055 | S17.2 |
| REQ-056 | S17.3 |
| REQ-058 | S18.1 |
| REQ-059 | S18.2 |
| REQ-060, REQ-061 | S10.2 |
| REQ-062, REQ-063 | S14.1 |
| REQ-064, REQ-065 | S3.4 |
| REQ-066, REQ-067 | S10.3 |
| REQ-N01 | S16.1 |
| REQ-N02, REQ-N03 | S4.2, S6.2, S19.4 |
| REQ-N04 | S1.5 |
| REQ-N05 | S9.3, S19.4 |
| REQ-N06, REQ-N07 | S1.1, S1.2, S4.3 |
| REQ-N08, REQ-N09 | S1.5, S19.3 |
| REQ-N10 | S11.2 |
| REQ-N11, REQ-N12 | S1.3, S2.1, S19.2 |
| REQ-N13 | S19.1 |
| REQ-N14 | S18.1 |
| REQ-N15 | S2.2, S15.1 |

Decision records and where they are implemented: ADR-002, ADR-003, ADR-012 and ADR-013 in S1.1 and S2.2; ADR-004, ADR-005 and ADR-016 in S1.2 and S1.3; ADR-006 and ADR-007 in S1.4; ADR-014, ADR-015 and ADR-020 in S1.5; ADR-017 in S2.1 and S18.1; ADR-011 in S2.2 and S8.1; ADR-018 and ADR-019 in S17.1 to S18.1; ADR-010 in S18.3; ADR-021 in S2.2 and S16.1.

Every requirement of PRD version 2 that is not withdrawn appears in the table.

## Assumptions

- The founder works on this full time (stated 2026-10-06) with AI agents doing most implementation.
- No one else contributes code, design, or operations. Design of the interfaces is done by the founder and agents without a designer.
- A native Russian speaker is available for review.
- Two hosting providers in Uzbekistan can be contracted by an individual.
- The scope does not grow again during the build.

## Open questions

1. Will the validation track run now, given that it has been deferred at four earlier points?
2. Who is the second person able to fail over and to post an outage notice?
3. How will the first shops be recruited for launch, and how many at once?
4. Is five months of full-time work without revenue or user feedback acceptable to the founder? This is a personal and financial question the plan cannot answer.

## Approvals

| Record | Subject | Status |
|---|---|---|
| DEC-010 / APR-010 | Version 1 plan | Superseded; its two gates are carried into M0 |
| DEC-018 | Version 2 plan: milestones M1 to M9, a build estimate of 92 to 138 focused days without calendar commitment, no pilot stage by founder decision, shops onboarded one at a time for the first two weeks, and the validation and legal track with its gates | Pending end-of-sequence review |
