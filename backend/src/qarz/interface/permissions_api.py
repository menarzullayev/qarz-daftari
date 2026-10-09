"""HTTP routes for the permission matrix. Each route is bound to a registered operation by name.

Three are the owner's and one is every member's own; all answer 404 while the platform switch
`permissions_on` is off (`qarz.application.permissions`).
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.permissions import (
    READ_CATALOGUE,
    READ_MEMBER_PERMISSIONS,
    READ_MY_PERMISSIONS,
    SET_MEMBER_PERMISSIONS,
    PermissionService,
)
from qarz.domain.permissions import MAX_OVERRIDES
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]

_Key = Annotated[str, Field(min_length=1, max_length=80)]


class Overrides(BaseModel):
    """The member's whole set of changes: what is granted beyond the role and what is denied despite it."""

    model_config = ConfigDict(extra="forbid", strict=True)

    granted: list[_Key] = Field(max_length=MAX_OVERRIDES)
    denied: list[_Key] = Field(max_length=MAX_OVERRIDES)


def add_permission_routes(app: FastAPI, service: PermissionService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]
    member = "/api/v1/shops/{shop_id}/staff/{membership_id}/permissions"

    @app.get("/api/v1/shops/{shop_id}/permissions", name=READ_CATALOGUE.name)
    async def catalogue(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.catalogue(user_id, shop_id)

    @app.get("/api/v1/shops/{shop_id}/permissions/mine", name=READ_MY_PERMISSIONS.name)
    async def mine(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.mine(user_id, shop_id)

    @app.get(member, name=READ_MEMBER_PERMISSIONS.name)
    async def read_member(shop_id: UUID, membership_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.read_member(user_id, shop_id, membership_id)

    @app.put(member, name=SET_MEMBER_PERMISSIONS.name)
    async def set_member(
        shop_id: UUID, membership_id: UUID, body: Overrides, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.set_member(
            user_id,
            shop_id,
            membership_id,
            granted=body.granted,
            denied=body.denied,
            request_key=idempotency_key,
        )
