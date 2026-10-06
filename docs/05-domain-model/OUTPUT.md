# Domain Model

Version 2. Status: rewritten for PRD version 2 (DEC-013 / APR-013); awaiting the founder's end-of-sequence review (DEC-014). Prepared 2026-10-06.
Version 1 (DEC-006) is superseded and remains in version history.

This model covers release 1 as defined in PRD version 2. Rules that go beyond what the PRD states are marked **decided here**; by the founder's instruction they were decided by the agent without stopping and are listed for review at the end.

## Entities

| ID | Entity | What it is |
|---|---|---|
| DOM-001 | Shop | A grocery shop: the tenant. Holds settings, catalog, customers, staff, and its own subscription |
| DOM-002 | Customer | One person's credit account in one shop, as the shop knows them |
| DOM-003 | Ledger entry | One immutable financial fact on a customer account: credit sale, opening balance, payment, or reversal |
| DOM-004 | Customer link | The connection between a customer record and a user account, created with consent |
| DOM-005 | Invitation | An unguessable token: a shop's counter code, a personal customer link, or a staff invitation |
| DOM-006 | Dispute | A customer's objection to one entry, with a reason and an outcome. Replaces the version 1 acknowledgement |
| DOM-007 | Reminder | A record that a reminder was sent to a customer, by Telegram or SMS |
| DOM-008 | Removal request | A customer's request to remove their identifying data, or an owner's request to delete a shop |
| DOM-009 | Measurement record | An identity-free fact kept for product metrics |
| DOM-010 | User | A person using the service, identified by a Telegram account, with a chosen language. May be staff in some shops and a customer of others |
| DOM-011 | Membership | A user's role in one shop: owner, manager, or seller, with a status |
| DOM-012 | Goods line | One line of a credit sale: name, quantity, unit, unit price, line total |
| DOM-013 | Catalog item | A good the shop sells, with unit and current price; entered by staff or learned from a typed line |
| DOM-014 | Promise | The repayment date attached to a credit entry, with the history of its changes |
| DOM-015 | Date change request | A customer's request to move a promise, with an outcome |
| DOM-016 | Payment notice | A customer's statement that they paid an amount, optionally with a receipt, with an outcome |
| DOM-017 | Import batch | One spreadsheet import: its rows, validation result, and the entries it created |
| DOM-018 | Subscription | A shop's commercial state: trial, paid-through date, limited, or suspended |
| DOM-019 | Subscription receipt | An owner's proof of a card transfer, with an administrator's decision |
| DOM-020 | Platform setting | A switch or value the administrator controls: trial, SMS, online payment, price, quotas |
| DOM-021 | Activity record | Who did what, when, to which subject, within a shop or on the platform |
| DOM-022 | Support access | A logged, time-limited permission for an administrator to see one shop's data |
| DOM-023 | Stored file | A receipt image or import file held by the service |

"Customer" always means the record inside one shop. One user linked to customer records in two shops is known to the system as one Telegram account, but no shop can see or query that fact, and the model offers no operation that matches customers across shops, including shops with the same owner (REQ-020, REQ-065).

## Value objects

| Value object | Definition |
|---|---|
| Money | Whole UZS, greater than zero for any entry or line total (REQ-N06) |
| Quantity | A positive number with up to three decimals, with a unit such as piece, kilogram, or litre |
| Balance | Whole UZS, zero or greater, always derived from entries |
| Display name | The name staff typed, plus a normalized form for matching (lower-cased, Cyrillic transliterated) |
| Phone number | Optional, international format |
| Telegram identity | The account identifier of a user |
| Language | Uzbek or Russian (REQ-051) |
| Role | Owner, manager, or seller (REQ-033) |
| Promised date | A calendar date in Tashkent time |
| Credit limit | Whole UZS; per customer, with a shop default (REQ-044) |
| Payment history indicator | Derived per customer from that shop's records only: share of due credit repaid by its promised date, and longest delay in days (REQ-045) |
| Dispute reason, decline reason | Short required free text |
| Reminder template | One of a fixed set of approved wordings per language |
| Consent record | The text version shown, the agreement, and when |
| Actor | A user in a role, a customer, an administrator, or the system |
| Subscription period | Whole months |

## Aggregates

| Aggregate | Root | Contains | Consistency guaranteed inside |
|---|---|---|---|
| Shop | DOM-001 | Settings, reminder configuration, default credit limit, SMS quota use | Settings change together; exactly one owner |
| Staff | DOM-011 | Memberships and staff invitations of a shop | One owner; a user has at most one membership per shop |
| Catalog | DOM-013 | A shop's catalog items | Names unique within a shop after normalization |
| Customer account | DOM-002 | Ledger entries with goods lines and promises, disputes, the current link, date change requests, payment notices, credit limit | Balance, entry order, overdue status, and dispute state always agree |
| Reminder log | DOM-007 | Sent reminders of a customer | Frequency limits |
| Import batch | DOM-017 | Rows and created entries | Applied whole or not at all; undone whole |
| Subscription | DOM-018 | Receipts and their decisions for one shop | Paid-through date changes only by an approved receipt, an online payment, or an administrator action |
| Platform settings | DOM-020 | All switches and values | Each change recorded |

The customer account remains the unit of consistency for money: every operation that changes a balance touches exactly one customer account. Shop totals, reports, and an owner's combined totals across shops are sums computed from accounts.

## Relationships

- A User has any number of Memberships, at most one per Shop, and any number of Customer links, at most one per Shop.
- A Shop has exactly one owner Membership, many Customers, one Catalog, one Subscription.
- A Customer belongs to one Shop and has many Ledger entries in strict order.
- A credit entry has zero or more Goods lines and exactly one Promise. A Goods line may refer to the Catalog item it was chosen from; the line keeps its own name and price.
- A reversal refers to exactly one earlier entry on the same Customer.
- A Dispute, a Date change request, and a Payment notice each belong to one Customer; the first two refer to one entry.
- An accepted Payment notice refers to the payment entry it produced.
- An Import batch refers to the entries it created.
- A Subscription receipt belongs to one Subscription and may refer to one Stored file.
- Every Ledger entry, Activity record, and decision refers to the Membership or administrator that made it (REQ-035).
- Measurement records refer to shops and entries by opaque identifiers and never to names, phones, or Telegram identities (REQ-030).

## Invariants

| No. | Invariant | Source |
|---|---|---|
| INV-1 | A saved ledger entry's kind, total, customer, author, and time never change, and no entry is removed. | REQ-011, REQ-N07 |
| INV-2 | A customer's balance equals credits and opening balances minus payments, with each reversed entry and its reversal cancelling out. | REQ-N06 |
| INV-3 | A balance is never negative. | PRD version 1 criterion, kept |
| INV-4 | Every entry total and line total is a positive whole number of UZS. | REQ-007, REQ-N06 |
| INV-5 | An entry can be reversed at most once; a reversal cannot be reversed. | Kept from version 1 |
| INV-6 | A reversal carries the full total of the entry it reverses. | Kept from version 1 |
| INV-7 | When a credit entry has goods lines, the sum of line totals equals the entry total. | REQ-037 |
| INV-8 | Goods lines can be added to an amount-only entry once, by its author or a manager or owner, no later than the end of the day after the sale, and only if they sum to the total. Afterward lines never change. | REQ-038 |
| INV-9 | Every credit entry has exactly one current promised date; every change of it is kept with time, actor, and reason. | REQ-008, REQ-067 |
| INV-10 | Data of one shop is readable and changeable only by that shop's active memberships according to role, by the linked customer for their own account, and by an administrator holding a current support access. | REQ-020, REQ-059, REQ-N12 |
| INV-11 | A shop has exactly one owner at all times. | REQ-031 |
| INV-12 | No customer link exists without a consent record. | REQ-014 |
| INV-13 | A customer cannot be archived while their balance is above zero. | REQ-005 |
| INV-14 | At most one automatic reminder per customer in seven days and one manual reminder per customer per day, across Telegram and SMS together. | REQ-023, REQ-025, REQ-N10 |
| INV-15 | A payment notice, a dispute, or a date change request never changes a balance or a promised date by itself; only a staff decision does. | REQ-060, REQ-017, REQ-067 |
| INV-16 | A subscription's paid-through date moves only through an approved receipt, a confirmed online payment, or a logged administrator action. | REQ-055 |
| INV-17 | A changed catalog price never alters a saved goods line. | REQ-041 |
| INV-18 | Every state change records the time and the actor. | REQ-N07, REQ-035 |

## Business rules

Recording:

| No. | Rule | Source |
|---|---|---|
| BR-1 | A credit sale's promised date is the one chosen by the seller; if none is chosen, the shop default applies: a number of days after the sale, 30 unless the shop changes it. | REQ-008; default decided here |
| BR-2 | Payments are not attached to particular credit sales; they reduce the account balance. | Kept |
| BR-3 | To decide what is overdue, payments are applied to credit sales and opening balances from the oldest to the newest. The allocation is calculated, never stored. | REQ-009 |
| BR-4 | A customer is overdue when, after BR-3, some credit is not fully covered and its current promised date has passed. | REQ-026 |
| BR-5 | Reversing a credit lowers the balance; reversing a payment raises it. A reversal that would break INV-3 is rejected. Only managers and owners reverse. | REQ-011, REQ-033 |
| BR-6 | A line chosen from the catalog takes the catalog's current price, which the seller may change for that line. A typed good not in the catalog is added to it as a learned item with the typed price. | REQ-040 |
| BR-7 | Line total is quantity times unit price, rounded to the nearest whole UZS, halves rounded up. | REQ-N06; rounding decided here |
| BR-8 | A sale that would take the balance above the customer's credit limit produces a warning showing balance and limit. If the shop setting forbids it, a seller cannot proceed and a manager or owner can. | REQ-044 |
| BR-9 | The payment history indicator counts only credit whose promised date has passed: on-time share is the value covered on or before the promised date, by BR-3 with payment times, divided by the value due. Customers with nothing yet due show no indicator. | REQ-045; calculation decided here |

Customer side:

| No. | Rule | Source |
|---|---|---|
| BR-10 | A customer is never asked to confirm an entry. Silence means nothing either way. | REQ-016 |
| BR-11 | A customer may dispute an entry that increases their debt, once per entry, within 30 days of being notified of it. | REQ-016; limits decided here |
| BR-12 | A dispute ends in one of three ways: staff reverse the entry; a manager or owner declines the dispute with a reason; the customer withdraws it. | REQ-017; decline decided here |
| BR-13 | While a dispute is open, the entry stays in the balance and its amount is left out of reminders. Once declined, it counts again. | Kept from version 1; extended here |
| BR-14 | An accepted payment notice creates a payment for the stated or a corrected amount, attributed to the accepting staff member. A declined notice changes nothing. | REQ-061 |
| BR-15 | An accepted date change request sets the new promised date. One request may be open per entry, and a declined request cannot be repeated for the same entry within seven days. | REQ-066, REQ-067; repeat limit decided here |
| BR-16 | A customer who starts the bot from a shop's counter code appears in that shop's waiting list with the name from their Telegram profile; staff attach them to a customer record. A personal link attaches directly. Consent is required in both cases before anything is stored beyond the waiting entry, which expires after 24 hours. | REQ-013, REQ-014; expiry decided here |

Reminders:

| No. | Rule | Source |
|---|---|---|
| BR-17 | A reminder can be sent only if the shop has reminders on, they are not off for that customer, the subscription is not suspended, and the customer is overdue or has a promise due today. | REQ-022, REQ-023 |
| BR-18 | Channel: Telegram if the customer has a reachable link; otherwise SMS if SMS is on for platform and shop, the customer has a phone number, and the shop has quota left; otherwise nothing is sent and the customer is listed for the shop as not reachable. | REQ-043 |
| BR-19 | A reminder is in the customer's language if known, else the shop's. | REQ-024, REQ-051 |

Staff and shops:

| No. | Rule | Source |
|---|---|---|
| BR-20 | Permissions follow REQ-033 and are checked for every operation regardless of client. | REQ-033, REQ-N11 |
| BR-21 | A suspended or removed member loses access at once; their entries and activity stay attributed to them. | REQ-034 |
| BR-22 | Ownership transfers only to an active manager of the same shop, after both confirm; the former owner becomes a manager. | REQ-036 |
| BR-23 | A user acts in one active shop at a time; every reply and screen names it. | REQ-064 |
| BR-24 | An import is validated in full before anything is saved. Each imported balance becomes one opening-balance entry with a promised date. Undoing an import within 24 hours reverses all its entries; customers it created are archived if their balance is then zero. | REQ-062, REQ-063 |
| BR-25 | Deleting a shop starts a 30-day waiting period in which the owner can export or cancel; then customer links end and the shop's data is erased. | REQ-048; period decided here |

Subscription:

| No. | Rule | Source |
|---|---|---|
| BR-26 | A new shop gets a 30-day trial if the trial switch is on when it is created. | REQ-052 |
| BR-27 | An approved receipt extends the paid-through date by the whole months the administrator records, starting from the later of today and the current paid-through date. | REQ-055; start rule decided here |
| BR-28 | Seven days and one day before the trial or paid period ends, the owner is warned. | REQ-057; timing decided here |
| BR-29 | In limited mode, recording new credit sales and imports is refused; viewing, exporting, recording payments, reversals, disputes, customer views, and reminders continue. | REQ-057; reminders continuing decided here |
| BR-30 | A suspended shop, an administrator action, allows only viewing and export by the owner. | REQ-058 |
| BR-31 | An administrator sees a shop's customers and entries only during a support access, which is requested with a reason, lasts at most 24 hours, and is shown to the owner. | REQ-059; duration decided here |

Removal:

| No. | Rule | Source |
|---|---|---|
| BR-32 | A customer's removal request is carried out at once if the balance is zero; otherwise it is recorded and carried out when the balance reaches zero. Anonymization replaces the display name with an anonymous label, erases phone and link, and removes receipt files they sent. | REQ-029; kept from version 1 and still needs legal confirmation |

## Lifecycle rules

| Subject | States and transitions |
|---|---|
| Customer | Active ⇄ Archived (only at zero balance); either → Anonymized (final) |
| Ledger entry | Recorded; may gain goods lines once (INV-8); may be marked reversed by a reversal |
| Promise | Set → Changed (any number of times, each recorded) |
| Dispute | Open → Reversed, Declined, or Withdrawn (all final) |
| Payment notice | Sent → Accepted or Declined (final); Sent → Expired after 14 days |
| Date change request | Open → Accepted or Declined (final); Open → Expired when the entry is fully paid or reversed |
| Customer link | Waiting (counter code, at most 24 hours) → Active → Ended; Active ⇄ Unreachable |
| Invitation | Issued → Used, Cancelled, or Expired |
| Membership | Invited → Active ⇄ Suspended → Removed (final) |
| Catalog item | Active ⇄ Hidden; learned items start Active and flagged as learned until a manager reviews them |
| Import batch | Uploaded → Validated → Applied → Undone (within 24 hours); Validated → Discarded |
| Subscription | Trial → Active → Limited ⇄ Active; any → Suspended ⇄ previous state. A shop created with the trial switch off starts Limited |
| Subscription receipt | Submitted → Approved or Rejected (final) |
| Support access | Requested → Active → Expired or Closed |
| Shop | Active → Deletion pending (30 days) → Erased; Deletion pending → Active on cancel |

## Domain boundaries

Inside: a shop's ledger with itemized sales, its staff and catalog, the customer's view and requests, reminders, the shop's subscription, and platform administration.

Outside, with the boundary stated so later stages do not cross it:

- **No customer identity across shops for shops.** A user may be linked in several shops, but no staff-facing or owner-facing operation exposes or uses that, including an owner's combined totals, which add amounts and never match people (REQ-065).
- **No money movement for customers.** Payments are notes that money changed hands elsewhere. A payment notice with a receipt is evidence for staff, not a transfer.
- **No interest, fees, or penalties on debts.** A debt is exactly what was recorded (EVID-017).
- **No stock.** The catalog holds names, units, and prices only; nothing counts quantities on hand.
- **Subscription money is outside the ledger.** Receipts and paid-through dates never touch customer accounts.
- **The reliability indicator stays inside the shop.** It is derived from one shop's records and shown only to its staff.
- **Telegram, SMS, and payment systems are channels.** The domain knows identities, reachability, and outcomes, not their mechanics.

## Traceability to requirements

| Model element | Requirements |
|---|---|
| DOM-001 Shop | REQ-001, REQ-042, REQ-048, REQ-065 |
| DOM-002 Customer | REQ-003, REQ-004, REQ-005, REQ-026, REQ-027, REQ-044, REQ-045 |
| DOM-003 Ledger entry | REQ-006, REQ-007, REQ-009, REQ-010, REQ-011, REQ-012, REQ-035, REQ-N06, REQ-N07 |
| DOM-004 Customer link | REQ-013, REQ-014, REQ-015, REQ-019, REQ-020, REQ-021 |
| DOM-005 Invitation | REQ-013, REQ-032, REQ-N11 |
| DOM-006 Dispute | REQ-016, REQ-017 |
| DOM-007 Reminder | REQ-022, REQ-023, REQ-024, REQ-025, REQ-043, REQ-N10 |
| DOM-008 Removal request | REQ-029, REQ-048, REQ-N05 |
| DOM-009 Measurement record | REQ-030 |
| DOM-010 User | REQ-050, REQ-051, REQ-064, REQ-N11 |
| DOM-011 Membership | REQ-031, REQ-032, REQ-033, REQ-034, REQ-036 |
| DOM-012 Goods line | REQ-037, REQ-038, REQ-041 |
| DOM-013 Catalog item | REQ-039, REQ-040, REQ-041 |
| DOM-014 Promise | REQ-008, REQ-023, REQ-067 |
| DOM-015 Date change request | REQ-066, REQ-067 |
| DOM-016 Payment notice | REQ-060, REQ-061 |
| DOM-017 Import batch | REQ-062, REQ-063 |
| DOM-018 Subscription | REQ-052, REQ-053, REQ-056, REQ-057 |
| DOM-019 Subscription receipt | REQ-054, REQ-055 |
| DOM-020 Platform setting | REQ-052, REQ-053, REQ-056, REQ-N14 |
| DOM-021 Activity record | REQ-035, REQ-047, REQ-058 |
| DOM-022 Support access | REQ-059 |
| DOM-023 Stored file | REQ-054, REQ-060, REQ-062 |
| Derived views: totals, reports, combined totals | REQ-026, REQ-046, REQ-065 |
| Export (a read of the above) | REQ-028 |

Requirements with no domain element because they constrain clients or operations: REQ-049, REQ-N01, REQ-N02, REQ-N03, REQ-N04, REQ-N08, REQ-N09, REQ-N12, REQ-N13 and REQ-N15; these pass to Architecture and the Technical Specification. Withdrawn requirements REQ-002 and REQ-018 have no element.

## Assumptions

- Shops think of credit as a running tab per customer, with a promised date per sale. From the founder's expectations (EVID-034), untested.
- Sellers will add goods lines after a rushed sale within a day. Untested.
- A 30-day default promise, 30-day dispute window, and the other periods decided here suit real shops. Agent judgment.
- A staff decision is an acceptable way to close a dispute the shop believes is wrong. Without it a customer could avoid reminders indefinitely by disputing.
- Deferring removal until a debt is settled is lawful (BR-32). Unverified; carried from version 1.
- The payment history indicator is lawful because it never leaves the shop. Agent inference, not legal advice.

## Open questions

1. Legal: recording a customer before consent; deferring removal; a per-customer reliability indicator; keeping receipt images sent by customers.
2. Should sellers, not only managers, be allowed to accept payment notices? The model allows any staff member, since recording a payment is already a seller's permission.
3. Should a declined dispute be visible to the customer with the shop's reason? The model says yes.
4. How is a shop recovered when its owner loses their Telegram account? An administrator procedure, not modelled.
5. Is rounding each line to whole UZS acceptable to shops that price by weight?

## Decisions / approvals

| Record | Subject | Status |
|---|---|---|
| DEC-006 / APR-006 | Version 1 business rules | Superseded in part; immutability and whole-entry reversal are kept |
| DEC-013 / APR-013 | Release 1 scope | Approved 2026-10-06 |
| DEC-014 | Rules decided in this version: dispute once per entry within 30 days, closed by reversal, decline, or withdrawal; one-time addition of goods lines; promised dates changeable with history; 30-day default promise; payment history calculation; limited-mode behavior; support access of at most 24 hours; 30-day shop deletion period | Pending end-of-sequence review |
