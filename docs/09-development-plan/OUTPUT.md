# Development Plan

Status: draft for review; awaiting human approval of scope sequencing, estimates and the pilot gate (DEC-010). Prepared 2026-10-06.
Upstream: Technical Specification (DEC-009), decision records ADR-001 to ADR-010, PRD (DEC-005).

**How to read the estimates.** They are in focused working days for one founder working with AI agents. They are agent estimates with no historical data behind them, so each is a range, and the plan commits to no calendar dates: the founder's weekly availability is unknown. Treat the totals as an order of magnitude.

## Milestones

| ID | Milestone | Outcome | Exit condition |
|---|---|---|---|
| M0 | Validation and legal gate | The four open checks are answered | See "Validation track" below; gates M6, and partly M3 |
| M1 | Foundation | Repository, continuous integration, schema, and an empty bot deployed to the production server | `/start` answers from the server in Uzbekistan; migrations run; CI is green |
| M2 | One-sided ledger | An owner can run their whole notebook in the bot without any customer involved | FEAT-001 to FEAT-004, FEAT-009, FEAT-010 pass their acceptance criteria |
| M3 | Two-sided ledger | Customers link, see balances, confirm and dispute | FEAT-005 to FEAT-007 pass their acceptance criteria |
| M4 | Reminders, data rights, measurement | Reminders, export, removal, pilot metrics | FEAT-008, FEAT-011, FEAT-012 pass their acceptance criteria |
| M5 | Pilot readiness | The system is safe to give to real shops | Restore rehearsed and timed; alerts proven; runbooks written; usability test done; legal gate passed |
| M6 | Pilot | Eight weeks with about ten shops | Pilot exit criteria from the PRD evaluated; continue, change, or stop decided |

**Validation track (M0), run by the founder in parallel with M1 and M2.** None of it requires code.

| Check | Why it gates | Must finish before |
|---|---|---|
| Hands-on test of pDaftar | Stop condition 2 in Market Research; shows whether per-entry confirmation already exists | M3 (the customer side is the differentiation) |
| 15 to 20 shopkeeper interviews (`docs/02-problem-discovery/interview-guide.md`) | Stop conditions 1 and 3; tests EVID-012 and EVID-013 | M3 |
| Legal review: four questions and the consent text (Technical Specification, open question 1) | Compliance with the personal data law (EVID-027) | M6; ideally before M3, because an adverse answer changes the linking design |
| Hosting provider and backup location chosen | ADR-008 leaves it open | End of M1 |

If the interviews or the pDaftar test trip a stop condition, work stops after M2 at the latest. M2 is useful to build either way only in the sense that it is cheap; it is not differentiated.

## Epics

| ID | Epic | Milestone | Features |
|---|---|---|---|
| E1 | Platform foundation | M1 | Enables all |
| E2 | Shop and customer management | M2 | FEAT-001, FEAT-002 |
| E3 | Ledger core | M2 | FEAT-003, FEAT-004, FEAT-009, FEAT-010 |
| E4 | Customer linking and consent | M3 | FEAT-005 |
| E5 | Acknowledgement and customer view | M3 | FEAT-006, FEAT-007 |
| E6 | Reminders | M4 | FEAT-008 |
| E7 | Data rights and measurement | M4 | FEAT-011, FEAT-012 |
| E8 | Operations and pilot readiness | M5 | Non-functional requirements |

## Stories

| ID | Story | Requirements | Epic |
|---|---|---|---|
| S1.1 | Project skeleton with layered structure, linting, type checking, tests, and CI | REQ-N07 | E1 |
| S1.2 | Database schema, migrations, and separate roles with immutability enforced | REQ-011, REQ-N06, REQ-N07 | E1 |
| S1.3 | Webhook endpoint with secret check, duplicate handling, and health endpoint | REQ-N11, REQ-N09 | E1 |
| S1.4 | Outbox table and dispatcher with rate limits and retry | REQ-015 | E1 |
| S1.5 | Production server in Uzbekistan, Docker Compose deployment, TLS | REQ-N04 | E1 |
| S2.1 | Owner creates a shop with `/start` | REQ-001, REQ-002 | E2 |
| S2.2 | Owner adds, finds, renames, and archives customers, with Latin and Cyrillic matching | REQ-003, REQ-004, REQ-005 | E2 |
| S3.1 | Entry parser for "name amount" messages, including amount formats and notes | REQ-006, REQ-N01, REQ-N03 | E3 |
| S3.2 | Record a credit sale with default and custom due dates; reply with balance | REQ-006, REQ-007, REQ-008, REQ-010 | E3 |
| S3.3 | Record a payment; reject overpayment | REQ-009, REQ-010 | E3 |
| S3.4 | Reverse an entry; history shows both | REQ-011, REQ-012 | E3 |
| S3.5 | Balance, allocation, and overdue calculation in the domain layer | REQ-009, REQ-N06 | E3 |
| S3.6 | Totals, customer list by balance, overdue list, customer history | REQ-026, REQ-027 | E3 |
| S4.1 | Owner generates a personal link and QR image; tokens hashed and single use | REQ-013, REQ-N11 | E4 |
| S4.2 | Customer consent screen; link created only on agreement | REQ-014 | E4 |
| S4.3 | Customer disconnects; owner is told | REQ-021 | E4 |
| S5.1 | Customer is notified of every entry, payment, and reversal | REQ-015, REQ-012 | E5 |
| S5.2 | Customer confirms or disputes; owner sees disputes flagged | REQ-016, REQ-017, REQ-018 | E5 |
| S5.3 | Customer views balance and history; isolation between shops and customers | REQ-019, REQ-020 | E5 |
| S6.1 | Owner turns reminders on, picks template and hour, opts customers out | REQ-022, REQ-024 | E6 |
| S6.2 | Hourly reminder job with eligibility rules and frequency limits | REQ-023, REQ-N10 | E6 |
| S6.3 | Manual reminder from the overdue list, once a day per customer | REQ-025, REQ-N10 | E6 |
| S7.1 | Ledger export as a spreadsheet | REQ-028 | E7 |
| S7.2 | Removal request and anonymization, immediate or deferred | REQ-029, REQ-N05 | E7 |
| S7.3 | Identity-free measurement records and weekly metrics export | REQ-030 | E7 |
| S8.1 | Encrypted daily backups to a second location; restore rehearsed and timed | REQ-N08 | E8 |
| S8.2 | Uptime check, alerts, daily operator digest | REQ-N09 | E8 |
| S8.3 | Usability test of recording speed with two or three owners | REQ-N02 | E8 |
| S8.4 | Uzbek message catalog reviewed by a native speaker; build fails on missing strings | REQ-N01 | E8 |

## Tasks

Tasks are listed per story at the level needed to start; finer breakdown happens when a milestone begins.

**M1**
- S1.1: create the code repository; set up package layout (`interface`, `application`, `domain`, `infrastructure`); configure formatter, linter, type checker, test runner; CI workflow running all four plus a dependency scan.
- S1.2: write the first migration from the specification's schema; create roles `qd_owner`, `qd_app`, `qd_ro`, `qd_backup`; test that `qd_app` cannot update or delete entries.
- S1.3: webhook handler; constant-time secret check; `processed_update` deduplication; `/healthz`.
- S1.4: outbox repository; dispatcher loop with per-chat and global limits; handling of Telegram errors 429 and 403.
- S1.5: order the server; harden it (firewall, SSH keys, updates); Compose file for `bot`, `db`, `proxy`; register the bot and set the webhook; deploy script.

**M2**
- S2.1, S2.2: conversation handlers; name normalization and transliteration; search; archive rule.
- S3.1: grammar parser with a table of test cases covering every example in the specification plus malformed input.
- S3.2 to S3.4: application commands with row locking; replies; reverse button and confirmation step.
- S3.5: pure domain functions with property-based tests: balance never negative, reversal restores the previous balance, total equals the sum of account balances.
- S3.6: overview queries; paging of long lists.

**M3**
- S4.1: token generation and hashing; deep link; QR image.
- S4.2: consent text version 1 as reviewed; accept and decline paths; uniqueness rules for links.
- S4.3: disconnect flow.
- S5.1: notification templates; events wired to the outbox inside the command transaction.
- S5.2: callback handlers; dispute reason prompt; state machine tests for every allowed and forbidden transition.
- S5.3: `/qarzim` with paging; authorization tests that try every cross-shop and cross-customer access.

**M4**
- S6.1 to S6.3: settings handlers; hourly job; eligibility query; dispute exclusion; limit tests including the "no reminder at zero balance" and "opted-out" cases.
- S7.1: spreadsheet generation.
- S7.2: anonymization procedure; deferred processing job; test that no identifying field survives.
- S7.3: measurement writes; weekly export script; schema test that the measurement schema holds no identifying columns.

**M5**
- S8.1: backup script, encryption, shipping, pruning; full restore onto a fresh server with the time recorded.
- S8.2: external check; alert rules; digest; a deliberate failure of each alert to prove it fires.
- S8.3: sit with owners, time them, adjust the grammar.
- S8.4: language review.
- Runbooks and launch checklist as defined in Stage 10.

## Estimates

| Milestone | Low | High | Main uncertainty |
|---|---|---|---|
| M1 Foundation | 3 | 5 | Ordering and reaching a server in Uzbekistan; untested webhook reachability |
| M2 One-sided ledger | 4 | 7 | Parser edge cases; overview rendering in chat |
| M3 Two-sided ledger | 4 | 7 | Consent and linking rules; authorization tests |
| M4 Reminders, data rights, measurement | 4 | 6 | Reminder eligibility logic |
| M5 Pilot readiness | 3 | 6 | Restore rehearsal; usability findings may send work back to M2 |
| **Build total** | **18** | **31** | |
| M6 Pilot | 8 weeks elapsed | | Roughly half a day a week of support, plus recruiting shops |

Validation track effort, founder's own time: interviews two to four days; pDaftar test half a day; lawyer consultation unknown.

Recurring cost during build and pilot: one server at about 125,000 to 250,000 UZS a month (EVID-031), backup storage at a second provider (price not researched), and a legal consultation (price not researched). No other paid service is required.

## Dependencies

| Item | Depends on |
|---|---|
| M1 deployment (S1.5) | Hosting provider chosen (M0) |
| M2 | M1 schema and webhook |
| M3 | M2; pDaftar test and interviews done (M0); ideally legal answers |
| S4.2 consent screen | Reviewed consent text (M0 legal) for production; the draft is enough for development |
| M4 | M3 for reminders (need links); S7.1 and S7.3 depend only on M2 |
| M5 | M4; backup location chosen (M0) |
| M6 | M5; legal gate passed; ten shops recruited |
| External | Telegram Bot API availability; a registered bot; a domain name and certificate for the webhook |

## Critical path

M1 → M2 → M3 → M4 → M5 → M6, with two gates from the validation track:

1. **Before M3:** interviews and the pDaftar test. This is where a stop decision is cheapest, after 7 to 12 days of build.
2. **Before M6:** legal review. No real customer data is processed before it.

Work that can run off the critical path: export (S7.1) and measurement (S7.3) any time after M2; backup scripting (S8.1) any time after M1; recruiting pilot shops during M3 and M4, which the interviews can start.

## Risks

| Risk | Likelihood | Impact | Response |
|---|---|---|---|
| A stop condition is met by interviews or the pDaftar test | Medium | Project ends or pivots | Do M0 early; limit spend to M1 and M2 before the gate |
| Legal review rejects recording unlinked customers or Telegram notifications | Medium | Redesign of linking or notifications; possibly no viable product | Seek advice before M3; fallback of nickname-only records is already identified in the PRD |
| Recording is slower than the notebook in real use | Medium | Owners abandon in week one | Usability test in M5 (S8.3) and again in the first pilot week; grammar is cheap to change |
| Customers do not link | Medium | Differentiation unused; pilot cannot answer its main question | Track link rate from day one; give owners a QR card for the counter |
| Too few pilot shops recruited | Medium | Pilot inconclusive | Start recruiting during interviews |
| Solo founder unavailable | Medium | Schedule slips; during the pilot, outages go unattended | No calendar commitments; pilot shops keep notebooks; runbooks for the common failures |
| Hosting in Uzbekistan unreliable or webhook unreachable | Low to medium | Outages; fall back to long polling (ADR-006) or change provider | Test reachability in M1 before building on it |
| Estimates are wrong | High | Longer build | Ranges given; re-estimate at each milestone |
| Telegram policy or access change | Low | Product unavailable | Accepted for the MVP (ADR-001) |

## Release plan

| Stage | Who uses it | Entry condition | Data |
|---|---|---|---|
| Development | Founder, with a separate test bot | M1 | Invented data only |
| Internal alpha | Founder acting as owner and customer on the production server | M3 | Invented data only |
| Friendly test | One or two shop owners the founder knows, recording alongside their notebook | M4, and legal gate passed | Real data, with consent |
| Pilot | About ten shops for eight weeks | M5 complete and launch criteria from Stage 10 met | Real data, with consent |

Release mechanics: every change goes through CI; releases are tagged and deployed manually outside shop hours (ADR-008); migrations are forward-only, so rollback means redeploying the previous image and, if a migration must be undone, restoring from the pre-release backup taken as part of every release. During the pilot, changes are limited to fixes and grammar adjustments unless a pilot finding requires more.

After the pilot the founder decides, using the PRD's exit criteria, among three outcomes: continue toward a paid product (which reopens billing, SMS fallback, and multiple sellers, deferred as FEAT-013 to FEAT-015), change direction, or stop.

## Traceability to requirements

| Requirements | Stories |
|---|---|
| REQ-001, REQ-002 | S2.1 |
| REQ-003, REQ-004, REQ-005 | S2.2 |
| REQ-006, REQ-007, REQ-008, REQ-010 | S3.1, S3.2 |
| REQ-009 | S3.3, S3.5 |
| REQ-011, REQ-012 | S1.2, S3.4, S5.1 |
| REQ-013, REQ-014 | S4.1, S4.2 |
| REQ-015 | S1.4, S5.1 |
| REQ-016, REQ-017, REQ-018 | S5.2 |
| REQ-019, REQ-020, REQ-021 | S5.3, S4.3 |
| REQ-022, REQ-023, REQ-024, REQ-025 | S6.1, S6.2, S6.3 |
| REQ-026, REQ-027 | S3.6 |
| REQ-028 | S7.1 |
| REQ-029 | S7.2 |
| REQ-030 | S7.3 |
| REQ-N01 | S3.1, S8.4 |
| REQ-N02, REQ-N03 | S3.1, S8.3 |
| REQ-N04 | S1.5, S8.1 |
| REQ-N05 | S7.2, S7.3 |
| REQ-N06, REQ-N07 | S1.1, S1.2, S3.5 |
| REQ-N08, REQ-N09 | S1.3, S8.1, S8.2 |
| REQ-N10 | S6.2, S6.3 |
| REQ-N11 | S1.3, S4.1 |

Decision records and where they are implemented: ADR-001 in S3.1 and S3.6; ADR-002 and ADR-003 in S1.1; ADR-004 and ADR-005 in S1.2; ADR-006 in S1.3; ADR-007 in S1.4; ADR-008 in S1.5; ADR-009 in S8.1; ADR-010 in S7.3.

Every requirement in the PRD is covered by at least one story.

## Assumptions

- The founder builds with AI agents and can give the work focused days; how many per week is unknown.
- Ten shops can be recruited through the founder's own contacts and the interviews.
- A lawyer familiar with the personal data law can be consulted at a cost the founder accepts.
- Pilot owners will keep their paper notebooks for the eight weeks.
- No one else contributes code or operations.

## Open questions

1. How many days a week can the founder give this? It converts the estimates into dates.
2. Will the validation track really run before M3, given that it was deferred at two earlier stages?
3. Which city or district supplies the pilot shops?
4. Who answers when a pilot shop has a problem and the founder is unavailable?

## Approvals

| Record | Subject | Status |
|---|---|---|
| DEC-009 / APR-009 | Technical specification | Approved 2026-10-06 |
| DEC-010 | Development plan: milestone order M1 to M6, build estimate of 18 to 31 focused days without calendar commitment, a hard gate before M3 (interviews and pDaftar test) and a hard gate before any real customer data (legal review) | Approval pending |
