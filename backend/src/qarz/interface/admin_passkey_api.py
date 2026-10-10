"""Routes of an administrator's passkeys (ADR-017): signing in with one, and keeping one's own."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.admin_passkeys import (
    ADD_PASSKEY,
    LIST_PASSKEYS,
    PASSKEY_CHALLENGE,
    REMOVE_PASSKEY,
    SIGN_IN_PASSKEY,
    START_PASSKEY,
    AdminPasskeys,
)
from qarz.interface.auth_api import SESSION_COOKIE

BASE = "/api/admin/v1/passkeys"
B64 = Field(min_length=1, max_length=4096, pattern=r"^[A-Za-z0-9_-]+$")


class PasskeyAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = B64
    client_data: str = B64
    authenticator_data: str = B64
    signature: str = B64


class NewPasskey(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    label: str = Field(min_length=1, max_length=60)
    client_data: str = B64
    authenticator_data: str = B64
    public_key: str = B64
    algorithm: int


def add_passkey_sign_in_routes(app: FastAPI, passkeys: AdminPasskeys) -> None:
    """The two routes anybody may call: they are how an administrator who is outside comes in."""

    @app.post("/api/v1/auth/admin-passkey/challenge", name=PASSKEY_CHALLENGE.name)
    async def passkey_challenge() -> dict[str, Any]:
        return passkeys.challenge()

    @app.post("/api/v1/auth/admin-passkey", name=SIGN_IN_PASSKEY.name)
    async def sign_in_passkey(body: PasskeyAnswer, response: Response) -> dict[str, Any]:
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

    @app.post(BASE + "/challenge", name=START_PASSKEY.name)
    async def start_passkey(user_id: admin) -> dict[str, Any]:
        return await passkeys.start(user_id)

    @app.post(BASE, name=ADD_PASSKEY.name, status_code=201)
    async def add_passkey(body: NewPasskey, user_id: admin) -> dict[str, Any]:
        return await passkeys.add(
            user_id,
            label=body.label,
            client_data=body.client_data,
            authenticator_data=body.authenticator_data,
            public_key=body.public_key,
            algorithm=body.algorithm,
        )

    @app.delete(BASE + "/{passkey_id}", name=REMOVE_PASSKEY.name, status_code=204)
    async def remove_passkey(passkey_id: UUID, user_id: admin) -> None:
        await passkeys.remove(user_id, passkey_id)
