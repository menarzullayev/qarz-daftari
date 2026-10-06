# Founder Hypotheses from the Interview Guide

Date: 2026-10-06. Record: EVID-034.

**What this is.** The founder answered the twenty questions of `interview-guide.md` himself, choosing or writing the answer he considers most likely for a typical mahalla grocery shop. These are one informed person's expectations. They are **not** shopkeeper interviews, they do not satisfy the validation gate before milestone M3, and they must not be cited as field evidence. Their value is that they state what the founder expects to hear, so real interviews can confirm or contradict specific points.

The agent offered a suggested answer for each question. Where the founder chose something else, it is marked.

## Answers

| No. | Question | Founder's answer | Agent's suggestion |
|---|---|---|---|
| 1 | Shop age and daily customers | 3+ years, 50 to 150 customers a day | Same |
| 2 | Top problems | Unpaid credit; shortage of working capital; competition from supermarkets | Unpaid credit |
| 3 | Who gets credit | Almost anyone who asks | Only known regulars |
| 4 | Number of debtors and total owed | 100+ people, more than 50 million UZS | 20 to 50 people, 5 to 20 million UZS |
| 5 | Debt relative to weekly turnover | About one week of turnover | More than one week |
| 6 | How credit is recorded | Name, date, amount **and the list of goods**; if the buyer says roughly when they will repay, that is written too (own words) | Name, date, amount |
| 7 | Time to record; behavior in a queue | 30 to 60 seconds; postponed when there is a queue | Same |
| 8 | Who records when the owner is away | A family member **or a hired seller** (own words) | A family member |
| 9 | Share repaid on time | The shopkeeper does not know | About half |
| 10 | Debts never repaid | The shopkeeper does not know exactly | A few people a year, 5 to 10% |
| 11 | How reminders happen today | All of: when the customer comes in; phone or message; visiting the home or through relatives; doing nothing and waiting | When the customer comes in |
| 12 | Is reminding awkward | Awkward, but customers understand | Awkward, and some customers were offended |
| 13 | Disputes over amounts | Sometimes, a few times a year | Same |
| 14 | Why not stop giving credit | Both: customers would leave, and neighborly obligation | Customers would leave |
| 15 | Experience with ledger apps | Have not heard of them, have not tried | Same |
| 16 | Telegram use | Shopkeeper yes; more than half of credit customers | Same |
| 17 | Reaction to the concept | Wanted; reminders appeal; worry about recording speed | Same |
| 18 | How customers would react | **The customer should not have to confirm anything.** When taking goods on credit in the shop, the customer opens Telegram and starts the bot. Seeing the exact debt is good and is not taken as distrust; as a rule of the shop it is convenient (own words) | Most accept, some will not link |
| 19 | If slower than the notebook | Abandoned | Same |
| 20 | Monthly price | 100,000 UZS is a good price (own words) | 20,000 to 40,000 UZS |

## Where these answers conflict with approved documents

Nothing below has been changed in the approved documents. Each point needs either confirmation from real interviews or an explicit decision by the founder.

| Point | Founder's hypothesis | Approved position | Affected |
|---|---|---|---|
| A | Customers should not confirm anything; they only link and see their debt | Positioning is "acknowledgement-first"; per-entry confirmation and dispute are the stated differentiation | DEC-004, DEC-005; PRD features for acknowledgement; Domain Model acknowledgement rules; Technical Specification |
| B | Shops record the list of goods | An entry holds an amount and an optional note of at most 120 characters; goods are out of scope | PRD scope and recording requirements; Domain Model boundary "no goods" |
| C | The repayment date is what the customer promises at the time of sale | Default due date is one day of the month per shop, overridable per entry | PRD due date requirement; Domain Model due date rule |
| D | Family members and hired sellers record sales | One Telegram account per shop; multiple sellers deferred as a later feature | PRD scope; Domain Model boundary "no staff" |
| E | 100+ debtors and more than 50 million UZS is typical | Pilot sized for small ledgers; chat-only lists | Chat-only decision record; overview rendering |
| F | Credit goes to almost anyone who asks | Model assumes known regulars; no credit limit in the MVP | PRD scope; relevance of the parked cross-shop idea |
| G | Shopkeepers do not know their repayment or loss rates | Repayment improvement is the north-star metric, with baselines expected from interviews | Vision metrics; pilot measurement must create its own baseline |
| H | 100,000 UZS a month is acceptable | Pricing direction of 20,000 to 40,000 UZS | DEC-004 |

Point A is the most consequential. Without per-entry confirmation, the product's remaining differences from the incumbent (EVID-020) are grocer focus, linking at the counter, and free Telegram notifications, all of which the incumbent already offers in some form.

## Points that agree with the approved direction

- Unpaid credit is expected among the top problems (question 2), in line with EVID-012.
- Recording speed is decisive (questions 7, 17, 19), in line with the vision principle "faster than the notebook". Question 7 adds a design hint: record the amount at once and add the goods later.
- Reminders are wanted and are not expected to offend (questions 12, 17).
- Shopkeepers are not expected to know the competing apps (question 15), which points to distribution, not features, as the obstacle.
- Telegram reaches the shopkeeper and most credit customers (question 16).

## What real interviews should test first

1. Do customers and shopkeepers want confirmation, or only visibility? (Point A)
2. Is the list of goods necessary, or is it written only because the notebook invites it? (Point B)
3. How often is a hired seller or family member the one recording? (Point D)
4. Would a shopkeeper pay 100,000 UZS a month, when asked without a prompt? (Point H)
