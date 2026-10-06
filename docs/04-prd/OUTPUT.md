# Product Requirements Document

Status: MVP scope approved by the founder on 2026-10-06 (DEC-005 / APR-005).
Upstream: Vision (DEC-002), Problem Discovery (DEC-003, provisional), Market Research (DEC-004).

**Standing caveat.** No shopkeeper has been interviewed and no competitor has been tested hands-on. Every requirement below traces to a problem hypothesis, not to a validated need. The stop conditions in Market Research still apply.

## Product scope

**Product:** a Telegram-based credit ledger for mahalla grocery shops in which the customer acknowledges each debt.

**MVP goal:** learn, with a small group of pilot shops, whether acknowledged entries and automatic Telegram reminders improve repayment (METRIC-001) and whether recording is fast enough to replace the notebook (METRIC-004).

In the MVP:

- Shop owner records credit sales and payments in Telegram.
- Customer is linked through Telegram, acknowledges or disputes entries, and can see their balance.
- Reminders go out through Telegram on the owner's terms.
- Owner sees what is owed in total and what is overdue.
- The product is free. There is no billing.

Not in the MVP, and why:

| Excluded | Reason |
|---|---|
| Paid tier and billing | Willingness to pay is untested (EVID-013); Telegram payment rules are unresolved (EVID-028) |
| SMS fallback | Per-message cost (EVID-021); need depends on how many customers lack Telegram, which is unmeasured |
| Multiple sellers per shop | Adds permissions and audit complexity; whether assistants record sales is an open question |
| Reports and analytics beyond totals | Bookkeeping, not repayment (vision principle 1) |
| Itemized goods, inventory, POS | Out of scope by vision |
| Cross-shop debtor information | Out of scope by vision; legal basis absent |
| Lending, interest, payment processing | Out of scope by vision (EVID-009) |
| Native mobile apps, Russian interface | Later; Telegram and Uzbek cover the pilot |

## Target users

| User | Description | Needs in the MVP |
|---|---|---|
| Shop owner (primary) | Runs a mahalla grocery shop, serves customers personally, decides who gets credit; uses Telegram on an ordinary Android phone | Record in seconds, see balances, be paid without confrontation |
| Customer (secondary) | Regular buyer who takes goods on credit; may or may not use Telegram | Know what they owe, have payments recognized, not be embarrassed |

A customer who never joins on Telegram must still be recordable. The product degrades to a one-sided ledger for that customer (vision principle 3).

## Features

| ID | Feature | Summary | Priority |
|---|---|---|---|
| FEAT-001 | Shop onboarding | Owner starts the bot, names the shop, and is ready to record in under two minutes | Must |
| FEAT-002 | Customer book | Add, find, and edit customers with minimal data | Must |
| FEAT-003 | Fast credit entry | Record a credit sale as one short chat message or a few taps | Must |
| FEAT-004 | Payment recording | Record full or partial repayment | Must |
| FEAT-005 | Customer linking and consent | Customer connects by link or QR code and consents to the record | Must |
| FEAT-006 | Acknowledgement and dispute | Linked customer confirms or disputes each entry | Must |
| FEAT-007 | Customer balance view | Customer sees balance and history for that shop | Must |
| FEAT-008 | Reminders | Owner-controlled Telegram reminders tied to due dates | Must |
| FEAT-009 | Outstanding overview | Total owed, per-customer balances, overdue list | Must |
| FEAT-010 | Corrections and history | Mistakes are fixed by reversing entries; nothing is silently changed | Must |
| FEAT-011 | Export and deletion | Owner exports the ledger; customer data can be removed on request | Should |
| FEAT-012 | Pilot measurement | Anonymous usage and repayment measures for the pilot | Must |
| FEAT-013 | SMS fallback | Reminders for customers without Telegram | Later |
| FEAT-014 | Multiple sellers | Assistants record under the owner's shop | Later |
| FEAT-015 | Paid tier and billing | Subscription for advanced features | Later |

## User stories

Owner:

1. As a shop owner, I want to type a name and an amount and be done, so that a queue never waits on my phone. (FEAT-003)
2. As a shop owner, I want to see what a customer already owes before I give more, so that I decide knowingly. (FEAT-009)
3. As a shop owner, I want the customer to confirm the amount, so that nobody argues about it later. (FEAT-006)
4. As a shop owner, I want reminders to go out on the day we agreed, in polite words I approved, so that I do not have to ask in person. (FEAT-008)
5. As a shop owner, I want to switch reminders off for a particular customer, so that I do not offend someone I trust. (FEAT-008)
6. As a shop owner, I want to fix a wrong entry without the customer thinking I altered the books. (FEAT-010)
7. As a shop owner, I want my ledger as a file, so that I am not locked in. (FEAT-011)

Customer:

8. As a customer, I want a message each time something is added to my debt, so that I know what I owe. (FEAT-006)
9. As a customer, I want to say that an entry is wrong, so that mistakes are caught early. (FEAT-006)
10. As a customer, I want to see my balance whenever I like, without asking the shopkeeper. (FEAT-007)
11. As a customer, I want my debt to be visible only to me and that shop. (FEAT-005)

## Functional requirements

Onboarding and customers:

| ID | Requirement | Feature |
|---|---|---|
| REQ-001 | An owner can create a shop by starting the bot and entering a shop name. No other field is mandatory. | FEAT-001 |
| REQ-002 | One Telegram account owns one shop in the MVP. | FEAT-001 |
| REQ-003 | An owner can add a customer with a display name only. A phone number is optional. | FEAT-002 |
| REQ-004 | An owner can find a customer by typing part of the name; matching ignores case and tolerates Latin and Cyrillic spelling of the same name. | FEAT-002 |
| REQ-005 | An owner can rename or archive a customer. A customer with a non-zero balance cannot be archived. | FEAT-002 |

Recording:

| ID | Requirement | Feature |
|---|---|---|
| REQ-006 | An owner can record a credit sale by sending one message containing a customer name and an amount, for example "Ali 45000". If the name is unknown, the bot offers to create the customer. If it is ambiguous, the bot asks which customer. | FEAT-003 |
| REQ-007 | Every credit entry stores the amount in UZS as a fixed number at the time of sale, the date and time, and an optional short note. The amount never changes with later prices. | FEAT-003 |
| REQ-008 | Every credit entry has a due date. The default is set once per shop (for example, the 5th of next month) and can be overridden per entry. | FEAT-003 |
| REQ-009 | An owner can record a full or partial payment against a customer; the balance is reduced by that amount, oldest debt first. | FEAT-004 |
| REQ-010 | After each entry or payment the bot shows the owner the customer's new balance. | FEAT-003, FEAT-004 |
| REQ-011 | An entry or payment is never edited or deleted. A mistake is corrected by a reversing entry that references the original, and both remain visible in the history. | FEAT-010 |
| REQ-012 | A reversal on a linked customer's account is sent to that customer like any other entry. | FEAT-010, FEAT-006 |

Customer side:

| ID | Requirement | Feature |
|---|---|---|
| REQ-013 | An owner can generate, for each customer, a personal link and QR code that connects that customer's Telegram account to their record in that shop. | FEAT-005 |
| REQ-014 | When connecting, the customer is shown what will be stored, by whom, and why, and must agree before the link is made. The agreement and its time are stored. | FEAT-005 |
| REQ-015 | A linked customer receives a message for each new entry, payment, and reversal, showing the amount and the new balance. | FEAT-006 |
| REQ-016 | A linked customer can respond to an entry with "confirm" or "dispute". A dispute requires a short reason. | FEAT-006 |
| REQ-017 | A disputed entry is flagged to the owner, stays in the balance, and is marked as disputed until the owner reverses it or the customer withdraws the dispute. | FEAT-006 |
| REQ-018 | An entry that receives no response remains valid and is shown as unconfirmed. Lack of response is never treated as confirmation. | FEAT-006 |
| REQ-019 | A linked customer can view their balance and full history for that shop at any time. | FEAT-007 |
| REQ-020 | A customer sees only their own record in a shop. An owner sees only their own shop. No user can see a customer's records in another shop. | FEAT-005, FEAT-007 |
| REQ-021 | A customer can disconnect. Notifications stop; the owner's ledger keeps the entries under the display name. | FEAT-005 |

Reminders and overview:

| ID | Requirement | Feature |
|---|---|---|
| REQ-022 | Reminders are off until the owner turns them on for the shop. The owner can turn them off for any individual customer. | FEAT-008 |
| REQ-023 | A reminder is sent to a linked customer on the due date if the balance is above zero, and at most once every seven days afterward while it remains overdue. | FEAT-008 |
| REQ-024 | Reminder wording comes from a small set of fixed polite templates chosen by the owner. A reminder states the shop, the balance, and nothing about other customers. | FEAT-008 |
| REQ-025 | The owner can send a reminder manually to one customer, limited to one per day per customer. | FEAT-008 |
| REQ-026 | The owner can see total outstanding credit, a list of customers by balance, and a list of overdue customers with days overdue. | FEAT-009 |
| REQ-027 | The owner can open any customer and see balance, history, and the confirmation status of each entry. | FEAT-009 |

Data and measurement:

| ID | Requirement | Feature |
|---|---|---|
| REQ-028 | The owner can export the full ledger as a spreadsheet file. | FEAT-011 |
| REQ-029 | On request, a customer's identifying data is removed; amounts remain under an anonymous label so the shop's totals stay correct. | FEAT-011 |
| REQ-030 | The system records, without customer identities, the measures needed for METRIC-001 to METRIC-004: entry timestamps, time from first keystroke to saved entry where measurable, confirmation outcomes, due dates, and payment dates. | FEAT-012 |

## Non-functional requirements

| ID | Requirement |
|---|---|
| REQ-N01 | Language: all owner and customer text is in Uzbek (Latin script). Input in Cyrillic is accepted. |
| REQ-N02 | Speed: recording a credit sale for an existing customer takes one message and one bot reply. In a usability test with pilot owners the median time is 10 seconds or less. The 10-second figure is a product target, not a measured notebook benchmark. |
| REQ-N03 | Low connectivity: the core flows in REQ-006, REQ-009 and REQ-010 work through plain chat messages, without loading a Mini App. |
| REQ-N04 | Data location: all personal data is stored on servers physically located in Uzbekistan (EVID-027). |
| REQ-N05 | Data minimization: the only personal data stored is a display name, an optional phone number, a Telegram identifier once linked, and the ledger entries. |
| REQ-N06 | Money: amounts are whole UZS, stored as integers. Balances are always derivable from the entry history. |
| REQ-N07 | Integrity: no interface, including administrative ones, can alter or delete a saved entry. Every change of state is recorded with time and actor. |
| REQ-N08 | Durability: a saved entry survives a server failure. Backups are taken daily and a restore is tested before the pilot. |
| REQ-N09 | Availability: the bot is expected to respond during shop hours (06:00 to 23:00 local time); planned maintenance happens outside them. |
| REQ-N10 | Abuse limits: reminder limits in REQ-023 and REQ-025 are enforced by the system and cannot be raised by the owner. |
| REQ-N11 | Security: an owner's ledger is reachable only from the owner's Telegram account; customer links cannot be guessed and can be regenerated. |

## Priorities

| Priority | Items | Rationale |
|---|---|---|
| Must (MVP) | FEAT-001 to FEAT-010, FEAT-012 | Minimum needed to test the two MVP questions |
| Should (MVP if time allows) | FEAT-011 | Trust and legal hygiene; export can be manual during the pilot |
| Later | FEAT-013, FEAT-014, FEAT-015 | Depend on what the pilot shows |

Build order within Must: recording and balances first (FEAT-001 to FEAT-004, FEAT-009, FEAT-010), because the product must be useful one-sided; then linking, acknowledgement and reminders (FEAT-005 to FEAT-008); measurement (FEAT-012) from the first day of the pilot.

## Constraints

- One founder working with AI agents, no dedicated budget (unconfirmed assumption carried from Idea Selection).
- Telegram is the only client in the MVP; the product inherits Telegram's bot limits and policies (EVID-028).
- Personal data must be hosted in Uzbekistan and the database may need registration before processing (EVID-027).
- No lending, interest, or money movement (EVID-009).
- Free during the MVP.
- Pilot scale: on the order of ten shops. The MVP is not required to serve more.

## Acceptance criteria

| Feature | Criterion |
|---|---|
| FEAT-001 | A new owner goes from first message to first recorded entry in under two minutes without help. |
| FEAT-003 | Sending "Ali 45000" for an existing customer creates one entry of 45,000 UZS with the default due date and returns the new balance in a single reply. An unknown name triggers an offer to create the customer; two matching names trigger a choice. |
| FEAT-004 | A payment of 20,000 against a balance of 45,000 leaves 25,000 and is shown in the history. A payment larger than the balance is rejected with an explanation. |
| FEAT-005 | Scanning a customer's QR code and agreeing links the account; declining links nothing and stores nothing about the Telegram account. The link for one customer cannot be used to view another. |
| FEAT-006 | A linked customer receives each entry within one minute and can confirm or dispute it. A disputed entry appears flagged in the owner's view. An unanswered entry shows as unconfirmed, never as confirmed. |
| FEAT-007 | A linked customer can open their balance and see every entry, payment, and reversal for that shop and nothing from any other shop. |
| FEAT-008 | With reminders on, a customer with an overdue balance receives one reminder on the due date and no more than one per seven days after. With reminders off for that customer, none is sent. A zero balance never triggers a reminder. |
| FEAT-009 | The total outstanding equals the sum of all customer balances, and each balance equals the sum of that customer's entries minus payments and reversals. |
| FEAT-010 | Reversing an entry restores the previous balance, leaves both records visible, and notifies a linked customer. No entry can be edited or deleted by any route. |
| FEAT-012 | For any pilot shop, the share of credit value repaid by its due date and the share of entries confirmed can be computed from stored data without customer identities. |

MVP exit criteria for the pilot, proposed by the agent: after eight weeks, most pilot shops still record weekly (METRIC-002); owners report that recording is not slower than the notebook (METRIC-004); and enough entries have passed their due date to compare repayment of confirmed and unconfirmed entries (METRIC-001, METRIC-003). These decide whether to continue, change, or stop.

## Requirement traceability

| Feature | Requirements | Problem | Metric | Evidence |
|---|---|---|---|---|
| FEAT-001 | REQ-001, REQ-002 | PROB-002 | METRIC-002 | EVID-020 |
| FEAT-002 | REQ-003, REQ-004, REQ-005 | PROB-002 | METRIC-004 | EVID-027 |
| FEAT-003 | REQ-006, REQ-007, REQ-008, REQ-010 | PROB-002 | METRIC-004 | EVID-017 |
| FEAT-004 | REQ-009, REQ-010 | PROB-002 | METRIC-001 | EVID-014 |
| FEAT-005 | REQ-013, REQ-014, REQ-020, REQ-021 | PROB-002 | METRIC-003 | EVID-027 |
| FEAT-006 | REQ-015, REQ-016, REQ-017, REQ-018 | PROB-001, PROB-002 | METRIC-003 | EVID-004, EVID-019 |
| FEAT-007 | REQ-019, REQ-020 | PROB-002 | METRIC-003 | EVID-020 |
| FEAT-008 | REQ-022, REQ-023, REQ-024, REQ-025 | PROB-001, PROB-003 | METRIC-001 | EVID-015, EVID-019 |
| FEAT-009 | REQ-026, REQ-027 | PROB-004 | METRIC-001 | EVID-016 |
| FEAT-010 | REQ-011, REQ-012 | PROB-002 | METRIC-003 | EVID-017 |
| FEAT-011 | REQ-028, REQ-029 | PROB-002 | METRIC-002 | EVID-027 |
| FEAT-012 | REQ-030 | PROB-001 | METRIC-001 | EVID-019 |

Non-functional requirements:

| Requirement | Serves | Basis |
|---|---|---|
| REQ-N01 | All features | Target users (Uzbek-speaking owners and customers) |
| REQ-N02, REQ-N03 | FEAT-003, FEAT-004 | METRIC-004; vision principle "faster than the notebook" |
| REQ-N04, REQ-N05 | FEAT-002, FEAT-005, FEAT-011 | EVID-027 |
| REQ-N06, REQ-N07 | FEAT-003, FEAT-004, FEAT-010 | PROB-002; EVID-017 |
| REQ-N08, REQ-N09 | All features | METRIC-002 |
| REQ-N10 | FEAT-008 | PROB-003; vision principle "protect the relationship" |
| REQ-N11 | FEAT-005, FEAT-007 | EVID-027 |

Every Must feature traces to at least one problem from Problem Discovery. PROB-001, the primary problem, is addressed directly only by FEAT-006, FEAT-008 and FEAT-012; the rest make the ledger usable enough for those three to matter.

## Evidence / assumptions

Evidence used: EVID-004, EVID-009, EVID-013, EVID-014, EVID-015, EVID-016, EVID-017, EVID-019, EVID-020, EVID-021, EVID-027 and EVID-028 (see `docs/evidence/`). No new evidence was gathered in this stage.

Assumptions built into the requirements:

- A one-message entry is faster than writing in a notebook. Untested.
- Owners will show customers a QR code and customers will scan it. Untested.
- Acknowledged debts are repaid more reliably. No evidence either way (EVID-019); the MVP exists to test this.
- A weekly reminder cap is polite enough for customers and frequent enough for owners. Agent judgment.
- The default due date model (one day per month per shop) matches how grocers think about payday credit. Agent judgment based on EVID-001 (credit repaid around payday).
- The consent step in REQ-014 satisfies the personal data law. Agent inference from EVID-027, not legal advice.

## Open questions

1. **Unlinked customers and consent.** REQ-003 lets an owner store a named person's debts before that person has agreed to anything. A paper notebook does the same, but automated processing may be treated differently under the personal data law (EVID-027). This needs a lawyer's answer before the pilot. A fallback is to store unlinked customers under a nickname the owner chooses.
2. Does the database need to be registered in the State Register before a ten-shop pilot?
3. Should the default due date be per shop, per customer, or a number of days after each sale?
4. Do owners need to record what was bought, or only how much? The MVP stores an optional note only.
5. Is the oldest-debt-first rule in REQ-009 what owners and customers expect?
6. Should a dispute pause reminders for that customer?

## Decisions / approvals

| Record | Subject | Status |
|---|---|---|
| DEC-004 / APR-004 | Positioning and pricing direction | Approved 2026-10-06 |
| DEC-005 | MVP scope as defined in this document: features FEAT-001 to FEAT-012, free, Telegram-only, Uzbek-only, pilot of about ten shops | Approved 2026-10-06 |
