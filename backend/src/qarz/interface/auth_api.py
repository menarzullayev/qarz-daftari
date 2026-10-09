"""Sign-in routes and the session authenticator (ADR-017)."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.auth import (
    READ_ME,
    SIGN_IN_WEB,
    SIGN_IN_WEBAPP,
    SIGN_OUT,
    SIGN_OUT_EVERYWHERE,
    UPDATE_ME,
    AuthService,
)

CurrentUser = Callable[..., Awaitable[UUID]]
# Ends the administrator sessions of a person; given only where the administrators' side is served.
EndAdminSessions = Callable[[UUID], Awaitable[int]]

SESSION_COOKIE = "qd_session"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class SessionAuthenticator:
    """Resolves a request to a user through a server-side session.

    A Mini App sends its token as a bearer header. The web panel uses an HTTP-only cookie and must add the
    CSRF token to every request that changes something. The two kinds are not interchangeable: a cookie
    session's token is refused as a bearer token, which would otherwise sidestep the CSRF check.
    """

    def __init__(self, auth: AuthService) -> None:
        self._auth = auth

    async def user_id(self, request: Request) -> UUID | None:
        header = request.headers.get("Authorization")
        if header is not None:
            scheme, _, token = header.partition(" ")
            if scheme != "Bearer" or not token:
                return None
            info = await self._auth.resolve(token)
            if info is None or info.kind != "webapp":
                return None
            request.state.session_token = token
            return info.user_id

        token = request.cookies.get(SESSION_COOKIE) or ""
        if not token:
            return None
        info = await self._auth.resolve(token)
        if info is None or info.kind != "web":
            return None
        if request.method not in _SAFE_METHODS and not self._auth.csrf_matches(info, request.headers.get(CSRF_HEADER)):
            return None
        request.state.session_token = token
        return info.user_id


class WebAppSignIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    init_data: str = Field(min_length=1, max_length=4096)


class MePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    lang: str


def add_auth_routes(
    app: FastAPI, auth: AuthService, current_user: CurrentUser, end_admin_sessions: EndAdminSessions | None = None
) -> None:
    user = Annotated[UUID, Depends(current_user)]

    @app.post("/api/v1/auth/telegram-webapp", name=SIGN_IN_WEBAPP.name)
    async def sign_in_webapp(body: WebAppSignIn) -> dict[str, Any]:
        issued = await auth.sign_in_webapp(body.init_data)
        return {"token": issued.token, "expires_at": issued.expires_at.isoformat()}

    @app.post("/api/v1/auth/telegram-login", name=SIGN_IN_WEB.name)
    async def sign_in_web(body: dict[str, str | int | None], response: Response) -> dict[str, Any]:
        issued = await auth.sign_in_web(dict(body))
        response.set_cookie(
            SESSION_COOKIE,
            issued.token,
            expires=issued.expires_at,
            path="/api",
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return {"csrf_token": issued.csrf_token, "expires_at": issued.expires_at.isoformat()}

    @app.post("/api/v1/auth/sign-out", name=SIGN_OUT.name, status_code=204)
    async def sign_out(request: Request, response: Response, user_id: user) -> None:
        token = getattr(request.state, "session_token", None)
        if token is not None:
            await auth.sign_out(token)
        response.delete_cookie(SESSION_COOKIE, path="/api")

    @app.post("/api/v1/auth/sign-out-everywhere", name=SIGN_OUT_EVERYWHERE.name, status_code=204)
    async def sign_out_everywhere(response: Response, user_id: user) -> None:
        # Like sign-out: no Idempotency-Key, because a repeat finds nothing left to end, and the same
        # answer whether one session was ended or ten. The caller's own session ends with the others,
        # so the cookie of a web session is cleared too.
        # The person's administrator sessions end as well, and first: they are another role's rows and
        # another transaction, and if that one fails nothing has been ended and the person asks again.
        if end_admin_sessions is not None:
            await end_admin_sessions(user_id)
        await auth.sign_out_everywhere(user_id)
        response.delete_cookie(SESSION_COOKIE, path="/api")

    @app.get("/api/v1/me", name=READ_ME.name)
    async def read_me(user_id: user) -> dict[str, Any]:
        return await auth.me(user_id)

    @app.patch("/api/v1/me", name=UPDATE_ME.name)
    async def update_me(body: MePatch, user_id: user) -> dict[str, Any]:
        return await auth.update_me(user_id, body.lang)
