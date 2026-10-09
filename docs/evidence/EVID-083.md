# Evidence Record

- Evidence ID: EVID-083
- CLAIM: The 30-minute load test was repeated on main at 516e4d6 for the first time since open debts were stored. The first attempt fell behind: recording an entry took 188 ms at the median on the server against 26 ms before, because the trigger that refreshes a customer's open debts deleted by customer with no index leading on the customer and read all 738 534 rows each time. Migration 0033 adds the index (pull request 80, CI passed on all jobs) and the trigger takes 1 to 5 ms. With it the run sent 182 572 requests in 30 minutes, recorded 92 226 entries, abandoned none, had no error, and met every target, including the overview and debtor lists of the large shop that earlier runs missed. The servers ran on the planned host but outside its containers, without proxy, tunnel or worker, with the driver on the same machine, and the index was created by hand in the load database
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/80 ; docs/10-operations/load-test.md
- DATE: 2026-10-09
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
