# Market Research

> **Partly superseded on 2026-10-06.** By founder decision (DEC-012 / APR-012) the pricing direction is now 100,000 UZS a month and positioning no longer rests on customer acknowledgement. The research findings below are unchanged; the Positioning and Pricing recommendations no longer describe the approved direction.

Status: positioning and pricing direction approved by the founder on 2026-10-06 (DEC-004 / APR-004). Field checks are still outstanding.
Upstream: `docs/02-problem-discovery/OUTPUT.md` (passed provisionally, DEC-003 / APR-003; field interviews still outstanding).

**Headline finding.** The closest competitor is stronger than Idea Selection assumed. pDaftar already offers a Telegram bot, Telegram reminders, a customer-facing debt link, and a free tier, and it claims thousands of daily business users (EVID-020, EVID-021). The idea approved in DEC-001 is therefore not a gap in the market. What remains open is narrower: whether a product built only for the mahalla grocer, and centered on the customer acknowledging each debt, performs better on repayment (PROB-001) than a general-purpose ledger.

## Market definition

**Served market:** owner-operated neighborhood grocery shops in Uzbekistan that sell to regular customers on informal credit.

| Layer | Figure | Source and caveat |
|---|---|---|
| Retail trade enterprises, all kinds | 85,585 on 1 November 2025 | EVID-023; not split by food retail or size |
| Registered individual entrepreneurs, all sectors | 281,861 on 1 October 2025 | EVID-024; share that are grocers unknown; low confidence |
| Small grocers' share of grocery retail sales | 48% | EVID-002; snippet only, year unverified |
| Grocery shops that sell on credit | Unknown | No source found |
| Internet users | 32.7 million, 89% of population (early 2025) | EVID-026 |
| Telegram users | About 25 million, 76% of the internet audience (2025) | EVID-025 |

The number that matters most, how many grocery shops sell on credit, could not be found. An honest market size cannot be computed from these sources.

Illustrative ceiling, to show the order of magnitude only: if 20,000 shops paid 25,000 UZS a month, revenue would be 500 million UZS a month, roughly 42,000 USD at the exchange rate implied by pDaftar's price list (EVID-021). Both inputs are agent assumptions. The scenario shows that this is a small market in revenue terms even at an adoption level no local competitor has reported.

## Competitors

| Product | Reach claimed | Platforms | Relevant features | Price |
|---|---|---|---|---|
| **pDaftar** (EVID-020, EVID-021) | 50,000+ Android downloads, 5,000+ businesses daily (self-reported) | iOS, Android, web, Telegram bot | Ledger, automatic SMS and Telegram reminders, customer debt link, shop QR page, multi-seller with action log, cash book, installments | Free tier; about 23,500, 118,000 and 590,000 UZS a month; SMS 170 to 360 UZS |
| **Mafin** (EVID-022) | 3,600+ installations since 2018 | iOS, Android | Ledger, SMS reminders, offline mode, multi-currency, team use | 36,000 and 66,000 UZS a month; 14-day trial |
| **Nasiya** (EVID-004) | Unknown; open-source project | Web, Telegram bot and Mini App | Dashboard, credit limits, reminders, customer page, per-entry customer confirmation | Tariffs exist in the product; amounts not verified |
| QARZDAFTAR, Daftar Qarz, daftar.uz, qarz-app (EVID-005) | Unknown | iOS or Telegram | Basic debt tracking | Not researched |
| REGOS and other POS systems (EVID-005) | Unknown | Desktop and web | Credit sales as one module | Not researched |

Reading of the field:

- pDaftar is the incumbent to beat. It is general-purpose (wholesale, building materials, auto parts, and corner shops), which leaves room for a product shaped only around the grocer, but it also means the grocer can already get most of what the vision describes, free.
- Mafin shows what slow adoption looks like: about 3,600 installations in roughly eight years for a paid-only app (EVID-022).
- Per-entry customer confirmation was found only in Nasiya (EVID-004) and not on pDaftar's site (EVID-020). Absence from a marketing page is not proof that the feature is absent.
- No competitor publishes evidence that its product improves repayment (EVID-019, EVID-020).

## Substitutes

| Substitute | Strength | Weakness |
|---|---|---|
| Paper notebook | Free, instant, universal | One-sided, no reminders, no totals (PROB-002, PROB-004) |
| Refusing credit | Eliminates the risk | Likely loses the most valuable customers (EVID-015) |
| Collecting in person or by phone | No tool needed | Time and relationship cost (PROB-003, EVID-014, EVID-015) |
| Phone notes, spreadsheets, Telegram "Saved Messages" | Free and already installed | Unstructured; usage not evidenced |
| Organized installment and BNPL services | Shift credit risk to a financier | Aimed at durable goods and online retail, not daily groceries (EVID-018) |

## SWOT

| | |
|---|---|
| **Strengths** | No install on either side if Telegram-native; reminders at no per-message cost, where competitors charge 160 to 360 UZS per SMS (EVID-021, EVID-022); narrow focus allows a faster entry flow; founder's Telegram bot experience (unconfirmed assumption) |
| **Weaknesses** | No users, no brand, no field validation; solo founder; concept is not novel (EVID-004, EVID-020); no proof that confirmation improves repayment (EVID-019) |
| **Opportunities** | Telegram reaches about three quarters of internet users (EVID-025); regulators focus on BNPL, not shop ledgers (EVID-018); incumbents do not position on repayment outcomes |
| **Threats** | pDaftar can add any missing feature quickly; the ledger category monetizes poorly even at scale (EVID-029, EVID-030); personal data localization and registration duties (EVID-027); Telegram payment rules (EVID-028); platform dependence |

## Positioning

Three options were considered.

| Option | Description | Assessment |
|---|---|---|
| A. Head-on general ledger | Compete with pDaftar across all credit-selling businesses | Not recommended. Incumbent has reach, platforms, and a free tier. |
| **B. Grocer-only, acknowledgement-first** | Only for mahalla grocery shops. Every credit entry is sent to the customer and acknowledged; reminders are automatic and free through Telegram; entry is built to beat the notebook on speed. Promise: fewer disputes and faster repayment. | Recommended, conditionally. It is the only option with a defensible difference, but the difference is unproven. |
| C. Stop | Conclude that the market is served and too small | A legitimate outcome if interviews show that grocers who tried pDaftar are satisfied, or that PROB-001 is not their main problem. |

Recommended positioning statement: *for mahalla grocers who sell on credit, a Telegram ledger where the customer confirms every debt, so less is disputed and more is repaid.*

Conditions that should stop the project rather than be worked around:

1. Interviews show grocers do not rank unpaid credit among their main problems.
2. Grocers already using a competitor report that it solves the problem.
3. Customers refuse to acknowledge debts digitally, or shopkeepers will not ask them to.

## Pricing

| Reference | Price |
|---|---|
| pDaftar free tier | 0, unlimited time (EVID-021) |
| pDaftar entry paid tier | About 23,500 UZS a month (EVID-021) |
| Mafin entry tier | 36,000 UZS a month (EVID-022) |
| SMS reminder | 160 to 360 UZS each (EVID-021, EVID-022) |
| International precedent | Free ledger; revenue sought from financial services, with losses persisting (EVID-007, EVID-029, EVID-030) |

Recommended direction, not a price list:

- **The core must be free**: recording, customer acknowledgement, and Telegram reminders. A free incumbent tier makes anything else a non-starter.
- **A paid tier in the range the market already accepts**, roughly 20,000 to 40,000 UZS a month, for things a growing shop needs: several sellers, history and reports, SMS fallback for customers without Telegram at pass-through cost.
- **No revenue from lending or payments** in this product, consistent with the vision (EVID-009).
- Willingness to pay is still untested (EVID-013). Do not build billing before the interviews report what grocers say they would pay.

Payment collection is itself an open issue: subscriptions sold inside a Telegram bot may have to use Telegram Stars (EVID-028), which would complicate paying in UZS through local payment systems.

## Demand signals

| Signal | Direction | Strength |
|---|---|---|
| pDaftar's claimed 5,000+ daily businesses (EVID-020) | Positive: businesses in Uzbekistan do adopt digital ledgers | Medium; self-reported, all business types |
| Several independent local products built for the same need (EVID-004, EVID-005, EVID-022) | Positive for the problem, negative for differentiation | Medium |
| Mafin's 3,600 installations in about eight years (EVID-022) | Negative for paid-only models | Medium |
| Press accounts of shops failing over unpaid credit (EVID-014) | Positive for PROB-001 | Low; anecdotal |
| Loss-making ledger apps at scale abroad (EVID-029, EVID-030) | Negative for revenue potential | Low to medium |
| Shopkeeper interviews | None yet | Not available |

## Regulatory / market risks

| Risk | Detail | Severity |
|---|---|---|
| Personal data law | Customer names, phones, and debts are personal data. Storage must be on servers located in Uzbekistan, the database registered before processing, and consent must meet formal content rules (EVID-027). The acknowledgement step could double as consent, but that is an agent inference and needs a lawyer's confirmation. | High |
| Hosting constraint | Localization rules out the usual foreign cloud defaults for the primary data store (EVID-027). This is an input to the architecture stage. | Medium |
| Telegram payment policy | Digital services sold through bots may be required to use Telegram Stars (EVID-028). | Medium |
| Platform dependence | Access to or rules of Telegram can change; the product has no other channel in its first version. | Medium |
| Drift into regulated credit | Any feature that looks like lending, interest, or scoring across shops would enter licensed territory (EVID-009, EVID-010). Informal shop credit itself is outside the regulator's current focus (EVID-018). | Low while scope holds |
| Incumbent response | pDaftar can copy acknowledgement and grocer-specific flows. | High |
| Small revenue pool | See the illustrative ceiling in Market definition. | High |

## Evidence register

Added in this stage: EVID-020 to EVID-030 inclusive. Reused: EVID-002, EVID-004, EVID-005, EVID-007, EVID-009, EVID-010, EVID-013, EVID-014, EVID-015, EVID-018 and EVID-019 (see `docs/evidence/`).

Correction to earlier work: EVID-003 described pDaftar as an iOS app from its App Store listing only. EVID-020 supersedes that picture. Idea Selection scored candidate B's differentiation at 2 out of 5 on the earlier, incomplete view; on current evidence it would be lower.

Limits: competitor reach figures are self-reported; several figures come from search-result summaries and not from the primary page (EVID-024, EVID-029); no competitor product was installed and tested hands-on; legal conclusions come from law-firm summaries, not from the statute text or from counsel.

## Assumptions

- A grocer-only product can be meaningfully faster and simpler than a general-purpose ledger.
- Customer acknowledgement reduces disputes and improves repayment. No evidence supports or refutes this (EVID-019).
- Grocers will ask customers to acknowledge debts, and customers will do it.
- pDaftar's claimed usage is mostly outside the grocery segment. Not verified.
- Carried forward and untested: EVID-012, EVID-013, and the founder-context assumptions.

## Open questions

1. How many grocery shops in Uzbekistan sell on credit?
2. Do grocers who tried pDaftar or Mafin still use them, and if they stopped, why?
3. Does pDaftar support customer confirmation of each entry? A hands-on test would answer this.
4. Does a subscription to this kind of tool fall under Telegram's Stars requirement?
5. What exactly do registration and localization require of a one-person company, and what are the penalties?
6. Is there a non-subscription revenue source that stays within the vision's limits?

## Decisions / approvals

| Record | Subject | Status |
|---|---|---|
| DEC-003 / APR-003 | Primary user and problem, provisional | Approved 2026-10-06 |
| DEC-004 | Positioning option B (grocer-only, acknowledgement-first) and pricing direction (free core, paid tier at 20,000 to 40,000 UZS a month), with the three stop conditions | Approved 2026-10-06 |

The founder chose to **continue with positioning B to the PRD** on 2026-10-06. The agent's recommendation, to pause until pDaftar had been tested hands-on and shopkeepers interviewed, was not taken.

Consequence accepted with this choice: the PRD is written against a differentiation that is thin and unproven. The three stop conditions under Positioning remain in force, and the two checks remain the cheapest way to test them.
