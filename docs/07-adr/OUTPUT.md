# Architecture Decision Records

Version 2. Status: rewritten for the version 2 architecture; approved by the founder on 2026-10-06 (DEC-016 / APR-016) with no changes to the rules the agent decided. Prepared 2026-10-06.
Version 1 (ten records, DEC-008) remains in version history.

Records keep their numbers. Four version 1 records are superseded, six are kept or amended, and eleven are new. By the founder's instruction the new records were decided by the agent without stopping and are all subject to his review.

| ID | Decision | State | Reversibility |
|---|---|---|---|
| ADR-001 | Chat-only Telegram bot | Superseded by ADR-011 | - |
| ADR-002 | Modular monolith | Amended: two process types | High |
| ADR-003 | Python with aiogram | Kept; extended by ADR-012 | Medium |
| ADR-004 | PostgreSQL as the only database | Kept | Medium |
| ADR-005 | Append-only ledger enforced in the database | Amended: one bounded addition of goods lines; promised dates changeable with history | Low |
| ADR-006 | Webhook with idempotent update handling | Kept; request keys added for API writes | High |
| ADR-007 | Transactional outbox | Kept; now also carries SMS | High |
| ADR-008 | Single server in Uzbekistan | Superseded by ADR-014 | - |
| ADR-009 | Daily backups, 24-hour loss window | Superseded by ADR-015 | - |
| ADR-010 | Identity-free measurement data kept apart | Kept | Medium |
| ADR-011 | Three clients from one front-end application, with the chat fast path kept | New | Medium |
| ADR-012 | FastAPI for the HTTP API | New | Medium |
| ADR-013 | TypeScript and React for the front end | New | Medium |
| ADR-014 | Two servers in Uzbekistan, primary and standby, manual failover | New | Medium |
| ADR-015 | Streaming replication and continuous archiving; 5-minute loss bound, 1-hour recovery | New | High |
| ADR-016 | Tenant isolation by PostgreSQL row-level security | New | Low |
| ADR-017 | Authentication only through Telegram identity; administrator allow-list and second factor | New | Medium |
| ADR-018 | Platform switches and prices stored in the database | New | High |
| ADR-019 | Subscription by card transfer with administrator approval; online payment adapters built and off | New | High |
| ADR-020 | Self-hosted S3-compatible file store | New | Medium |
| ADR-021 | Two languages through message catalogs | New | High |

---

## ADR-002: Modular monolith (amended)

**Status:** Accepted, amended.

**Context / problem.** Release 1 now has an HTTP API, three clients, background work (reminders, imports, exports, expiries), and a scale target (REQ-N13). It is still built and run by one person.

**Options considered.** (1) One codebase, two process types: API and worker. (2) Separate services per area. (3) One process doing everything.

**Selected solution.** Option 1.

**Rationale.** Background work must not slow request handling, so the worker is its own process; everything else gains nothing from separation at this scale and would cost distributed transactions. Modules own their tables and talk through application commands, so a module can be split out later.

**Consequences.** Module boundaries are kept by import rules checked in continuous integration. Scheduled jobs need a single active runner, taken by a database lock.

**Evidence.** EVID-032 | **Reversibility.** High. **Approval.** Agent; approved by the founder on 2026-10-06.

---

## ADR-003: Python with aiogram (kept)

**Status:** Accepted on 2026-10-06 by the founder; unchanged. The HTTP side is decided in a separate record (ADR-012).

---

## ADR-004: PostgreSQL as the only database (kept)

**Status:** Accepted; unchanged in substance.

**Note for version 2.** The outbox, job queue, platform settings, and sessions all live in PostgreSQL. No cache or broker is introduced. The design capacity of 50 entries a second (REQ-N13) does not require one; the load test decides whether that holds.

**Evidence.** EVID-032 | **Reversibility.** Medium. **Approval.** Founder, under DEC-007; carried.

---

## ADR-005: Append-only ledger enforced in the database (amended)

**Status:** Accepted, amended.

**Context / problem.** Version 2 lets goods lines be added to an amount-only entry after the sale (REQ-038) and lets promised dates change on request (REQ-067), while the PRD still requires that entries are never edited or deleted (REQ-011, REQ-N07).

**Options considered.** (1) Allow updates to entries for these cases. (2) Keep entries insert-only; store goods lines and promise changes as separate insert-only records tied to the entry, with database constraints limiting when lines may be added.

**Selected solution.** Option 2. The application role has no update or delete permission on entries, goods lines, or promise history. Lines for an entry are inserted in a single batch; a constraint trigger rejects a second batch, a batch whose sum differs from the entry total, and a batch after the allowed time. The current promised date is the latest row of the promise history.

**Rationale.** The financial fact, who owes how much, stays immutable in the strict sense. What changes is descriptive detail and a date, each with its own history.

**Consequences.** Reads must join to the latest promise; an index and a view make this cheap. The rule "only one batch of lines" lives in the database, not only in code.

**Evidence.** EVID-017 | **Reversibility.** Low. **Approval.** Founder for immutability (DEC-006); the amendment is the agent's, review pending.

---

## ADR-006: Webhook with idempotent handling (kept, extended)

**Status:** Accepted.

**Extension.** Every write call to the HTTP API carries a client-generated request key; the server stores it with the result and returns the stored result for a repeat. This gives the Mini App and web panel the same protection against double submission that update identifiers give the chat (REQ-011).

**Evidence.** EVID-032 | **Reversibility.** High. **Approval.** Founder, under DEC-008; extension by the agent.

---

## ADR-007: Transactional outbox (kept, extended)

**Status:** Accepted.

**Extension.** The outbox carries a channel: Telegram or SMS. The dispatcher applies per-channel rate limits and, for SMS, the shop's monthly quota (REQ-043). Messages to staff, customers, the administrator, and the receipt review group all go through it.

**Evidence.** EVID-032 | **Reversibility.** High. **Approval.** Founder, under DEC-008; extension by the agent.

---

## ADR-010: Identity-free measurement data kept apart (kept)

**Status:** Accepted; unchanged.

---

## ADR-011: Three clients from one front-end application, chat fast path kept

**Status:** Accepted by the agent; approved by the founder on 2026-10-06. Supersedes the chat-only record (ADR-001).

**Context / problem.** The founder chose a Mini App plus a separate web panel (question form, 2026-10-06). Itemized entry, large customer lists, reports, and administration do not fit a chat. Recording in a queue on a poor connection still must work (REQ-N03).

**Options considered.** (1) Separate applications for Mini App, web panel, and admin. (2) One application with three entry points and responsive layouts. (3) Mini App only.

**Selected solution.** Option 2, with the chat message path kept for amount-only sales and payments.

**Rationale.** The Mini App and web panel show the same data to the same roles; writing screens once and adapting by width meets REQ-N15 and halves the front-end work. The admin panel shares components but is a separate entry point with separate authentication. Keeping the chat path preserves the fastest way to record.

**Consequences.** Two ways to record a sale must stay consistent; both call the same application commands. The staff workspace must be designed mobile-first.

**Evidence.** EVID-004, EVID-020, EVID-034 | **Reversibility.** Medium. **Approval.** Agent; founder chose the client set.

---

## ADR-012: FastAPI for the HTTP API

**Status:** Accepted by the agent; approved by the founder on 2026-10-06.

**Context / problem.** The Mini App, web panel, and admin panel need an HTTP API in the Python codebase already chosen (ADR-003).

**Options considered.** (1) FastAPI. (2) Django with Django REST Framework. (3) aiohttp.

**Selected solution.** Option 1.

**Rationale.** It is asynchronous like aiogram, so both share one event loop, database pool, and transaction handling; request and response models are typed and produce the API description the front end is generated from. Django would bring an administration site but a different persistence model from the append-only, permission-restricted ledger design (ADR-005).

**Consequences.** The admin panel is built, not inherited. Database access uses SQLAlchemy core with explicit SQL for ledger queries.

**Evidence.** None external. **Reversibility.** Medium. **Approval.** Agent.

---

## ADR-013: TypeScript and React for the front end

**Status:** Accepted by the agent; approved by the founder on 2026-10-06.

**Options considered.** (1) React with TypeScript. (2) Vue. (3) Server-rendered pages.

**Selected solution.** Option 1, built to static files, with an API client generated from the server's API description.

**Rationale.** The Telegram Mini App ecosystem and component libraries are strongest there, and the founder's other repositories already use TypeScript. Server-rendered pages suit the admin panel but not an itemized entry screen used at a counter.

**Consequences.** A second language and toolchain in the project. Bundle size matters on low-end phones (REQ-N15) and is checked in continuous integration.

**Evidence.** None external. **Reversibility.** Medium. **Approval.** Agent.

---

## ADR-014: Two servers in Uzbekistan, primary and standby, manual failover

**Status:** Accepted by the agent; approved by the founder on 2026-10-06. Supersedes the single-server record (ADR-008).

**Context / problem.** Version 2 requires recovery within 1 hour and availability of 99.5% in shop hours (REQ-N09), with data in Uzbekistan (REQ-N04, EVID-027).

**Options considered.** (1) One server with fast restore. (2) Primary and standby at two providers or facilities, manual scripted failover. (3) Three nodes with automatic failover.

**Selected solution.** Option 2.

**Rationale.** Restoring a large database onto a new server cannot be relied on within an hour. Automatic failover needs a third vote and adds a failure mode, two primaries, that is worse than an hour's outage for a ledger. A warm standby promoted by a rehearsed script meets the target with the least machinery.

**Consequences.** Failover needs a person. Monthly cost roughly doubles (EVID-031). The standby also hosts staging, which must be isolated from the replica.

**Evidence.** EVID-027, EVID-031 | **Reversibility.** Medium. **Approval.** Agent.

---

## ADR-015: Streaming replication and continuous archiving

**Status:** Accepted by the agent; approved by the founder on 2026-10-06. Supersedes the daily-backup record (ADR-009).

**Context / problem.** At most 5 minutes of entries may be lost (REQ-N08).

**Options considered.** (1) Asynchronous streaming replication plus log archiving every minute with pgBackRest, weekly full and daily differential backups. (2) Synchronous replication. (3) Daily dumps.

**Selected solution.** Option 1.

**Rationale.** Asynchronous replication normally lags by seconds; minute-level archiving bounds the loss even if the standby is also lost. Synchronous replication would make every sale wait on the link between two providers and stop recording when the standby is unreachable.

**Consequences.** A sale acknowledged to a seller can be lost if the primary dies within the replication lag; the bound is minutes, not zero, and that is stated to shops. Point-in-time recovery becomes possible, which also undoes operator mistakes. Backups are encrypted with a key kept off both servers.

**Evidence.** EVID-031 | **Reversibility.** High. **Approval.** Agent.

---

## ADR-016: Tenant isolation by row-level security

**Status:** Accepted by the agent; approved by the founder on 2026-10-06.

**Context / problem.** Thousands of shops share one database. One shop's data must be unreachable from another "below the application code as well as in it" (REQ-N12), and administrators must not read shop data without a logged support access (REQ-059).

**Options considered.** (1) Application-level filtering by shop only. (2) Shared tables with PostgreSQL row-level security keyed to a session setting. (3) A schema or database per shop.

**Selected solution.** Option 2. Every tenant table carries the shop identifier; policies allow rows only for the shop set in the session; the application role cannot bypass policies; the tenant is set once per transaction from the authenticated context. Customer sessions and administrator support access use their own narrowly scoped policies.

**Rationale.** A forgotten filter in one query is the classic multi-tenant leak; with policies it returns nothing. A schema per shop does not scale to thousands and complicates migrations.

**Consequences.** Queries across shops, such as an owner's combined totals and platform statistics, must be written as explicit, reviewed functions. Connection pooling must reset the tenant setting between uses. Tests attempt every cross-tenant access.

**Evidence.** EVID-027 | **Reversibility.** Low once data and code depend on it. **Approval.** Agent.

---

## ADR-017: Authentication only through Telegram identity

**Status:** Accepted by the agent; approved by the founder on 2026-10-06.

**Context / problem.** Users arrive through chat, Mini App, and a web browser (REQ-050). The PRD says there are no separate passwords.

**Options considered.** (1) Telegram identity everywhere: bot updates, signed Mini App launch data, and Telegram Login on the web. (2) Phone number with one-time codes. (3) Email and password for the web.

**Selected solution.** Option 1. The server validates Telegram's signature in each case and issues its own session. Administrators are additionally restricted to an allow-list of Telegram identities and must pass a time-based second factor. One exception, changed by the founder on 2026-10-08 (DEC-064): a subscription receipt may be approved or rejected from the review group's buttons by anyone Telegram names, at the moment of the press, as the creator or an administrator of that group, without the allow-list and without the second factor. Such a person has no administrator account and can do nothing else; the decision is recorded with their Telegram user identifier. Who administers the review group is therefore as sensitive as the allow-list. A second exception, decided by the owner on 2026-10-10 after being warned twice and after declining a narrower option (module switches only): a deployment setting, `QD_ADMIN_SECOND_FACTOR`, read from the environment at start, with the values `required` (the default) and `off`; any other value refuses to start. With `off` an administrator who has a confirmed second factor is asked for no code anywhere: not at the door, not for a sensitive setting, not for replacing a shop's owner. The owner chose `off` for his own installation, because a code for every switch was blocking development; the default in the repository and in every deployment file stays `required`. A third change, decided by the owner on 2026-10-10: Telegram is no longer the only way in for an administrator. Three more are added, each leading to the same server-side session of the same allow-listed person and deciding nothing about who is an administrator: a **service key** (a bearer token made on the server, shown once, stored as a hash, without an expiry until revoked; the owner was offered keys limited to the module switches and chose full administrator rights), a **login and password** (set only from the server's command line, at least 14 characters, five wrong attempts lock the login for 15 minutes, every sign-in by password is written to the audit and announced to the operators' chats), and a **passkey** (built after the first two). While the second factor is `required`, a key or a password alone opens the door and no administrator operation: the code is still asked for.

**Rationale.** One identity per person across all clients, no credentials to store or leak, and no SMS cost. Option 2 needs the SMS provider that is switched off.

**Consequences.** Losing a Telegram account means losing access; recovery is an administrator procedure. Telegram becomes the identity provider as well as the channel, deepening the dependency already accepted. The second factor's secret must be stored and backed up safely. With `QD_ADMIN_SECOND_FACTOR=off` the allow-listed Telegram account is the only thing between a person and the whole administrators' side: whoever takes over that account, or a signed-in browser of it, can change the payment cards, the price, the review group and online payment with nothing more to pass. What remains is that it is visible and reversible: the API logs a warning at every start, the settings screen says so permanently, the worker's watch repeats `AdminSecondFactorOff` to the operators' chat, every change made without a code says so in the audit, and stored secrets are kept, so `required` brings the codes back with no new enrolment.

**Evidence.** EVID-025 | **Reversibility.** Medium. **Approval.** Agent.

---

## ADR-018: Platform switches and prices stored in the database

**Status:** Accepted by the agent; approved by the founder on 2026-10-06.

**Selected solution.** Trial on or off, trial length, subscription price, SMS on or off and quotas, online payment on or off, the receiving card number, and the review group are rows in a settings table, changed in the admin panel, cached briefly by the application, and logged on every change (REQ-N14, REQ-058). Alternatives were environment variables, which need a restart and leave no audit trail, and a third-party flag service, which would sit outside Uzbekistan.

**Consequences.** A wrong setting takes effect at once; changes to price and card number ask for the second factor again. Not on a deployment that runs with `QD_ADMIN_SECOND_FACTOR=off` (ADR-017, the exception of 2026-10-10): there no setting asks for a code, and the audit row of each such change carries `second_factor: off`.

**Evidence.** None external. **Reversibility.** High. **Approval.** Agent.

---

## ADR-019: Subscription by card transfer with administrator approval

**Status:** Accepted on the founder's decision of 2026-10-06; details by the agent.

**Context / problem.** No registered business entity exists, so no contract with an online payment provider is possible yet. The founder decided that owners pay by transfer to a personal card and send the receipt through the bot; receipts go to the administrator and a review group; an administrator approves (REQ-054, REQ-055), and so may a Telegram administrator of the review group, from the group (changed by the founder on 2026-10-08, DEC-064). He states this is lawful (EVID-035); that is unverified.

**Options considered.** (1) Manual receipts only. (2) Manual receipts now, with Click and Payme adapters built behind a switch. (3) Telegram Stars.

**Selected solution.** Option 2, as the founder specified.

**Rationale.** Manual approval needs no contract. Building the adapters now means switching to online payment later is a setting, not a project.

**Consequences.** Approval is manual work that grows with the number of shops, and a forged or reused receipt can be approved by mistake; each receipt's file hash and stated amount are stored and duplicates are flagged. Selling a subscription through a bot outside Telegram's payment mechanism may conflict with Telegram's rules (EVID-028), with consequences for the bot that this design cannot prevent. Receipts contain card details and are personal data. Adapters that cannot run in production cannot be fully proven before they are switched on.

**Evidence.** EVID-028, EVID-035 | **Reversibility.** High. **Approval.** Founder.

---

## ADR-020: Self-hosted S3-compatible file store

**Status:** Accepted by the agent; approved by the founder on 2026-10-06.

**Selected solution.** Receipt images and import files are kept in an S3-compatible object store run on the service's own servers and replicated to the standby. Alternatives were database large objects, which bloat backups and replication, and a foreign object storage service, which conflicts with data localization (EVID-027).

**Consequences.** One more component to operate and back up. Files are served only through short-lived signed links after authorization. Retention follows the data minimization requirement (REQ-N05).

**Evidence.** EVID-027 | **Reversibility.** Medium. **Approval.** Agent.

---

## ADR-021: Two languages through message catalogs

**Status:** Accepted by the agent; approved by the founder on 2026-10-06.

**Selected solution.** Every user-facing string is a key resolved from an Uzbek and a Russian catalog, on the server for chat messages and notifications and in the front end for screens (REQ-051, REQ-N01). The build fails when a key is missing in either language. A user's language is stored on the user; a notification uses the recipient's language. The alternative, text written in code, was rejected because two languages cannot be kept in step that way.

**Consequences.** All copy needs two reviewed versions. Reminder templates exist per language.

**Evidence.** None external. **Reversibility.** High. **Approval.** Agent.

---

## Superseded records

- **ADR-001** (chat only), **ADR-008** (single server), **ADR-009** (daily backups, 24-hour loss): accepted under DEC-008 for the pilot MVP, superseded on 2026-10-06 by ADR-011, ADR-014 and ADR-015 after the change of direction (DEC-012). Their text is in version history.

## Decisions not taken here

- Hosting providers and a third backup location.
- SMS provider.
- Whether notifications should carry less detail to reduce what passes through Telegram.
- A hash chain over ledger entries for tamper evidence against the operator.
- Owner recovery after losing a Telegram account.
