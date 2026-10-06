# Vision

Status: strategic direction approved by the founder on 2026-10-06 (DEC-002 / APR-002).
Upstream: `docs/idea-selection/OUTPUT.md` (candidate B approved, DEC-001 / APR-001).

## Mission

Help neighborhood shopkeepers in Uzbekistan get paid back for what they sell on credit, without putting the relationship with their customers at risk.

## Vision

Selling on credit ("nasiya") stays what it is today: a favor between neighbors, based on trust. What changes is that every such sale becomes a record both sides have seen and acknowledged, and the shopkeeper no longer has to choose between losing the money and confronting a neighbor.

In three to five years, a mahalla shopkeeper opens Telegram instead of a notebook, a customer knows exactly what they owe and to whom, and an unpaid debt is the exception that both sides can see coming, not a silent loss discovered months later.

## Problems addressed

These problem statements are the starting hypotheses for Stage 02. Only PROB-001 has any external support, and that support is a single anecdotal account.

| ID | Problem | Support |
|---|---|---|
| PROB-001 | Goods sold on credit are repaid late or not at all; the shopkeeper absorbs the loss or chases the debt door to door. | EVID-001 (anecdotal) |
| PROB-002 | The paper ledger is one-sided: the customer never sees or acknowledges the entry, so amounts can be forgotten or disputed. | Agent inference from EVID-001 |
| PROB-003 | Asking a neighbor for money is socially costly, so reminders are delayed or avoided. | Assumption, not evidenced |
| PROB-004 | The shopkeeper has no running view of total outstanding credit or of which debts are overdue. | Assumption, not evidenced |

## Long-term goals

Each goal is tied to a metric. No baselines exist yet (see the evidence gaps in Idea Selection), so no numeric targets are set here; Stage 02 must establish baselines before targets are committed.

| ID | Goal | Metric |
|---|---|---|
| METRIC-001 | Shopkeepers recover more of what they lend. This is the north-star outcome. | Share of credit value repaid within the agreed term, per shop, compared with the shop's own baseline |
| METRIC-002 | The product replaces the notebook rather than sitting beside it. | Shops that record credit sales in the product every week |
| METRIC-003 | Debts are mutually acknowledged. | Share of credit entries confirmed by the customer |
| METRIC-004 | Recording a sale is not slower than writing it down. | Median time to record one credit sale |
| METRIC-005 | The product sustains itself financially. | Revenue per active shop; the revenue model itself is an open question |

## Product principles

1. **Repayment over bookkeeping.** A feature earns its place by helping the shopkeeper get paid, not by making the ledger more complete.
2. **Faster than the notebook.** If recording a sale takes longer than scribbling it, shopkeepers will stop during the first rush hour.
3. **Useful alone, better together.** The product must work when only the shopkeeper uses it. Customer participation is an upgrade, never a precondition.
4. **Protect the relationship.** The shopkeeper controls whether, when, and how a reminder goes out. No public shaming, no pressure tactics.
5. **A record, not a lender.** The product does not lend, hold, or move money. Lending needs a Central Bank license (EVID-009) and is out of scope.
6. **A customer's debt belongs to that shop relationship.** Debt data is not shared across shops. Cross-shop reputation (candidate C) stays parked until its legal basis is established.
7. **Telegram-first.** Start where shopkeepers and customers are assumed to already be (EVID-011), and avoid app-install friction. Whether Telegram alone is enough is an open question.
8. **Evidence before build.** Demand is unproven; assumptions are tested with shopkeepers before scope is committed.

## Strategic assumptions

| Assumption | Basis | If wrong |
|---|---|---|
| Collection, not recording, is the main pain | EVID-012 (assumption) | Positioning shifts toward bookkeeping, where competition is strongest (EVID-003, EVID-005) |
| Shopkeepers will pay for better repayment | EVID-013 (assumption) | A different revenue model is needed (EVID-007 shows the Indian precedent is a free ledger) |
| Shopkeepers and enough of their credit customers use Telegram | Not supported at this granularity by EVID-011 | An SMS or other fallback becomes a core requirement, with per-message cost |
| Customer confirmation makes debts more likely to be repaid | Not evidenced | The two-sided design loses its main advantage over one-sided ledgers |
| Existing local products have not won the market | No adoption figures found (EVID-005) | Differentiation through distribution becomes much harder |
| Founder context: solo founder with AI agents, Telegram bot experience, no dedicated budget | Agent inference, not confirmed by the founder | Scope and timeline assumptions in later stages must be revised |

## Out of scope

- Lending, financing, or any handling of money (candidate D).
- Sharing debtor information between shops (candidate C).
- Point-of-sale, inventory, or accounting (candidate E).
- Markets outside Uzbekistan.

## Evidence

This stage adds no new evidence. It relies on the records from Idea Selection: EVID-001, EVID-003, EVID-005, EVID-007, EVID-009, EVID-011, EVID-012, EVID-013. The mission and vision are statements of intent, not factual claims; every factual claim above cites a record or is marked as an assumption.

## Open questions

1. How will the product make money, given that the closest international precedent gives the ledger away?
2. What share of credit customers can be reached through Telegram, and what happens for those who cannot?
3. Which personal data obligations apply to storing customers' names, phone numbers, and debts, and where must that data be hosted?
4. How will the first shops be reached: door to door, wholesalers, mahalla networks, or Telegram channels?
5. Which city or district is the first market?
6. Is "Qarz Daftari" the product name or only a working title?
7. Are the founder-context assumptions correct?

Questions 1 to 5 are inputs to Stages 02 and 03. Questions 6 and 7 need an answer from the founder.

## Decisions / approvals

| Record | Subject | Status |
|---|---|---|
| DEC-001 / APR-001 | Candidate B enters the pipeline | Approved 2026-10-06 |
| DEC-002 / APR-002 | Mission, vision, and strategic direction as stated in this document | Approved 2026-10-06 |
