# Implementation Progress

Tracks the build against `OUTPUT.md` in this directory. A story is listed as done only when its checks passed in CI on the main branch; the evidence column says where that can be seen. Updated at the end of each working session.

Last updated: 2026-10-06.

## Milestone M1: Platform foundation

| Story | State | Evidence | Notes |
|---|---|---|---|
| S1.1 Repository, layers, tooling, CI | Done | EVID-036; pull request 1 | Backend and front-end checks run on every push and pull request. Import contracts are enforced and have three negative tests. |
| S1.2 Schema as migrations, roles, row-level security, schema checks as tests | Done | EVID-036; pull request 1 | 24 database rule tests against PostgreSQL built by the migration. The goods-line time limit is now tested. Migrations run as the owner role; a separate CI step applies them to an empty database. |
| S1.3 Tenant context, authorization framework, cross-tenant and cross-role harness | Not started | - | Next. The authorization and tenant suite must be blocking before any business operation is added. |
| S1.4 Webhook, idempotency, outbox and dispatcher | Not started | - | |
| S1.5 Two servers, replication, archiving, file store, failover | Blocked on the founder | - | Needs two hosting providers in Uzbekistan chosen, ordered, and paid for. Compose files and scripts can be written and rehearsed locally first. |
| S2.1 Sign-in from chat, Mini App, and web | Not started | - | Needs test bots created by the founder in BotFather. |
| S2.2 Front-end shell, generated API client, message catalogs | Started | Pull request 1 | Three entry points build. No API client, catalogs, or screens yet. |

M1 exit condition (a user signs in through all three clients; the cross-tenant suite runs; failover rehearsed once) is **not met**.

## Milestones M2 to M8

Not started.

## Deviations from the approved documents

| Item | Approved text | Implementation | Why | Needs a decision |
|---|---|---|---|---|
| Creation of role `qd_app` | `CREATE ROLE qd_app LOGIN PASSWORD 'set-at-deploy' NOBYPASSRLS` in `docs/08-technical-spec/schema.sql` | The migration creates the role `NOLOGIN NOBYPASSRLS` if it does not exist; login and password are set at deploy | A password literal must not live in the repository or run in every environment | No; a test asserts this is the only difference between the migration and the approved schema |
| Layer order for the import contract | The architecture names interface, application, domain, and infrastructure without ordering infrastructure | Enforced order: interface, infrastructure, application, domain | A single ordering is needed for an automatic check; application code depends on abstractions, infrastructure implements them | No |
| Dependency lock | "Pinned with hashes" | Backend: hash-pinned lock generated for Linux and Python 3.12 with uv. Front end: npm lockfile | pip-tools downloaded several gigabytes to compute hashes and did not finish | No |

## Launch criteria (docs/10-operations/OUTPUT.md)

| No. | Criterion | State |
|---|---|---|
| 1 | Interviews and pDaftar test | Open; founder |
| 2 | Legal review | Open; founder |
| 3 | Registration if required | Open; founder |
| 4 | M1 to M8 complete, acceptance criteria pass in CI | In progress: 2 of 49 stories done |
| 5 | Authorization and tenant suite covers every operation | Not started (S1.3) |
| 6 | Load test | Not started |
| 7 | Security review | Not started |
| 8 | Failover and restore rehearsals | Not started |
| 9 | Alerts triggered and received | Not started |
| 10 | Runbooks executed | Not started |
| 11 | Usability sessions | Not started |
| 12 | Two servers in use | Open; founder |
| 13 | Second operator named | Open; founder |
| 14 | Both languages reviewed | Not started |
| 15 | Payment process run end to end with a test shop | Not started |
| 16 | Launch approval | Open; founder |

## What the founder is needed for next

1. Done on 2026-10-06: development and test bots exist and the local `.env` is filled. Still missing there: a public HTTPS address (`QD_PUBLIC_BASE_URL`).
2. Choose two hosting providers or facilities in Uzbekistan (S1.5).
3. Run the shopkeeper interviews and the pDaftar test. By DEC-020 they no longer block the build, but launch criterion 1 cannot be met without them, and the later they happen the more of the build rests on untested assumptions.
