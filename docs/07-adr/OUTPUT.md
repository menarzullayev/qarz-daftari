# Architecture Decision Records

Status: all ten records accepted by the founder on 2026-10-06 (DEC-008 / APR-008), including the three first approved by the agent as routine.
Upstream: `docs/06-architecture/OUTPUT.md` (approved, DEC-007 / APR-007).

Ten records follow, one per decision proposed in the architecture. Seven formalize choices the founder already approved (mostly under DEC-007); three are routine, reversible engineering choices that the stage contract allows the agent to approve; they are marked as such so the founder can overrule them.

| ID | Decision | Approval basis | Reversibility |
|---|---|---|---|
| ADR-001 | Chat-only Telegram bot, no Mini App | Founder, DEC-007 | High |
| ADR-002 | Modular monolith in one process | Founder, DEC-007 | High |
| ADR-003 | Python with aiogram | Founder, question form 2026-10-06 | Medium |
| ADR-004 | PostgreSQL as the only data store | Founder, DEC-007 | Medium |
| ADR-005 | Append-only ledger enforced in the database; balances derived | Founder, DEC-006 and DEC-007 | Low |
| ADR-006 | Webhook delivery with idempotent update handling | Agent, routine | High |
| ADR-007 | Transactional outbox for outbound messages | Agent, routine | High |
| ADR-008 | Single virtual server in Uzbekistan, Docker Compose | Founder, DEC-007 | Medium |
| ADR-009 | Daily encrypted backups, four-hour recovery, 24-hour loss window | Founder, DEC-007 | High |
| ADR-010 | Identity-free measurement data kept apart from personal data | Agent, routine | Medium |

---

## ADR-001: Chat-only Telegram bot, no Mini App

**Status:** Accepted.

**Context / problem.** The PRD requires that core flows work through plain chat messages on poor connections (REQ-N03) and that recording a sale takes one message and one reply (REQ-N02). A Mini App would give richer screens for history and overview.

**Options considered.**
1. Chat only: messages, commands, inline buttons.
2. Chat for recording, Mini App for history and overview.
3. Mini App for everything.

**Selected solution.** Option 1.

**Rationale.** It satisfies REQ-N02 and REQ-N03 directly, halves what must be built and tested, and needs no web front end, hosting of static assets, or Mini App authentication. The existing open-source competitor chose option 2 (EVID-004); the pilot will show whether owners miss the richer screens.

**Consequences.** History and overdue lists (REQ-026, REQ-027) are rendered as text and paged with buttons; long histories are awkward. Export (REQ-028) covers the case where an owner wants the full picture. Adding a Mini App later does not disturb the application or domain layers.

**Evidence.** EVID-004, EVID-020, EVID-025

**Reversibility.** High.

**Approval.** Founder, under DEC-007 / APR-007.

---

## ADR-002: Modular monolith in one process

**Status:** Accepted.

**Context / problem.** One person builds and operates a system for about ten shops. The domain has one consistency unit, the customer account, and every command touches exactly one.

**Options considered.**
1. One process with internal layers: interface, application, domain, infrastructure.
2. Separate services for bot, reminders, and delivery.
3. Serverless functions per update.

**Selected solution.** Option 1.

**Rationale.** A single process gives ordinary database transactions across everything a command does, one thing to deploy, and one log to read. Options 2 and 3 buy independent scaling that the pilot volume, a few hundred transactions a day, does not need, and option 3 depends on platforms that are not available inside Uzbekistan (ADR-008).

**Consequences.** Scheduler and message dispatcher run inside the same process as update handling; a crash stops all three until restart. Layer boundaries must be kept by discipline and import rules, since nothing physical enforces them.

**Evidence.** EVID-032 (platform limits far above pilot volume).

**Reversibility.** High; layers can be split out later.

**Approval.** Founder, under DEC-007 / APR-007.

---

## ADR-003: Python with aiogram

**Status:** Accepted.

**Context / problem.** The requirements do not constrain the language. The realistic candidates are the two ecosystems the founder already works in.

**Options considered.**
1. Python 3.12 with aiogram 3.
2. TypeScript with grammY.

**Selected solution.** Option 1.

**Rationale.** Both have mature Bot API support. The founder has existing Python bots and chose Python when asked.

**Consequences.** Type safety relies on type hints and a checker run in continuous integration, which is weaker than a compiled type system. Money is handled as integers (REQ-N06), which avoids the usual floating-point risk in either language. Library versions are pinned.

**Evidence.** None external; founder preference.

**Reversibility.** Medium. The domain layer is small and pure and could be ported; the interface layer would be rewritten.

**Approval.** Founder, through the question form on 2026-10-06, recorded in APR-007.

---

## ADR-004: PostgreSQL as the only data store

**Status:** Accepted.

**Context / problem.** The system needs durable storage for the ledger, a queue for outbound messages, state for scheduled jobs, and name search that tolerates Latin and Cyrillic spelling (REQ-004).

**Options considered.**
1. PostgreSQL for everything.
2. PostgreSQL plus Redis for queue and scheduler.
3. SQLite.

**Selected solution.** Option 1.

**Rationale.** One store means one thing to back up and restore (REQ-N08) and lets an entry and its notification be saved in one transaction (ADR-007). PostgreSQL's role and permission system is what makes ADR-005 enforceable. SQLite would be simpler still but has no comparable permission model and is awkward to back up while running.

**Consequences.** The queue is a table polled by the dispatcher, adequate at pilot volume and well beyond. If throughput ever demands it, a broker can be introduced behind the dispatcher without touching application code.

**Evidence.** EVID-032

**Reversibility.** Medium.

**Approval.** Founder, under DEC-007 / APR-007.

---

## ADR-005: Append-only ledger enforced in the database; balances derived

**Status:** Accepted.

**Context / problem.** The PRD requires that no entry is ever edited or deleted by any route, including administrative ones (REQ-011, REQ-N07), and that balances are always derivable from history (REQ-N06). The product's promise is a record both sides can trust.

**Options considered.**
1. Enforce immutability in application code only.
2. Enforce it in the database: the application's database role cannot update or delete rows in the entry table.
3. Option 2 plus a hash chain across entries for tamper evidence.

**Selected solution.** Option 2. Balances are computed from entries; any stored balance is a cache that can be rebuilt and is never the source of truth.

**Rationale.** Application-only enforcement fails exactly when there is a bug or a hurried manual fix. Database permissions make the rule hold regardless. A hash chain adds tamper evidence against the operator, which matters once there are disputes with legal weight; it is not needed to run a ten-shop pilot.

**Consequences.** Mistakes can be corrected only by reversal entries, including mistakes made by the operator. Schema changes to the entry table must be additive. Data removal (REQ-029) works because identifying data lives on the customer record, not on entries. This decision is hard to undo: once owners and customers rely on immutability, weakening it would break trust.

**Evidence.** EVID-017 (amount fixed at the time of sale).

**Reversibility.** Low.

**Approval.** Founder, under DEC-006 / APR-006 (immutability and whole-entry reversal) and DEC-007 / APR-007.

---

## ADR-006: Webhook delivery with idempotent update handling

**Status:** Accepted by the agent as routine and reversible.

**Context / problem.** Telegram can deliver updates by webhook or by long polling, and may deliver the same update more than once. A repeated "Ali 45000" must not create two debts (REQ-006, REQ-011).

**Options considered.**
1. Webhook with a secret token header; record each update identifier and ignore repeats.
2. Long polling with the same deduplication.

**Selected solution.** Option 1.

**Rationale.** A webhook has no idle connection to maintain and gives an obvious external health check. Deduplication by update identifier is needed in either case.

**Consequences.** The server needs a public HTTPS endpoint and a valid certificate, handled by the reverse proxy. If Telegram cannot reliably reach hosts in Uzbekistan, which is untested, switching to long polling is a configuration change.

**Evidence.** EVID-032

**Reversibility.** High.

**Approval.** Agent.

---

## ADR-007: Transactional outbox for outbound messages

**Status:** Accepted by the agent as routine and reversible.

**Context / problem.** A customer must be notified of every entry, payment, and reversal (REQ-015), reminders have strict frequency limits (REQ-023, REQ-N10), and Telegram enforces rate limits and can be temporarily unavailable.

**Options considered.**
1. Send messages directly inside the command handler.
2. Save the message to an outbox table in the same transaction as the domain change; a dispatcher sends, retries, and records the outcome.

**Selected solution.** Option 2.

**Rationale.** With option 1 a crash or a Telegram error between saving and sending produces an entry the customer never hears about, or a reminder that is recorded but not sent. The outbox makes "saved" and "will be sent" one fact. The dispatcher is also the single place where rate limits are respected.

**Consequences.** Notifications are delivered at least once; a message may rarely arrive twice, so confirm and dispute actions must be safe to repeat. Delivery is slightly delayed, well within the one-minute criterion in the PRD. Messages to customers who blocked the bot fail permanently and mark the link unreachable.

**Evidence.** EVID-032

**Reversibility.** High.

**Approval.** Agent.

---

## ADR-008: Single virtual server in Uzbekistan, Docker Compose

**Status:** Accepted. The provider is not yet chosen.

**Context / problem.** Personal data of Uzbek citizens must be stored on servers physically located in Uzbekistan (REQ-N04, EVID-027). The usual managed cloud platforms have no region there.

**Options considered.**
1. One virtual server at a commercial provider in Uzbekistan, running everything under Docker Compose.
2. Database in Uzbekistan, application on a foreign cloud platform.
3. Everything on a foreign cloud platform.

**Selected solution.** Option 1.

**Rationale.** Option 3 conflicts with the localization rule. Option 2 keeps stored data local but sends every query result abroad for processing, which may not satisfy the rule and adds latency and a second system to operate. Option 1 is unambiguous and cheap, roughly 125,000 to 250,000 UZS a month (EVID-031).

**Consequences.** No managed database, no automatic failover; the operator handles patching, monitoring, and recovery. Provider reliability is unverified. Messages still pass through Telegram's servers abroad (EVID-033); this decision does not resolve that legal question.

**Evidence.** EVID-027, EVID-031, EVID-033

**Reversibility.** Medium. Moving to another provider in Uzbekistan is a restore from backup; moving abroad is not open while the rule stands.

**Approval.** Founder, under DEC-007 / APR-007.

---

## ADR-009: Daily encrypted backups, four-hour recovery, 24-hour loss window

**Status:** Accepted.

**Context / problem.** A saved entry must survive server failure (REQ-N08) and the bot must be available during shop hours (REQ-N09). There is one server and one operator.

**Options considered.**
1. Daily encrypted dump to a second location in Uzbekistan; restore onto a new server.
2. Continuous archiving of database changes in addition to daily dumps.
3. A standby replica with failover.

**Selected solution.** Option 1, with stated targets: service restored within four hours, at most 24 hours of entries lost. The founder was offered option 2 and chose option 1.

**Rationale.** Option 1 is simple enough to rehearse and to trust. Pilot shops are expected to keep their paper notebooks, so a day's entries could be re-entered. Options 2 and 3 are the right answer once shops rely on the product alone.

**Consequences.** A server loss can erase up to a day of entries, and pilot owners must be told this plainly. The backup key is kept off the server; losing it makes the backups useless. A restore is rehearsed before the pilot and the measured time is recorded.

**Evidence.** EVID-031

**Reversibility.** High; option 2 can be added without changing the application.

**Approval.** Founder, under DEC-007 / APR-007.

---

## ADR-010: Identity-free measurement data kept apart from personal data

**Status:** Accepted by the agent as routine.

**Context / problem.** The pilot must measure repayment, confirmation, and entry speed (REQ-030) without building up a second copy of personal data, and removal requests must not destroy the pilot's results (REQ-029).

**Options considered.**
1. Compute metrics by querying the live ledger when needed.
2. Write separate measurement records that carry opaque shop and entry identifiers and no names, phones, or Telegram identities.
3. Send events to an external analytics service.

**Selected solution.** Option 2.

**Rationale.** Option 1 loses information when a customer is anonymized and tempts analysis to join against personal data. Option 3 would send data to a provider abroad, in conflict with the hosting decision (ADR-008). Separate identity-free records survive anonymization and can be exported for analysis safely.

**Consequences.** A little more is written per command. Whether an opaque identifier that can still be joined to a customer record inside the same database counts as personal data is a legal nuance; the records must not be exported together with the mapping.

**Evidence.** EVID-027

**Reversibility.** Medium.

**Approval.** Agent.

---

## Decisions not taken here

Named so they are not mistaken for settled:

- Which hosting provider (ADR-008 leaves it open).
- Whether notifications should omit the customer's name to reduce what passes through Telegram (depends on legal advice; see the architecture's open question 1).
- A hash chain over entries (ADR-005, option 3).
- How an owner recovers a shop after losing their Telegram account.
