# Evidence Record

- Evidence ID: EVID-082
- CLAIM: Pull request 74 runs the back-end tests as four jobs chosen by a hash of the name of each test, each with its own database, skips the heavy jobs when a pull request changes only documents that no test reads, and cancels superseded runs of a pull request but never of main. A step in every run checks that the four parts are disjoint and together are the whole suite: 6133 tests, the same as unsplit. A run took 6 minutes 0 seconds against 21 minutes 23 seconds before, and a documents-only run 13 seconds. One test that failed every day between 19:00 and 24:00 UTC, because its seed took the date in UTC and the test in Tashkent time, was corrected without changing what it asserts
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/74 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37830617457 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37812132772
- DATE: 2026-10-09
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
