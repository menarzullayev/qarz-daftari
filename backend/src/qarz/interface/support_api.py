"""HTTP routes for support access (REQ-059): the administrator's under `/api/admin/v1`, the owner's
under the shop. Each route is bound to a registered operation by name.

The administrator's routes take the dependency of the administrator's side, so everything said there
holds here: allow-list, active account, admin session, and the answer of an unknown route for anyone else.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from qarz.application.errors import ValidationFailed
from qarz.application.support_access import (
    CLOSE_SUPPORT,
    END_SHOP_SUPPORT,
    LIST_SHOP_SUPPORT,
    LIST_SUPPORT,
    OPEN_SUPPORT,
    SUPPORT_LIST_CUSTOMERS,
    SUPPORT_READ_CUSTOMER,
    SupportAccessRequired,
    SupportAccessService,
)
from qarz.domain.support_access import DEFAULT_HOURS
from qarz.interface.observability import SECURITY_SUPPORT_ACCESS_OPENED, SECURITY_SUPPORT_ACCESS_REQUIRED
from qarz.interface.shops_api import IdempotencyKey

UserDependency = Callable[..., Awaitable[UUID]]
ADMIN = "/api/admin/v1"


class OpenBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: str = Field(max_length=2000)
    hours: int = DEFAULT_HOURS


def add_owner_support_routes(app: FastAPI, service: SupportAccessService, current_user: UserDependency) -> None:
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/shops/{shop_id}/support-access"

    @app.get(base, name=LIST_SHOP_SUPPORT.name)
    async def list_for_shop(shop_id: UUID, user_id: user, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        return await service.list_for_shop(user_id, shop_id, cursor=cursor, limit=limit)

    @app.post(base + "/{access_id}/end", name=END_SHOP_SUPPORT.name)
    async def end_by_owner(
        shop_id: UUID, access_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.end_by_owner(user_id, shop_id, access_id, idempotency_key)


def add_admin_support_routes(app: FastAPI, service: SupportAccessService, admin_user: UserDependency) -> None:
    admin = Annotated[UUID, Depends(admin_user)]

    @app.post(ADMIN + "/shops/{shop_id}/support-access", name=OPEN_SUPPORT.name, status_code=201)
    async def open_access(
        shop_id: UUID, request: Request, user_id: admin, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        # Read here, after the caller is known to be an administrator: see admin_api.
        try:
            body = OpenBody.model_validate_json(await request.body())
        except ValidationError as error:
            fields = {".".join(str(part) for part in item["loc"]) or "_": item["msg"] for item in error.errors()}
            raise ValidationFailed(fields) from error
        opened = await service.open(user_id, shop_id, body.reason, body.hours, idempotency_key)
        # An administrator can now read a shop's data: counted and logged as a security event.
        request.state.security_event = SECURITY_SUPPORT_ACCESS_OPENED
        return opened

    @app.post(ADMIN + "/shops/{shop_id}/support-access/close", name=CLOSE_SUPPORT.name)
    async def close_access(shop_id: UUID, user_id: admin, idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
        return await service.close(user_id, shop_id, idempotency_key)

    @app.get(ADMIN + "/support-access", name=LIST_SUPPORT.name)
    async def list_all(
        user_id: admin, shop_id: UUID | None = None, open: bool = False, cursor: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        return await service.list_all(user_id, shop_id=shop_id, open_only=open, cursor=cursor, limit=limit)

    @app.get(ADMIN + "/shops/{shop_id}/customers", name=SUPPORT_LIST_CUSTOMERS.name)
    async def list_customers(
        shop_id: UUID,
        request: Request,
        user_id: admin,
        q: str | None = None,
        status: str = "active",
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        try:
            return await service.list_customers(user_id, shop_id, query=q, status=status, cursor=cursor, limit=limit)
        except SupportAccessRequired:
            request.state.security_event = SECURITY_SUPPORT_ACCESS_REQUIRED
            raise

    @app.get(ADMIN + "/shops/{shop_id}/customers/{customer_id}", name=SUPPORT_READ_CUSTOMER.name)
    async def read_customer(shop_id: UUID, customer_id: UUID, request: Request, user_id: admin) -> dict[str, Any]:
        try:
            return await service.read_customer(user_id, shop_id, customer_id)
        except SupportAccessRequired:
            request.state.security_event = SECURITY_SUPPORT_ACCESS_REQUIRED
            raise
