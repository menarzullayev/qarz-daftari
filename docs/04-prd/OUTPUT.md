# Product Requirements Document

Version 2. Status: release 1 scope approved by the founder on 2026-10-06 (DEC-013 / APR-013), including four added features (FEAT-024 to FEAT-027) and the decision to release everything at once.
Version 1 (pilot MVP, DEC-005) is superseded and remains in version history.
Upstream: Vision (DEC-002), Problem Discovery (DEC-003, provisional), Market Research (DEC-004, pricing superseded by DEC-012).

**Standing caveats, unchanged by this revision.**

- No shopkeeper has been interviewed and no competitor has been tested hands-on. The scope below rests on the founder's own expectations (EVID-034), which are hypotheses.
- The two gates approved in DEC-010 still stand: interviews and a pDaftar test before the customer-facing work, and legal review before any real customer data.
- The agent recommended a small pilot first and records its disagreement with building the full product before validation. The founder decided otherwise; this document specifies what he decided.

**Downstream documents are stale.** Stages 05 to 10 were written for version 1 and must be revised before they are relied on.

## Product scope

**Product:** a credit ledger service for mahalla grocery shops, used through Telegram and the web. Shop staff record itemized credit sales and payments; customers are notified and can see and dispute their debt; reminders go out automatically; owners get reports; the service is sold by monthly subscription.

**Release 1 target:** a production-grade, multi-tenant service that can be sold to shops, not a pilot. "Production-grade" is defined by the non-functional requirements below.

What changed from version 1:

| Area | Version 1 | Version 2 |
|---|---|---|
| Customer confirmation | Customer confirms or disputes each entry | Customer is notified and may dispute; is never asked to confirm |
| Entry content | Amount and a short note | Itemized goods lines from a shop catalog, with unlisted goods learned automatically |
| Due date | One day of the month per shop | Date promised by the customer at each sale, with quick choices |
| Staff | One owner account | Owner, manager, and seller roles |
| Interface | Chat only | Chat, Telegram Mini App, and a web panel |
| Languages | Uzbek | Uzbek and Russian |
| Reminders | Telegram only | Telegram, with SMS as fallback |
| Credit control | None | Per-customer credit limit and in-shop payment history rating |
| Reports | Totals only | Owner reports |
| Commercial | Free pilot | 30-day trial, then 100,000 UZS a month; manual card-transfer payments approved by an administrator |
| Platform administration | Operator by hand | Administration panel |
| Scale | About ten shops | Thousands of shops |

Still out of scope, by the approved vision:

| Excluded | Reason |
|---|---|
| Lending, interest, penalties, holding or moving customers' money | Licensed activity (EVID-009) |
| Sharing customer information between shops | No legal basis; vision principle 6 |
| Stock levels, purchasing, cash register, tax reporting | Point-of-sale territory (EVID-005); the catalog here exists only to itemize credit sales |
| Native mobile apps | Telegram Mini App and web cover the need |
| Markets outside Uzbekistan | Vision |

## Target users

| User | Description | Main needs |
|---|---|---|
| Shop owner | Runs the shop, decides credit policy, pays the subscription | Know what is owed, be repaid, control staff, see reports |
| Manager | Trusted family member or senior employee | Everything except ownership and subscription |
| Seller | Family member or hired seller at the counter | Record sales and payments fast; no access to settings or reversals |
| Customer | Buys on credit; may or may not use Telegram | See exactly what is owed and for what; be reminded politely; object to mistakes |
| Platform administrator | The founder, later support staff | Approve payments, manage trials and subscriptions, support shops |

A customer without Telegram must still be recordable; for them the product is a one-sided ledger with optional SMS reminders.

## Features

| ID | Feature | Summary | Release 1 |
|---|---|---|---|
| FEAT-001 | Shop onboarding | Create a shop, choose language, start the trial | Yes |
| FEAT-002 | Customer book | Customers with name, optional phone, status | Yes |
| FEAT-003 | Credit entry | Fast amount-only entry in chat; itemized entry in the Mini App; promised repayment date | Yes |
| FEAT-004 | Payment recording | Full or partial repayment | Yes |
| FEAT-005 | Customer linking and consent | Customer starts the bot at the counter by QR code or personal link and consents | Yes |
| FEAT-006 | Notification and dispute | Customer is told of every entry and can dispute it; no confirmation step | Yes (redefined) |
| FEAT-007 | Customer debt view | Customer sees balance, entries, and goods per shop in a Mini App page | Yes |
| FEAT-008 | Reminders | Automatic and manual reminders, owner-controlled | Yes |
| FEAT-009 | Outstanding overview | Totals, balances, overdue list, search and filters | Yes |
| FEAT-010 | Corrections and history | Reversal entries; nothing silently changed | Yes |
| FEAT-011 | Export and deletion | Ledger export; removal of customer data on request | Yes |
| FEAT-012 | Measurement | Identity-free product metrics | Yes |
| FEAT-013 | SMS fallback | Reminders by SMS to customers without Telegram | Yes, behind a switch |
| FEAT-014 | Staff and roles | Owner, manager, seller; invitations; per-entry authorship | Yes |
| FEAT-015 | Subscription and payments | Trial, monthly subscription, card-transfer receipts approved by an administrator; online payment integration present but switched off | Yes |
| FEAT-016 | Goods catalog and itemized lines | Shop catalog with prices; unlisted goods learned on first use | Yes |
| FEAT-017 | Mini App workspace | Staff workspace inside Telegram | Yes |
| FEAT-018 | Credit limit and payment history rating | Per-customer limit and an in-shop reliability indicator | Yes |
| FEAT-019 | Owner reports | Repayment, overdue aging, top debtors, staff activity | Yes |
| FEAT-020 | Web panel | Desktop back office for owner and manager | Yes |
| FEAT-021 | Platform administration | Shops, subscriptions, payment approvals, switches, support tools | Yes |
| FEAT-022 | Russian interface | Second interface language | Yes |
| FEAT-023 | Activity log | Who did what and when, visible to the owner | Yes |
| FEAT-024 | Customer payment notice | A customer tells the shop they have paid, optionally with a receipt; staff accept or decline | Yes |
| FEAT-025 | Ledger import | Existing customers and opening balances loaded from a spreadsheet | Yes |
| FEAT-026 | Several shops per person | One person can own or work in more than one shop and switch between them | Yes |
| FEAT-027 | Promised date change request | A customer asks to move a promised date; staff accept or decline | Yes |

The founder asked for more functions in release 1 and, offered six candidates, chose four (FEAT-024 to FEAT-027 inclusive). Scheduled report delivery and a referral scheme were not chosen and are not in release 1.

## User stories

Owner and manager:

1. As an owner, I want to invite my seller so that sales recorded in my absence carry their name. (FEAT-014)
2. As an owner, I want sellers unable to reverse entries or change settings. (FEAT-014)
3. As an owner, I want to set a credit limit per customer and be warned when a sale would exceed it. (FEAT-018)
4. As an owner, I want to see how reliably each customer has paid in my shop before giving more. (FEAT-018)
5. As an owner, I want a monthly picture of how much was lent, repaid on time, and is overdue. (FEAT-019)
6. As an owner, I want to work on a computer when reviewing the month. (FEAT-020)
7. As an owner, I want to try the service free and then pay by card transfer. (FEAT-015)

Seller:

8. As a seller, I want to record a sale in seconds during a queue and add the goods afterward. (FEAT-003)
9. As a seller, I want to pick goods from the shop's list with prices filled in, and type a new one when it is missing. (FEAT-016)
10. As a seller, I want to note the date the customer promised to pay with one tap. (FEAT-003)

Customer:

11. As a customer, I want to start the bot at the counter and from then on see what I owe and for which goods. (FEAT-005, FEAT-007)
12. As a customer, I do not want to be asked to press anything for each purchase. (FEAT-006)
13. As a customer, I want to object when an entry is wrong. (FEAT-006)
14. As a customer, I want my debt visible only to me and that shop. (FEAT-005)

Administrator:

15. As an administrator, I want payment receipts to reach me and a review group, and to approve or reject each with one action. (FEAT-015, FEAT-021)
16. As an administrator, I want to switch the free trial, SMS, and online payments on or off without a release. (FEAT-021)

## Functional requirements

Identifiers from version 1 are kept where the meaning survives. "Changed" marks a requirement whose content differs from version 1; "Withdrawn" entries remain listed so earlier references resolve.

Shop, staff, and customers:

| ID | Requirement | Feature | Status |
|---|---|---|---|
| REQ-001 | A person can create a shop by starting the bot, choosing a language, and entering a shop name. | FEAT-001 | Changed |
| REQ-002 | Withdrawn. Version 1 limited a shop to one Telegram account; replaced by the staff requirements (REQ-031 to REQ-035 inclusive) | FEAT-014 | Withdrawn |
| REQ-003 | Staff can add a customer with a display name only. A phone number is optional. | FEAT-002 | Kept |
| REQ-004 | Staff can find a customer by part of the name or phone; matching ignores case and tolerates Latin and Cyrillic spelling. | FEAT-002 | Changed |
| REQ-005 | An owner or manager can rename or archive a customer. A customer with a non-zero balance cannot be archived. | FEAT-002 | Changed |
| REQ-031 | A shop has exactly one owner and any number of managers and sellers. | FEAT-014 | New |
| REQ-032 | An owner invites a manager or seller by a personal link; the invited person joins with their own Telegram account. | FEAT-014 | New |
| REQ-033 | Permissions by role. Seller: add customers, record credit sales and payments, view customers and balances. Manager: everything a seller can do, plus reversals, catalog, credit limits, reminders, reports, exports. Owner: everything, plus staff, subscription, shop settings, and shop deletion. | FEAT-014 | New |
| REQ-034 | An owner can suspend or remove a staff member at once; their past entries remain attributed to them. | FEAT-014 | New |
| REQ-035 | Every entry, payment, reversal, and settings change records which staff member made it. | FEAT-014, FEAT-023 | New |
| REQ-036 | An owner can transfer ownership to a manager; the transfer requires confirmation by both. | FEAT-014 | New |

Recording:

| ID | Requirement | Feature | Status |
|---|---|---|---|
| REQ-006 | Staff can record a credit sale by one chat message with a customer name and an amount, for example "Ali 45000". An unknown name offers to create the customer; an ambiguous one asks which. | FEAT-003 | Kept |
| REQ-007 | Every credit entry stores its total in UZS as a fixed number at the time of sale, the date and time, and the author. The total never changes with later prices. | FEAT-003 | Changed |
| REQ-008 | Every credit entry has a promised repayment date. After saving, the seller is offered one-tap choices (tomorrow, end of week, a set payday, a chosen date); if none is chosen, the shop's default applies. | FEAT-003 | Changed |
| REQ-037 | A credit entry may carry goods lines, each with a name, quantity, unit price, and line total. When lines are present, the entry total equals the sum of the lines. | FEAT-016 | New |
| REQ-038 | An amount-only entry can have goods lines added later by its author or a manager, until the end of the next day and only if the lines sum to the recorded total. Afterward the entry is fixed. | FEAT-003, FEAT-016 | New |
| REQ-039 | A shop has a catalog of goods with names, units, and current prices, maintained by the owner or manager. | FEAT-016 | New |
| REQ-040 | When entering a line, the seller chooses from the catalog with the price filled in and editable for that line. A name not in the catalog can be typed with a price; it is saved to the catalog as a learned item for next time. | FEAT-016 | New |
| REQ-041 | Changing a catalog price never changes any saved entry. | FEAT-016 | New |
| REQ-009 | Staff can record a full or partial payment; the balance is reduced, oldest debt first. | FEAT-004 | Kept |
| REQ-010 | After each entry or payment the author sees the customer's new balance. | FEAT-003, FEAT-004 | Kept |
| REQ-011 | A saved entry or payment is never edited or deleted, apart from the one-time addition of goods lines (REQ-038); a mistake is corrected by a reversing entry that references the original; both remain visible. | FEAT-010 | Changed |
| REQ-012 | A reversal on a linked customer's account is sent to that customer like any other entry. | FEAT-010, FEAT-006 | Kept |

Customer side:

| ID | Requirement | Feature | Status |
|---|---|---|---|
| REQ-013 | A shop has a counter QR code and link; staff can also produce a personal link for one customer. A customer who starts the bot from the counter code is attached to their record by staff; a personal link attaches directly. | FEAT-005 | Changed |
| REQ-014 | When connecting, the customer is shown what will be stored, by whom, and why, and must agree before the link is made. The agreement, its text version, and its time are stored. | FEAT-005 | Kept |
| REQ-015 | A linked customer receives a message for each new entry, payment, and reversal, showing the amount, the goods if recorded, the promised date, and the new balance. | FEAT-006 | Changed |
| REQ-016 | A linked customer can dispute an entry, giving a short reason. The customer is never asked to confirm an entry. | FEAT-006 | Changed |
| REQ-017 | A disputed entry is flagged to the owner and managers, stays in the balance, and is marked disputed until staff reverse it or the customer withdraws the dispute. | FEAT-006 | Kept |
| REQ-018 | Withdrawn. Version 1 defined a confirmed and unconfirmed status for entries; entries now have no confirmation status. | FEAT-006 | Withdrawn |
| REQ-019 | A linked customer can view balance, entries, goods, and promised dates for each shop they are linked to. | FEAT-007 | Changed |
| REQ-020 | A customer sees only their own record in a shop. Staff see only their own shop. No user can see a customer's records in another shop. | FEAT-005, FEAT-007 | Kept |
| REQ-021 | A customer can disconnect. Notifications stop; the shop's ledger keeps the entries under the display name. | FEAT-005 | Kept |

Reminders:

| ID | Requirement | Feature | Status |
|---|---|---|---|
| REQ-022 | Reminders are off until the owner or manager turns them on for the shop, and can be turned off for any customer. | FEAT-008 | Kept |
| REQ-023 | A reminder is sent on the promised date if the debt is unpaid, and at most once every seven days afterward while it remains overdue. | FEAT-008 | Changed |
| REQ-024 | Reminder wording comes from fixed polite templates in the customer's language, chosen by the shop. A reminder states the shop and the amount and nothing about other customers. | FEAT-008 | Changed |
| REQ-025 | A manager or owner can send a reminder manually to one customer, limited to one per day per customer. | FEAT-008 | Changed |
| REQ-042 | The shop chooses the hour reminders are sent, between 08:00 and 20:00. | FEAT-008 | New |
| REQ-043 | When SMS is switched on for the platform and the shop, a reminder to a customer with a phone number and no reachable Telegram link is sent by SMS, within a monthly quota per shop set by the administrator. | FEAT-013 | New |

Credit control, overview, reports:

| ID | Requirement | Feature | Status |
|---|---|---|---|
| REQ-026 | Staff can see total outstanding credit, customers by balance, and overdue customers with days overdue, with search and filters. | FEAT-009 | Changed |
| REQ-027 | Staff can open any customer and see balance, history with goods, promised dates, and disputes. | FEAT-009 | Changed |
| REQ-044 | An owner or manager can set a credit limit per customer and a shop default. A sale that would exceed the limit warns the seller; whether sellers may proceed is a shop setting. | FEAT-018 | New |
| REQ-045 | Each customer shows an in-shop payment history indicator derived only from that shop's records, for example the share of debt repaid by the promised date and the longest delay. It is visible to staff only and is never shared outside the shop. | FEAT-018 | New |
| REQ-046 | An owner or manager can view reports for a chosen period: credit given, repaid, repaid by the promised date, overdue by age band, largest debtors, disputes, and entries per staff member. | FEAT-019 | New |
| REQ-047 | An owner can view the activity log of the shop, filtered by staff member, customer, and type of action. | FEAT-023 | New |

Data rights and measurement:

| ID | Requirement | Feature | Status |
|---|---|---|---|
| REQ-028 | An owner or manager can export the ledger, customers, and reports as spreadsheet files. | FEAT-011 | Changed |
| REQ-029 | On request, a customer's identifying data is removed; amounts remain under an anonymous label so totals stay correct. | FEAT-011 | Kept |
| REQ-030 | The system records identity-free measures of usage, repayment, disputes, and entry speed. | FEAT-012 | Changed |
| REQ-048 | An owner can delete the shop. Customer links end, staff lose access, and data is erased after a stated waiting period during which the owner can export or cancel. | FEAT-011 | New |

Clients:

| ID | Requirement | Feature | Status |
|---|---|---|---|
| REQ-049 | The Mini App gives staff: itemized entry, customer search and detail, overview, catalog, credit limits, reminders, and, by role, reports and settings. | FEAT-017 | New |
| REQ-050 | The web panel gives owners and managers the same data as the Mini App on a desktop layout, plus exports, staff management, activity log, and subscription. Sign-in is by Telegram account; there are no separate passwords. | FEAT-020 | New |
| REQ-051 | Every interface is available in Uzbek (Latin) and Russian. Each user chooses a language; messages to a customer use the customer's choice. | FEAT-022 | New |

Subscription and administration:

| ID | Requirement | Feature | Status |
|---|---|---|---|
| REQ-052 | A new shop starts a free trial of 30 days when the trial switch is on, which is the default. The administrator can turn the trial off for new shops and can extend or end it for any shop. | FEAT-015 | New |
| REQ-053 | The subscription price is one amount per shop per month, set by the administrator; the initial value is 100,000 UZS. | FEAT-015 | New |
| REQ-054 | To pay, the owner is shown a card number and the amount, pays by transfer outside the system, and sends the receipt image or file through the bot. | FEAT-015 | New |
| REQ-055 | Each receipt is delivered to the administrator and to a designated review group. An administrator approves or rejects it with a reason. Approval extends the subscription by the paid period; the owner is told the outcome either way. | FEAT-015, FEAT-021 | New |
| REQ-056 | Online payment through local payment systems is implemented behind a platform switch that is off by default. | FEAT-015 | New |
| REQ-057 | Owners are warned before a trial or subscription ends. When it ends, the shop becomes limited: staff can still view, export, and record payments, customers can still see their debts, and new credit entries are blocked until payment is approved. | FEAT-015 | New |
| REQ-058 | Administrators have a panel to list and search shops, see subscription state and payment history, approve receipts, change switches and prices, suspend a shop, and act on support requests. Every administrator action is logged. | FEAT-021 | New |
| REQ-059 | An administrator cannot read a shop's customers or entries except through an explicit support access that is logged and visible to the shop owner. | FEAT-021 | New |

Features added on approval:

| ID | Requirement | Feature | Status |
|---|---|---|---|
| REQ-060 | A linked customer can send the shop a payment notice stating an amount, optionally with a receipt image. A notice never changes the balance by itself. | FEAT-024 | New |
| REQ-061 | Staff accept a payment notice, which records a payment attributed to the accepting staff member and linked to the notice, or decline it with a reason. The customer is told the outcome. | FEAT-024 | New |
| REQ-062 | An owner or manager can import customers with opening balances and promised dates from a spreadsheet in a published template. The import is validated and previewed before anything is saved, and rejected rows are listed with reasons. | FEAT-025 | New |
| REQ-063 | Imported balances are stored as opening-balance entries marked as imported, with author and time. A whole import can be undone within 24 hours, which reverses every entry it created. | FEAT-025 | New |
| REQ-064 | One person can be owner or staff in several shops. Every client lets them choose the active shop, and chat entry applies to the active shop, which is always shown in the reply. | FEAT-026 | New |
| REQ-065 | Each shop has its own subscription, staff, catalog, and customers. An owner can see combined totals across the shops they own; customer records are never merged or matched across shops, even under one owner. | FEAT-026 | New |
| REQ-066 | A linked customer can ask to move the promised date of a credit entry, with an optional reason. One request can be open per entry. | FEAT-027 | New |
| REQ-067 | An owner or manager accepts or declines a date change request. On acceptance the new date applies to reminders and overdue status, and the original date and the change remain in the history. | FEAT-027 | New |

## Non-functional requirements

| ID | Requirement | Status |
|---|---|---|
| REQ-N01 | Language: Uzbek (Latin) and Russian throughout; Cyrillic input is accepted everywhere. | Changed |
| REQ-N02 | Speed: an amount-only credit sale for an existing customer takes one message and one reply. An itemized sale of five catalog goods can be entered in the Mini App in under 45 seconds by a practiced seller. Both are product targets to be measured, not benchmarks. | Changed |
| REQ-N03 | Low connectivity: recording an amount-only sale and a payment works through plain chat messages without loading the Mini App. | Kept |
| REQ-N04 | Data location: all personal data is stored on servers physically located in Uzbekistan (EVID-027). | Kept |
| REQ-N05 | Data minimization: personal data is limited to display name, optional phone, Telegram identifier, language, and ledger content. Payment receipts are kept only as long as needed for accounting and disputes. | Changed |
| REQ-N06 | Money: amounts are whole UZS stored as integers; quantities may be fractional to three decimals; line totals are rounded to whole UZS and entry totals equal the sum of line totals. Balances are derivable from history. | Changed |
| REQ-N07 | Integrity: no interface, including administrative ones, can alter or delete a saved entry beyond the one-time addition of goods lines (REQ-038); every change of state records time and actor. | Changed |
| REQ-N08 | Durability: a saved entry survives the loss of a server. At most 5 minutes of entries may be lost in a disaster. | Changed |
| REQ-N09 | Availability: at least 99.5% during 06:00 to 23:00 local time, measured monthly; service restored within 1 hour of a server loss. | Changed |
| REQ-N10 | Abuse limits: reminder limits are enforced by the system and cannot be raised by a shop. | Kept |
| REQ-N11 | Security: access is by Telegram identity and shop role, checked on every request in every client; invitation and customer links cannot be guessed and can be revoked. | Changed |
| REQ-N12 | Tenant isolation: one shop's data cannot be read or changed from another shop under any role, and this is enforced below the application code as well as in it. | New |
| REQ-N13 | Scale: the service supports 5,000 active shops, 500,000 customer accounts, and a sustained 50 recorded entries a second without breaching the speed targets. These figures are agent-chosen design capacities, not forecasts. | New |
| REQ-N14 | Operability: switches for trial, SMS, online payment, and price take effect without a release. | New |
| REQ-N15 | Usability: staff interfaces work on a low-end Android phone and adapt from phone to desktop widths. | New |

## Priorities

Everything marked "Yes" under Features is in release 1 by founder decision. Within release 1 the build order is set by dependency and by where a stop decision is cheapest:

| Order | Group | Features |
|---|---|---|
| 1 | Core ledger for one shop with roles | FEAT-001, FEAT-002, FEAT-003, FEAT-004, FEAT-009, FEAT-010, FEAT-014, FEAT-023 |
| 2 | Catalog and Mini App workspace | FEAT-016, FEAT-017 |
| 3 | Customer side | FEAT-005, FEAT-006, FEAT-007 |
| 4 | Reminders and credit control | FEAT-008, FEAT-018, FEAT-013 |
| 5 | Reports, web panel, second language | FEAT-019, FEAT-020, FEAT-022, FEAT-011 |
| 6 | Subscription and administration | FEAT-015, FEAT-021, FEAT-012 |
| With group 1 | Several shops per person; import | FEAT-026, FEAT-025 |
| With group 3 | Customer payment notice; date change request | FEAT-024, FEAT-027 |

The agent advised putting the product in front of a few shops after group 3. On 2026-10-06 the founder decided instead that all of release 1 is built before any shop uses it. The consequence is that the first feedback from real use arrives only after the whole build.

## Constraints

- One founder working full time with AI agents; no dedicated budget.
- No registered business entity exists yet. Online payment systems and SMS providers that require a contract with a registered entity cannot be used until one exists, so both ship switched off. Subscription payments are collected by transfer to a personal card. The founder states that this is lawful and has decided to proceed (EVID-035); the agent has not verified it and no adviser has been consulted, so it is carried as an unverified assumption and a risk, not as an established fact.
- Selling a subscription through a Telegram bot outside Telegram's own payment mechanism may conflict with Telegram's rules for digital services (EVID-028).
- Personal data must be hosted in Uzbekistan and the database may need registration before processing (EVID-027).
- No lending, interest, or money movement on behalf of customers (EVID-009).
- Telegram remains the primary client and a platform dependency.

## Acceptance criteria

| Feature | Criterion |
|---|---|
| FEAT-003 | "Ali 45000" from a seller creates one entry of 45,000 UZS attributed to that seller and returns the balance and one-tap date choices in a single reply. |
| FEAT-006 | A linked customer receives each entry within one minute, with no confirmation prompt, and can dispute it with a reason; the dispute appears flagged to owner and managers. |
| FEAT-014 | A seller cannot reverse an entry, change the catalog, see reports, or open settings by any client or crafted request. A removed seller loses access at once and their entries still show their name. |
| FEAT-015 | A new shop has 30 trial days when the switch is on. A receipt sent through the bot reaches the administrator and the review group; approval extends the subscription and notifies the owner; rejection notifies the owner with the reason. After expiry, new credit entries are refused while payments, viewing, and export still work. |
| FEAT-016 | Choosing three catalog goods and one typed new good produces four lines whose totals sum to the entry total; the new good appears in the catalog as learned. Changing a catalog price afterward leaves the saved entry unchanged. |
| FEAT-018 | A sale that would exceed the customer's limit shows a warning with the current balance and limit; with the shop setting "sellers may not exceed", a seller's attempt is refused and a manager's is allowed. |
| FEAT-019 | For a chosen month, the report's total of credit given equals the sum of that month's credit entries, and its repaid-by-promised-date share can be recomputed from the exported ledger. |
| FEAT-020 | An owner signs in to the web panel with their Telegram account and sees the same balances as in the Mini App; a person without a role in any shop sees nothing. |
| FEAT-021 | Turning the trial switch off stops trials for shops created afterward and changes nothing for existing ones. An administrator's attempt to open a shop's customers without support access is refused and logged. |
| FEAT-022 | Every screen and message exists in both languages; a customer who chose Russian receives reminders in Russian from a shop that works in Uzbek. |
| Isolation | For every request type, an attempt by a member of one shop to read or change another shop's data is refused. |

Acceptance criteria for FEAT-001, FEAT-002, FEAT-004, FEAT-005, FEAT-007 to FEAT-012 follow version 1 with the changes noted in the requirement tables.

## Requirement traceability

| Feature | Requirements | Problem | Metric | Evidence |
|---|---|---|---|---|
| FEAT-001 | REQ-001, REQ-052 | PROB-002 | METRIC-002 | EVID-020 |
| FEAT-002 | REQ-003, REQ-004, REQ-005 | PROB-002 | METRIC-004 | EVID-027 |
| FEAT-003 | REQ-006, REQ-007, REQ-008, REQ-010, REQ-038 | PROB-002 | METRIC-004 | EVID-017, EVID-034 |
| FEAT-004 | REQ-009, REQ-010 | PROB-002 | METRIC-001 | EVID-014 |
| FEAT-005 | REQ-013, REQ-014, REQ-020, REQ-021 | PROB-002 | METRIC-003 | EVID-027, EVID-034 |
| FEAT-006 | REQ-015, REQ-016, REQ-017, REQ-012 | PROB-001, PROB-002 | METRIC-003 | EVID-034 |
| FEAT-007 | REQ-019, REQ-020 | PROB-002 | METRIC-003 | EVID-020 |
| FEAT-008 | REQ-022, REQ-023, REQ-024, REQ-025, REQ-042 | PROB-001, PROB-003 | METRIC-001 | EVID-015, EVID-019 |
| FEAT-009 | REQ-026, REQ-027 | PROB-004 | METRIC-001 | EVID-016, EVID-034 |
| FEAT-010 | REQ-011, REQ-012 | PROB-002 | METRIC-003 | EVID-017 |
| FEAT-011 | REQ-028, REQ-029, REQ-048 | PROB-002 | METRIC-002 | EVID-027 |
| FEAT-012 | REQ-030 | PROB-001 | METRIC-001 | EVID-019 |
| FEAT-013 | REQ-043 | PROB-001, PROB-003 | METRIC-001 | EVID-021, EVID-034 |
| FEAT-014 | REQ-031, REQ-032, REQ-033, REQ-034, REQ-035, REQ-036 | PROB-002 | METRIC-002 | EVID-034 |
| FEAT-015 | REQ-052, REQ-053, REQ-054, REQ-055, REQ-056, REQ-057 | PROB-001 | METRIC-005 | EVID-013, EVID-021, EVID-028, EVID-034 |
| FEAT-016 | REQ-037, REQ-039, REQ-040, REQ-041 | PROB-002 | METRIC-004 | EVID-017, EVID-034 |
| FEAT-017 | REQ-049 | PROB-004 | METRIC-004 | EVID-004, EVID-034 |
| FEAT-018 | REQ-044, REQ-045 | PROB-001, PROB-004 | METRIC-001 | EVID-014, EVID-034 |
| FEAT-019 | REQ-046 | PROB-004 | METRIC-001 | EVID-034 |
| FEAT-020 | REQ-050 | PROB-004 | METRIC-002 | EVID-034 |
| FEAT-021 | REQ-055, REQ-058, REQ-059 | PROB-001 | METRIC-005 | EVID-027 |
| FEAT-022 | REQ-051 | PROB-002 | METRIC-002 | EVID-034 |
| FEAT-023 | REQ-035, REQ-047 | PROB-002 | METRIC-002 | EVID-034 |
| FEAT-024 | REQ-060, REQ-061 | PROB-001, PROB-003 | METRIC-001 | EVID-034 |
| FEAT-025 | REQ-062, REQ-063 | PROB-004 | METRIC-002 | EVID-034 |
| FEAT-026 | REQ-064, REQ-065 | PROB-004 | METRIC-002 | EVID-034 |
| FEAT-027 | REQ-066, REQ-067 | PROB-001, PROB-003 | METRIC-001 | EVID-034 |

Non-functional requirements:

| Requirement | Serves | Basis |
|---|---|---|
| REQ-N01 | All features | REQ-051; founder decision |
| REQ-N02, REQ-N03 | FEAT-003, FEAT-004, FEAT-016 | METRIC-004; EVID-034 on recording speed |
| REQ-N04, REQ-N05 | FEAT-002, FEAT-005, FEAT-011, FEAT-015 | EVID-027 |
| REQ-N06, REQ-N07 | FEAT-003, FEAT-004, FEAT-010, FEAT-016 | PROB-002; EVID-017 |
| REQ-N08, REQ-N09, REQ-N13 | All features | METRIC-002; founder decision on production grade |
| REQ-N10 | FEAT-008, FEAT-013 | PROB-003 |
| REQ-N11, REQ-N12 | FEAT-005, FEAT-007, FEAT-014, FEAT-020, FEAT-021 | EVID-027 |
| REQ-N14 | FEAT-015, FEAT-013, FEAT-021 | Founder decision on switches |
| REQ-N15 | FEAT-017, FEAT-020 | Founder decision on adaptive interfaces |

Most new features trace to EVID-034, the founder's own expectations. That is a weak basis: one assumption-type record supports the larger part of release 1.

## Evidence / assumptions

Evidence used: EVID-004, EVID-009, EVID-013, EVID-014, EVID-015, EVID-016, EVID-017, EVID-019, EVID-020, EVID-021, EVID-027, EVID-028 and EVID-034 (see `docs/evidence/`). No new external evidence was gathered for this revision.

Assumptions built into the requirements:

- Shops want goods itemized in a credit record and will keep a catalog current. From EVID-034 only.
- Customers want visibility without a confirmation step. From EVID-034 only; it removes the differentiation that version 1 and DEC-004 rested on.
- Shops will pay 100,000 UZS a month while the incumbent offers a free tier and an entry tier of about 23,500 UZS (EVID-021). From EVID-034 only.
- Owners will pay by card transfer and send receipts. Untested.
- A limited mode after expiry, which never hides a shop's own data, is the right balance between revenue and trust. Agent decision.
- The design capacities in REQ-N13 are adequate. Agent decision.
- The reliability indicator in REQ-045 is lawful because it never leaves the shop. Agent inference, not legal advice.

## Open questions

1. **Accepting subscription payments on a personal card without a registered entity.** Decided by the founder on 2026-10-06: he states it is lawful and will proceed (EVID-035). Not verified; how such income is taxed has not been looked at.
2. **Telegram's payment rules.** Does collecting a subscription through the bot by card transfer breach the Stars requirement (EVID-028), and what is the consequence if it does?
3. Legal questions carried from version 1: recording a customer before consent; delaying removal while a balance is owed; notifications through Telegram's servers abroad; the consent text. Added by this revision: retention of payment receipts, which contain the payer's card details, and the lawfulness of SMS to customers who have not consented.
4. Which SMS provider, at what price, and does it require a registered entity?
5. Who sits in the receipt review group, and what stops a forged receipt from being approved?
6. Resolved on 2026-10-06: four candidate functions joined release 1 (FEAT-024 to FEAT-027 inclusive).
7. Does the web panel need anything the Mini App cannot do, or is it the same screens on a larger display?

## Decisions / approvals

| Record | Subject | Status |
|---|---|---|
| DEC-005 / APR-005 | Version 1 MVP scope | Superseded by DEC-012 |
| DEC-012 / APR-012 | Change of direction to a full production-grade product | Approved 2026-10-06 |
| DEC-013 / APR-013 | Release 1 scope: features FEAT-001 to FEAT-027, released all at once; roles, itemized entries, notification with dispute, two languages, subscription with manual payment approval on a personal card, and the non-functional requirements including scale and recovery targets | Approved 2026-10-06 |
