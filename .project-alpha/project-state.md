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
- DEC-013: release 1 scope in PRD version 2.
- Production launch approval: not requested; cannot be given until launch criteria are met.

## Blockers
- Change of direction (DEC-012 / APR-012, 2026-10-06): PRD is at version 2; stages 05 to 10 are marked PASSED or REVIEW in this file but their documents are stale and carry a banner saying so. They must be revised in order after DEC-013 is decided. The framework CLI has no transition to reopen a passed stage, so the stage statuses above overstate the real state.
- 10-operations is held in REVIEW by founder decision until production launch criteria are met.
- Global audit cannot pass with framework 1.0.0 as released (early-stage EVID and DEC references; APR-001 lacks a Decision field). A framework fix has been proposed but not made.

## Open questions
- Field validation outstanding: shopkeeper interviews and a hands-on pDaftar test. The founder's own answers are recorded as EVID-034 (assumption) and do not satisfy this gate.
- Legal review outstanding, now including: accepting subscription payments on a personal card without a registered entity, Telegram payment rules, receipt retention, and SMS without consent.
- No registered business entity; online payments and SMS ship switched off.
- Hosting and backup providers in Uzbekistan not chosen.
- Final product name not chosen; "Qarz Daftari" is a working title.

## Last validation
- Structural: not run
- Semantic: not run
- AI audit: not run
- Global audit: not run
