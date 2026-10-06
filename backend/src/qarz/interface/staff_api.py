"""HTTP routes for staff management. Each route is bound to a registered operation by name."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.staff import (
    ACCEPT_INVITATION,
    CANCEL_INVITATION,
    INVITE_STAFF,
    LIST_INVITATIONS,
    LIST_STAFF,
    REMOVE_STAFF,
    UPDATE_STAFF,
    StaffService,
)
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]


class Invite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    role: str


class MemberPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    role: str | None = None
    status: str | None = None


class Accept(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    token: str = Field(min_length=1, max_length=256)


def add_staff_routes(app: FastAPI, service: StaffService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/shops/{shop_id}/staff"

    @app.get(base, name=LIST_STAFF.name)
    async def list_staff(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.list_members(user_id, shop_id)

    @app.post(base + "/invitations", name=INVITE_STAFF.name, status_code=201)
    async def invite(
        shop_id: UUID, body: Invite, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.invite(user_id, shop_id, body.role, idempotency_key)

    @app.get(base + "/invitations", name=LIST_INVITATIONS.name)
    async def list_invitations(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.list_invitations(user_id, shop_id)

    @app.delete(base + "/invitations/{invitation_id}", name=CANCEL_INVITATION.name)
    async def cancel_invitation(
        shop_id: UUID, invitation_id: str, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.cancel_invitation(user_id, shop_id, invitation_id, idempotency_key)

    @app.patch(base + "/{membership_id}", name=UPDATE_STAFF.name)
    async def update_member(
        shop_id: UUID, membership_id: UUID, body: MemberPatch, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.update_member(
            user_id, shop_id, membership_id, role=body.role, status=body.status, request_key=idempotency_key
        )

    @app.delete(base + "/{membership_id}", name=REMOVE_STAFF.name)
    async def remove_member(
        shop_id: UUID, membership_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.remove_member(user_id, shop_id, membership_id, idempotency_key)

    @app.post("/api/v1/staff-invitations/accept", name=ACCEPT_INVITATION.name)
    async def accept(body: Accept, user_id: user) -> dict[str, Any]:
        return await service.accept_invitation(user_id, body.token)
