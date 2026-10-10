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
| 3 | ~~When the free plan is switched off altogether, preview and tell the owners as when it is lowered?~~ Decided on 2026-10-10: yes. The settings screen asks for the count before saving, and each owner of a shop that was free is told once (`free_plan_off`). | #100 |
| 4 | A payment notice with a receipt is now recorded as paid by card by default. Keep? | #100 |
| 5 | Ask Eskiz whether an approved template's amount may read `12.50 $` or two amounts; otherwise eight new templates | #100 |
| 6 | Three points on the network. Decided on 2026-10-10: a delivery note whose author has since left the shop or lost the right to sell on credit is posted in the owner's name (migration 0048); advance payments are accepted, as a setting of each shop, off by default (technical specification, Advances), so a buyer's payment beyond its debt is confirmed by a supplier that accepts advances. Still open: partner members' chat identifiers stored in the acting shop's outbox. The fourth, totals compared instead of lines, is closed (migration 0047) | #101 |
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

## After the measured expansion (9 to 10 October 2026)

Not part of the 7 hours 3 minutes above. The founder took further decisions and these were merged:

| Pull request | What | Migration |
|---|---|---|
| #105 | Leftovers that needed no decision (line-by-line check of a delivery note, phone bottom navigation at 200 % text, searchable pickers, a lock for the dollars setting, the remaining two-language tables) | 0047 |
| #106 | Advances accepted behind a per-shop setting; a delivery note whose issuer has left is posted in the owner's name; switching the free plan off is previewed and announced | 0048 |
| #107 | One definition of the product's name (`backend/src/qarz/domain/brand.json`) and the name HisoBox, with a mark chosen by the founder; the `single-host` flake fixed | - |
| #108 | A sale for cash without a customer | 0049 |

`main` was at `f205323`, migration 0049, when this table was written, and was not deployed then: the CI
run on that commit failed three times on Docker Hub (500, 504 and 429 from `auth.docker.io` and
`registry-1.docker.io`), not on the code; every pull request was green before its merge.

What has happened since:

- **Production was deployed on 2026-10-09 at migration 0049** (it had run `2c638ba`, migration 0037). Every
  switch of the expansion stayed off.
- **The public host moved to `hisobox.bugvector.uz`**; `qarz.bugvector.uz` answers with a 302 to it.
- **#110 is merged:** the local stack's teardown (`deploy/production/scripts/local.sh down`) removes only
  the images of its own release, so it no longer takes the images production runs, or its rollback
  images, with it (item 2 of the list below).

### What the expansion cost

The clock measured time and not tokens. The session's own counter on 10 October: 12.6 million output
tokens, 140 million written to cache and 3.1 billion read from cache; 53 % of the plan's week in about three
days, most of it on 9 October. Each agent re-reads its whole conversation at every step, about thirty
agents ran, several started helpers of their own, and each module was brought onto the others two or three
times with a full test run each time. Parallel work was fast and not cheap.

### Planned, not started (decided by the founder on 10 October, stopped to save the plan's limit)

In this order, one agent at a time, each told not to start helpers:

1. **Deploy** `main` once its CI is green: twelve migrations (0038 to 0049) tested first on a copy of
   production; every switch stays off.
2. **`e2e/stack.sh down` removes every tag of the images production uses** (the rollback images too) when
   run on the production machine. Make it remove only what it built. Small; before the next local e2e run.
3. **The new bot and address:** `@hisoboxbot`, `hisobox.bugvector.uz` (the old address redirects), the two
   Telegram groups renamed. The founder has yet to say who creates the bot.
4. **Village shops** (switch `village_on`): family members under a customer with "who took it" on an entry,
   no limit per member; reminders to the head of the household naming the member; a shop's own list of
   places with filter and a report by place; a fast entry screen for moving a paper book in, on the import path.
5. **Countries** (switch `countries_on`): a shop's country decides its base currency (UZS, KZT, KGS, TJS, TMT)
   with USD as the optional second, its time zone and default language; phones of the five countries, and the
   same phone on several customers; prices per country; a country opens only when a lawyer's texts are ready.
6. **Offline writing** (switch `offline_on`): credit sales and payments queued on the device and sent exactly
   once when the connection returns; opt-in per device.

Languages stay at six. Open questions for the founder are in the table above, plus: whether members with
`stock.receive` but without `stock.view` may see the items list (question 9); whether the consent text's
version must change because the product's name in it changed.

## The shared product catalogue (10 October 2026)

One catalogue for the whole platform, behind the switch `catalog_on` (off by default, changed with a
code from the authenticator like every switch), migration 0050. Built by one agent, in one pull request.
Nothing was deployed and nothing was imported into production.

### What the founder decided

| # | Decision |
|---|---|
| 1 | The catalogue is the platform's, not a shop's. An owner adding an item searches it, picks one and types only the shop's own price. What is not in it is added by hand exactly as before. |
| 2 | **The photos are copied to our own storage.** This is the founder's decision, taken knowing that the photos come from third-party retailers and that the risk about the rights to them is his. |
| 3 | Names are the Russian original and Uzbek in Latin; Uzbek in Cyrillic is made by the transliterator that already exists. A search reads both. Most seed rows have no Uzbek name yet: the field may be empty and the Russian name is shown; translating is a later task on the data. |
| 4 | An item a shop adds by hand reaches the catalogue only after the platform's administrator approves it. In the shop it works at once either way. |
| 5 | Barcodes are filled in by shops. The catalogue starts with none; when a shop attaches a barcode to an item it picked from the catalogue, the code is proposed through the same queue, and once approved any shop that scans it is offered the item. |
| 6 | The catalogue keeps an approximate retail price in Tashkent. The form shows it under the price field as advice ("Toshkentda taxminan 12 990 so'm") and never fills it in or saves it. |
| 7 | A new switch, `catalog_on`, off by default and protected by the second factor like the others. Off, nothing changes anywhere. |

### What was built

- **Tables that belong to no shop** (`shared_item`, `shared_barcode`), the first of their kind beside forced
  row-level security. The ordinary application reads them and can write neither; the administrators' role
  loads items (the import), and a barcode gets there only through the approval function. A shop's proposal
  (`shared_suggestion`) is a shop's row under the tenant policy: a shop reads its own and no other's, can
  add one only as a waiting proposal, and can neither decide, change nor delete one
  (`backend/tests/db/test_shared_catalog_schema.py`). A shop's item says which catalogue item it was
  picked from, and a shop holds a catalogue item once.
- **What leaves a shop in a proposal: the item's name, its unit and a barcode.** Never a price, a
  quantity, a cost, a supplier or a customer: the table has no column for one. The administrators' queue,
  and the audit of its decisions, do not say which shop a proposal came from; the queue shows only how
  many other shops proposed the same. A shop's proposals are erased with the shop; what was approved
  stays in the catalogue and says nothing of its origin. At most 200 proposals of one shop wait at a
  time: past that its additions still work in the shop and are not proposed.
- **Search and pick** (`/api/v1/shops/{shop}/shared-catalog`): every typed word must be in the names or
  the package size, in either language and either script (the names' matching form, with a trigram
  index); an optional category; pages. Picking makes the shop's own item, named in the picker's language
  with the package size, at the price typed; picking again answers with the item the shop already has; a
  name the shop already uses is refused as for an item typed by hand. Whoever may add items
  (`goods.edit`) may search and pick.
- **Barcodes:** a scan that no item of the shop has falls through to the catalogue, wherever a barcode is
  looked up, and a picked item carries the codes the catalogue has for it.
- **Photos** are kept in our own file store under the SHA-256 of their content, without the metadata they
  came with, and served by our own host at `/files/catalog/<hash>` with `Cache-Control: public,
  max-age=31536000, immutable` (the proxy says `no-store` for every other answer there). No address of a
  third party is stored, sent or shown: the import does not read `img_url`, the client drops a photo
  address that is not `/files/catalog/<hash>`, and the pages' content policy allows images from this host
  alone.
- **The administrator's panel:** the switch among the settings; while it is on, a link from the settings
  to the queue, where an item is approved under a Russian and an Uzbek name (at least one) and a
  category, a barcode is approved as it is, and either is rejected. A suggestion is decided once; a
  barcode that already names another catalogue item is not moved.
- **Clients** (the panel and the Mini App share these screens): adding an item starts with the search;
  "not found: add by hand" opens the form there always was. All texts are in six languages. Tajik and
  Karakalpak were written by a model, as before, and wait for the same review
  (`docs/10-operations/translation-review.md`).
- **Twenty-one categories** (`qarz.domain.shared_catalog.CATEGORIES`), each named in six languages in the
  client. The import maps the seed's own sections to them; a section it does not know is "other".

### Loading the seed

The seed is not in the repository and must stay out of it: `source.json` (the rows, each with a stable
sixteen-digit key), `categories.json` (which section is which category) and `img/<key>.png|jpg|webp`. A
row without a photo file is an item without a photo.

    python -m qarz.interface.import_shared_catalog DIRECTORY

It connects with `QD_ADMIN_DATABASE_URL` and writes photos to the file store the environment names. It is
not a migration and CI does not run it. Running it again adds nothing: a row is known by its key; names,
sizes, categories and prices are brought up to date; a photo already kept is not sent again; and an item
keeps its photo when a later run finds no file for it. It turns no switch on.

On the single host the command is `deploy/production/scripts/single-host.sh catalog-import DIRECTORY`: a
one-off container of the API's image with the seed mounted read-only (the API's own `/tmp` holds 64 MB
and the photos are many times that). **That subcommand was read and syntax-checked and has never been run
against a real stack.**

### Open

- **Uzbek names.** 488 of the seed's 13 426 rows have one; the rest show the Russian name to every
  reader. Running the import again with the names filled in brings them in.
- **The rights to the photos** (decision 2) are the founder's risk. Nothing here checks them.
- **A shop's own photo** of an item it adds by hand: there is no way to attach one, so an approved
  suggestion has no photo.
- **Telling a shop what became of its proposal.** Today it is told nothing, whichever way it was decided.
- **A barcode on an item whose own proposal still waits** is not proposed: only a barcode attached to an
  item already tied to the catalogue is.
- **Correcting or hiding a catalogue item** (a wrong name, a duplicate) has no screen: the import brings a
  seeded row up to date, and anything else is a statement run as the administrators' role.
- **The unit of a seeded item is "dona"** (a piece): the seed says how big a package is, not how a shop
  sells it. A shop changes the unit of its own item after picking it.
- **The search is by words, not by meaning or misspelling**: "kola" does not find "cola".
- **No screen of this module has been seen by a person**, like the rest of the expansion.

## Territories and a customer's address (10 October 2026)

One territory reference for the whole platform (region, district, mahalla, street) and an optional address
on a customer that is picked from it, behind the switch `address_on` (off by default, changed with a code
from the authenticator like every switch), migration 0051. Built by one agent, in one pull request.
Nothing was deployed and nothing was imported into production.

### What the owner decided

| # | Decision |
|---|---|
| 1 | The platform gets a territory reference: region, district, mahalla, street. It is seeded for all of Uzbekistan down to the mahalla, and with streets for one district, Qo'shrabot of Samarqand region, the owner's first market. |
| 2 | A district carries aggregate counts where they are known (population, families, households). Numbers only. |
| 3 | **No resident data.** The reference never holds a person. A customer is entered by the shop as today; the address only helps describe them. This is a deliberate boundary, not something left for later. |
| 4 | Built together with an address on the customer's card (region, district, mahalla, street), behind a new switch `address_on`, off by default and protected by the second factor like the others. Off, nothing changes anywhere. |

### The boundary: places, never people

The four tables hold names of places, state codes and, for a district, three totals. They have no column
that could hold a person, a household, a phone or a document, and a test lists their columns so that one
cannot be added without changing that test on purpose (`backend/tests/db/test_territories_schema.py`).
The import reads four files of places and nothing else. A shop is never given a list of who lives
anywhere: the lists a shop reads are the same for every shop and do not change when a customer gets an
address. A district's totals are stored and are not sent to any client.

A customer's address is the opposite kind of data: it is personal, and it is the shop's own. It is five
columns of the shop's `customer` row, under the same forced row-level security as the name and the
phone, and it goes wherever those go:

- **Read and written** by whoever may read and write the customer. Adding one with an address needs what
  adding a customer needs; changing an address needs what editing a customer needs (a seller adds and
  does not edit). An administrator with a support access a shop's owner opened sees it on the customer's
  page, as they see the name and the phone.
- **Removed with the customer's data.** A customer who asks for their data to be removed loses the
  address in the same statement that removes the name and the phone.
- **Exported to the owner.** While the switch is on, the owner's workbook has the address as the last
  column of the customers sheet. While it is off the column is not there.
- **Erased with the shop**, being columns of a row that is.
- **Never in a list of customers**, in a reminder, in an SMS, on the customer's own page or in a
  customer's read-only link.

### What was built

- **Tables that belong to no shop** (`geo_region`, `geo_district`, `geo_mahalla`, `geo_street`), like the
  shared catalogue's. The ordinary application and the worker read them; neither can write one. The
  administrators' role loads them (the import) and has no right to delete from them. A region and a
  district are known by their state code (SOATO); a mahalla by the seed's code or, for the three rows
  without one, by its region, district and name; a street by its mahalla and name.
- **A mahalla always has a region and only sometimes a district.** Where the seed does not say which
  district a mahalla is in, `district_id` is null. Nothing fills it in: not the import (the seed's
  four-character grouping code is kept as `source_group` and links nothing), not the application, and
  not what a shop says about its own customer.
- **The address on a customer:** a region, and optionally a district, a mahalla, and a street that is
  either a street of the reference or up to 120 characters the shop typed, never both. The chain is
  checked by the application and held by foreign keys in the database: the district and the mahalla are
  of the customer's region, the street is of the customer's mahalla. A mahalla whose district the
  reference knows brings that district with it and cannot be put under another. For a mahalla whose
  district it does not know, a district the shop names is kept on the customer as the shop's word.
- **Lists** (`/api/v1/shops/{shop}/territories/regions`, `/districts`, `/mahallas`, `/streets`, `/last`):
  regions and districts whole, by name; mahallas and streets by typing, every typed word found in the
  name through the names' matching form, so that any apostrophe a phone types and a name typed in
  Cyrillic find the row written in Latin. Asking for the mahallas of a district gives that district's
  own and those of the region whose district is unknown. A region and a district are named in Russian
  and English for those readers; everything else is Uzbek, in Cyrillic for who reads it so.
- **The place the shop used last** (`/territories/last`): the region, district and mahalla of the
  customer whose address the shop set most recently, never the street. The form for a new customer
  starts from it, so a village shop does not pick the same three things for every customer. It is read
  from the shop's own customers and stored nowhere else.
- **Clients** (the panel and the Mini App share these screens): the forms that add and edit a customer
  get an optional address: two selects, a mahalla found by typing, a street picked from the list or
  typed, and a button that clears it. Two mahallas of one name are told apart by the seed's group.
  The customer's card shows the address in one line. All texts are in six languages; Tajik and
  Karakalpak were written by a model, as before, and wait for the same review
  (`docs/10-operations/translation-review.md`).
- **The administrator's panel:** the switch among the settings.

### When a place disappears from a later seed

Each of the four files is the whole of its table. A place that was loaded before and is in the file no
longer is marked `retired`: the lists stop offering it, and it is never deleted. A customer whose address
points at it keeps the address and is still shown the place's name. If a later seed has the place again,
it becomes active under the identifier it always had. No role has the right to delete a row of the
reference, and the database refuses to delete one a customer points at whoever asks. Because a missing
file would read as "this table is now empty", the import refuses to start unless all four files are there.

### Loading the seed

The seed is not in the repository and must stay out of it: `regions.csv`, `districts.csv`,
`mahallas.csv` and `streets.csv`, comma-separated, UTF-8 (a byte order mark is accepted).

    python -m qarz.interface.import_territories DIRECTORY

It connects with `QD_ADMIN_DATABASE_URL`. It is not a migration and CI does not run it. Running it again
adds nothing and brings names up to date. It prints counts and no row, and turns no switch on. Run
against a scratch database on the development machine, twice, it reported: 14 regions, 206 districts,
9 442 mahallas (8 326 of them with a region and no district) and 298 streets added and nothing skipped
the first time; nothing added and all of them brought up to date the second time. The scratch database
was dropped afterwards.

On the single host the command is `deploy/production/scripts/single-host.sh territories-import DIRECTORY`:
a one-off container of the API's image with the seed mounted read-only. **That subcommand was read and
syntax-checked and has never been run against a real stack.**

### Where the data comes from

Regions, districts and mahallas are from
[`uzinfocom-org/digital-health-ig`](https://github.com/uzinfocom-org/digital-health-ig), licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); the names were normalised to plain
apostrophes and the files reshaped, nothing else was changed. The streets of Qo'shrabot district and that
district's three totals were read by the owner from the government's "Raqamli mahalla" dashboard. The
same attribution is in the repository's `README.md`.

### What the data does not have

- **A mahalla's district is known for Samarqand region only**: 1 116 of 9 442 mahallas. Everywhere else a
  shop picks a region and searches the region's mahallas; the district stays optional and unlinked.
- **946 names are shared by two or more mahallas of one region** where the district is unknown (2 991
  rows). The picker shows the seed's group beside such a name; it is a code, not the name of a district.
- **Streets exist for one district** (Qo'shrabot, 298 streets and villages in 35 mahallas). Everywhere
  else the street is what the shop types.
- **A district's totals are known for one district.**
- **Mahallas and streets have an Uzbek name only.**
- **Three mahallas have no code** in the seed and are keyed by district and name: renaming one in a later
  seed makes a new row and retires the old one.

### Decided by the builder, for the owner to confirm

- A typed street is at most 120 characters, and may be given with a region alone.
- A district the shop names for a mahalla whose district is unknown is kept on that customer (see above).
- An administrator's support access shows the address on the customer's page.
- The owner's export has the address in one cell, widest place first.
- Looking a place up needs the permission to add a customer; a list of customers never carries addresses,
  and the customer list cannot be filtered or searched by place.
- A district's totals are stored and shown nowhere.
- The import of customers from a spreadsheet does not read an address.

### Open

- **Showing the attribution in the product.** It is in the documents; whether CC BY 4.0 also asks for a
  line inside the application where the names are shown is a question for whoever reviews licences.
- **Naming the districts of the seed's 206 groups** would link every mahalla to its district. The groups
  are one per district; which is which is not known.
- **A filter and a report by place** (the village shops module planned above) are not built: an address
  is stored and shown, not yet searched.
- **Correcting a place** (a wrong name, a missing street) has no screen: the import is the only way in.
- **No screen of this module has been seen by a person, or opened against a real server or inside
  Telegram.** The two forms and the card were rendered once in a browser 375 px wide against canned
  answers, with a mahalla name of eighty characters: nothing reached past the edge of the screen.
