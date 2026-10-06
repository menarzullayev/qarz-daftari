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
- Production launch approval (launch criterion 11 in docs/10-operations/OUTPUT.md): not requested; cannot be given until criteria 1 to 10 are met.

## Blockers
- 10-operations is held in REVIEW by founder decision (2026-10-06) until the production launch criteria are met. Not a workflow BLOCKED state.
- Global audit cannot pass with framework 1.0.0 as released: early-stage EVID and DEC references are reported as forward references, and approval APR-001 lacks a Decision field the documented CLI command never asked for. A framework fix has been proposed but not made.

## Open questions
- Field validation outstanding: 15 to 20 shopkeeper interviews and a hands-on pDaftar test (gate before milestone M3).
- Legal review outstanding: four questions and the consent text (gate before any real customer data).
- Hosting and backup providers in Uzbekistan not chosen.
- Final product name not chosen; "Qarz Daftari" is a working title.

## Last validation
- Structural: not run
- Semantic: not run
- AI audit: not run
- Global audit: not run
