# Evidence Record

- Evidence ID: EVID-070
- CLAIM: Storing each unpaid debt's remainder and promised date in a table kept by the database from the ledger (pull request 56, the founder's decision DEC-053) passed CI on main in the run for commit a85ae2f. On the generated load database the overview's totals for a shop with 200 298 entries went from 361 ms to 2.3 ms in single statements, the table held no row that differed from the ledger for that shop, and rewriting the busiest customer's rows cost about 31 ms; the 30-minute load run was not repeated, so launch criterion 6 stays open
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/56 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37581699462 ; docs/10-operations/load-test.md
- DATE: 2026-10-07
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
