"""HTTP routes for the catalog and the review of learned items. Each route is bound to a registered operation."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.catalog import (
    ACCEPT_LEARNED,
    CREATE_ITEM,
    DISMISS_LEARNED,
    HIDE_ITEM,
    LIST_CATALOG,
    MERGE_LEARNED,
    UNHIDE_ITEM,
    UPDATE_ITEM,
    CatalogService,
)
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]


class NewItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(max_length=200)
    unit: str | None = Field(default=None, max_length=40)
    price: int


class ItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = Field(default=None, max_length=200)
    unit: str | None = Field(default=None, max_length=40)
    price: int | None = None


class MergeInto(BaseModel):
    # Not strict: an identifier arrives as a string.
    model_config = ConfigDict(extra="forbid")

    into: UUID


def add_catalog_routes(app: FastAPI, catalog: CatalogService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/shops/{shop_id}/catalog"

    @app.get(base, name=LIST_CATALOG.name)
    async def list_catalog(
        shop_id: UUID,
        user_id: user,
        q: Annotated[str | None, Query()] = None,
        status: Annotated[str, Query()] = "active",
        learned: Annotated[bool | None, Query()] = None,
        cursor: Annotated[str | None, Query(max_length=400)] = None,
        limit: Annotated[int, Query()] = 50,
    ) -> dict[str, Any]:
        return await catalog.list(user_id, shop_id, query=q, status=status, learned=learned, cursor=cursor, limit=limit)

    @app.post(base, name=CREATE_ITEM.name, status_code=201)
    async def create_item(
        shop_id: UUID, body: NewItem, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await catalog.create(user_id, shop_id, body.name, body.unit, body.price, idempotency_key)

    @app.patch(base + "/{item_id}", name=UPDATE_ITEM.name)
    async def update_item(
        shop_id: UUID, item_id: UUID, body: ItemPatch, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await catalog.update(
            user_id, shop_id, item_id, name=body.name, unit=body.unit, price=body.price, request_key=idempotency_key
        )

    @app.post(base + "/{item_id}/hide", name=HIDE_ITEM.name)
    async def hide_item(
        shop_id: UUID, item_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await catalog.set_hidden(user_id, shop_id, item_id, hidden=True, request_key=idempotency_key)

    @app.post(base + "/{item_id}/unhide", name=UNHIDE_ITEM.name)
    async def unhide_item(
        shop_id: UUID, item_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await catalog.set_hidden(user_id, shop_id, item_id, hidden=False, request_key=idempotency_key)

    @app.post(base + "/{item_id}/accept", name=ACCEPT_LEARNED.name)
    async def accept_learned(
        shop_id: UUID, item_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await catalog.accept_learned(user_id, shop_id, item_id, idempotency_key)

    @app.post(base + "/{item_id}/dismiss", name=DISMISS_LEARNED.name)
    async def dismiss_learned(
        shop_id: UUID, item_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await catalog.dismiss_learned(user_id, shop_id, item_id, idempotency_key)

    @app.post(base + "/{item_id}/merge", name=MERGE_LEARNED.name)
    async def merge_learned(
        shop_id: UUID, item_id: UUID, body: MergeInto, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await catalog.merge_learned(user_id, shop_id, item_id, body.into, idempotency_key)
