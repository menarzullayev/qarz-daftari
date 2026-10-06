# Goal prompt for the AI project manager

Paste the block below after `/goal` in a session opened in this repository (`D:\Linux\qarz-daftari`).

```text
Implement release 1 of Qarz Daftari through milestone M8, exactly as specified by the approved documents in this repository, and stop at the launch gate.

SOURCES OF TRUTH (read before any work, in this order)
- .project-alpha/project-state.md and .project-alpha/decisions/ (DEC-012 to DEC-019 are the current direction)
- docs/04-prd/OUTPUT.md (version 2: features FEAT-001..027, requirements REQ-*, acceptance criteria)
- docs/05-domain-model/OUTPUT.md (entities, invariants INV-*, rules BR-*)
- docs/06-architecture/OUTPUT.md and docs/07-adr/OUTPUT.md (ADR-002..021)
- docs/08-technical-spec/OUTPUT.md, docs/08-technical-spec/schema.sql, docs/08-technical-spec/tests/schema_checks.sql
- docs/09-development-plan/OUTPUT.md (milestones M1..M8, stories S*.*)
- docs/10-operations/OUTPUT.md (test strategy, CI/CD, runbooks, 16 launch criteria)
If documents conflict, the higher one in this list wins; report the conflict.

HOW TO WORK
- Build in milestone order M1 -> M8. Code lives in this repository under backend/ (Python 3.12, FastAPI, aiogram 3) and frontend/ (TypeScript, React), per the ADRs.
- A story is done only when its acceptance criteria pass in CI, including a negative test proving each rule rejects what it must reject. The authorization and tenant suite (every operation as every role and as another shop) is blocking from M1.
- Measure before claiming: run the tests, the schema checks, and real renders; never report "done" from reading code.
- Branch, commit (Conventional Commits, English) and open PRs freely; merge your own PR only when CI is green.
- Do not change an approved decision, requirement, or rule. If one is wrong or missing, stop that story, record a proposed decision with `project-alpha decision ... --approval-status pending`, and ask the founder.
- Use the project-alpha CLI for evidence, decisions, approvals; never hand-edit event history.
- Report to the founder (Saidakbar aka) in Uzbek (Latin), briefly, at the end of every milestone: what passed, what was measured, what is open.

HARD STOPS - ask the founder and wait
- Before starting M4: shopkeeper interview results and the hands-on pDaftar test must be recorded as evidence. If they are not, stop and ask.
- Before any real customer data, any real shop, or any payment: legal review must be recorded. You never onboard real users.
- Ordering or paying for servers, domains, or any service; production deploys; sending any message to people outside the test bots; enabling the SMS or online-payment switches; anything irreversible.
- Entering passwords, 2FA codes, payment details, or solving CAPTCHAs: the founder does these.
Until servers exist, build and verify everything locally and in CI with Docker; mark deployment-dependent criteria as blocked, not passed.

DONE WHEN
1. Milestones M1..M8 in docs/09-development-plan/OUTPUT.md are complete and every PRD acceptance criterion passes in CI.
2. docs/10-operations/OUTPUT.md launch criteria 4 to 11 and 14 are met with recorded evidence (test output, timed rehearsals, load-test numbers), or each unmet one is listed with the exact reason it is blocked.
3. A final report in Uzbek lists, criterion by criterion, what is proven, what is blocked on the founder (criteria 1, 2, 3, 12, 13, 15, 16), and the measured results against NFR-001..013.
Do not attempt M9 (launch). Launch needs the founder's explicit approval.
```

## Notes for the founder

- The goal ends at M8, not at launch. Launch criteria 1, 2, 3, 12, 13, 15 and 16 need you: interviews and the pDaftar test, legal review, registration if required, choosing and paying for two servers, naming a second operator, a test run of the payment process, and the launch approval itself.
- The agent will stop before M4 if the interview results and the pDaftar test are not recorded. That gate was approved in DEC-010 and carried into DEC-018.
- Estimated effort in the plan is 92 to 138 focused days. A `/goal` session will work through it in pieces; expect to answer questions at each hard stop and at the end of each milestone.
