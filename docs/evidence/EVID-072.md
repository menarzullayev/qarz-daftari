# Evidence Record

- Evidence ID: EVID-072
- CLAIM: Dockerfiles, an nginx proxy, a Compose file and deploy, rollback and smoke scripts (pull request 60), and the backup schedule, an automatic restore test and backup monitoring (pull request 61), passed CI on main in the run for commit 8684219, including jobs that build the images and check the files. Both were proven only in containers on one developer machine with a self-signed certificate: nothing ran on a server, no timer ran under systemd, and no backup crossed between two hosts
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/60 ; https://github.com/menarzullayev/qarz-daftari/pull/61 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37642615202
- DATE: 2026-10-07
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
