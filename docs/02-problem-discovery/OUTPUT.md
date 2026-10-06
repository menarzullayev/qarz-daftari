# Problem Discovery

Status: desk research complete; field validation not yet done; awaiting human decision (DEC-003). Prepared 2026-10-06.
Upstream: `docs/01-vision/OUTPUT.md` (approved, DEC-002 / APR-002).

**What this document is and is not.** Everything below comes from published sources and reasoning. Nobody has yet spoken to a shopkeeper for this project. The stage contract asks for validated problem statements; these are evidence-ranked hypotheses plus the plan to validate them (`interview-guide.md`).

## Target users

| Role | Who | Status |
|---|---|---|
| **Primary user** | Owner-operator of a mahalla grocery shop who sells to regular customers on credit and personally decides who gets credit | Proposed, pending DEC-003 |
| Secondary user | The credit customer: a household buyer who takes goods now and pays later | Proposed |
| Possible third user | Family member or assistant who serves customers when the owner is away | Assumption; affects who can record and who can grant credit |

Customer segments suggested by the sources, none of them measured:

- Regulars who pay on payday or pension day. This is the relationship the product should protect.
- People who could pay but prefer credit (EVID-014).
- Serial debtors whose names appear in several shops' ledgers at once (EVID-014).

Not target users: supermarkets and chains, organized installment and BNPL sellers (EVID-018), wholesalers.

## Problems / pain points

| ID | Problem | Evidence | Strength |
|---|---|---|---|
| PROB-001 | Credit is repaid late or not at all; the shopkeeper absorbs the loss or chases it in person | EVID-001 and EVID-014 (Uzbekistan, anecdotal: one shopkeeper recovered about a tenth, another burned his ledgers); EVID-016 (Kazakhstan, political statement) | Consistent across sources, but no measurement for Uzbekistan |
| PROB-002 | The ledger is one-sided and inconsistent, so amounts are forgotten or disputed | EVID-017 (a grocer recording product names instead of amounts and charging later prices); EVID-015 (only about half of surveyed Mumbai owners keep written records) | Indirect |
| PROB-003 | Chasing repayment is costly in time and in relationships | EVID-015 (about 8 hours a week tracking and enforcing, Mumbai); EVID-014 (home visits) | Time cost supported abroad; social cost not evidenced |
| PROB-004 | The shopkeeper lacks a running view of total outstanding and overdue credit | EVID-016 (debt can exceed half of working capital, Kazakhstan) | Weak; inferred |

Observations from the research that do not fit the four problem statements and should be tested in interviews:

- **Credit is a sales tool, not only a risk.** In the Mumbai study, the 12% of customers who received credit produced over 30% of revenue, and 78% of them shopped only at that store (EVID-015). A shopkeeper may therefore be unable to simply refuse credit, and a product that makes credit feel adversarial could cost the shop its best customers.
- **Serial debtors move between shops** (EVID-014). This is the problem that parked candidate C addresses; the vision keeps it out of scope.
- **Inflation erodes old debts** (EVID-014), which raises the cost of every week of delay.
- **Working capital is squeezed** between customers who pay late and suppliers who want prepayment (EVID-016, Kazakhstan).

## Jobs to be done / use cases

Shopkeeper:

1. When a regular asks to take goods on credit during a busy moment, I want to record it in seconds, so the queue keeps moving and nothing is forgotten. (PROB-002, METRIC-004)
2. When a customer asks for more credit, I want to see what they already owe and how they have paid before, so I can decide without guessing. (PROB-004)
3. When a customer pays part of a debt, I want the balance updated in a way we both accept, so there is no argument later. (PROB-002, METRIC-003)
4. When payday or pension day comes, I want customers reminded without my having to confront them, so I get paid and keep the relationship. (PROB-001, PROB-003, METRIC-001)
5. At the end of the week, I want to know how much is owed in total and what is overdue, so I know whether I can pay my supplier. (PROB-004)

Customer:

6. When I buy on credit, I want to know exactly what I owe and to which shop, so I am not surprised or overcharged. (PROB-002)
7. When I pay, I want proof that it was recorded. (PROB-002)

## Current alternatives

| Alternative | How it serves the jobs | Where it falls short |
|---|---|---|
| Paper notebook | Fast, free, familiar | One-sided, no reminders, no totals, can be lost or disputed (EVID-001, EVID-017) |
| Memory and verbal agreement | Zero effort | Reported as the starting point before defaults forced written ledgers (EVID-014) |
| Refusing credit | Removes the risk | Likely loses the highest-value customers (EVID-015); prevalence in Uzbekistan not evidenced |
| Collecting in person | Sometimes works | Time-consuming, socially costly, low recovery in the one account available (EVID-014) |
| Local ledger apps | Digital record, SMS reminders, some with customer confirmation (EVID-003, EVID-004, EVID-005) | Adoption unknown; whether they improve repayment is unproven (EVID-019) |
| POS systems with a credit module | Full sales record | Heavy for a small shop (EVID-005) |

## Problem severity

| Problem | Frequency | Impact when it happens | Confidence in this rating |
|---|---|---|---|
| PROB-001 | Unknown for Uzbekistan; described as routine in EVID-014 | Potentially fatal to the shop (EVID-014); large share of working capital at risk (EVID-016) | Low to medium |
| PROB-002 | Unknown | Disputes and write-offs; also a religious and ethical concern for some customers (EVID-017) | Low |
| PROB-003 | About 8 hours a week in the one study that measured it (EVID-015, India) | Owner time and strained relationships | Low for Uzbekistan |
| PROB-004 | Unknown | Supplier payment difficulty (EVID-016) | Low |

PROB-001 is the proposed primary problem. The ranking rests on consistency between sources, not on measurement.

## Success metrics

Product metrics carried from the vision, with what Stage 02 learned about each:

| Metric | Finding |
|---|---|
| METRIC-001 Repayment within term | No baseline exists. Interviews must ask shopkeepers to estimate the share repaid on time and the share written off. |
| METRIC-002 Weekly active shops | Depends on entry speed (METRIC-004). |
| METRIC-003 Customer confirmation rate | Whether confirmation improves repayment is unproven (EVID-019). |
| METRIC-004 Time to record a sale | The notebook is the benchmark; measure it during shop visits. |
| METRIC-005 Revenue per shop | Willingness to pay is still an assumption (EVID-013). |

Validation criteria for this stage, proposed by the agent and open to change by the founder. They decide whether the direction holds after interviews with 15 to 20 shopkeepers:

| Question | Direction holds if | Otherwise |
|---|---|---|
| Is non-repayment a top problem? (EVID-012) | Most shopkeepers name late or unpaid credit among their top three problems without being prompted | Revisit the positioning in the vision |
| Is the loss material? | Typical outstanding credit is a meaningful share of weekly turnover by the shopkeeper's own estimate | Product is a convenience, not a necessity |
| Would they change behavior? | A clear majority say they would record sales on a phone if it took no longer than the notebook | Entry speed or the device is a blocker |
| Can customers be reached? | Shopkeepers estimate that most credit customers use Telegram | A fallback channel becomes a core requirement |
| Would they pay? (EVID-013) | Some shopkeepers name a monthly price they would accept | Revenue model must change |

## Evidence

Records added in this stage: EVID-014 to EVID-019. Records reused from earlier stages: EVID-001, EVID-003, EVID-004, EVID-005, EVID-012 and EVID-013 (see `docs/evidence/`).

Limits of this evidence:

- The only Uzbekistan-specific sources are two press items (EVID-014, EVID-017), read through machine summaries.
- The only quantified source is one study in Mumbai (EVID-015), read through a university blog summary; whether its figures transfer to Uzbekistan is unknown.
- EVID-016 is a politician's statement about rural Kazakhstan.
- No source measures whether digital ledgers or reminders improve repayment (EVID-019).

## Assumptions

- The Mumbai and Kazakhstan findings indicate the direction of the situation in Uzbekistan. This is an inference from similar practice, not a fact.
- The owner is the person who records sales and decides on credit.
- Customers will accept a digital record of their debt and will not see it as distrust.
- Carried forward and still untested: EVID-012 (collection is the main pain) and EVID-013 (willingness to pay).
- Founder context and the project name remain unconfirmed, as noted in the vision.

## Open questions

1. What share of a typical shop's sales is on credit, and how much is never repaid?
2. Do shopkeepers want to give less credit, or to keep giving it and lose less?
3. Who physically records the sale, and on what device?
4. Do customers see a reminder from a bot as less awkward than one from the shopkeeper, or as more offensive?
5. How do shopkeepers who refuse credit fare against those who give it?
6. Are any of the existing local apps actually used in shops, and if they were tried and dropped, why?

## Decisions / approvals

| Record | Subject | Status |
|---|---|---|
| DEC-002 / APR-002 | Strategic direction | Approved 2026-10-06 |
| DEC-003 | Primary user is the shop owner; primary problem is PROB-001; both provisional until interviews are done | Approval pending |

The founder is asked to choose how this stage closes:

- **Option 1 (recommended): hold the stage until interviews are done.** Run the interviews in `interview-guide.md`, record the results as evidence, then pass the stage. This follows the product principle "evidence before build".
- **Option 2: approve provisionally and continue.** Pass the stage now on desk research, start Market Research in parallel, and accept that later stages may need rework if interviews contradict the primary problem (PROB-001).
