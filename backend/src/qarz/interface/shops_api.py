"""HTTP routes for shop operations. Each route is bound to a registered operation by name."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Header
from pydantic import BaseModel, ConfigDict

from qarz.application.shops import READ_SHOP, UPDATE_SHOP, ShopService, ShopUpdate

CurrentUser = Callable[..., Awaitable[UUID]]

# Every write carries this header; a repeat returns the stored result (ADR-006). Its presence and format
# are checked by the application after authorization, so an outsider cannot tell a write route exists.
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


class ShopPatch(BaseModel):
    # Unknown fields are rejected, so a typo is an error and not a silently ignored change.
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = None
    lang: str | None = None
    default_promise_days: int | None = None


def add_shop_routes(app: FastAPI, service: ShopService, current_user: CurrentUser) -> None:
    # Routes are added to the application itself so that the authorization suite can enumerate them.
    user = Annotated[UUID, Depends(current_user)]

    @app.get("/api/v1/shops/{shop_id}", name=READ_SHOP.name)
    async def read_shop(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.read(user_id, shop_id)

    @app.patch("/api/v1/shops/{shop_id}", name=UPDATE_SHOP.name)
    async def update_shop(
        shop_id: UUID, body: ShopPatch, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        change = ShopUpdate(name=body.name, lang=body.lang, default_promise_days=body.default_promise_days)
        return await service.update(user_id, shop_id, change, idempotency_key)
