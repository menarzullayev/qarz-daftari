# Evidence Record

- Evidence ID: EVID-063
- CLAIM: The data generator, loader and load driver, two indexes and two query rewrites it led to, and a statement timeout on the application's connections (story S19.1, pull request 43) passed CI on main in the run for commit 516b0c8. On a developer machine with PostgreSQL in Docker, 5 000 shops and 3.69 million entries, 30 minutes at 51 writes a second: recording, search and the itemized sale met their targets; the overview and the debtors list of a shop with 200 000 entries did not (95th percentile 349 to 414 ms against 300 ms). This is one machine and generated data and does not meet launch criterion 6
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/43 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37556875850 ; docs/10-operations/load-test.md
- DATE: 2026-10-07
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
