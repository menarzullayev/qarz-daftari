# Evidence Record

- Evidence ID: EVID-079
- CLAIM: Pull request 69 adds a sender for Eskiz behind the existing port, with the switch off; it was never run against Eskiz, whose documentation describes no error answers, so the mapping of failures is an agent's reading. Its end-to-end job failed twice because the proxy held pages and their files to the API's limit by address and answered 429 to script files when two pages opened in one second; pull request 71 gives them a limit of their own and adds a smoke check that failed on the old configuration (run 37801935208) and passes on the new (run 37802237896). Pull request 69 then passed all six jobs on commit e0860bf. On 2026-10-08 CI stopped because of the account's billing; the founder chose to make the repository public, after the agent searched the whole history and all 69 pull request heads for secrets and found none
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/69 ; https://github.com/menarzullayev/qarz-daftari/pull/71 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37801935208 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37802237896
- DATE: 2026-10-08
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
