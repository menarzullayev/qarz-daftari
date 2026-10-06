"""HTTP routes for disputes: the customer opens and withdraws, a manager or owner lists and declines."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.disputes import (
    DECLINE_DISPUTE,
    LIST_DISPUTES,
    OPEN_DISPUTE,
    WITHDRAW_DISPUTE,
    DisputeService,
)
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]


class NewDispute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: UUID
    reason: str = Field(max_length=1000)


class Decline(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: str = Field(max_length=1000)


def add_dispute_routes(app: FastAPI, service: DisputeService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]

    @app.post("/api/v1/me/accounts/{link_id}/disputes", name=OPEN_DISPUTE.name, status_code=201)
    async def open_dispute(link_id: UUID, body: NewDispute, user_id: user) -> dict[str, Any]:
        return await service.open(user_id, link_id, body.entry_id, body.reason)

    @app.post("/api/v1/me/accounts/{link_id}/disputes/{dispute_id}/withdraw", name=WITHDRAW_DISPUTE.name)
    async def withdraw_dispute(link_id: UUID, dispute_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.withdraw(user_id, link_id, dispute_id)

    @app.get("/api/v1/shops/{shop_id}/disputes", name=LIST_DISPUTES.name)
    async def list_disputes(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.list_open(user_id, shop_id)

    @app.post("/api/v1/shops/{shop_id}/disputes/{dispute_id}/decline", name=DECLINE_DISPUTE.name)
    async def decline_dispute(
        shop_id: UUID, dispute_id: UUID, body: Decline, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.decline(user_id, shop_id, dispute_id, body.reason, idempotency_key)
