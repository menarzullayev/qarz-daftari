# HisoBox

A shop's books in one place for mahalla grocery shops in Uzbekistan, used through Telegram and the web: credit sales and the debt ledger, the cash book, stock, suppliers. Formerly "Qarz Daftari", the working title until 2026-10-10; the repository, the package `qarz` and the `QD_*` names keep it (`docs/08-technical-spec/OUTPUT.md`, "Brand").

**Status:** in development. Nothing here is deployed or used by real shops. The service must not process real customer data until the launch criteria in `docs/10-operations/OUTPUT.md` are met.

## Where things are

| Path | Contents |
|---|---|
| `docs/` | Approved product and engineering documents, stage by stage. Start with `docs/04-prd/OUTPUT.md`. |
| `.project-alpha/` | Project state, decisions, approvals, and event history kept by the documentation framework |
| `backend/` | Python 3.12 backend: API and worker (FastAPI, aiogram), migrations, tests |
| `frontend/` | TypeScript and React application with three entry points: Mini App, web panel, admin panel |
| `GOAL-PROMPT.md` | The implementation brief for the build |

The documents are the source of truth. Code implements them; it does not redefine them.

## Development

Requirements: Python 3.12, Node 24, Docker.

```bash
docker compose -f docker-compose.dev.yml up -d
```

Backend:

```bash
cd backend
python -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.lock && .venv/bin/pip install --no-deps -e .
export QD_TEST_ADMIN_URL=postgresql://postgres:postgres@127.0.0.1:54329/postgres
.venv/bin/ruff format --check . && .venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/lint-imports --config pyproject.toml
.venv/bin/pytest -q
```

Frontend:

```bash
cd frontend
npm ci
npm run lint && npm run typecheck && npm test && npm run build
```

### API description and the front end's types

The back end writes its API description from its own code, without a server or a database, to `backend/openapi.json`. The front end's types of the API are generated from that file into `frontend/src/shared/api.generated.ts`. Both files are committed, so building the front end needs no Python. After changing a request or response shape:

```bash
cd backend && .venv/bin/python -m qarz.interface.api_description   # writes openapi.json
cd ../frontend && npm run api:types                                # writes src/shared/api.generated.ts
npm run typecheck                                                  # shows what the front end must follow
```

CI regenerates both and fails when a committed file differs (`python -m qarz.interface.api_description --check` in the `backend-checks` job, `npm run api:check` in the frontend job). Routes kept out of the schema (provider callbacks, the Telegram webhook, metrics, file links) are not described, and the running application still serves no description.

A route's answer is typed in the description only when the route has a response model from `backend/src/qarz/interface/answers.py`; the others answer with an open object. Only reads carry one, because a write may answer with a stored result of an older shape. The models are closed and strict, so they refuse an answer they do not describe instead of changing it; `backend/tests/api/test_typed_answers.py` compares the bytes with and without a model. In the front end, `Wire` (`frontend/src/shared/api.ts`) names the generated shapes, and the readers and request builders are checked against them with `fieldsOf<Wire["..."]>`.

The database tests create a throwaway database, apply the real migrations, and exercise the schema's rules as the restricted application role. They fail, and are not skipped, when no database is configured.

### What CI runs

One workflow, `.github/workflows/ci.yml`, on every pull request and every push to `main`.

| Job | What it does |
|---|---|
| `changes` | Decides whether a pull request changes documentation only (below). |
| `backend-checks` | Format, lint, types, layering, the API description, the migrations on an empty database, the proof of the shards, the dependency audit. Once. |
| `backend-tests (1)` to `(4)` | The backend test suite as four jobs side by side, each with a PostgreSQL of its own: `pytest -q --shard=N/4`. |
| `backend` | The one result of the three above: red when a check or a shard failed, was cancelled, or was skipped without reason. |
| `frontend`, `e2e`, `deploy-files`, `backup-files`, `single-host` | As before. |
| `documents` | No unresolved placeholder in a stage document. Always runs. |

**Shards.** A test's shard comes from a hash of its node identifier (`backend/tests/sharding.py`), so one costly file is spread over all four and adding a test moves no other. `pytest -q` without `--shard` runs everything, and that is what to run locally; `pytest -q --shard=2/4` runs what the second job runs, in the same order. Nothing is assumed about the split: `backend-checks` collects the suite without the option and once per shard and fails unless the shards are disjoint and their union is the whole suite (`python scripts/check_shards.py 4`, which prints the counts). Tests that share a shard share one database, as the whole suite does in a local run, so a test must not depend on what another left behind or on the order.

**Another order.** `pytest -q --shuffle=7` runs the same tests in an order fixed by the seed (any text), the same on every machine; it combines with `--shard`. Use it to show that a test depends on nothing another left behind, and that a change to the fixtures leaks nothing from one test to the next.

**One application for the API tests.** Building the application is the costliest thing an API test did before its first request (FastAPI works out the parameters of some 130 routes), so the `client` fixture (`backend/tests/api/conftest.py`) builds it once for the session, around stand-ins for the seven things each test has of its own: the two database connections, the clock, the allow-list, the cipher, the file store and the two Telegram fakes. A test puts its own behind the stand-ins and takes them away at its end; the engines, their connections, the event loop and the `TestClient` are still made for each test. The application object itself keeps one thing between requests, its request counters, and they are set back before each test. `backend/tests/api/test_shared_app.py` walks everything the application holds and fails for anything else that could change, so a service that starts to remember something (a cache, a counter, a lock) must either get it from a stand-in or be named there with the code that sets it back. `pytest -q --app-per-test` builds the application for each test, as before; use it when a failure looks like one test reaching another.

**Documentation only.** When a pull request changes nothing but Markdown under `docs/`, files under `.project-alpha/`, and Markdown at the top of the repository, every job except `changes`, `backend` and `documents` is skipped and the run ends green. The rule is an allow-list in `backend/scripts/ci_scope.py`; any other path, no path at all, or a failure of the `changes` job runs everything, and so does every push to `main`. Two documents are read by backend tests and therefore run everything: `docs/10-operations/runbooks.md` (the SMS templates, `tests/test_sms_templates.py`) and `docs/08-technical-spec/schema.sql` (`tests/db/test_schema_rules.py`). A test that starts to read another document must be listed in `backend/tests/test_ci_scope.py`, which fails until it is.

**Superseded runs.** A new push to a pull request cancels the run of the push before it. A run on `main` is never cancelled.

## Third-party data

The territory reference (regions, districts and mahallas of Uzbekistan) is loaded from a seed that is not in this repository. That seed is derived from [`uzinfocom-org/digital-health-ig`](https://github.com/uzinfocom-org/digital-health-ig), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); apostrophes were normalised and the files reshaped. See "Territories and a customer's address" in `docs/09-development-plan/EXPANSION.md`.

## Dependencies

`backend/requirements.lock` pins every backend dependency with hashes for Linux and Python 3.12, the platform of CI and deployment. Regenerate it with [uv](https://docs.astral.sh/uv/) after changing `pyproject.toml`:

```bash
cd backend
uv pip compile pyproject.toml --extra dev --generate-hashes --python-version 3.12 \
  --python-platform x86_64-unknown-linux-gnu --no-header -o requirements.lock
```
