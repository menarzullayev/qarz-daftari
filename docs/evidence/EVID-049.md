# Evidence Record

- Evidence ID: EVID-049
- CLAIM: Stories S9.1, S9.2 and S9.3 are implemented and verified in CI on main: personal links and a counter code, the waiting list, consent before anything is stored, notifications of every entry, payment, reversal and chosen date in the customer's language, the customer's own page, disconnecting, and removal of identifying data at once or when the debt is settled; also the owner's combined totals left from S3.4. The customer-side database functions are tested directly as the application role. 51 hand mutations in Python and SQL were each caught or led to the removal of an unreachable check. The run on main for the first of the two changes failed: a test compared a period's last day with the server's date instead of the Tashkent date and fails between 19:00 and 24:00 UTC; the next change fixed the test and its run passed. The consent texts are agent drafts and have had no legal review
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/21 ; https://github.com/menarzullayev/qarz-daftari/pull/22 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37518135034 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37515704664
- DATE: 2026-10-07
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
