# Project State

framework_version: 1.0.0
current_stage: 10-operations
lifecycle: REVIEW
global_audit_status: BLOCKED
blocked_stage:
blocked_reason:

## Stage status
- idea-selection: PASSED
- 01-vision: PASSED
- 02-problem-discovery: PASSED
- 03-market-research: PASSED
- 04-prd: PASSED
- 05-domain-model: PASSED
- 06-architecture: PASSED
- 07-adr: PASSED
- 08-technical-spec: PASSED
- 09-development-plan: PASSED
- 10-operations: REVIEW

## Workflow invariants
- Stages execute sequentially.
- A stage cannot start until its predecessor is PASSED and its handoff is READY.
- A stage cannot PASS from REVIEW until its OUTPUT.md passes the executable output checks and required human approval is recorded.
- BLOCKED requires a persisted reason.
- Global audit requires every stage to be PASSED, every required handoff to be READY, and all global consistency checks to pass.
- Production Ready requires explicit human approval of the global audit.

## Pending approvals
- DEC-014 to DEC-019: version 2 of the domain model, architecture, decision records, technical specification, development plan, and operations definition. Written without stopping by founder instruction; awaiting his single end-of-sequence review.
- Production launch approval: not requested; cannot be given until the sixteen launch criteria in docs/10-operations/OUTPUT.md are met.

## Blockers
- Stages 05 to 09 show PASSED in this file because the framework CLI cannot reopen a passed stage. Their documents are now version 2 and are NOT yet approved (DEC-014 to DEC-018 pending). Treat them as in review.
- 10-operations is held in REVIEW by founder decision until production launch criteria are met.
- Global audit cannot pass with framework 1.0.0 as released (early-stage EVID and DEC references are reported as forward references; APR-001 lacks a Decision field the documented CLI command never asked for). A framework fix has been proposed but not made.

## Open questions
- Field validation outstanding: shopkeeper interviews and a hands-on pDaftar test. The founder's own answers are recorded as EVID-034 (assumption) and do not satisfy this gate.
- Legal review outstanding: recording before consent, deferred removal, in-shop reliability indicator, notifications and receipts through Telegram abroad, receipt retention, SMS without consent, consent text in two languages, and subscription payments to a personal card (the founder states this is lawful, EVID-035; unverified).
- No registered business entity; online payments and SMS ship switched off.
- Two hosting providers in Uzbekistan and a second operator are not chosen.
- Final product name not chosen; "Qarz Daftari" is a working title.

## Last validation
- Structural: not run
- Semantic: not run
- AI audit: not run
- Global audit: not run
