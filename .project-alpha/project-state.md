# Project State

framework_version: 1.0.0
current_stage: idea-selection
lifecycle: REVIEW
global_audit_status: NOT_RUN
blocked_stage:
blocked_reason:

## Stage status
- idea-selection: REVIEW
- 01-vision: NOT_STARTED
- 02-problem-discovery: NOT_STARTED
- 03-market-research: NOT_STARTED
- 04-prd: NOT_STARTED
- 05-domain-model: NOT_STARTED
- 06-architecture: NOT_STARTED
- 07-adr: NOT_STARTED
- 08-technical-spec: NOT_STARTED
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
