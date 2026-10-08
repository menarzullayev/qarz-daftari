# Load test with generated data (S19.1, REQ-N13)

Status: the tooling exists and has been run on a developer machine: one 30-minute run, and one 5-minute
run after later changes of the main branch were merged in (see "Which numbers come from which run"). This is a first signal and a
proof that the script works. **It is not launch criterion 6**: that needs the same run on the staging
server (docs/10-operations/OUTPUT.md, "Test strategy" and launch criterion 6). A laptop-class machine
with PostgreSQL in Docker is not the production server, and nothing here says how the real servers behave.

Targets are those of docs/08-technical-spec/OUTPUT.md, "Performance targets". Design capacity is REQ-N13:
5,000 shops, 500,000 customer accounts, 50 recorded entries a second.

## What was built

Everything is under `backend/loadtest/`; nothing in `backend/src/qarz` imports it.

| Part | What it does |
|---|---|
| `dataset.py` | Generates shops in memory, deterministically for a profile, a seed and an anchor instant. Profile `full` is 5,000 shops and 500,000 customers, one shop of them at the NFR-005 size (2,000 customers, 200,000 entries). |
| `load.py` | Creates a database named `qd_load_*`, builds it with the real migrations, and copies the rows in as the migration owner, 200 shops per transaction. Every constraint and trigger checks them, except one trigger (below). |
| `invariants.py` | The ledger rules, checked on generated rows in memory (tests) and on the loaded database (after every load, which fails if one is broken). |
| `drive.py` | Starts the production wiring (`uvicorn qarz.interface.asgi:build --factory`) as separate processes connected as `qd_app`, sends an open load over HTTP, and prints the table below. |
| `report.py` | Percentiles and the comparison with the targets. |
| `docker-compose.yml` | A PostgreSQL 16 for the run, with its data on a volume. |

Tests: `backend/tests/test_loadtest_dataset.py` (no database) and `backend/tests/db/test_loadtest_load.py`
(loads a tiny set into a database of its own and drives the real application for a few seconds). They run
in CI with the rest and take about ten seconds together.

## How to run it

From `backend/`, with the development dependencies installed:

```sh
docker compose -p qd-loadtest -f loadtest/docker-compose.yml up -d
export QD_LOAD_ADMIN_URL=postgresql://postgres:postgres@127.0.0.1:54330/postgres

# about 8 minutes and 3.2 GB for the full profile
PYTHONPATH=src python -m loadtest.load --database qd_load_full --profile full --seed 1

# NFR-006 asks for 30 minutes
PYTHONPATH=src python -m loadtest.drive --database qd_load_full --duration 1800 --out results.json

docker compose -p qd-loadtest -f loadtest/docker-compose.yml down -v    # removes the data
```

`PYTHONPATH=src` is needed only where the package is not installed from this checkout. Profiles `tenth`
and `tiny` are smaller.
`--base-url` drives a server that is already running instead of starting one; that is how the staging run
is meant to be done. `--now` fixes the anchor instant so that two loads give identical rows.

Safety: the loader refuses any database whose name does not start with `qd_load_`. Generated staff
sessions have predictable tokens, so such a database must never be reachable from outside.

## What the load is

- **Data**: 5,000 shops; 500,000 customers; 3,685,952 ledger entries spread over 400 days; 2,608,378
  promises; 1,096,948 goods lines; 9,006 staff members with sessions; 48,856 customers linked to a Telegram
  user (so recording for them queues a notification); 125,275 catalog items; one activity row per entry.
  3.2 GB with indexes. One shop holds 2,000 customers and 200,000 entries; its accounts have 71 entries at the
  median, 491 at the 99th percentile and 1,153 at most. The others average 100 customers and about 7 entries a customer.
- **Server**: four API processes, each with the application's default pool of at most 10 connections,
  connected as `qd_app` with row-level security in force. Authentication is the real one: a bearer token
  resolved through `user_session` on every request, and the webhook secret for chat messages.
- **Load**, open (Poisson arrivals, a request is sent at its time whether or not earlier ones answered):
  50 recorded entries a second across all shops and 1 more in the large shop, as 30% chat credit sales,
  10% chat payments, 25% API credit sales, 20% API payments, 15% credit sales with ten goods lines;
  48 reads a second across the other shops and 2 in the large shop (customer list 15%, search by name
  30%, search by phone 5%, overview 15%, debtors 10%, overdue debtors 5%, one customer's page 20%);
  one report every 30 seconds in the large shop. About 101 requests a second in all, for 30 minutes.
- **Response time** is from sending the request to the last byte of the answer, measured in the driver.

Why a separate server process and not the application inside the driver's process (`httpx.ASGITransport`):
in one process the driver and the application share one event loop and one core, each request waits for
the driver's own work, and no HTTP is parsed. Separate processes cost a loopback round trip, which a real
client pays too. The in-process form is used only in the test suite, where the point is correctness.

## Which numbers come from which run

| Run | Code and schema | Length | What it is used for here |
|---|---|---|---|
| A, "first run" | Main as of pull request 37, before the fixes of this story | 90 seconds | "What the first run found" |
| B, "the 30-minute run" | The same with this story's fixes: migrations up to 0018, plus 0020. No rate limits and no statement timeout existed yet. | 30 minutes | The results table and every figure in "Targets", unless run C is named |
| C, "the confirmation run" | Main as of pull request 45 merged in (migrations 0011, 0019, 0021, then 0020; rate limits; the security fixes) and the statement timeout of this story. Default rate limits and the default 5-second statement timeout in force. A database loaded afresh at that schema. | 5 minutes | "The confirmation run on the merged code" only |

Run B was not repeated after the merge. Run C is too short to replace it: it shows that nothing broke and
roughly where the figures are, not a 30-minute result.

## The machine it ran on

7 October 2026 (Tashkent date). The same machine and settings for all three runs.

| | |
|---|---|
| Machine | Desktop, Intel Core i7-13700F (16 cores, 24 threads), 32 GB RAM, one SSD, Windows 11 Pro |
| PostgreSQL | 16.15 (`postgres:16-alpine`) in Docker Desktop 29.7 (a Linux VM given 8 CPUs and 11.7 GB); container limited to 4 CPUs and 6 GB; `shared_buffers` 2 GB, `work_mem` 16 MB, `effective_cache_size` 4 GB; data on a Docker volume; `fsync` on |
| API | 4 uvicorn processes on the Windows host, Python 3.12.10, plain asyncio loop and `h11` (no `uvloop`, no `httptools`); not inside the container's limits |
| Driver | One Python process on the same host |
| Not controlled | Other containers and other test runs were using the same machine during the run |

How this differs from production (docs/06-architecture: primary with 4 cores and 8 GB for proxy, API,
worker and PostgreSQL together): here the API and the driver had their own cores outside the database's
four, which flatters the result; every query crossed Docker Desktop's port forwarding, which does the
opposite; there was no proxy, no TLS, no worker sending the outbox, no replication to a standby, and no
network between client and server.

## Results (run B)

30 minutes, 182,558 requests, 92,597 entries recorded (51.4 a second), no request abandoned.

| Operation | Shop | Calls | p50 ms | p95 ms | p99 ms | max ms | Refused | Errors | Target (p95) | Result |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| chat_credit | large | 581 | 44 | 56 | 83 | 170 | 0 | 0 | 500 ms (NFR-001) | met |
| chat_credit | other | 27,338 | 41 | 53 | 71 | 718 | 0 | 0 | 500 ms (NFR-001) | met |
| chat_payment | large | 186 | 44 | 56 | 102 | 369 | 0 | 0 | 500 ms (NFR-001) | met |
| chat_payment | other | 9,024 | 40 | 52 | 64 | 742 | 0 | 1 | 500 ms (NFR-001) | met |
| api_credit | large | 482 | 28 | 40 | 51 | 76 | 0 | 0 | - | no target |
| api_credit | other | 22,798 | 26 | 37 | 48 | 746 | 0 | 0 | - | no target |
| api_payment | large | 367 | 27 | 38 | 60 | 96 | 0 | 0 | - | no target |
| api_payment | other | 18,028 | 25 | 35 | 46 | 740 | 0 | 0 | - | no target |
| api_itemized_10 | large | 285 | 38 | 53 | 60 | 81 | 0 | 0 | 400 ms (NFR-009) | met |
| api_itemized_10 | other | 13,508 | 37 | 49 | 60 | 754 | 0 | 0 | 400 ms (NFR-009) | met |
| customers_list | large | 542 | 30 | 39 | 48 | 318 | 0 | 0 | 300 ms (NFR-005) | met |
| customers_list | other | 12,941 | 18 | 27 | 35 | 738 | 0 | 0 | 300 ms (NFR-005) | met |
| customers_search | large | 1,104 | 27 | 37 | 45 | 259 | 0 | 0 | 300 ms (NFR-005) | met |
| customers_search | other | 25,803 | 17 | 26 | 33 | 492 | 0 | 0 | 300 ms (NFR-005) | met |
| customers_search_phone | large | 180 | 19 | 28 | 32 | 35 | 0 | 0 | 300 ms (NFR-005) | met |
| customers_search_phone | other | 4,389 | 17 | 26 | 33 | 391 | 0 | 0 | 300 ms (NFR-005) | met |
| overview | large | 525 | 264 | 349 | 384 | 841 | 0 | 0 | 300 ms (NFR-005) | NOT met |
| overview | other | 12,998 | 19 | 28 | 37 | 700 | 0 | 0 | 300 ms (NFR-005) | met |
| debtors | large | 329 | 322 | 409 | 454 | 523 | 0 | 0 | 300 ms (NFR-005) | NOT met |
| debtors | other | 8,519 | 21 | 31 | 39 | 541 | 0 | 0 | 300 ms (NFR-005) | met |
| debtors_overdue | large | 180 | 319 | 414 | 434 | 451 | 0 | 0 | 300 ms (NFR-005) | NOT met |
| debtors_overdue | other | 4,272 | 21 | 31 | 40 | 613 | 0 | 0 | 300 ms (NFR-005) | met |
| customer_page | large | 724 | 21 | 31 | 38 | 661 | 0 | 0 | - | no target |
| customer_page | other | 17,389 | 19 | 28 | 37 | 697 | 0 | 0 | - | no target |
| report_period_year | large | 29 | 1,797 | 1,920 | 1,959 | 1,959 | 0 | 0 | 5,000 ms (NFR-011) | met |
| report_overdue | large | 36 | 315 | 377 | 382 | 382 | 0 | 0 | - | no target |

"large" is the shop of the NFR-005 size; "other" is the 4,999 others together. A result is "met" when at
most one call in twenty was slower than the target, counting every failed call as a slow one.

Checks on the run itself:

- Every entry the server acknowledged is in the ledger: 55,468 API calls answered 201 and 37,129 chat
  messages answered 200 against 92,597 new entries. A chat message answers 200 whatever it did, so this
  count is what shows that the chat figures measure recording and not a question back to the seller.
- 46,478 notifications and chat replies were queued in the outbox. None was sent: no worker ran.
- The ledger rules were checked again on the database after the run and none was broken.
- The one error was a connection that broke while a chat message was being read (`ReadError`), in
  182,558 requests. No call was refused.
- The driver kept up: 99% of requests were sent within 24 ms of their scheduled instant. The longest
  delay was 658 ms, and several operations show a maximum near 700 to 750 ms at once, which looks like
  one stall of the machine or the database rather than a slow operation. The cause was not found.

## The confirmation run on the merged code (run C)

5 minutes at the same rates, 30,434 requests, 15,337 entries recorded (51.1 a second). No request failed,
none was refused, **none was answered 429** and none was cancelled by the statement timeout. Every
acknowledged write is in the ledger (9,235 API calls and 6,102 chat messages against 15,337 new entries).

| Operation | Shop | Calls | p50 ms | p95 ms | p99 ms | Target (p95) | Result |
|---|---|---:|---:|---:|---:|---|---|
| chat_credit | large / other | 92 / 4,446 | 56 / 52 | 82 / 69 | 211 / 110 | 500 ms (NFR-001) | met |
| chat_payment | large / other | 24 / 1,540 | 55 / 50 | 72 / 66 | 97 / 95 | 500 ms (NFR-001) | met |
| api_credit | large / other | 77 / 3,864 | 37 / 34 | 46 / 48 | 54 / 63 | - | no target |
| api_payment | large / other | 56 / 3,034 | 35 / 32 | 51 / 46 | 55 / 65 | - | no target |
| api_itemized_10 | large / other | 49 / 2,155 | 52 / 47 | 69 / 64 | 84 / 96 | 400 ms (NFR-009) | met |
| customers_list | large / other | 88 / 2,158 | 36 / 23 | 49 / 33 | 279 / 45 | 300 ms (NFR-005) | met |
| customers_search | large / other | 187 / 4,483 | 34 / 22 | 47 / 32 | 58 / 44 | 300 ms (NFR-005) | met |
| customers_search_phone | large / other | 29 / 720 | 22 / 22 | 34 / 33 | 60 / 50 | 300 ms (NFR-005) | met |
| overview | large | 103 | 303 | 401 | 426 | 300 ms (NFR-005) | NOT met |
| overview | other | 2,174 | 24 | 36 | 47 | 300 ms (NFR-005) | met |
| debtors | large | 61 | 369 | 453 | 893 | 300 ms (NFR-005) | NOT met |
| debtors | other | 1,406 | 26 | 39 | 50 | 300 ms (NFR-005) | met |
| debtors_overdue | large | 29 | 380 | 519 | 1,146 | 300 ms (NFR-005) | NOT met |
| debtors_overdue | other | 708 | 26 | 40 | 58 | 300 ms (NFR-005) | met |
| customer_page | large / other | 109 / 2,837 | 30 / 25 | 45 / 39 | 81 / 55 | - | no target |
| report_period_year | large | 1 | 2,006 | 2,006 | 2,006 | 5,000 ms (NFR-011) | met (one call) |
| report_overdue | large | 4 | 365 | 415 | 415 | - | no target |

The same targets are met and missed as in run B. Most percentiles are 15 to 30 percent higher than in
run B. Whether that is the merged code (each request now also passes the rate limiter, the body limit
and a stricter membership lookup), a database that had been loaded minutes before, or the other work on
the machine was not separated; a 30-minute run on the merged code would be needed to say.

## Rate limits and the statement timeout during a run

**Rate limits.** The application limits each signed-in user to 120 requests a minute (burst 60) and each
shop to 600 a minute (burst 200). The load test runs with these defaults in force and does not raise
them: its load is spread over 9,006 staff members and 5,000 shops, as real load is. The busiest shop gets
about 183 requests a minute from 8 staff members, a third of its limit. A request that is held back is
answered 429; the driver counts those in a column of their own, apart from refusals and errors, and
counts each against the target of its operation. Run C had none. Two things to know: the chat webhook is
not a signed-in caller and is not limited by these settings; and each API process keeps its own
counters, so with the four processes of this test a caller is in fact allowed four times the rate
(finding P39-2 of the security review). To run with other limits, set `QD_RATE_USER_PER_MINUTE`,
`QD_RATE_USER_BURST`, `QD_RATE_SHOP_PER_MINUTE` and `QD_RATE_SHOP_BURST` in the environment of the driver
(the servers it starts inherit it) or of the server driven with `--base-url`; the result file records the
values used.

**Statement timeout.** Run A showed that a few slow queries of one shop can stop the service for all.
The application now sets `statement_timeout` on every connection it opens: 5 seconds for the API
(`QD_STATEMENT_TIMEOUT_MS`), 60 seconds for the worker (`QD_WORKER_STATEMENT_TIMEOUT_MS`), 0 for no limit.
It is a setting of the application's connections, not of the role, so migrations, the test suite's own
connections and this loader are not limited. A cancelled statement rolls its transaction back and the
caller is answered 503 with the code `TIMEOUT`; in the chat the seller is told nothing was recorded.
The slowest call of run B, the one-year report, took 2 seconds, so the default leaves room; in run C
nothing was cancelled. What this does not do: the queries that stopped run A ran for 16 to 60 seconds
each, so with the timeout each would have been cut off at 5 seconds and retried by whoever waits for it.
It bounds the damage of a bad plan. It does not remove the bad plan.

## Targets: met, not met, not measured

| Target | State after run B | Why |
|---|---|---|
| NFR-001 chat recording within 500 ms | **Met for the part measured**: webhook receipt to the reply being queued, p95 56 ms. | The reply being *sent* was not measured: that is the worker and Telegram, and no external service was called. |
| NFR-002 notification leaves the outbox within 10 s | **Not measured** | Needs the worker and a Telegram endpoint (or a stand-in for one). |
| NFR-005 list, search and overview within 300 ms in a shop of 2,000 customers and 200,000 entries | **Met** for the customer list and search (p95 28 to 39 ms). **Not met** for the overview and the debtors lists (p95 349 to 414 ms). | See "What is still slow". |
| NFR-006 50 entries a second across 5,000 shops for 30 minutes without breaching NFR-001 or NFR-005 | **Rate sustained** (51.4 a second for 30 minutes, NFR-001 kept). **Not met as written**, because NFR-005 is breached in the large shop with or without the write load. | On this machine only. |
| NFR-009 itemized entry with ten lines within 400 ms | **Met**, p95 53 ms | |
| NFR-011 one-year period report within 5 s in a shop of the NFR-005 size | **Met**, 29 calls, slowest 1,959 ms | A small sample. Exports that run as jobs do not exist yet and were not measured. |
| NFR-003, NFR-004, NFR-007, NFR-008, NFR-010, NFR-012, NFR-013 | Not part of a load test | |

Also not measured: the worker's jobs (reminders over all shops, subscription review, weekly figures,
shop erasure) running beside the load; the customer's own pages; catalog search; the activity list; staff
and settings calls; sign-in; a cold cache after a restart; growth over weeks; more than one shop of the
large size; a shop larger than NFR-005 names; a load that reaches the rate limits; failover under load.

## What the first run found, and what was changed

The first run, on the code as it was, did not survive 90 seconds at the design rate: median response
times of 3 to 55 seconds for every operation in every shop, 947 of 9,142 requests abandoned. The
database's four cores were fully used by a handful of queries from the large shop that each ran for
tens of seconds.

The cause is one fact with several faces: row-level security adds `shop_id = current_setting('qd.shop_id')`
to every query, and the planner cannot see which shop that is. It plans every query for an average shop
of about 700 entries. For the shop with 200,000 that is wrong by a factor of 270.

| Finding | Change in this pull request |
|---|---|
| The customer list, the search and the chat's lookup by name joined the page of customers to the balance of *every* customer of the shop, and the planner sometimes recomputed that for each customer on the page: one search in the large shop took 1 to 60 seconds. | Query rewrite in `infrastructure/db.py`: the page is chosen first and a balance is read for each customer on it. |
| Leaving out reversed entries walked the index of the reversals of all shops. | Migration 0020, index `ledger_reversal_shop (shop_id, reverses_id)`. |
| Reading one customer's entries was planned as the intersection of the customer index and the whole shop's index: ten million index entries for a page of 50 customers. | Migration 0020, index `ledger_shop_customer (shop_id, customer_id, seq)`. |
| The overview and debtors queries looked up the promised date of every debt, 127,000 index lookups, though only uncovered debts need one. | Query rewrite: the lookup is made for uncovered debts only (about 4,000). |

The two rewrites have tests of their results in `tests/api/test_customers_ledger.py`, and the existing
comparison of the SQL figures with the domain rules on generated accounts still passes. For the indexes
`tests/db/test_schema_rules.py` checks only that they exist as defined: whether the planner uses them
depends on the size of the tables, so no test pins a plan, and the evidence that they are used at this
size is the run above. Cost of the two indexes: about 210 MB at this size, and two more index
entries per recorded entry; the write figures above include that cost.

Effect on single queries in the large shop, measured alone: search 1,129 ms to 15 ms; overview 2,758 ms to
about 280 ms; the report's "fell due" query 11.4 s to 1.3 s.

## What is still slow, and is not fixed here

**The overview and the debtors lists in a shop of 200,000 entries: about 265 to 320 ms at the median and
350 to 415 ms at the 95th percentile, against 300 ms.** They take the same time with no other load.

They compute the oldest-first allocation of every customer of the shop on every call, from every entry
the shop has ever recorded: nothing is stored (INV-2). The time is the reading, sorting and adding up of
200,000 rows, and it grows with the life of the shop. An index does not remove that and neither does
rewording the query, so it is outside what this story may change. Options, for a decision:

1. Keep per-customer figures that are updated in the transaction that records an entry (a balance and
   the uncovered debts), and derive the lists from them. This touches the rule that balances are never
   stored and needs a way to prove the stored figures always equal the derived ones.
2. Leave settled history out: an account whose balance has been zero since some entry needs nothing
   before it. A "settled up to seq" marker per customer would bound the work by open debt, not by age.
3. Accept a looser target for the overview of very large shops, or lower the size NFR-005 promises.

The same query shape serves the overdue report (377 ms at p95, no target) and the reminder job.

Two more things a reader should know:

- **The planner's blindness to the shop is not cured, only worked around** in the places this run
  exercised. Any other query that is cheap for an average shop and expensive for a large one can fail the
  same way, and a few slow queries from one large shop were enough to stop the service for all 5,000.
  The statement timeout added with this story contains that (above); it does not prevent it.
- **Recording loads the whole account** of the customer (`entries_of`), so its cost grows with the
  account. In the large shop, where the longest account has 1,153 entries, it was 40 ms at p95. Accounts of
  many thousands of entries were not generated.

## Afterwards: open debts are stored (migration 0026)

The founder chose, on 2026-10-07, to cure the slow overview by storing what it needs rather than by
changing the target. A stored balance alone was measured first and does not help: the total owed and the
number of debtors come from it at once, but the overdue and due-today figures still need the oldest-first
allocation over every debtor's entries, and restricting that to customers who owe took as long as before
(400 ms against 361 ms; 1 324 of the large shop's 2 000 customers owe). So each debt that is not yet fully
paid is stored with what is left of it and its promised date (table `open_debt`), rewritten by the
database for a customer whenever an entry or a promise of theirs is added.

Measured on the same generated database (`qd_load_merged`, 3.69 million entries) with the migration applied
by hand, single statements timed in `psql`, no other load:

| What | Before | After |
|---|---|---|
| The overview's totals for the shop with 200 298 entries | 361 ms | 2.3 ms |
| Rows read for it | every entry of the shop | 3 993 stored rows |
| Filling the table for the whole database, once, in the migration | - | 52 s |
| Size of the table and its indexes | - | 122 MB (739 789 rows) |
| Stored rows that differ from the ledger, checked for the large shop | - | 0 |
| Rewriting one customer's rows, paid on every entry or promise of theirs: the busiest customer, 1 153 entries | - | about 31 ms |

What this does not show: the 30-minute run was **not repeated** with the table in place, so the percentiles
of run B and run C stand as measured before it, and the cost added to each write under load is known only
from the single-statement figure above. A customer with a very long account pays the most for each entry.
Launch criterion 6 is as open as it was.

## Decisions taken where the documents are silent

| Question | Decision | Reason |
|---|---|---|
| How many reads accompany 50 writes a second? | 50 a second, 2 of them in the large shop | About one screen looked at per entry recorded. Two reads a second is far more than eight staff members produce, and gives enough samples for a 99th percentile. |
| How many shops of the NFR-005 size? | One, among 4,999 of about 100 customers | NFR-005 names "a shop"; REQ-N13 fixes the totals. |
| How long are accounts? | Large shop: 100 entries on average with a long tail. Others: about 7. | NFR-005 gives 200,000 entries for 2,000 customers; the rest is chosen to keep the total near 3.7 million. |
| How many API processes? | 4, each with the default pool (10 connections) | The architecture says "several worker processes" on 4 cores. |
| PostgreSQL settings | `shared_buffers` 2 GB, `work_mem` 16 MB; 4 CPUs | A quarter of the primary's 8 GB. Production settings are not specified anywhere yet. |
| What counts as "recorded" for a chat message? | A message naming exactly one customer, which records in one step | That is the exchange REQ-N02 and NFR-001 describe. Messages that need a question back are not in the mix. |
| Where does NFR-001 stop? | At the reply being queued in the outbox | Sending needs Telegram. |
| Which target for an amount-only API entry and for the customer's page? | None; reported without a verdict | The specification sets none. |
| How is a target judged when calls fail? | Each failed call counts as slower than the target | Otherwise a server that drops requests looks fast. |
| The goods-line time-limit trigger during the load | Switched off while copying, on again after; its rules are checked on the loaded data | It compares the sale's date with the clock at insert time, so no history can pass it. |
| Measurement events (`measure.event`) | Not generated | They are read only by the weekly job, which was not run. |
| Rate limits during the run | The defaults, in force; 429 counted separately | Raising them would measure a server that is not the one deployed, and the design load does not reach them. |
| How long may a statement run? | 5 s for the API, 60 s for the worker, both settable | The slowest API call measured took 2 s; the worker's jobs read across shops. Neither figure comes from a requirement. |
| What does a caller get when a statement is cancelled? | 503 with code `TIMEOUT` | Nothing was saved and trying again is right; 500 would say the server is broken. |
| Online payments (`online_payment`, migration 0019) | Not generated, not driven | The switch is off by default and no target names them. |

## Before this can count as launch criterion 6

1. Run it on the staging server with the production PostgreSQL settings, proxy and worker in place, the
   driver on another machine, and `--base-url`.
2. Decide what to do about the overview in large shops (above), then run again.
3. Add the outbox leg (NFR-001 end to end, NFR-002) with a stand-in for Telegram.
4. Run the worker's jobs during the load.
5. Repeat the 30-minute run on the merged code (run C was 5 minutes), with the rate limits and the
   statement timeout as they will be deployed and the number of API processes that will be deployed.

<!-- probe: CI scope, not to be merged -->
