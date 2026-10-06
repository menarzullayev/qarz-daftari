# Domain Model

Status: draft for review; awaiting human approval of core business rules (DEC-006). Prepared 2026-10-06.
Upstream: `docs/04-prd/OUTPUT.md` (MVP scope approved, DEC-005 / APR-005).

This model covers the MVP only. It names the concepts and rules the PRD implies and settles the ambiguities the PRD left open. Where a rule goes beyond what the PRD states, it is marked **decided here** and listed for approval under DEC-006.

## Entities

| ID | Entity | What it is | Identity |
|---|---|---|---|
| DOM-001 | Shop | A grocery shop and its ledger settings | Shop ID; owned by exactly one Telegram account |
| DOM-002 | Customer | One person's credit account in one shop, as the owner knows them | Customer ID, unique within the system; meaningful only inside its shop |
| DOM-003 | Ledger entry | One immutable financial fact on a customer's account: a credit sale, a payment, or a reversal | Entry ID; sequence number within the customer account |
| DOM-004 | Customer link | The connection between a customer record and a Telegram account, created with the customer's consent | Link ID |
| DOM-005 | Link invitation | A personal, unguessable token the owner hands to the customer as a link or QR code | Token |
| DOM-006 | Acknowledgement | The customer's response to an entry that increases their debt | One current state per such entry, with a history of changes |
| DOM-007 | Reminder | A record that a reminder was sent to a customer | Reminder ID |
| DOM-008 | Removal request | A customer's request to have their identifying data removed | Request ID |
| DOM-009 | Measurement record | An identity-free fact kept for the pilot metrics | Record ID |

"Customer" always means the record inside one shop. The same person buying on credit in two shops is two unrelated Customer records, and nothing in the model connects them (REQ-020).

## Value objects

| Value object | Definition |
|---|---|
| Money | A whole number of UZS, greater than zero for any entry (REQ-N06) |
| Balance | A whole number of UZS, zero or greater, always derived from entries |
| Display name | The name the owner typed, plus a normalized form used only for matching: lower-cased, with Cyrillic transliterated to Latin (REQ-004) |
| Phone number | Optional, in international format |
| Telegram identity | The Telegram account identifier of an owner or a linked customer |
| Due date | A calendar date in Tashkent time |
| Due date rule | A day of the month, 1 to 31, set per shop (REQ-008) |
| Entry note | Optional short free text |
| Dispute reason | Required short free text given with a dispute (REQ-016) |
| Reminder template | One of a fixed set of approved wordings (REQ-024) |
| Consent record | What the customer was shown, that they agreed, and when (REQ-014) |
| Actor | Who caused a change: the owner, the customer, or the system |

## Aggregates

| Aggregate | Root | Contains | Consistency guaranteed inside |
|---|---|---|---|
| Shop | DOM-001 | Due date rule, reminder switch, chosen reminder template | Settings change together; one owner per shop |
| Customer account | DOM-002 | Ledger entries (DOM-003), acknowledgements (DOM-006), the current link (DOM-004), outstanding invitation (DOM-005), per-customer reminder opt-out | Balance, entry ordering, acknowledgement state, and link state are always consistent with each other |
| Reminder log | DOM-007 | Sent reminders for a customer | Frequency limits |
| Removal request | DOM-008 | Request and its outcome | Processed once |

The customer account is the unit of consistency: every operation that changes a balance touches exactly one customer account. Shop totals are sums over accounts and may be computed on demand.

## Relationships

- A Shop has many Customers. A Customer belongs to exactly one Shop.
- A Customer has many Ledger entries, in a strict order.
- A reversal entry refers to exactly one earlier entry on the same Customer.
- A Customer has at most one active Customer link at a time, and any number of ended ones.
- A Telegram account can be the owner of at most one Shop (REQ-002) and can hold links to Customers in any number of shops, at most one per shop.
- An Acknowledgement belongs to one debt-increasing Ledger entry and records which Customer link made it.
- A Reminder belongs to one Customer and was delivered through one Customer link.
- Measurement records refer to shops and entries by opaque identifiers and never to names, phones, or Telegram identities (REQ-030).

## Invariants

These must hold after every operation.

| No. | Invariant | Source |
|---|---|---|
| INV-1 | A saved ledger entry never changes and is never removed. | REQ-011, REQ-N07 |
| INV-2 | A customer's balance equals credits minus payments, with every reversed entry and its reversal cancelling out. | REQ-N06 |
| INV-3 | A balance is never negative. An operation that would make it negative is rejected. | PRD acceptance criterion for FEAT-004 |
| INV-4 | Every entry amount is a positive whole number of UZS, fixed when the entry is saved. | REQ-007 |
| INV-5 | An entry can be reversed at most once. A reversal cannot itself be reversed; a mistaken reversal is corrected by recording a new entry. | Decided here |
| INV-6 | A reversal carries the same amount as the entry it reverses. Partial reversal does not exist. | Decided here |
| INV-7 | An entry with no customer response is unconfirmed. Only an explicit customer action makes it confirmed. | REQ-018 |
| INV-8 | A user can read a customer account only as the owner of its shop or through the active link to that account. | REQ-020, REQ-N11 |
| INV-9 | No customer link exists without a consent record. | REQ-014 |
| INV-10 | A customer cannot be archived while their balance is above zero. | REQ-005 |
| INV-11 | At most one automatic reminder per customer in any seven days, and at most one manual reminder per customer per day. The owner cannot change these limits. | REQ-023, REQ-025, REQ-N10 |
| INV-12 | Every state change records the time and the actor. | REQ-N07 |

## Business rules

Recording:

| No. | Rule | Source |
|---|---|---|
| BR-1 | A credit sale gets a due date from the shop's due date rule unless the owner sets one. The default is the next occurrence of the rule's day strictly after the sale date; if the month is shorter than the day, the last day of that month is used. | REQ-008; calculation decided here |
| BR-2 | Payments are not attached to particular credit sales. They reduce the account balance. | Decided here; refines REQ-009 |
| BR-3 | To decide what is overdue, payments are applied to credit sales from the oldest sale to the newest. This allocation is calculated when needed and never stored, so reversing an entry simply changes the calculation. | REQ-009; decided here |
| BR-4 | A customer is overdue when, after BR-3, some credit sale is not fully covered and its due date has passed. Days overdue count from the earliest such due date. | REQ-026 |
| BR-5 | Reversing a credit sale lowers the balance; reversing a payment raises it. A reversal that would break INV-3 is rejected, and the owner is told to reverse the payment first. | REQ-011; decided here |

Acknowledgement:

| No. | Rule | Source |
|---|---|---|
| BR-6 | Acknowledgement applies to every entry that increases the customer's debt: credit sales and reversals of payments. Payments and reversals of credit sales are sent as notifications only. | Decided here; refines REQ-016 |
| BR-7 | A dispute does not change the balance. The disputed amount stays owed in the ledger until the owner reverses the entry. | REQ-017 |
| BR-8 | A dispute ends in one of two ways: the owner reverses the entry, or the customer withdraws the dispute, which returns the entry to unconfirmed. | REQ-017; decided here |
| BR-9 | A confirmed entry cannot later be disputed by the customer in the product. If they change their mind they speak to the owner, who can reverse it. | Decided here |
| BR-10 | Entries recorded before a customer linked are shown in their history as unconfirmed and can be confirmed or disputed one by one. | Decided here |

Reminders:

| No. | Rule | Source |
|---|---|---|
| BR-11 | A reminder can be sent only if the shop has reminders on, the owner has not switched them off for that customer, the customer has an active reachable link, and the customer is overdue or has a credit sale due today. | REQ-022, REQ-023 |
| BR-12 | The amount stated in a reminder is the overdue amount excluding entries under open dispute. If nothing remains after that exclusion, no reminder is sent. | Decided here; answers PRD open question 6 |
| BR-13 | A reminder names the shop and the amount and uses the shop's chosen template. It never mentions other customers or other shops. | REQ-024 |

Linking and data:

| No. | Rule | Source |
|---|---|---|
| BR-14 | An invitation can be used once. Generating a new invitation for a customer cancels the previous one. | REQ-013, REQ-N11 |
| BR-15 | If the customer declines consent, no link is created and nothing about their Telegram account is kept. | PRD acceptance criterion for FEAT-005 |
| BR-16 | When a customer disconnects, the link ends, notifications stop, and the entries and past acknowledgements remain. | REQ-021 |
| BR-17 | A removal request is carried out immediately if the balance is zero: the display name is replaced by an anonymous label, the phone number and Telegram identity are erased, and the link ends. If the balance is above zero, the request is recorded and carried out when the balance reaches zero. | REQ-029; the condition on balance is decided here and needs legal confirmation |
| BR-18 | Names are not unique within a shop. When the owner's text matches several customers, the owner must choose. | REQ-006 |

## Lifecycle rules

**Customer:** Active → Archived (only at zero balance) → Active again. Active or Archived → Anonymized, which is final.

**Ledger entry:** Recorded. It may later be marked as reversed by the existence of a reversal entry; the entry itself is unchanged.

**Acknowledgement of a debt-increasing entry:**

| From | Event | To |
|---|---|---|
| Unconfirmed | Customer confirms | Confirmed (final) |
| Unconfirmed | Customer disputes with a reason | Disputed |
| Disputed | Customer withdraws | Unconfirmed |
| Disputed | Owner reverses the entry | Closed by reversal (final) |
| Unconfirmed or Confirmed | Owner reverses the entry | Closed by reversal (final) |

**Link invitation:** Issued → Used, or Issued → Cancelled (replaced or customer anonymized).

**Customer link:** Active → Ended (customer disconnects, owner replaces the invitation after a lost phone, or customer anonymized). An Active link becomes Unreachable when Telegram reports that the customer blocked the bot, and returns to Active when the customer messages the bot again. Unreachable links receive no reminders.

**Removal request:** Received → Completed, or Received → Waiting for zero balance → Completed.

**Shop:** Active only. Closing a shop is not modelled in the MVP.

## Domain boundaries

Inside the model: the ledger of one shop, the customer's view of their own account, acknowledgement, and reminders.

Deliberately outside, with the boundary stated so later stages do not cross it by accident:

- **No person identity across shops.** The model has no concept of a person separate from a customer record, so cross-shop lookups are impossible by construction (vision principle 6).
- **No money movement.** A payment is a note that money changed hands outside the system. The model holds no accounts, wallets, or transfers (EVID-009).
- **No interest, fees, or penalties.** A debt is exactly the sum of what was recorded (EVID-017).
- **No goods.** An entry has an amount and an optional note, not line items.
- **No staff.** The only actor on the shop side is the owner (FEAT-014 is deferred).
- **No billing.** There is no subscription, plan, or invoice concept (FEAT-015 is deferred).
- **Telegram is a channel, not part of the domain.** Chats, messages, and buttons belong to the interface. The domain knows only a Telegram identity and whether a link is reachable.

## Traceability to requirements

| Model element | Requirements |
|---|---|
| DOM-001 Shop | REQ-001, REQ-002, REQ-008, REQ-022, REQ-024 |
| DOM-002 Customer | REQ-003, REQ-004, REQ-005, REQ-020, REQ-027 |
| DOM-003 Ledger entry | REQ-006, REQ-007, REQ-009, REQ-010, REQ-011, REQ-012, REQ-N06, REQ-N07 |
| DOM-004 Customer link | REQ-014, REQ-015, REQ-019, REQ-020, REQ-021, REQ-N11 |
| DOM-005 Link invitation | REQ-013, REQ-N11 |
| DOM-006 Acknowledgement | REQ-016, REQ-017, REQ-018 |
| DOM-007 Reminder | REQ-023, REQ-025, REQ-N10 |
| DOM-008 Removal request | REQ-029, REQ-N05 |
| DOM-009 Measurement record | REQ-030 |
| Shop totals and overdue list (derived) | REQ-026 |
| Export (a read of DOM-002 and DOM-003) | REQ-028 |

Requirements with no domain element because they constrain the interface or operations, not the model: REQ-N01, REQ-N02, REQ-N03, REQ-N04, REQ-N08 and REQ-N09; these pass to Architecture and the Technical Specification.

Every functional requirement from REQ-001 to REQ-030 appears above.

## Assumptions

- Owners think of credit as a running tab per customer, not as separate invoices. BR-2 and BR-3 depend on this. Untested.
- One due day per month per shop fits how payday credit works. Untested; carried from the PRD.
- A customer who confirmed an entry and later objects will raise it in person. BR-9 depends on this.
- Tashkent time is correct for every pilot shop.
- A removal request can lawfully wait until a debt is settled (BR-17). This is an agent assumption about the personal data law (EVID-027), not legal advice.

## Open questions

1. Is it lawful to hold a named customer's record before they have consented, and to delay removal while a balance is owed? Both need a lawyer (carried from PRD open question 1).
2. Should a customer who links later be asked to confirm their opening balance as a single figure, in addition to BR-10? This would be kinder to the customer but adds a concept the PRD does not have.
3. If an owner loses access to their Telegram account, how is the shop recovered? Not modelled.
4. Should an overpayment be allowed as a credit in the customer's favor? INV-3 forbids it in the MVP.

## Decisions / approvals

| Record | Subject | Status |
|---|---|---|
| DEC-005 / APR-005 | MVP scope | Approved 2026-10-06 |
| DEC-006 | Core business rules decided in this stage: running-balance model with calculated oldest-first allocation (BR-2, BR-3); acknowledgement only for debt-increasing entries (BR-6); no dispute after confirmation (BR-9); disputed amounts excluded from reminders (BR-12); whole-entry reversal only (INV-5, INV-6); removal deferred until zero balance (BR-17) | Approval pending |
