"""HTTP routes for date change requests: the customer asks, a manager or owner lists, accepts and declines."""

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.date_requests import (
    ACCEPT_DATE_REQUEST,
    DECLINE_DATE_REQUEST,
    LIST_DATE_REQUESTS,
    OPEN_DATE_REQUEST,
    DateRequestService,
)
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]


class NewDateRequest(BaseModel):
    # Not strict: an identifier and a date arrive as strings.
    model_config = ConfigDict(extra="forbid")

    entry_id: UUID
    requested_date: date
    reason: str | None = Field(default=None, max_length=1000)


class DateDecline(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: str | None = Field(default=None, max_length=1000)


def add_date_request_routes(app: FastAPI, service: DateRequestService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/shops/{shop_id}/date-requests"

    @app.post("/api/v1/me/accounts/{link_id}/date-requests", name=OPEN_DATE_REQUEST.name, status_code=201)
    async def open_date_request(link_id: UUID, body: NewDateRequest, user_id: user) -> dict[str, Any]:
        return await service.open(user_id, link_id, body.entry_id, body.requested_date, body.reason)

    @app.get(base, name=LIST_DATE_REQUESTS.name)
    async def list_date_requests(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.list_open(user_id, shop_id)

    @app.post(base + "/{request_id}/accept", name=ACCEPT_DATE_REQUEST.name)
    async def accept_date_request(
        shop_id: UUID, request_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.accept(user_id, shop_id, request_id, idempotency_key)

    @app.post(base + "/{request_id}/decline", name=DECLINE_DATE_REQUEST.name)
    async def decline_date_request(
        shop_id: UUID,
        request_id: UUID,
        user_id: user,
        body: DateDecline | None = None,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        return await service.decline(
            user_id, shop_id, request_id, None if body is None else body.reason, idempotency_key
        )
