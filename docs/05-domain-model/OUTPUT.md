# Domain Model

Version 2. Status: rewritten for PRD version 2 (DEC-013 / APR-013); approved by the founder on 2026-10-06 (DEC-014 / APR-014) with no changes to the rules the agent decided. Prepared 2026-10-06.
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
| DOM-018 | Subscription | A shop's commercial state: trial, paid-through date, limited, or suspended; with the free plan switched on, a shop without a period that the plan holds is free, which is worked out and never stored (BR-33) |
| DOM-019 | Subscription receipt | An owner's proof of a card transfer, with an administrator's decision |
| DOM-020 | Platform setting | A switch or value the administrator controls: trial, SMS, online payment, price, quotas |
| DOM-021 | Activity record | Who did what, when, to which subject, within a shop or on the platform |
| DOM-022 | Support access | A logged, time-limited permission for an administrator to see one shop's data |
| DOM-023 | Stored file | A receipt image or import file held by the service |

"Customer" always means the record inside one shop. One user linked to customer records in two shops is known to the system as one Telegram account, but no shop can see or query that fact, and the model offers no operation that matches customers across shops, including shops with the same owner (REQ-020, REQ-065).

## Value objects

| Value object | Definition |
|---|---|
| Money | An amount with its currency: whole UZS, or, in a shop that works in dollars, whole US cents. Greater than zero for any entry or line total (REQ-N06). Never a fraction of the smallest unit, never converted (BR-36) |
| Currency | UZS or USD. UZS wherever none is named, so every record made before dollars existed is so'm (BR-36) |
| Quantity | A positive number with up to three decimals, with a unit such as piece, kilogram, or litre |
| Balance | Of one currency: whole UZS or whole US cents, zero or greater, always derived from the entries of that currency. A customer has one per currency and no total of them (BR-37) |
| Display name | The name staff typed, plus a normalized form for matching (lower-cased, Cyrillic transliterated) |
| Phone number | Optional, international format |
| Telegram identity | The account identifier of a user |
| Language | Uzbek or Russian (REQ-051) |
| Role | Owner, manager, or seller (REQ-033) |
| Promised date | A calendar date in Tashkent time |
| Credit limit | Per customer, with a shop default (REQ-044). Whole UZS for the so'm debt; a separate one in whole US cents for the dollar debt (BR-40) |
| Payment history indicator | Derived per customer from that shop's records only: share of due credit repaid by its promised date, and longest delay in days (REQ-045). Shown to the shop's staff and, on their own page, to the customer it is about (changed by the founder on 2026-10-08, DEC-066) |
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
| INV-19 | Amounts of different currencies are never added, compared or netted, and no figure shown or stored is a sum of them. There is no exchange rate anywhere. | Expansion decision 8 (2026-10-09) |
| INV-20 | A reversal is in the currency of the entry it reverses. | INV-6; decision 8 |

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
| BR-9 | The payment history indicator counts only credit whose promised date has passed: on-time share is the value covered on or before the promised date, by BR-3 with payment times, divided by the value due. Customers with nothing yet due show no indicator. The customer sees the same figures on their own page (changed by the founder on 2026-10-08, DEC-066). | REQ-045; calculation decided here |

Currencies (expansion module F; decided by the founder on 2026-10-09 as decision 8, "UZS and USD, separate balances, no conversion"; the details marked **decided here** were decided by the agent):

| No. | Rule | Source |
|---|---|---|
| BR-36 | Every amount of a customer's debt has a currency: UZS or USD. So'm are whole so'm; dollars are whole cents. One entry is 100 to 100 000 000 so'm, or 0.01 to 10 000.00 dollars. A dollar amount is written with its thousands grouped, two decimals and the sign after it: `1 250.50 $`. | Decision 8; the dollar range and the way of writing decided here |
| BR-37 | A customer account holds a so'm book and a dollar book in one sequence of entries. BR-2 to BR-5 and BR-9 hold inside each book: a payment reduces the debt of its own currency only and may not exceed it, payments cover the debts of their own currency from the oldest, and overdue status and the payment history indicator are worked out for each currency by itself. INV-3 holds for each balance. | Decision 8 |
| BR-38 | A shop works in dollars when the platform switch `usd_on` is on and the shop's own setting is on. Both are off by default; only the owner changes the shop's setting. With either off the shop behaves as before dollars existed: nothing is recorded or shown in dollars, and `$` in a chat message is not read as a currency. | Module rules of 2026-10-09 |
| BR-39 | The shop's setting cannot be turned off while any customer owes dollars; the refusal says so. If the platform switch is turned off while dollars are owed, those debts are kept, are hidden, cannot be acted on, and still count wherever a debt forbids something (INV-13, BR-32); they return when the switch is on again. | Module rules; the second sentence decided here |
| BR-40 | BR-8 applies per currency: the existing limit and shop default are so'm and are compared with the so'm balance; dollars have a limit and a shop default of their own (1.00 to 1 000 000.00 dollars), compared with the dollar balance. A shop that sets no dollar limit has none. | Recommended in the module brief; decided here |
| BR-41 | In the chat an amount is dollars only when it carries a dollar mark (`$` before or after, or the word usd, dollar, доллар) and the shop works in dollars; without a mark it is so'm, whatever its size. A dollar amount has at most two decimals. An amount that could be read two ways (`1.250$`, `50$ so'm`) is asked about and never guessed. | REQ-N03 (never guess); decided here |
| BR-42 | Wherever a figure is shown per customer, per shop or per owner (a balance, what is overdue, a total, a report, an export), the so'm figure and the dollar figure are shown side by side. A reminder is one message per customer stating what is due in each currency; the limits of INV-14 are per customer, not per currency. | INV-19, INV-14 |
| BR-43 | Not in dollars for now, and refused or left out rather than half done: goods lines on a dollar sale (the catalog's prices and BR-7's rounding are so'm); a spreadsheet import (BR-24 records so'm only); an SMS reminder (it states the so'm amount only, and a customer who owes only dollars and has no Telegram link is listed as not reachable); the product metrics of DOM-009 (dollar events are stored with their currency and left out of the weekly figures). Subscription prices and payments are so'm only. | Decided here |
| BR-44 | The cash book, behind the platform switch `cash_book_on`: an entry is income or expense, paid in cash, by card or by transfer, in one currency (so'm; dollars only while the shop works in them, BR-38), with an amount in that currency's unit and inside its range for one entry (BR-36), under a category of its own direction, on a Tashkent day that is not in the future and not more than 31 days back, with an optional note, written by a member of staff. With the switch off nothing of the cash book exists: no route, no section, no command, and no answer of the ledger differs | Founder's decision 5 of 2026-10-09 (expansion module H) |
| BR-45 | A cash book entry is never changed and never deleted. A wrong one is cancelled with a reason (3 to 300 characters) and stays in the book, marked, with who cancelled it and when; a cancelled entry counts in no total and no balance, and a cancellation is never taken back | Founder's decision 5 of 2026-10-09 (expansion module H) |
| BR-46 | A balance of the cash book is kept per way of paying and per currency: what a day or a period opened with, plus the income, minus the expense, among the entries that stand. Balances and totals of different currencies are never added (INV-19). A balance may be below zero: the book records what was written into it and does not stop an expense because an income is missing | Founder's decision 5 of 2026-10-09 (expansion module H) |
| BR-47 | Categories are the shop's own, income and expense as separate lists; a default set in the shop's language is written the first time the cash book is used. A name is unique within a direction whatever its case, apostrophe or alphabet. A category is renamed, archived (it then takes no new entry and keeps its old ones) and brought back; it is deleted only while no entry, cancelled or not, was ever written under it. A shop has at most 100 | Founder's decision 5 of 2026-10-09 (expansion module H) |
| BR-48 | A customer's payment recorded in the ledger is money received: while the cash book is on it is written into the book as income of the category "debt repaid", in the same transaction, once (the database refuses a second entry for one payment and an entry that is not that payment's amount and currency). It is cancelled by reversing the payment (BR-10) and in no other way; nobody writes into that category by hand, and it is renamed but never archived or deleted. A credit sale is not money and writes nothing | Founder's decision 5 of 2026-10-09 (expansion module H) |
| BR-49 | A payment is taken to be cash unless it says otherwise: the API and the forms take an optional method (cash, card, transfer), and in the chat a payment whose note is exactly a way of paying ("Ali -45000 karta") was paid that way; a note that says anything more names none. While the cash book is off the method does not exist: a request that names one is refused as it was before the field existed | Founder's decision 5 of 2026-10-09 (expansion module H) |
| BR-50 | Payments recorded while the cash book was off are not in it. The owner may copy the standing ones in, optionally from a chosen day: each on the Tashkent day it was paid, as cash, by its author. Nothing does this unasked, and doing it again writes nothing | Founder's decision 5 of 2026-10-09 (expansion module H) |
| BR-51 | By default managers and the owner read the cash book, record income, record expense, cancel an entry and arrange the categories, each as a permission of its own (`cash.view`, `cash.record_income`, `cash.record_expense`, `cash.cancel`, `cash.categories`); copying past payments in is the owner's (`cash.backfill`). A seller has no part unless the owner grants one. The bot answers `/kassa` with today's totals and balances to a member who may read the book and never writes to it: a message with a name and an amount is always about a customer | Founder's decision 5 of 2026-10-09 (expansion module H) |
| BR-60 | The stock (expansion module I) exists only while the platform switch `stock_on` is on. An item of the catalog is counted in stock only when the shop says so; every item that existed is not counted, so a shop that only uses the catalog sees no change. A counted item has one of the stock's units (dona, kg, g, l, ml, m, quti, paket, juft, qop, blok), is reviewed (not learned) and is not an alias, and its unit no longer changes once it has a movement. | Decision 6 of 2026-10-09; decided here |
| BR-61 | Stock is a ledger of movements and never an edited number. A movement is one item, a signed quantity with three decimals, a kind (receipt, sale, customer return, supplier return, write-off, correction, reversal), who and when, and what caused it. A wrong movement is undone by exactly one opposite movement; nothing is edited or deleted. What is on hand and what it is worth are the sums of the movements. | Decided here (the brief's rule) |
| BR-62 | Cost is a weighted average kept as a pool: per item the quantity on hand `Q`, its value at cost `V` and the currency of `V`; the average is `V / Q`. A receipt of `q` at cost `c` adds `q` and `round(q x c)`. Goods that leave (sale, write-off, return to a supplier, a stocktake that found less) leave at the average: `round(V x q / Q)`, and when everything leaves `V` becomes exactly zero. Nothing on hand is worth nothing (`Q <= 0` means `V = 0`). Every product of a quantity and a cost is rounded half up to a whole minor unit, once. | Decided here |
| BR-63 | Goods that come in without a price of their own (a customer's return, a stocktake that found more) come in at the current average, which therefore does not move; while nothing is on hand, at the last known cost; with no cost known, at none. | Decided here |
| BR-64 | Cancelling restores cost: a cancelled outgoing movement comes back at the cost it left with; a cancelled incoming movement leaves with the value it brought, so cancelling a wrong receipt restores the average it disturbed. If the stock no longer holds that much value, it leaves at the average instead, so the value is never negative. | Decided here |
| BR-65 | A sale may take an item below zero, because shops sell before they write the receipt down: the seller is warned and the item shows a negative quantity. While it is at or below zero it has no value; a sale then carries the last known cost for the margin only. A receipt into a shortfall values only what remains on hand. A shop may turn on "refuse sales beyond stock" (off by default): the sale is then refused whole. A write-off and a return to a supplier never go below zero. | Recommended in the module brief; decided here |
| BR-66 | The cost of an item is kept in one currency at a time: the currency of its receipts. While something of it is on hand, a receipt in the other currency is refused; once nothing is on hand the next receipt starts the cost again in its own currency. Selling prices are so'm, so margin (selling price less average cost) exists only for an item whose cost is in so'm. No stock figure is a sum of two currencies. | INV-19; decided here |
| BR-67 | A credit sale with goods lines takes each line of a counted item out of the stock, when the line is in the unit the item is counted in (a line in another unit takes nothing and says so). Reversing the ledger entry puts the goods back, whether or not the stock is still switched on. An amount-only sale, and a sale recorded in the chat, name no goods and move nothing. There is no cash sale without a customer. | Decided here |
| BR-68 | A stock document is a draft, then posted, then cancelled; a draft may be dropped. A draft changes nothing. Posting writes its lines and movements and the money that goes with them, in one transaction. A posted document is never edited: it is cancelled with a reason, which reverses every movement and entry it wrote. Cancelling is refused when it would take back goods that have since left the stock. | Decided here |
| BR-69 | A purchase receipt names goods, quantities and unit costs in one currency, and optionally a supplier. With a supplier the total is owed to them and what was paid at once is a payment on their account; without a supplier it was paid in full. A line may name a good the catalog does not have: it is added, counted from the start. Receiving an item turns its counting on. | Decided here |
| BR-70 | A customer's return of goods puts them back in stock and lowers the customer's debt by an ordinary payment entry of the ledger, under the ledger's own rules (never more than is owed, INV-4); what is handed back in money instead is recorded on the document. That entry is cancelled only with its document. So'm only. | INV-1, INV-4; decided here |
| BR-71 | A write-off names why (damaged, expired, lost, own use). A stocktake is a list of counted quantities, one per item; reading the draft shows each count beside the books, and posting writes a correction for each difference against the books as they are at that moment. | Decided here |
| BR-72 | A supplier's account is an append-only ledger, each currency a book of its own: a purchase or an opening balance raises what the shop owes, a payment or a return of goods lowers it, and an entry is cancelled by a reversal that says why. A payment may exceed what is owed: the balance below zero is an advance. A supplier with a balance that is not zero cannot be archived, and an archived one takes no entry. While the cash book is on, a payment to a supplier, what is paid at once on a receipt, and what is handed back to a customer for returned goods are expenses of the cash book under the stock's own categories, written with them and cancelled only with them (BR-45, BR-48); goods returned by a customer are not money received. | Decided here |
| BR-73 | Cost and margin are shown only to a member who holds `stock.costs.view` (managers and owners by default): for anyone else the figures are absent from every answer, and the stock report is closed. What is on hand is every member's to see. | Decided here |
| BR-74 | A barcode names one item of a shop. Eight, twelve or thirteen digits must carry a correct check digit (EAN-8, UPC-A, EAN-13); anything else is the text of a Code-128 label: printable ASCII, at most 48 characters. An item has at most ten. | Decided here |

The network between shops (expansion module J; rules BR-80 to BR-96):

| No. | Rule | Source |
|---|---|---|
| BR-80 | The network between shops exists only while the platform switches `network_on` and `stock_on` are both on (a delivery is a stock receipt). With either off nothing of it exists: no route, no section, no message, and no other answer of the service differs. What was recorded while it was on stays in each shop's own books and export. | Decision 7 of 2026-10-09 (expansion module J) |
| BR-81 | Two shops connect only by a code: one shop makes it (saying whether it will be the buyer or the supplier), hands it over outside the service, and the other presents it, asking for the opposite role. A code is 32 random bytes, kept only as its hash, works once and for 48 hours, and a shop has at most 10 open at a time. There is no list of shops, no search and no way to learn whether a shop is on the platform; every reason a code does not work is the same answer. The shop that made the code accepts or declines the request, once. Two shops have at most one live link in each direction. | Decision 7 of 2026-10-09; decided here |
| BR-82 | What a shop learns of its partner is its name, when the link is asked for, and its contact phone (the shop's `share_phone`), when the link is accepted; both are kept as they were at that moment. Nothing else is ever shown or stored on the other side: no customers, balances, staff, stock, catalogue, cost or price beyond what is put on an order or a note. A step the partner took is shown as the partner's, never as a named person's. A declined or ended link shows nothing it had not shown before. | Decision 7 of 2026-10-09; decided here |
| BR-83 | Inside its own books the partner is an ordinary row: for the buyer one of its suppliers, for the supplier one of its customers. A shop may name an existing row when it accepts, or later while the link has none; otherwise one is made from the partner's name and phone at the shop's first step that needs it. The supplier's customer row is a customer like any other: the ordinary ledger, limits and reminders apply to it, it counts toward the free plan (BR-33, BR-34: with the plan full the link is not accepted unless an existing customer is named), and it is archived only at zero balance. It stands for a shop, not a person: it has no customer link and no removal request; when the partner is erased, the phone the network wrote there is cleared and the name and the entries stay as the shop's own record. | Decision 7 of 2026-10-09; decided here |
| BR-84 | Either side ends a link at any time (and the shop that asked may take a request back). Ending closes what still waited: orders not received are cancelled, notes not answered are void, payments not answered lapse, each saying that the link ended. What was posted to either shop's books stays, and both keep reading the history. Nothing new is done over an ended link; connecting again takes a new code. | Decision 7 of 2026-10-09; decided here |
| BR-85 | An order is written by the buyer: lines of a free-text name, a quantity with three decimals in one of the stock's units, optionally tied to one of the buyer's own catalogue items, with a note and a wanted date. While it is a draft it is the buyer's alone and the supplier sees nothing. Sent, it is numbered per link and exists on both sides under one identifier. Order: draft, sent, accepted, delivered, received; or declined (by the supplier) or cancelled (by the buyer). The supplier's catalogue and prices are not shown to the buyer: there is no price list. | Decision 7 of 2026-10-09; decided here |
| BR-86 | The supplier accepts an order by naming the currency (so'm, or dollars when the platform and both shops work in them) and, for every line, the quantity it will deliver, which may differ from what was asked or be nothing, and its unit price; it may tie a line to one of its own catalogue items. The buyer sees each change beside what it asked for. The total must be an amount one ledger entry can hold (BR-36). | Decision 7 of 2026-10-09; decided here |
| BR-87 | The buyer cancels and the supplier declines, each with a reason the other reads, while nothing of the order has been received: when it is sent or accepted, and after a delivery only when the buyer has rejected the note. Each is that side's own step: one side cannot take the other's. | Decision 7 of 2026-10-09; decided here |
| BR-88 | A delivery note is issued by the supplier for an accepted order: its accepted lines with quantities and prices, the currency, the total, and what was handed over on delivery (all of it: paid; none: on credit; some: part). What a note says never changes once issued. A correction is a new note with a reason that supersedes it, while it is not received. A note is read inside the application by each side and printed from there; there is no public link to it, because a link would put a document of two shops outside both shops' sign-in for no need: each side already holds its copy. | Decision 7 of 2026-10-09; decided here |
| BR-89 | A delivery takes effect only when the buyer confirms it, and then on both sides at once, in one transaction: the buyer's books take a purchase receipt of the note's goods at the note's prices from that supplier, with what was paid on delivery as a payment to it; the supplier's books take a credit sale of the note's total to the buyer's account, a payment for what was paid on delivery, and the goods of its counted items out of its stock. If either side's books refuse, neither is written and the note still waits. The buyer says which of its own items each line is, in the line's unit, or adds it as a new one with its selling price. | Decision 7 of 2026-10-09 (expansion module J) |
| BR-90 | A note is confirmed as it stands or not at all. Goods that arrived short or wrong are answered by rejecting the note with a reason and, line by line, what did arrive; nothing is posted, the supplier sees it and issues a corrected note, which the buyer then confirms. So no figure reaches either shop's books that the other did not state itself. There are no part deliveries of an order and no returns between shops through the network: a shop corrects its own books with its own stock documents. | Decision 7 of 2026-10-09; decided here |
| BR-91 | The supplier's sale is written with the authority of the note: in the name of the member who issued it, who needed `credits.record` then, whatever happens to that member afterwards; no member of the supplier is present when the buyer confirms. The goods leave the supplier's stock whatever it holds, since they were carried out when delivered; a shop that refuses sales beyond its stock is refused the note itself when it holds less. Only a line tied to a counted item in the line's unit moves stock. Why a supplier's books refused a confirmation is not told to the buyer. | Decision 7 of 2026-10-09; decided here |
| BR-92 | A payment of the buyer to the supplier is recorded by either side. The side that records it writes it into its own books at once by its ordinary path (a payment on the supplier's account; a customer's payment), and the other side sees it awaiting confirmation: its books take it only when it confirms, by its own ordinary path. It may decline with a reason, which writes nothing anywhere. The recorder may take a payment back while it waits: its own entry is cancelled with it. A payment is in one currency both shops work in. | Decision 7 of 2026-10-09 (expansion module J) |
| BR-93 | Each shop's balance with a partner is its own books' and is never changed to match the other's. Per partner and currency a shop is shown its own balance beside what both sides confirmed through the network (deliveries received less payments confirmed), the difference between the two, and what explains a difference: notes delivered and not confirmed, its own payments not yet confirmed or declined and still in its books, the partner's payments it has not confirmed, and anything it recorded outside the network. The partner's books are never read to do this. | Decision 7 of 2026-10-09 (expansion module J) |
| BR-94 | A suspended shop does nothing in the network but let its owner look (BR-30), and nothing is confirmed into its books. A limited shop reads and answers what was already sent to it (confirms or rejects a note, answers or takes back a payment, cancels, ends a link) and starts nothing: no code, no link, no order, no acceptance and no delivery note, since a delivery is a new credit sale (BR-29). A shop on the free plan does everything. | Decision 7 of 2026-10-09; decided here |
| BR-95 | Five permissions: `network.view` (read; managers and owner), `network.manage` (codes, asking, accepting, declining, ending, naming the partner's row; the owner), `network.order` (write, send, cancel orders; managers and owner), `network.fulfil` (accept or decline orders, issue and correct notes), `network.confirm` (confirm or reject notes; record, confirm, decline, take back payments). A step that writes a book also needs that book's own permission: `stock.receive` to confirm a note, `suppliers.pay` when something was paid on delivery or to record a payment as the buyer, `credits.record` to issue a note, `payments.record` to take a payment as the supplier, `entries.cancel` to take that back. The network is never a way around them. | Decision 7 of 2026-10-09; decided here |
| BR-96 | Every step is in the history of the thing on both sides and in both shops' activity logs, with who and when on the side that took it and "the partner" on the other. The partner's members who hold the permission the news is for are told in Telegram, each in their own language, once: the shop's name and what both already hold (a number, a total, a reason). The owner's export holds the shop's own side. When a shop is erased its side is deleted; the partner keeps its own books and history, its link ends, what waited is closed, and the partner is shown as removed with no name and no phone. | Decision 7 of 2026-10-09; decided here |

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
| BR-27 | An approved receipt extends the paid-through date by the whole months the administrator records, starting from the later of today and the current paid-through date. A shop still on trial keeps the trial days it has left: its months start from the trial's last day. | REQ-055; start rule decided here; trial days kept by the founder's decision of 2026-10-09 |
| BR-28 | Seven days and one day before the trial or paid period ends, the owner is warned. | REQ-057; timing decided here |
| BR-29 | In limited mode, recording new credit sales and imports is refused; viewing, exporting, recording payments, reversals, disputes, customer views, and reminders continue. | REQ-057; reminders continuing decided here |
| BR-30 | A suspended shop, an administrator action, allows only viewing and export by the owner. | REQ-058 |
| BR-31 | An administrator sees a shop's customers and entries only during a support access, which is requested with a reason, lasts at most 24 hours, and is shown to the owner. | REQ-059; duration decided here |
| BR-33 | The free plan, behind the platform switch `free_plan_on` (off by default). While it is on, a shop that has no running trial or paid period and no more customers than the plan holds (`free_plan_customers`, 30 by default, 1 to 10 000) is free instead of limited: every operation works, with no end date. The customers counted are the shop's customers whose status is active; archived and anonymized customers are not counted. Free is worked out from the count each time and is not stored: the subscription row says only that no period runs. A suspended shop stays suspended (BR-30). | Expansion decision 1-2 of 2026-10-09; what is counted decided here |
| BR-34 | While the free plan is on, a shop without a running trial or paid period cannot have more active customers than the plan holds: adding a customer, taking one out of the archive, and applying an import that would exceed the number are refused with `FREE_PLAN_FULL`, which names the number and how to subscribe. A running trial or paid period has no such limit. A shop over the number whose period ends becomes limited (BR-29) as before; one at or under it becomes free, and the owner is told which. The warnings of BR-28 say which of the two follows. | Expansion decision 1-2 of 2026-10-09 |
| BR-35 | While the free plan is on, SMS reminders are sent only for shops in a paid period, within the monthly quota per shop: not during a trial, not for a free or limited shop. The owner is shown whether SMS is included and how many are left this month. With the free plan off, SMS does not depend on the subscription. | Expansion decision 3 of 2026-10-09; the trial excluded decided here |

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
- **No exchange rate and no conversion.** So'm and dollars are separate books. The service never turns one into the other, never values a dollar debt in so'm, and offers no figure that adds them (INV-19).
- **No stock.** The catalog holds names, units, and prices only; nothing counts quantities on hand.
- **No shop sees into another.** Two linked shops share an order, a delivery note and a payment, each holding its own copy; neither sees the other's customers, balances, staff, stock or catalogue, and neither's books are changed by the other's step without its own confirmation (BR-82, BR-89, BR-93).
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
- The payment history indicator is lawful because it never leaves the shop, other than to the customer it is about (changed by the founder on 2026-10-08, DEC-066). Agent inference, not legal advice.

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
| DEC-014 | Rules decided in this version: dispute once per entry within 30 days, closed by reversal, decline, or withdrawal; one-time addition of goods lines; promised dates changeable with history; 30-day default promise; payment history calculation; limited-mode behavior; support access of at most 24 hours; 30-day shop deletion period | Approved 2026-10-06 (APR-014) |
