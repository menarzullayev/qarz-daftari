"""HTTP routes for deleting a shop: the owner asks, may cancel, and can see when erasure is due."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.shop_deletion import CANCEL_DELETION, READ_DELETION, REQUEST_DELETION, ShopDeletionService
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]


class DeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # The shop's name, typed by the owner to confirm.
    confirm_name: str = Field(max_length=200)


def add_shop_deletion_routes(app: FastAPI, service: ShopDeletionService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]
    path = "/api/v1/shops/{shop_id}/deletion"

    @app.get(path, name=READ_DELETION.name)
    async def read_deletion(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.state(user_id, shop_id)

    @app.post(path, name=REQUEST_DELETION.name, status_code=201)
    async def request_deletion(
        shop_id: UUID, body: DeletionRequest, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.request(user_id, shop_id, body.confirm_name, idempotency_key)

    @app.delete(path, name=CANCEL_DELETION.name)
    async def cancel_deletion(shop_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
        return await service.cancel(user_id, shop_id, idempotency_key)
