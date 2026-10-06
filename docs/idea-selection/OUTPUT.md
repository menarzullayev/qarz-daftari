# Idea Selection

Status: awaiting human decision. Prepared 2026-10-06. Evidence records live in `docs/evidence/`.

## Candidate ideas

All candidates address the same observed practice: neighborhood (mahalla) shops in Uzbekistan sell to regular customers on credit ("nasiya") and track it in a paper ledger (EVID-001).

| ID | Candidate | One-line description |
|---|---|---|
| A | Shopkeeper ledger app | Native mobile app where the shopkeeper records debts and payments and sends SMS reminders. One-sided: the customer never touches the product. |
| B | Telegram-first two-sided ledger | Shopkeeper records debts in a Telegram bot / Mini App; the customer is linked by phone or link, confirms each entry, sees the balance, and receives automatic reminders. |
| C | Shared debtor reputation | Shops in an area share repayment history so a shopkeeper can check a customer before giving credit. |
| D | Ledger plus financing | Use ledger data to lend working capital to shops or to finance customer purchases. |
| E | POS and inventory with credit module | Full point-of-sale and stock system for small shops, with sell-on-credit as one feature. |

## Evaluation criteria

Criteria come from the stage contract. Each is scored 1 (weak) to 5 (strong) with equal weight; equal weighting is an agent choice, not a validated model. "Regulatory safety" is scored so that higher means less regulatory risk.

1. Problem severity
2. Willingness to pay
3. Market potential
4. Differentiation
5. MVP feasibility
6. Distribution
7. Founder advantage
8. Regulatory safety
9. Evidence quality

## Evidence

| ID | Claim (short) | Type | Confidence | Risk |
|---|---|---|---|---|
| EVID-001 | Credit is given on trust, tracked on paper, and repayment is late or refused (one anecdotal account) | FACT | MEDIUM | MEDIUM |
| EVID-002 | Independent small grocers hold 48% of grocery retail sales (snippet only, year unverified) | FACT | LOW | LOW |
| EVID-003 | pDaftar is a live local competitor: ledger, SMS reminders, installments, paid tiers, 179 App Store ratings | FACT | HIGH | LOW |
| EVID-004 | Open-source "Nasiya" SaaS already implements a Telegram bot + Mini App with customer confirmation | FACT | HIGH | LOW |
| EVID-005 | Further local products exist (QARZDAFTAR, Daftar Qarz, daftar.uz, qarz-app, REGOS); no adoption figures found | FACT | MEDIUM | LOW |
| EVID-006 | Khatabook reached 10M monthly active users (2021) and a ~600M USD valuation in India | FACT | HIGH | LOW |
| EVID-007 | Indian ledger apps are free and monetize through financial services | INFERENCE | MEDIUM | MEDIUM |
| EVID-008 | Istanbul Grocers Chamber moved 1,750 grocers toward a digital ledger | FACT | MEDIUM | LOW |
| EVID-009 | Lending in Uzbekistan requires a Central Bank license | FACT | MEDIUM | MEDIUM |
| EVID-010 | Central Bank study: installment purchases can cost up to 52% more; regulator is watching consumer credit | FACT | MEDIUM | MEDIUM |
| EVID-011 | Uzbekistan is second worldwide by number of Telegram channels (2023); no shopkeeper-level usage data | FACT | MEDIUM | LOW |
| EVID-012 | The shopkeeper's main pain is collecting, not recording | ASSUMPTION | LOW | MEDIUM |
| EVID-013 | Shopkeepers will pay a subscription for better repayment | ASSUMPTION | LOW | MEDIUM |

Evidence gaps that no source filled: share of shop revenue sold on credit, average debt size, default rate, number of mahalla shops, and Telegram usage among shopkeepers and their customers.

## Candidate comparison

| Criterion | A | B | C | D | E |
|---|---|---|---|---|---|
| Problem severity | 3 | 4 | 5 | 4 | 3 |
| Willingness to pay | 2 | 3 | 4 | 4 | 3 |
| Market potential | 3 | 3 | 4 | 5 | 3 |
| Differentiation | 1 | 2 | 5 | 4 | 1 |
| MVP feasibility | 5 | 5 | 2 | 1 | 2 |
| Distribution | 2 | 4 | 2 | 2 | 2 |
| Founder advantage | 3 | 4 | 2 | 1 | 2 |
| Regulatory safety | 5 | 5 | 1 | 1 | 5 |
| Evidence quality | 3 | 3 | 1 | 2 | 3 |
| **Total (max 45)** | **27** | **33** | **26** | **24** | **24** |

Scoring rationale:

- **A** only replaces paper with a screen. If EVID-012 holds, it leaves the real pain untouched, and it competes head-on with pDaftar and several clones (EVID-003, EVID-005). SMS reminders also carry a per-message cost.
- **B** attacks collection: a debt the customer has confirmed is harder to dispute, and reminders arrive without the shopkeeper having to confront a neighbor. Telegram removes app-install friction on both sides (EVID-011) and makes reminders free. Differentiation is low because Nasiya already demonstrates this design (EVID-004).
- **C** targets the most severe version of the problem and is the most differentiated, but it means processing and sharing third-party personal financial data without a clear legal basis, and it needs many shops in one area before it is useful. No evidence was gathered on its legality.
- **D** has the largest upside (EVID-006, EVID-007) but requires a Central Bank license (EVID-009), capital, and credit-risk expertise, in a segment the regulator is already scrutinizing (EVID-010). It cannot be an MVP.
- **E** is a large build in a market with established POS vendors (EVID-005), and credit tracking is a minor feature there.

## Leading candidate

**B — Telegram-first two-sided ledger**, positioned around getting paid back rather than around bookkeeping.

Stress test:

| Challenge | Assessment |
|---|---|
| Nasiya already does this, and it is open source | True (EVID-004). The product concept is not a moat. No evidence that Nasiya or any rival has meaningful adoption (EVID-005), so the contest is distribution and retention, not features. |
| Customers may not use Telegram, especially pensioners | Plausible and unmeasured. The design must work when only the shopkeeper uses it, with customer confirmation as an upgrade, and may need an SMS fallback. |
| Entering every sale during a rush is slower than scribbling in a notebook | Serious adoption risk. Entry must take a few seconds; this is a core product requirement, not a detail. |
| Shopkeepers may not pay | Unproven (EVID-013). The Indian precedent is free ledgers monetized elsewhere (EVID-007), which argues against relying on subscriptions alone. |
| Reminders may damage neighborly relationships | Unmeasured. Tone and shopkeeper control over when reminders go out matter. |
| The pain may be recording after all, not collecting | If EVID-012 is wrong, B still covers recording, so the downside is limited. |

## Risks

| Risk | Severity | Note |
|---|---|---|
| No validated demand: every demand claim rests on one anecdote and two assumptions | High | Must be resolved in Problem Discovery through shopkeeper interviews before any build. |
| Weak differentiation against existing local products | High | Requires a distribution plan, not a feature list. |
| Unclear monetization | Medium | Subscription willingness is assumed, not observed. |
| Personal data obligations for storing customers' names, phones, and debts | Medium | Not researched in this stage; legal requirements must be established before the PRD. |
| Platform dependence on Telegram | Medium | Policy or access changes would hit the whole product. |
| Drift toward lending (candidate D) without a license | Medium | Out of scope unless a separate approved decision changes that. |

## Assumptions

- The shopkeeper's main pain is collection rather than record-keeping (EVID-012).
- Shopkeepers will pay for improved repayment (EVID-013).
- Most shopkeepers and a meaningful share of their credit customers use Telegram (not supported at this granularity by EVID-011).
- Founder context: a solo founder working with AI agents, with prior Telegram bot experience, no lending license, and no dedicated budget. This is an agent inference and has not been confirmed by the founder.
- The first market is urban and suburban mahalla grocery shops in Uzbekistan, Uzbek-language first.

## Rejected / parked ideas

| ID | Status | Reason |
|---|---|---|
| A | Rejected | Subsumed by B; undifferentiated against live competitors. |
| C | Parked | Highest potential differentiation, but blocked on legal basis for sharing debtor data and on network density. Revisit once B has shops concentrated in one area. |
| D | Parked | Needs a license, capital, and risk expertise. Revisit only if B produces repayment data at scale. |
| E | Rejected | Large build, crowded market, credit is peripheral. |

## Recommendation

Select **candidate B** and enter Stage 01 with the positioning "help the shopkeeper get paid back". Treat EVID-012 and EVID-013 as the first things to test in Stage 02: interview shopkeepers before committing to scope.

This recommendation has moderate confidence. The comparison between candidates is reasonably robust; the claim that any of them has real demand is not yet supported.

## Human decision

Pending. Decision record: DEC-001 (approval required, status pending).

The founder is asked to decide:

1. Whether candidate B is the idea that enters the pipeline.
2. Whether the founder-context assumptions above (team, budget, experience, first market) are correct.
3. Whether "Qarz Daftari" stays as the working project name.
