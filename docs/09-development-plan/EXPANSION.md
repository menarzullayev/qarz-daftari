# Expansion: from a credit ledger to a shop system

Decided by the founder on 2026-10-09 after comparing the product with pDaftar, one question at a time.
The clock for "how long does this take with AI agents" started at **2026-10-09T11:00Z**. Each module's
row is filled in when its pull request is merged.

## What was decided

| # | Area | Decision |
|---|---|---|
| 1-2 | Price | A free plan with no end date, up to 30 customers per shop; above that 100 000 UZS a month |
| 3 | Reminders | SMS (Eskiz) for paying shops, a monthly quota inside the price; no voice calls |
| 4 | Instalment schedules | Not now |
| 5 | Cash book | Full: income, expense, categories, cash and card |
| 6 | Stock | Full: receipts of goods, cost price, returns and write-offs, barcodes |
| 7 | Suppliers | Full network between shops: orders, confirmation by the other side, delivery note |
| 8 | Currency | UZS and USD, separate balances, no conversion |
| 9 | Paying the subscription | Transfer and receipt stays; Payme later |
| 10 | Permissions | A matrix of separate permissions per member of staff |
| 11 | Languages | Uzbek Latin and Russian (exist), Uzbek Cyrillic, Tajik, Karakalpak, English |
| 12 | Customer's side | A secret read-only link and QR code |
| 13 | Clients | Mini App, web and an installable web app (PWA) now; Android and iOS later |
| 14 | Order | Everything in parallel |
| 15 | Release | One release when everything is ready; nothing is deployed before |

The agent advised against 14 and 15 (shared tables; months without a release; one large deployment)
and builds every module as its own pull request behind its own switch, so that a staged release stays
possible. The founder's decisions stand.

## Modules

| Id | Module | Depends on | Switch (platform setting) | Migration | Merged | Agent time |
|---|---|---|---|---|---|---|
| A | Free plan (30 customers) and SMS for paying shops | - | `free_plan_on` | 0038 | 11:44Z (0:44) | #91, 43 min |
| B | Customer link and QR; installable web app | - | `customer_links_on` | 0040 | 12:49Z (1:49) | #92, 89 min + 17 min to rebase on G |
| F | USD beside UZS | - | `usd_on` | 0041 | 13:23Z (2:23) | #94, 96 min + 46 min to rebase on G and B |
| G | Permission matrix | - | `permissions_on` | 0039 | 12:37Z (1:37) | #93, 73 min + 22 min to rebase on A |
| H | Cash book | F, G | `cash_book_on` | 0042 | 13:54Z (2:54) | #95, 64 min |
| I | Stock | F, G | `stock_on` | 0043 | 14:45Z (3:45) | #96, 114 min |
| J | Suppliers and the network between shops | I | `network_on` (and `stock_on`) | 0045 | 16:29Z (5:29) | #99, 103 min; final pass #101 at 17:12Z |
| E | Four more languages | all texts final (built beside H and I, before J: see below) | - | 0044 | 15:23Z (4:23) | #97, 119 min |
| - | Gaps left by the modules above (free plan in the administrator's panel, stock check in the operations watch, and others) | A, F, G, H, I | the modules' own | 0046 (three count functions; no table) | | |
| - | Leftovers after the expansion ("Open after the expansion", below) | all | the modules' own | 0047 (`network_receipt_finish` replaced to compare lines; one index on `stock_document`; no table) | | |

Module E was built while H, I and J were still being written (H and I were merged first and are
translated; J is not), so it does not wait for their texts: a key
that Tajik, Karakalpak or English lacks reads Uzbek at run time, Uzbek Cyrillic is generated from Uzbek,
and the tests of the four new languages checked what was there and not what was missing. The final pass
after J was merged (2026-10-09) translated the network into Tajik, Karakalpak and English and made
completeness strict: CI and the tests now fail on a text that one of them lacks
(`docs/08-technical-spec/OUTPUT.md`, "Languages"; `docs/10-operations/translation-review.md`). Tajik and
Karakalpak were written by a model and need a native speaker's review before they are announced.

## Rules every module follows

- Its own branch and pull request; merged to `main` when CI is green. **Nothing is deployed.**
- A platform setting of kind `switch`, off by default, `needs_code=True`. With the switch off the service
  behaves exactly as before: no new route answers, no new button or command is offered, no existing
  answer changes shape. A test proves it.
- Migrations are forward-only raw SQL with the number in the table above; `down_revision` is the number
  before it. Tenant tables carry `shop_id` and forced row-level security like every existing one; rights
  are granted to the roles by name and enumerated in `backend/tests/db/test_database_roles.py`.
- Layers, lint, types, the size budget and the style-sheet guards are CI's and are not relaxed.
- Every rule has a negative test. Every visible text exists in Uzbek and Russian.
- New screens use the design system's tokens and components (`frontend/src/shared/tokens.css`).


## Result: how long it took

Measured on 2026-10-09 with the clock of the machine the agents ran on. "Done" here means merged to
`main` with CI green and every module behind its switch, off. Nothing was deployed.

| Mark | Time (UTC) | Since the start |
|---|---|---|
| Start: the fifteen decisions written down | 11:00 | 0:00 |
| First wave merged (A, G, B, F) | 13:23 | 2:23 |
| All eight modules merged | 16:29 | 5:29 |
| Front-end quality pass merged (#98) | 16:51 | 5:51 |
| Final pass on the network merged (#101) | 17:12 | 6:12 |
| Gaps left by the modules closed (#100) | 17:31 | 6:31 |
| A race the combined modules exposed on `main`, corrected (#103) | 17:54 | 6:54 |
| `main` green with everything in it | 18:03 | 7:03 |

Before starting, the coordinating agent had called this "several months of work" (stock "several weeks
on its own", the network "two to three months"). That estimate was of a team of people and was wrong by
far more than an order of magnitude for writing the code. After two hours of measurement it estimated
10 to 14 hours in all; it took 6 hours 31 minutes to the last module merge and 7 hours 3 minutes to a green `main`.

The half hour between the two is itself a finding. Every pull request was green on its own; `main` with all of
them failed twice in a row, on the load test's check that times within a customer's entries never run backwards.
The race was always there (an entry's time is read before the customer is locked, its number taken under the
lock); the work the modules added between the two points made it show. No pull request's CI could have shown
it, only the run after everything was merged. It was corrected in #103 with a test that fails without the fix.

What the six and a half hours produced, against the commit the plan was merged at (`68ec0b2`):
533 files changed, about 106 000 lines added; nine migrations (0038 to 0046); the back-end suite grew
from 6 546 to about 11 550 tests and the front-end suite from 2 323 to 3 980; one CI run still takes
6 to 7 minutes.

What the figure does not contain, and what remains before any of it reaches a shop:

- No new screen has been seen by a person. Every check was a test, a build or CI.
- Tajik and Karakalpak were written by a model and are unreviewed (`docs/10-operations/translation-review.md`).
- The camera barcode scanner, the installed web app and the printed QR code were never tried on a device.
- The network between shops is the first feature to cross the tenant boundary. Its author and a second
  agent reviewed it; no person has. Four points are left to the founder
  (`docs/10-operations/security-review.md`): the author of a delivery note who has since left the shop,
  totals compared instead of lines, partner members' chat identifiers in the outbox, advance payments.
  (The second was closed afterwards, without a decision being needed: migration 0047 compares the lines.)
- The founder chose one release for everything. It has not been made.

Where the time went, as observed:

- Writing ran in parallel and was fast: a module took 45 to 120 minutes from an empty worktree to a
  green pull request.
- Merging did not run in parallel. Every module after the first had to be brought onto the ones merged
  before it and pass the full suite again; that cost 15 to 45 minutes each time and is why the first
  wave, written in about 95 minutes, took 143 to land.
- A full local run of the back-end suite takes 10 to 20 minutes on one machine shared by several
  agents; CI's four shards take under 6.
- Twice an agent waited for a helper it had started whose report was delivered to the coordinator
  instead: about 20 minutes lost on the network module and about as long on the gaps pass.
- The client and the server of the network module were written by different agents from a described
  contract: seven of twelve points where the client had guessed were wrong, one of which would have
  silently dropped a paid amount when a delivery note was corrected. An end-to-end journey written
  afterwards is what found them.

## Open after the expansion

Decisions waiting for the founder:

| # | Question | From |
|---|---|---|
| 1 | Inside Telegram, keep Telegram's own button blue (contrast about 4.1:1) or ours (4.5:1 and above)? | #98 |
| 2 | The reminders screen now shows a text in the reader's language only, not in both. Keep? | E |
| 3 | When the free plan is switched off altogether, preview and tell the owners as when it is lowered? | #100 |
| 4 | A payment notice with a receipt is now recorded as paid by card by default. Keep? | #100 |
| 5 | Ask Eskiz whether an approved template's amount may read `12.50 $` or two amounts; otherwise eight new templates | #100 |
| 6 | Three points on the network: a delivery note whose author has since left the shop or lost the right to sell on credit; partner members' chat identifiers stored in the acting shop's outbox; advance payments between linked shops (`EXCEEDS_BALANCE`). The fourth, totals compared instead of lines, is closed (migration 0047) | #101 |
| 8 | The product's name | founder |
| 9 | A member who holds `stock.receive` or `stock.adjust` without `stock.view` now reaches the documents list in the Mini App, but not the quick receipt or a document's form: those read the stock's settings, the list of items and the barcode lookup, which the server gives to `stock.view` alone. Open those three reads to who writes documents (they show what is on hand), or keep it so? | leftovers |

Closed after the expansion, in one pull request (`fix/expansion-leftovers`, migration 0047), none of which
needed a decision: a delivery note is compared line by line when it is received; money paid on delivery
reaches the supplier's cash book by the method the buyer named; the screens offer declining a payment by
`network.confirm` alone, as the server decides it; dollars cannot be turned off under an import that is
writing dollar rows (the shop's advisory lock for the setting); the documents list has an index for a
filter by state; a supplier, and a link's row of the books, is chosen out of all of them by searching
and not out of a first page; the Mini App's stock section opens to whoever writes documents; the bottom
navigation no longer covers the end of a screen on a phone at 200 % text; the two-language tables in
code (stock units, write-off reasons, the cash book's categories, the notes a stock document writes, the
states in the network's export sheets) are texts in all six languages, and a test refuses such a table.

Decided since: a sale for cash without a customer (question 7 of this list as it stood) was decided on
2026-10-09 and built as migration 0049: a sixth kind of stock document, behind `stock_on`, with its money
in the cash book (`docs/08-technical-spec/OUTPUT.md`, "Cash sales"; BR-98 to BR-104).

Known and left for later: the period export of the cash book is written inside the request, not by the
worker; goods lines on a dollar sale stay refused; `network_receipt_finish` does not ask whether the
entries it is given were written in the confirming transaction (`security-review.md`); a notice shown
above the tab bar is still placed by the bar's nominal height and lies over a taller bar at 200 % text,
and the overview and the customer book are still up to 76 px wider than a 390 px window at that size;
the suppliers' screen reads the stock's settings, so a member with `suppliers.view` and without
`stock.view` is refused there; the remark beside a refused field (`error.fields`) is an English
identifier that no screen shows (`translation-review.md`); categories of the cash book and notes written
before the texts were translated stay in the language they were written in.

Only a person can do: try every module in a test shop with its switch on; have the two translations
reviewed; try the installed app, the camera scanner and a printed QR code on a phone; an independent
security review of the network; the Eskiz contract; the one release.
