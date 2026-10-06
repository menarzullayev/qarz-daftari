"""HTTP application factory. Only the health endpoint exists so far (technical specification, API contract)."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Response, status

HealthCheck = Callable[[], Awaitable[bool]]


def create_app(database_reachable: HealthCheck) -> FastAPI:
    app = FastAPI(title="Qarz Daftari", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz(response: Response) -> dict[str, str]:
        # Reveals only up or down; no versions, hosts, or error text.
        try:
            ok = await database_reachable()
        except Exception:
            ok = False
        if not ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "down"}
        return {"status": "ok"}

    return app
