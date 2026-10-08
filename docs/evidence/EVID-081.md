# Evidence Record

- Evidence ID: EVID-081
- CLAIM: Pull request 73 adds a Compose overlay that runs the proxy, API, worker, PostgreSQL, cloudflared and a backup scheduler on one host with no published port, takes the address of the visitor from the Cloudflare header only when the request comes from the container of the tunnel, and sends pgBackRest backups with WAL and an encrypted copy of the stored files to an S3 bucket. A CI job brings it up with MinIO in place of R2 and no tunnel, destroys the database volume, restores it, and runs negative checks (a forged header, a wrong passphrase, damaged data); it passed on commit 692af85. Its first run on main failed on a check that mistook fragments of encrypted names for plain ones, corrected in pull request 76. Nothing ran on Windows as a service, through a real tunnel, or against R2; no restore onto a second machine was timed; no recovery time is claimed
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/73 ; https://github.com/menarzullayev/qarz-daftari/pull/76 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37822504561
- DATE: 2026-10-09
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
