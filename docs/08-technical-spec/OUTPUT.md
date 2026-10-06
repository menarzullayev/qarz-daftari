# Technical Specification

Status: approved by the founder on 2026-10-06 (DEC-009 / APR-009). Legal review of the consent text and four legal questions remains a gate before the pilot.
Upstream: PRD (DEC-005), Domain Model (DEC-006), Architecture (DEC-007), decision records ADR-001 to ADR-010 (DEC-008).

Scope: the MVP only. The system has no public HTTP API; its contract with users is the Telegram conversation, and its contract with Telegram is the webhook and Bot API calls.

Conventions: timestamps are stored in UTC and shown in Tashkent time (UTC+5, no daylight saving). Dates such as due dates are calendar dates in Tashkent time. Amounts are whole UZS as 64-bit integers. Identifiers are UUID version 7 unless stated.

## API contract

### Webhook (ADR-006)

| Item | Specification |
|---|---|
| Endpoint | `POST /tg/webhook` over HTTPS, served by the reverse proxy |
| Authentication | Header `X-Telegram-Bot-Api-Secret-Token` must equal the configured secret; otherwise respond 403 and process nothing |
| Subscribed updates | `message`, `callback_query`, `my_chat_member` |
| Response | 200 with empty body after the update is durably handled or recognized as a duplicate; 500 on failure so Telegram redelivers |
| Idempotency | `update_id` is inserted into `processed_update` in the same transaction as the update's effects; a conflict means duplicate, respond 200 |
| Health | `GET /healthz` returns 200 when the database is reachable; exposes nothing else |

Only private chats are served. Messages from groups or channels are ignored.

### Owner conversation (ADR-001)

Free-text entry grammar. Anything that is not a command is parsed as an entry:

```
entry    := name SP amount [SP note]
payment  := name SP "-" amount [SP note]  |  name SP amount SP ("berdi" | "to'ladi" | "toladi")
name     := one or more words, not starting with a digit
amount   := digits with optional space or dot thousand separators, optional suffix "k" or "ming" (×1000)
note     := remaining text, at most 120 characters
```

Examples: `Ali 45000`, `Ali aka 45 000 non, yog'`, `Ali 45k`, `Ali -20000`, `Ali 20000 berdi`.

Parsing rules: amounts must be whole and between 100 and 100,000,000 UZS; decimals are rejected with an explanation. Cyrillic input is transliterated before matching (REQ-004, REQ-N01). The name is matched against the shop's customers by normalized prefix and substring: exactly one match proceeds; none offers "create customer"; several show buttons to choose (REQ-006, domain rule BR-18).

Every successful entry gets one reply stating customer, amount, due date, new balance, and an inline "Bekor qilish" (reverse) button valid for that entry (REQ-010, REQ-011).

| Command | Purpose | Requirements |
|---|---|---|
| `/start` | Create the shop (asks for a name) or show the short help | REQ-001, REQ-002 |
| `/mijoz` | Add a customer by name; optional phone | REQ-003 |
| `/mijozlar` | Paged customer list with balances; opening one shows history, confirmation status, link and reminder controls | REQ-005, REQ-027 |
| `/qarzlar` | Total outstanding and customers by balance | REQ-026 |
| `/muddati` | Overdue customers with days overdue and a "send reminder" button | REQ-025, REQ-026 |
| `/ulash` | Produce a customer's personal link and QR image | REQ-013 |
| `/eslatma` | Turn shop reminders on or off, choose the template and the hour reminders go out (08:00 to 20:00) | REQ-022, REQ-024 |
| `/sozlama` | Shop name and default due day | REQ-008 |
| `/eksport` | Send the ledger as an `.xlsx` file | REQ-028 |
| `/yordam` | Help with examples | REQ-N01 |

### Customer conversation

| Trigger | Behavior | Requirements |
|---|---|---|
| `/start <token>` from a link or QR | Show the consent text with "Roziman" and "Rad etaman" buttons. Agree: create link and consent record, show balance. Decline: store nothing, token stays usable. | REQ-013, REQ-014 |
| Entry notification | Greeting with the name the shop uses for the customer, amount, shop, new balance, buttons "Tasdiqlayman" and "E'tiroz" | REQ-015, REQ-016 |
| "E'tiroz" | Bot asks for a short reason (3 to 200 characters), then records the dispute | REQ-016, REQ-017 |
| `/qarzim` | Balance and paged history per linked shop, with status of each entry and buttons to confirm, dispute, or withdraw a dispute | REQ-019 |
| `/uzish` | Disconnect from a shop after confirmation | REQ-021 |
| `/ochirish` | Request removal of identifying data | REQ-029 |

A Telegram account can be an owner and a customer of other shops at once; the bot resolves intent by command and by callback data.

### Callback data

Format `v1:<action>:<id>`, at most 64 bytes. Actions: `ack.ok`, `ack.no`, `ack.undo`, `cust.pick`, `cust.new`, `entry.rev`, `entry.rev.yes`, `rem.send`, `rem.off`, `rem.on`, `page`, `consent.yes`, `consent.no`, `unlink.yes`. Every callback is authorized again on receipt; identifiers in callback data are never trusted on their own.

### Application commands

The interface layer calls these; each runs in one database transaction and locks the customer row.

| Command | Preconditions | Effects | Errors |
|---|---|---|---|
| RecordCredit | Caller owns the shop; customer active | Append entry; create acknowledgement if linked; queue notification; write measurement | `NOT_OWNER`, `CUSTOMER_NOT_FOUND`, `CUSTOMER_ARCHIVED`, `AMOUNT_INVALID` |
| RecordPayment | As above; amount at most the balance | Append entry; queue notification | As above plus `EXCEEDS_BALANCE` |
| ReverseEntry | Caller owns the shop; entry not reversed and not a reversal; result keeps balance at or above zero | Append reversal; close acknowledgement; queue notification | `ALREADY_REVERSED`, `CANNOT_REVERSE_REVERSAL`, `WOULD_GO_NEGATIVE` |
| AcceptInvitation | Token valid; consent given; no active link for this customer; this Telegram account has no other customer in the shop | Create link and consent; mark token used | `TOKEN_INVALID`, `ALREADY_LINKED` |
| Confirm, Dispute, WithdrawDispute | Caller holds the active link; state allows the transition | Change acknowledgement state; write history; notify owner on dispute | `NOT_LINKED`, `INVALID_STATE`, `REASON_REQUIRED` |
| SendManualReminder | Caller owns the shop; domain rule BR-11 holds; none sent today | Record reminder; queue message | `NOT_ELIGIBLE`, `LIMIT_REACHED` |
| RequestRemoval | Caller holds the active link | Anonymize now if balance is zero, else record a waiting request | - |

## Database schema

PostgreSQL 16 (ADR-004). Migrations are forward-only. Sketch of tables and the constraints that carry domain invariants:

```sql
CREATE TABLE shop (
  id              uuid PRIMARY KEY,
  owner_tg_id     bigint NOT NULL UNIQUE,              -- one shop per Telegram account
  name            text   NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
  due_day         smallint NOT NULL DEFAULT 5 CHECK (due_day BETWEEN 1 AND 31),
  reminders_on    boolean NOT NULL DEFAULT false,
  reminder_tpl    smallint NOT NULL DEFAULT 1,
  reminder_hour   smallint NOT NULL DEFAULT 10 CHECK (reminder_hour BETWEEN 8 AND 20),  -- Tashkent time
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE customer (
  id              uuid PRIMARY KEY,
  shop_id         uuid NOT NULL REFERENCES shop(id),
  display_name    text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 80),
  name_norm       text NOT NULL,                       -- lower-cased, transliterated
  phone           text,
  status          text NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','archived','anonymized')),
  reminders_off   boolean NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX customer_shop_name ON customer (shop_id, name_norm text_pattern_ops);

CREATE TABLE ledger_entry (
  id              uuid PRIMARY KEY,
  customer_id     uuid NOT NULL REFERENCES customer(id),
  seq             integer NOT NULL,                    -- order within the account
  kind            text NOT NULL CHECK (kind IN ('credit','payment','reversal')),
  amount          bigint NOT NULL CHECK (amount > 0),
  due_date        date,                                -- credits only
  note            text CHECK (length(note) <= 120),
  reverses_id     uuid REFERENCES ledger_entry(id),
  actor           text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (customer_id, seq),
  UNIQUE (reverses_id),                                -- an entry is reversed at most once
  CHECK ((kind = 'reversal') = (reverses_id IS NOT NULL)),
  CHECK ((kind = 'credit')   = (due_date IS NOT NULL))
);

CREATE TABLE acknowledgement (
  entry_id        uuid PRIMARY KEY REFERENCES ledger_entry(id),
  state           text NOT NULL DEFAULT 'unconfirmed'
                  CHECK (state IN ('unconfirmed','confirmed','disputed','closed')),
  dispute_reason  text,
  link_id         uuid,
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE acknowledgement_history (
  id uuid PRIMARY KEY, entry_id uuid NOT NULL, from_state text, to_state text NOT NULL,
  actor text NOT NULL, link_id uuid, reason text, at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE link_invitation (
  token_hash      bytea PRIMARY KEY,                   -- SHA-256 of the token
  customer_id     uuid NOT NULL REFERENCES customer(id),
  status          text NOT NULL DEFAULT 'issued' CHECK (status IN ('issued','used','cancelled')),
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_open_invitation ON link_invitation (customer_id) WHERE status = 'issued';

CREATE TABLE customer_link (
  id              uuid PRIMARY KEY,
  customer_id     uuid NOT NULL REFERENCES customer(id),
  shop_id         uuid NOT NULL REFERENCES shop(id),
  tg_id           bigint,                              -- erased on anonymization
  status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active','unreachable','ended')),
  consent_text_v  smallint NOT NULL,                   -- version of the text shown
  consent_at      timestamptz NOT NULL,                -- no link without consent
  ended_at        timestamptz, ended_by text
);
CREATE UNIQUE INDEX one_live_link_per_customer ON customer_link (customer_id) WHERE status <> 'ended';
CREATE UNIQUE INDEX one_customer_per_tg_per_shop ON customer_link (shop_id, tg_id) WHERE status <> 'ended';

CREATE TABLE reminder (
  id uuid PRIMARY KEY, customer_id uuid NOT NULL REFERENCES customer(id),
  kind text NOT NULL CHECK (kind IN ('auto','manual')),
  amount bigint NOT NULL CHECK (amount > 0), sent_on date NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (customer_id, kind, sent_on)                  -- at most one of each kind per day
);

CREATE TABLE removal_request (
  id uuid PRIMARY KEY, customer_id uuid NOT NULL REFERENCES customer(id),
  status text NOT NULL CHECK (status IN ('waiting','completed')),
  requested_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);

CREATE TABLE outbox_message (                          -- ADR-007
  id uuid PRIMARY KEY, chat_id bigint NOT NULL, payload jsonb NOT NULL,
  dedupe_key text UNIQUE, status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','sent','failed')),
  attempts smallint NOT NULL DEFAULT 0, next_try_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(), sent_at timestamptz
);
CREATE INDEX outbox_due ON outbox_message (next_try_at) WHERE status = 'pending';

CREATE TABLE processed_update (update_id bigint PRIMARY KEY, at timestamptz NOT NULL DEFAULT now());

CREATE TABLE audit_event (                             -- every state change: actor and time
  id uuid PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL, action text NOT NULL, subject_type text NOT NULL, subject_id uuid NOT NULL
);

CREATE SCHEMA measure;                                 -- ADR-010: no names, phones or Telegram ids
CREATE TABLE measure.event (
  id uuid PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(),
  shop_ref uuid NOT NULL, entry_ref uuid, kind text NOT NULL,
  amount bigint, due_date date, entry_ms integer
);
```

Verification: on 2026-10-06 this schema was executed as written in a throwaway PostgreSQL 16 container and created without error. Six negative checks behaved as intended: a second reversal of the same entry, a zero amount, a credit without a due date, and a second shop for one owner were each rejected by constraints, and an application role with update and delete revoked on `ledger_entry` was refused both operations while still able to insert. Not verified: indexes under load, the anonymization procedure, and the performance targets below.

Rules that the schema alone does not express:

- **Immutability (ADR-005).** The application connects as role `qd_app`, which has `SELECT` and `INSERT` on `ledger_entry`, `acknowledgement_history`, `audit_event` and `measure.event`, and no `UPDATE`, `DELETE` or `TRUNCATE`. Migrations run as a separate owner role that the running application never uses.
- **Balance and non-negativity.** Balance is `sum(credit) - sum(payment)`, with each reversed entry and its reversal excluded. Commands lock the `customer` row (`SELECT ... FOR UPDATE`), compute the balance, and reject a result below zero. No balance column is stored.
- **Allocation and overdue (domain rules BR-3 and BR-4).** Total payments are applied to non-reversed credits in `seq` order. A credit is overdue when its uncovered remainder is above zero and `due_date < today`. This is computed in the domain layer from the account's entries.
- **Default due date (BR-1).** The next date strictly after the sale date whose day equals `shop.due_day`, clamped to the last day of the month.
- **Anonymization.** Sets `display_name` to "Mijoz" plus a counter, clears `name_norm` to the same, clears `phone`, sets status, ends the link and sets its `tg_id` to null, cancels invitations. Entries are untouched.
- **Retention.** `processed_update` rows older than 14 days and sent `outbox_message` rows older than 30 days are deleted by a weekly job. Outbox payloads contain message text and are therefore personal data until deleted.

## Events

Domain events are produced inside the transaction and consumed synchronously in the same transaction; there is no message broker (ADR-002, ADR-004).

| Event | Raised by | Effects |
|---|---|---|
| CreditRecorded | RecordCredit | Customer notification with buttons if linked; measurement `credit` |
| PaymentRecorded | RecordPayment | Customer notification if linked; measurement `payment` |
| EntryReversed | ReverseEntry | Customer notification if linked; acknowledgement closed; measurement `reversal` |
| CustomerLinked | AcceptInvitation | Owner notice; customer sees balance; measurement `linked` |
| EntryConfirmed | Confirm | Measurement `confirmed` |
| EntryDisputed | Dispute | Owner notice with reason; measurement `disputed` |
| DisputeWithdrawn | WithdrawDispute | Owner notice |
| ReminderSent | Reminder job, SendManualReminder | Customer message; measurement `reminder` |
| LinkEnded | Disconnect, replacement, anonymization | Owner notice |
| LinkUnreachable | Dispatcher on Telegram error 403 | Link status change; no owner notice |
| CustomerAnonymized | Removal processor | Owner notice without the former name |

Scheduled jobs (in-process scheduler, Tashkent time): reminders every hour on the hour from 08:00 to 20:00, each run serving the shops whose `reminder_hour` matches; removal-request processing hourly; retention cleanup weekly; operator digest at 21:00 daily. The reminder job is idempotent through the unique constraint on `reminder`.

## Authentication / authorization

**Authentication.** There are no passwords. A user is whoever Telegram says sent the update: `from.id` in an update that arrived with the correct webhook secret. The owner role is the Telegram account in `shop.owner_tg_id`. The customer role is the Telegram account in an active `customer_link`.

**Authorization** is checked in the application layer on every command and every callback (REQ-020, REQ-N11):

| Action | Owner of that shop | Customer with active link to that account | Anyone else |
|---|---|---|---|
| Record, reverse, manage customers, export, settings | Yes | No | No |
| Read an account's balance and history | Yes | Own account only | No |
| Confirm, dispute, withdraw | No | Own account only | No |
| Disconnect, request removal | No | Own account only | No |
| Send reminder | Yes | No | No |

A failed check returns the same generic "not found" reply as a missing record, so that probing reveals nothing.

**Operator.** The operator has server access and therefore database access. The operator role for manual queries is read-only; any data correction goes through the application commands, so it is subject to the same rules and leaves the same audit trail.

## Security

| Area | Control |
|---|---|
| Transport | TLS 1.2 or later terminated by the reverse proxy with an automatically renewed certificate |
| Webhook | Secret header compared in constant time; only the webhook and health paths are routed |
| Invitation tokens | 32 random bytes from the operating system, base64url (43 characters, within Telegram's 64-character limit for `start` parameters); only the SHA-256 hash is stored; single use; cancelled on replacement |
| Input | All text length-limited and treated as data; amounts parsed strictly; all SQL parameterized; output sent as plain text or escaped for Telegram's formatting |
| Abuse | Per-account limit of 30 handled updates a minute, with a polite refusal above it; invitation attempts with invalid tokens are rate-limited per account |
| Secrets | Bot token, webhook secret, database passwords in a root-owned environment file with mode 600; the backup encryption key's private half is kept off the server |
| Database | Listens only on the internal Docker network; separate roles for application, migration, read-only operator, and backup |
| Logs | Structured, with internal identifiers only. Never logged: message text, names, phone numbers, Telegram identities, tokens. |
| Backups | `pg_dump` in custom format, encrypted with a public key before leaving the server, stored at a second provider in Uzbekistan (ADR-009) |
| Host | Key-only SSH on a non-default port, firewall allowing 443 and SSH, unattended security updates, non-root containers |
| Dependencies | Pinned with hashes; vulnerability scan in continuous integration |
| Data minimization | Schema holds only the fields the domain model names (REQ-N05) |

**Consent text**, version 1, shown before linking (REQ-014). Draft in Uzbek for legal review; it is the product's main compliance artifact and must not go live unreviewed:

> "{shop} do'koni sizning nasiya xaridlaringiz va to'lovlaringizni shu bot orqali yuritadi. Saqlanadigan ma'lumotlar: do'kon sizni qanday nomlagani, Telegram hisobingiz identifikatori, nasiya va to'lov yozuvlari. Maqsad: qarz hisobini ikki tomon ham ko'rib turishi va eslatmalar yuborish. Ma'lumotlar faqat sizga va shu do'konga ko'rinadi, boshqa do'konlarga berilmaydi. Istalgan payt /uzish orqali uzilishingiz yoki /ochirish orqali ma'lumotlaringizni o'chirishni so'rashingiz mumkin. Rozimisiz?"

## Performance targets

| ID | Target | Source |
|---|---|---|
| NFR-001 | Server-side handling of a recording message, from webhook receipt to reply sent, takes at most 500 ms at the 95th percentile | REQ-N02 |
| NFR-002 | A customer notification leaves the outbox within 10 seconds at the 95th percentile and within 60 seconds at worst under normal operation | REQ-015 |
| NFR-003 | The bot answers during 06:00 to 23:00 Tashkent time on at least 99% of days in the pilot, measured by the external check | REQ-N09 |
| NFR-004 | Recovery from total server loss within 4 hours, losing at most 24 hours of entries; proven by a rehearsal before the pilot | REQ-N08, ADR-009 |
| NFR-005 | Overview and overdue queries for a shop with 500 customers and 20,000 entries return within 1 second | REQ-026 |
| NFR-006 | The system sustains 5 recording messages a second, about a thousand times pilot load, without breaching NFR-001 | REQ-N02 |
| NFR-007 | All user-facing text is Uzbek in Latin script; a test fails the build if any message key lacks an Uzbek string | REQ-N01 |
| NFR-008 | No stored row outside `customer`, `customer_link` and unsent or recently sent `outbox_message` contains a name, phone number or Telegram identity; verified by a schema test | REQ-N05, ADR-010 |

The 500 ms figure excludes Telegram's own delivery time and the owner's typing, which the system cannot control; the 10-second usability target in REQ-N02 is measured separately with pilot owners.

## Integrations

| Integration | Detail |
|---|---|
| Telegram Bot API | Methods used: `setWebhook` (with `secret_token` and `allowed_updates`), `setMyCommands`, `sendMessage`, `editMessageReplyMarkup`, `answerCallbackQuery`, `sendPhoto` for QR images, `sendDocument` for exports. Client library: aiogram 3 (ADR-003). |
| Rate limits | Dispatcher sends at most 1 message a second per chat and 20 a second overall, below Telegram's limits (EVID-032). Daily reminders are spread across the run. |
| QR codes | Generated locally as PNG; no external service |
| Spreadsheet export | Generated locally as `.xlsx`; contains one shop's customers and entries; sent only to the owner's own chat |
| Backup storage | Encrypted files pushed over SSH or an S3-compatible interface, depending on the provider chosen |
| External uptime check | Calls `/healthz` every minute; alerts the operator by a channel independent of the server |

## Error handling

| Situation | Behavior |
|---|---|
| Message not understood | Short Uzbek hint with two examples; nothing saved |
| Unknown customer name | Offer to create the customer with that name |
| Several matching customers | Buttons to choose; the entry is saved only after the choice |
| Invalid amount | Explain the accepted format; nothing saved |
| Payment above balance | Reject, state the current balance (INV-3) |
| Reversal not allowed | Explain which rule blocks it and what to do first |
| Duplicate update or repeated button press | Treated as success without a second effect |
| Authorization failure | Generic "not found" reply; logged as a security event |
| Telegram 429 | Wait the `retry_after` period, then retry; counts toward no failure limit |
| Telegram 403 (bot blocked) | Mark the link unreachable; drop that customer's pending messages |
| Other Telegram or network errors | Retry with exponential backoff up to 24 hours, then mark failed and alert |
| Database error during a command | Roll back; reply "saqlanmadi, qayta urinib ko'ring"; alert the operator |
| Unhandled exception | Roll back; same reply; error logged with an identifier the owner can quote |

A reply that says an entry was saved is sent only after the transaction has committed (REQ-N08).

## Observability requirements

| Need | Specification |
|---|---|
| Logs | JSON lines to standard output, kept 30 days on the server; fields: time, level, event, update identifier, internal identifiers, duration, error code |
| Health | `/healthz` plus an external check every minute |
| Alerts to the operator | Webhook failing for 3 minutes; any unhandled exception; outbox messages pending longer than 10 minutes; backup not completed by 04:00; disk above 80% |
| Daily digest | Sent to the operator at 21:00: updates handled, entries recorded, notifications sent and failed, reminders sent, errors, last backup time and size |
| Pilot metrics | Weekly export from `measure.event`: on-time repayment share (METRIC-001), shops recording that week (METRIC-002), confirmation share (METRIC-003), entry handling times (METRIC-004) |
| Audit | `audit_event` and `acknowledgement_history` answer "who did what, when" for any account |

Because alerts travel through the same bot, a total outage is caught only by the external check. That check must notify through a channel that does not depend on the server.

## Traceability to requirements / ADRs

| Requirement | Specified in | Decision records |
|---|---|---|
| REQ-001, REQ-002 | Owner conversation `/start`; `shop.owner_tg_id` unique | ADR-001 |
| REQ-003, REQ-004, REQ-005 | `/mijoz`, `/mijozlar`; `customer.name_norm` and index | ADR-004 |
| REQ-006, REQ-007, REQ-008, REQ-010 | Entry grammar; RecordCredit; `ledger_entry`; default due date rule | ADR-001, ADR-005 |
| REQ-009 | RecordPayment; allocation rule | ADR-005 |
| REQ-011, REQ-012 | ReverseEntry; role `qd_app` without update or delete; unique `reverses_id` | ADR-005 |
| REQ-013, REQ-014 | `/ulash`; AcceptInvitation; `link_invitation`, `customer_link` consent columns; consent text | ADR-001 |
| REQ-015 | Events table; `outbox_message`; dispatcher | ADR-007 |
| REQ-016, REQ-017, REQ-018 | Confirm, Dispute, WithdrawDispute; `acknowledgement` states | ADR-005 |
| REQ-019, REQ-020, REQ-021 | `/qarzim`, `/uzish`; authorization matrix | ADR-001 |
| REQ-022, REQ-023, REQ-024, REQ-025 | `/eslatma`, `/muddati`; reminder job; `reminder` unique constraint | ADR-007 |
| REQ-026, REQ-027 | `/qarzlar`, `/muddati`, `/mijozlar`; NFR-005 | ADR-001 |
| REQ-028 | `/eksport`; spreadsheet integration | ADR-001 |
| REQ-029 | RequestRemoval; anonymization rule; `removal_request` | ADR-005, ADR-010 |
| REQ-030 | `measure.event`; pilot metrics export | ADR-010 |
| REQ-N01 | Conversation text; NFR-007 | ADR-001, ADR-003 |
| REQ-N02, REQ-N03 | Entry grammar; NFR-001, NFR-006 | ADR-001, ADR-002 |
| REQ-N04 | Deployment and backup integrations | ADR-008, ADR-009 |
| REQ-N05 | Schema; logs; NFR-008 | ADR-010 |
| REQ-N06 | `amount bigint`; derived balance; integer arithmetic in application code | ADR-003, ADR-005 |
| REQ-N07 | Role permissions; `audit_event` | ADR-005 |
| REQ-N08 | Commit-before-reply; backups; NFR-004 | ADR-009 |
| REQ-N09 | Health, alerts; NFR-003 | ADR-006, ADR-008 |
| REQ-N10 | `reminder` unique constraint; no setting for limits | ADR-007 |
| REQ-N11 | Authentication and authorization; invitation tokens | ADR-006 |

Every requirement from the PRD and every decision record from ADR-001 to ADR-010 appears in this table.

## Assumptions

- The automatic reminder limit of once per seven days is enforced by the reminder job's eligibility query; the unique constraint guards only against duplicates on the same day.
- A default due day of 5 suits payday credit. It is a per-shop setting, so a wrong default costs little.
- The "-" prefix and the words "berdi" and "to'ladi" are natural ways for owners to mark a payment. Untested; pilot feedback should adjust the grammar.
- Telegram's `from.id` is stable for an account and sufficient as identity. Account loss is handled by an operator procedure, not by the system.
- The consent text is adequate. It is an agent draft and has not been reviewed by a lawyer.
- 14 and 30 days are reasonable retention periods for technical tables. No legal basis was researched.

## Open questions

1. Legal review, now four items: recording a customer before consent; delaying removal while a balance is owed; notifications passing through Telegram's servers abroad (EVID-033); and the consent text above. All four bear on the personal data law (EVID-027).
2. Resolved on 2026-10-06: the founder chose that notifications include the name the shop uses for the customer. The agent had recommended omitting it. Consequence: the name, as well as the amount, passes through and is stored by Telegram abroad (EVID-033), which adds weight to legal question 1.
3. Hosting provider and backup location are not chosen (ADR-008), so the backup transport is specified as two alternatives.
4. Should the owner's reply include the customer's confirmation status history, or only on request? Affects message length.
5. Resolved on 2026-10-06: the founder chose that each owner sets the reminder hour. The specification limits the choice to 08:00 to 20:00 so that no reminder arrives at night; that limit is an agent decision.

## Approvals

| Record | Subject | Status |
|---|---|---|
| DEC-008 / APR-008 | Decision records ADR-001 to ADR-010 | Approved 2026-10-06 |
| DEC-009 | Security, data and compliance specification in this document: authentication by Telegram identity, authorization matrix, database roles enforcing immutability, token handling, log and retention rules, and the draft consent text subject to legal review before the pilot | Approved 2026-10-06 |
