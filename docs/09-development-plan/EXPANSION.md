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
| A | Free plan (30 customers) and SMS for paying shops | - | `free_plan_on` | 0038 | | |
| B | Customer link and QR; installable web app | - | `customer_links_on` | 0039 | | |
| F | USD beside UZS | - | `usd_on` | 0040 | | |
| G | Permission matrix | - | `permissions_on` | 0041 | | |
| H | Cash book | F, G | `cash_book_on` | 0042 | | |
| I | Stock | F, G | `stock_on` | 0043 | | |
| J | Suppliers and the network between shops | I | `network_on` | 0044 | | |
| E | Four more languages | all texts final | - | - | | |

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
