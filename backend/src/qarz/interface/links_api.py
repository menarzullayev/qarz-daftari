"""HTTP routes for connecting customers: personal links, the counter code, the waiting list."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict

from qarz.application.links import (
    ATTACH_WAITING,
    CREATE_LINK,
    DISMISS_WAITING,
    LIST_WAITING,
    READ_COUNTER_CODE,
    READ_LINK,
    ROTATE_COUNTER_CODE,
    LinkService,
)
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]


class Attach(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: UUID


def add_link_routes(app: FastAPI, service: LinkService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/shops/{shop_id}"

    @app.get(base + "/customers/{customer_id}/link", name=READ_LINK.name)
    async def read_link(shop_id: UUID, customer_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.link_state(user_id, shop_id, customer_id)

    @app.post(base + "/customers/{customer_id}/link", name=CREATE_LINK.name, status_code=201)
    async def create_link(
        shop_id: UUID, customer_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.create_link(user_id, shop_id, customer_id, idempotency_key)

    @app.get(base + "/counter-code", name=READ_COUNTER_CODE.name)
    async def read_counter_code(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.counter_code(user_id, shop_id)

    @app.post(base + "/counter-code", name=ROTATE_COUNTER_CODE.name, status_code=201)
    async def rotate_counter_code(
        shop_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.rotate_counter_code(user_id, shop_id, idempotency_key)

    @app.get(base + "/waiting", name=LIST_WAITING.name)
    async def list_waiting(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.waiting(user_id, shop_id)

    @app.post(base + "/waiting/{link_id}/attach", name=ATTACH_WAITING.name)
    async def attach_waiting(
        shop_id: UUID, link_id: UUID, body: Attach, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.attach(user_id, shop_id, link_id, body.customer_id, idempotency_key)

    @app.post(base + "/waiting/{link_id}/dismiss", name=DISMISS_WAITING.name)
    async def dismiss_waiting(
        shop_id: UUID, link_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.dismiss(user_id, shop_id, link_id, idempotency_key)
