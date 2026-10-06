# Project State

framework_version: 1.0.0
current_stage: 08-technical-spec
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
- 08-technical-spec: REVIEW
- 09-development-plan: NOT_STARTED
- 10-operations: NOT_STARTED

## Workflow invariants
- Stages execute sequentially.
- A stage cannot start until its predecessor is PASSED and its handoff is READY.
- A stage cannot PASS from REVIEW until its OUTPUT.md passes the executable output checks and required human approval is recorded.
- BLOCKED requires a persisted reason.
- Global audit requires every stage to be PASSED, every required handoff to be READY, and all global consistency checks to pass.
- Production Ready requires explicit human approval of the global audit.

## Pending approvals
None.

## Blockers
None.

## Open questions
None.

## Last validation
- Structural: not run
- Semantic: not run
- AI audit: not run
- Global audit: not run
