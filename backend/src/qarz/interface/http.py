"""HTTP application factory (technical specification, API contract)."""

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from qarz.application.auth import AuthService
from qarz.application.errors import AppError, Unauthenticated
from qarz.application.ports import Storage
from qarz.application.shops import ShopService
from qarz.application.telegram_updates import UpdateProcessor
from qarz.interface.auth_api import SessionAuthenticator, add_auth_routes
from qarz.interface.errors import app_error_handler, error_response
from qarz.interface.shops_api import add_shop_routes
from qarz.interface.telegram_webhook import add_webhook_route

HealthCheck = Callable[[], Awaitable[bool]]


class Authenticator(Protocol):
    """Resolves a request to the signed-in user."""

    async def user_id(self, request: Request) -> UUID | None: ...


def create_app(
    database_reachable: HealthCheck,
    storage: Storage | None = None,
    *,
    auth: AuthService | None = None,
    authenticator: Authenticator | None = None,
    webhook_secret: str | None = None,
) -> FastAPI:
    """Build the application.

    With only a health check it serves `/healthz`. With storage and an auth service it serves the API,
    authenticating through Telegram-backed sessions; `authenticator` replaces that only in tests.
    """
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

    app.add_exception_handler(AppError, app_error_handler)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = {".".join(str(part) for part in error["loc"][1:]) or "_": error["msg"] for error in exc.errors()}
        # A malformed identifier in the path must look like any other missing record.
        if any(error["loc"][0] == "path" for error in exc.errors()):
            return error_response("NOT_FOUND", getattr(request.state, "lang", "uz"))
        return error_response("VALIDATION", getattr(request.state, "lang", "uz"), fields)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code in (404, 405) else "ERROR"
        response = error_response(code, getattr(request.state, "lang", "uz"))
        response.status_code = 404 if code == "NOT_FOUND" else exc.status_code
        return response

    if storage is not None and auth is not None:
        resolver: Authenticator = authenticator or SessionAuthenticator(auth)

        async def current_user(request: Request) -> UUID:
            user_id = await resolver.user_id(request)
            if user_id is None:
                raise Unauthenticated()
            request.state.lang = await storage.user_language(user_id) or "uz"
            return user_id

        add_auth_routes(app, auth, current_user)
        add_shop_routes(app, ShopService(storage), current_user)

    if webhook_secret is not None and storage is not None:
        add_webhook_route(app, UpdateProcessor(storage), webhook_secret)

    return app
