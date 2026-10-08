# Qarz Daftari

A credit ledger service for mahalla grocery shops in Uzbekistan, used through Telegram and the web. "Qarz Daftari" is a working title.

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

CI regenerates both and fails when a committed file differs (`python -m qarz.interface.api_description --check` in the backend job, `npm run api:check` in the frontend job). Routes kept out of the schema (provider callbacks, the Telegram webhook, metrics, file links) are not described, and the running application still serves no description.

A route's answer is typed in the description only when the route has a response model from `backend/src/qarz/interface/answers.py`; the others answer with an open object. Only reads carry one, because a write may answer with a stored result of an older shape. The models are closed and strict, so they refuse an answer they do not describe instead of changing it; `backend/tests/api/test_typed_answers.py` compares the bytes with and without a model. In the front end, `Wire` (`frontend/src/shared/api.ts`) names the generated shapes, and the readers and request builders are checked against them with `fieldsOf<Wire["..."]>`.

The database tests create a throwaway database, apply the real migrations, and exercise the schema's rules as the restricted application role. They fail, and are not skipped, when no database is configured.

## Dependencies

`backend/requirements.lock` pins every backend dependency with hashes for Linux and Python 3.12, the platform of CI and deployment. Regenerate it with [uv](https://docs.astral.sh/uv/) after changing `pyproject.toml`:

```bash
cd backend
uv pip compile pyproject.toml --extra dev --generate-hashes --python-version 3.12 \
  --python-platform x86_64-unknown-linux-gnu --no-header -o requirements.lock
```
