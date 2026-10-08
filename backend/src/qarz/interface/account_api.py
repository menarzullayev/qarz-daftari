"""HTTP routes for a user's own shops, the activity log, and ownership transfer."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, ConfigDict

from qarz.application.account import (
    LIST_MY_SHOPS,
    READ_ACTIVITY,
    SET_ACTIVE_SHOP,
    AccountService,
    ActivityService,
)
from qarz.application.ownership import (
    ACCEPT_TRANSFER,
    CANCEL_TRANSFER,
    DECLINE_TRANSFER,
    READ_TRANSFER,
    START_TRANSFER,
    OwnershipService,
)
from qarz.interface.answers import MyShops
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]


class ActiveShop(BaseModel):
    # Not strict: an identifier arrives as a JSON string and must be read as a UUID.
    model_config = ConfigDict(extra="forbid")

    shop_id: UUID


class TransferStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    membership_id: UUID


def add_account_routes(
    app: FastAPI,
    account: AccountService,
    activity: ActivityService,
    ownership: OwnershipService,
    current_user: CurrentUser,
) -> None:
    user = Annotated[UUID, Depends(current_user)]

    @app.get("/api/v1/me/shops", name=LIST_MY_SHOPS.name, response_model=MyShops)
    async def my_shops(user_id: user) -> dict[str, Any]:
        return await account.my_shops(user_id)

    @app.put("/api/v1/me/active-shop", name=SET_ACTIVE_SHOP.name)
    async def set_active_shop(body: ActiveShop, user_id: user) -> dict[str, Any]:
        return await account.set_active_shop(user_id, body.shop_id)

    @app.get("/api/v1/shops/{shop_id}/activity", name=READ_ACTIVITY.name)
    async def list_activity(
        shop_id: UUID,
        user_id: user,
        actor: UUID | None = None,
        action: str | None = None,
        subject: UUID | None = None,
        cursor: Annotated[str | None, Query(max_length=300)] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return await activity.list(
            user_id, shop_id, actor=actor, action=action, subject=subject, cursor=cursor, limit=limit
        )

    transfer = "/api/v1/shops/{shop_id}/ownership-transfer"

    @app.get(transfer, name=READ_TRANSFER.name)
    async def read_transfer(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await ownership.read(user_id, shop_id)

    @app.post(transfer, name=START_TRANSFER.name, status_code=201)
    async def start_transfer(
        shop_id: UUID, body: TransferStart, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await ownership.start(user_id, shop_id, body.membership_id, idempotency_key)

    @app.delete(transfer, name=CANCEL_TRANSFER.name)
    async def cancel_transfer(shop_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
        return await ownership.cancel(user_id, shop_id, idempotency_key)

    @app.post(transfer + "/accept", name=ACCEPT_TRANSFER.name)
    async def accept_transfer(shop_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
        return await ownership.accept(user_id, shop_id, idempotency_key)

    @app.post(transfer + "/decline", name=DECLINE_TRANSFER.name)
    async def decline_transfer(shop_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
        return await ownership.decline(user_id, shop_id, idempotency_key)
