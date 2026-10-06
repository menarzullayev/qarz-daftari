# Evidence Record

- Evidence ID: EVID-048
- CLAIM: Story S8.1 is implemented in the front end and verified in CI: customers, amount-only and itemized credit sale, payment, reversal, customer detail with goods, adding goods later, promised date choice, overview with debtors, catalog with the review queue, and shop settings by role. 768 front-end tests pass; the staff first load is 95.93 KB gzip against a 300 KB budget. Written by a helper agent in two parts and checked by the orchestrator, who re-ran the checks and tried mutations of its own. Every screen was exercised only against a fake server and a local render at 375 pixels: nothing ran against the real back end or inside Telegram, and the 45-second target of REQ-N02 was not measured with a person
- SOURCE: https://github.com/menarzullayev/qarz-daftari/pull/17 ; https://github.com/menarzullayev/qarz-daftari/pull/23 ; https://github.com/menarzullayev/qarz-daftari/actions/runs/37520022593
- DATE: 2026-10-07
- CONFIDENCE: HIGH
- TYPE: FACT
- RISK: LOW
- Stage: 09-development-plan
