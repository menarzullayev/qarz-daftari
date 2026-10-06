# Goal prompt for the autonomous build

Version 2 (2026-10-06). Replaces the first prompt after DEC-020: the build no longer stops before M4, an orchestrating agent runs parallel helpers, and open points are decided by the agent and recorded for later review.

Paste the block below after `/goal` in a session opened in this repository (`D:\Linux\qarz-daftari`).

```text
Build release 1 of Qarz Daftari through milestone M8 without waiting for a human, exactly as specified by the approved documents in this repository, and stop at the launch gate.

READ FIRST, IN THIS ORDER
- docs/09-development-plan/PROGRESS.md (what is done, deviations, what is open)
- .project-alpha/project-state.md and .project-alpha/decisions/ (DEC-012..DEC-020 are the current direction)
- docs/04-prd, 05-domain-model, 06-architecture, 07-adr, 08-technical-spec (with schema.sql and tests/), 09-development-plan, 10-operations: each OUTPUT.md
- README.md and .env.example (never print values from .env)
If documents conflict, the earlier one in the list above wins among stage documents; record the conflict.

YOU ARE THE ORCHESTRATOR
- Work milestone by milestone, M1 -> M8, story by story (S*.* in the plan). Start with S1.3.
- Give independent stories to helper agents, each in its own git worktree and branch, with a brief that names the story, its requirements, the files it owns, and its acceptance tests. Never let two helpers edit the same files. Integrate one pull request at a time.
- Review every helper result yourself before merging: read the diff, run the checks. A helper's report is a claim, not evidence.

DEFINITION OF DONE FOR A STORY
- Its acceptance criteria pass in CI on main, with a negative test proving each rule rejects what it must reject.
- The authorization and tenant suite covers every new operation as every role, as a member of another shop, as an unrelated customer, and as an administrator without support access. This suite is blocking from S1.3 on.
- ruff, mypy --strict, import contracts, front-end lint and types, both dependency scans: all green.
- PROGRESS.md updated, and an evidence record added with the CI run link (project-alpha evidence ...).
Measure before claiming: run it, do not infer it from reading code.

DECIDING WITHOUT A HUMAN
- When something is unspecified, choose the most reasonable option consistent with the PRD and domain rules, implement it, and record it: project-alpha decision ... --approval-required yes --approval-status pending, stating the alternatives and why. Keep going.
- You may not contradict an approved requirement, invariant, business rule, or ADR. If one is wrong, record the proposed change as a pending decision, implement the approved behavior, and continue.
- Keep a running list of your pending decisions in PROGRESS.md for the founder's review.

GIT
- Conventional Commits in English. Branch and open pull requests freely. Merge your own pull request only when its CI run is green for the head commit. Never force-push main. Never commit .env or any secret.

NEVER DO THESE; MARK THE ITEM "BLOCKED ON FOUNDER" AND MOVE ON
- Order, pay for, or sign up for servers, domains, or any service. Enter passwords, 2FA codes, or payment details. Solve CAPTCHAs.
- Deploy to production, or onboard any real shop or customer, or store any real person's data.
- Send messages to anyone except the two project bots (@qarzdaftari_dev_bot, @qarzdaftari_test_bot) and BotFather settings for those two bots.
- Turn on the SMS or online-payment switches, or accept a real payment.
- Delete data you did not create, rewrite project-alpha event history, or weaken a test to make it pass.
Where a criterion needs real servers (S1.5, failover, the 1-hour and 5-minute targets, production alerts), build and rehearse it locally with Docker: two database containers, replication, archiving, scripted failover, timed. Record the local result and state plainly that it is not the production measurement.

DONE WHEN
1. Milestones M1..M8 are complete on main and every PRD acceptance criterion passes in CI.
2. Launch criteria 4 to 11 and 14 in docs/10-operations/OUTPUT.md each have recorded evidence (test output, timed local rehearsals, load-test numbers against NFR-001..013), or the exact reason they are blocked.
3. PROGRESS.md lists every agent-made decision awaiting review and every item blocked on the founder.
4. A final report in Uzbek (Latin) for Saidakbar aka states, criterion by criterion: proven, proven locally only, or blocked on the founder (criteria 1, 2, 3, 12, 13, 15, 16).
Do not attempt M9. Do not call the product "production ready": that requires the founder to close the seven criteria above and record a launch approval.
```

## What the founder still has to do

Agents will finish with seven launch criteria open. None can be closed by an agent:

| No. | Criterion |
|---|---|
| 1 | Shopkeeper interviews and a hands-on pDaftar test |
| 2 | Legal review, including the consent text in both languages |
| 3 | Registration of the personal data base, if required |
| 12 | Two servers at two providers in Uzbekistan, ordered and paid for |
| 13 | A second person able to fail over |
| 15 | The card-transfer payment process run end to end with a test shop |
| 16 | Launch approval |

## Practical notes

- The local `.env` is filled except `QD_PUBLIC_BASE_URL`. Stories that need Telegram to reach the service (webhook, Mini App, Telegram Login) can be built and tested with simulated updates; a live end-to-end check needs a public HTTPS address, which means a tunnel or a server that only the founder can provide.
- Parallel helpers multiply token spend. The plan's estimate of 92 to 138 focused days is for one builder; parallel work shortens elapsed time, not total effort.
- Every decision an agent makes alone is recorded as pending. Reviewing that list is the founder's main job while the build runs.
