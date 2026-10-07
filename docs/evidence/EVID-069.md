# Evidence Record

- Evidence ID: EVID-069
- CLAIM: Pay by card transfer with receipts decided by administrators (story S17.2, pull request 54), import of opening balances with the checking, applying and undoing done by the worker (story S14.1, pull request 53) and the screens for exports and support access (pull request 55) passed CI on main in the run for commit 6bdf59b. That run first failed on one test of 5 485, a statement timeout test whose 300 ms limit an ordinary statement reached on a slow runner, and passed when run again; the limit was raised in pull request 56. Nothing was run with a real bot, a real review group or a real file store, and the import template was not opened in a spreadsheet program
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/54 ; https://github.com/menarzullayev/qarz-daftari/pull/53 ; https://github.com/menarzullayev/qarz-daftari/pull/55 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37575995852
- DATE: 2026-10-07
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
