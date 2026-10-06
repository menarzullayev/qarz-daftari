# Evidence Record

- Evidence ID: EVID-047
- CLAIM: Stories S6.1, S6.2 and S6.3 are implemented in the API and verified in CI: catalog management with search across Latin and Cyrillic, learned items and their review (accept, dismiss, merge), itemized credit sales with line totals rounded half up, and goods lines added once until the end of the day after the sale. Both were written by helper agents; the orchestrator rebased them, re-ran every check and tried mutations of its own before merging. The helpers report 104 and 64 hand mutations, all caught except one equivalent mutant. The ten-line call time target NFR-009 was not measured
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/20 ; https://github.com/menarzullayev/qarz-daftari/pull/26 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37512934877
- DATE: 2026-10-07
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
