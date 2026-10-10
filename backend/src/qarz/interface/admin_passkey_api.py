"""Routes of an administrator's passkeys (ADR-017): signing in with one, and keeping one's own."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.admin_passkeys import (
    ADD_PASSKEY,
    LIST_PASSKEYS,
    REMOVE_PASSKEY,
    SIGN_IN_PASSKEY,
    START_PASSKEY,
    AdminPasskeys,
)
from qarz.interface.admin_api import _read as read_body
from qarz.interface.auth_api import SESSION_COOKIE
from qarz.interface.shops_api import IdempotencyKey

BASE = "/api/admin/v1/passkeys"
_B64 = r"^[A-Za-z0-9_-]+$"


class PasskeyAnswer(BaseModel):
    """A device's answer to a challenge. With nothing in it, it is the question: what must be answered?"""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str | None = Field(default=None, min_length=1, max_length=2048, pattern=_B64)
    client_data: str | None = Field(default=None, min_length=1, max_length=4096, pattern=_B64)
    authenticator_data: str | None = Field(default=None, min_length=1, max_length=4096, pattern=_B64)
    signature: str | None = Field(default=None, min_length=1, max_length=2048, pattern=_B64)


class NewPasskey(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    label: str = Field(min_length=1, max_length=60)
    client_data: str = Field(min_length=1, max_length=4096, pattern=_B64)
    authenticator_data: str = Field(min_length=1, max_length=4096, pattern=_B64)
    public_key: str = Field(min_length=1, max_length=2048, pattern=_B64)
    algorithm: int


def add_passkey_sign_in_routes(app: FastAPI, passkeys: AdminPasskeys) -> None:
    """The one route anybody may call: it is how an administrator who is outside comes in.

    Called with no answer it refuses as it refuses a wrong one, and says beside the refusal, in
    `WWW-Authenticate`, what a passkey would have to answer. So there is no route that hands out anything
    to a caller who has shown nothing.
    """

    @app.post("/api/v1/auth/admin-passkey", name=SIGN_IN_PASSKEY.name)
    async def sign_in_passkey(body: PasskeyAnswer, response: Response) -> dict[str, Any]:
        if body.id is None or body.client_data is None or body.authenticator_data is None or body.signature is None:
            raise passkeys.wanted()
        issued = await passkeys.sign_in(
            credential_id=body.id,
            client_data=body.client_data,
            authenticator_data=body.authenticator_data,
            signature=body.signature,
        )
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


def add_passkey_admin_routes(
    app: FastAPI, passkeys: AdminPasskeys, admin_user: Callable[[Request], Awaitable[UUID]]
) -> None:
    """An administrator's own passkeys: only a full administrator reaches these, and only their own."""
    admin = Annotated[UUID, Depends(admin_user)]

    @app.get(BASE, name=LIST_PASSKEYS.name)
    async def list_passkeys(user_id: admin) -> dict[str, Any]:
        return await passkeys.list(user_id)

    # A read: the challenge is kept nowhere, and asking for one changes nothing.
    @app.get(BASE + "/challenge", name=START_PASSKEY.name)
    async def start_passkey(user_id: admin) -> dict[str, Any]:
        return await passkeys.start(user_id)

    @app.post(BASE, name=ADD_PASSKEY.name, status_code=201)
    async def add_passkey(request: Request, user_id: admin, idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
        # Read here, after the caller is known to be an administrator: a malformed body must tell an
        # outsider nothing (see admin_api).
        body = await read_body(request, NewPasskey)
        return await passkeys.add(
            user_id,
            label=body.label,
            client_data=body.client_data,
            authenticator_data=body.authenticator_data,
            public_key=body.public_key,
            algorithm=body.algorithm,
            request_key=idempotency_key,
        )

    @app.post(BASE + "/{passkey_id}/remove", name=REMOVE_PASSKEY.name)
    async def remove_passkey(
        passkey_id: UUID, user_id: admin, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await passkeys.remove(user_id, passkey_id, idempotency_key)
