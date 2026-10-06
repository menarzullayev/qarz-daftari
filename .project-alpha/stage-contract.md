# Stage Contract

Every stage is executed as an explicit contract:

`PRECONDITIONS → INPUTS → AGENT TASK → RESEARCH/TOOLS → OUTPUTS → QUALITY GATE → DECISION → HANDOFF`

## Purpose

## Preconditions
- Previous stage must be PASSED (except `idea-selection`).
- Required handoff must exist and be `READY`.
- Required source outputs must be complete and free of unresolved placeholders.

## Inputs

## Agent task

## Research / tools

## Outputs
- Primary output: `docs/<stage>/OUTPUT.md`
- Evidence and decisions must remain traceable.

## Evidence requirements

## Cross-stage checks

## Human approval
- Required for high-impact stages.
- Approval must be recorded before PASS.

## Quality gate
- Stage cannot transition `REVIEW → PASSED` unless its output is present and passes the quality gate.

## Block conditions
- Missing prerequisite, failed quality gate, unresolved material contradiction, missing required evidence, or pending required approval.

## Handoff to next stage
- PASS creates `.project-alpha/handoffs/<from-stage>__to__<to-stage>.md` with `Status: READY`.
- The next stage consumes the previous stage's `OUTPUT.md` as its authoritative input.
