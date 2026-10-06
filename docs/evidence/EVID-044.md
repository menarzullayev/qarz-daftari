# Evidence Record

- Evidence ID: EVID-044
- CLAIM: The local part of story S1.5 is done: a container rehearsal of streaming replication, WAL archiving, point-in-time restore, failover with fencing of the old primary, and the file store. The orchestrator re-ran the whole cycle on 2026-10-06 and every check passed: failover from kill to first write took 20.5 seconds, no acknowledged row was lost after failover, and the archive alone bounded the loss to 20 seconds. This ran on one developer machine with containers; nothing ran on real servers or across two facilities
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/13 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37504497489 ; docs/10-operations/rehearsals/2026-10-06-local.md
- DATE: 2026-10-06
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
