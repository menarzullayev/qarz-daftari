# Evidence Record

- Evidence ID: EVID-065
- CLAIM: Payment notices with an optional receipt, the file store with a filesystem and an S3 adapter, receipts served only through five-minute signed links, duplicate detection by hash within a shop, and the hourly purge (story S10.2, pull request 36) passed CI on main in the run for commit 4fd845e. The S3 adapter was never run against a real bucket and the download of a file from Telegram never against the real Bot API; image receipts are checked by structure and stripped of metadata without decoding pixels, PDF receipts are stored with their metadata, and there is no malware scan
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/36 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37559288394
- DATE: 2026-10-07
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
