# Evidence Record

- Evidence ID: EVID-078
- CLAIM: Pull request 68 splits the one application role into qd_app, qd_admin and qd_worker (migration 0031), with a test that compares every table right and every SECURITY DEFINER function against one explicit table and tries each right a role was not given, and adds an operation and a control to end all of a person's sessions. CI passed on main in the run for commit 446bd43. The roles connected only in tests and in the local and CI end-to-end stack: nothing ran on a server, and upgrading an existing database was not rehearsed. The administrator side still runs inside the API process, so that process holds two of the three connections
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/68 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37790862313
- DATE: 2026-10-08
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
