"""HTTP routes of the shared product catalogue (the founder's decisions of 2026-10-10).

Every route here depends on `switched_on` before anything else, so while the platform switch
`catalog_on` is off each of them answers as a route that does not exist: to a member of staff, to an
administrator, to a stranger and to someone who is not signed in, alike. Each route but the photo's is
bound to a registered operation.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.shared_catalog import (
    APPROVE_SUGGESTION,
    IMAGE_PATH,
    LIST_SUGGESTIONS,
    LOOKUP_SHARED,
    PICK_SHARED,
    REJECT_SUGGESTION,
    SEARCH_SHARED,
    AdminSharedCatalogService,
    SharedCatalogService,
)
from qarz.interface.admin_api import _read as read_body
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]

# A photo's address is its content's SHA-256, so what it serves never changes: any cache may keep it.
IMAGE_CACHE = "public, max-age=31536000, immutable"


class PickBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # The shop's own price, in so'm. The catalogue's approximate price is never taken for it.
    price: int
    # Left out, the unit the catalogue names.
    unit: str | None = Field(default=None, max_length=40)


class ApproveSuggestionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # For an item: the names it gets in the catalogue (at least one) and its category. Not read for a barcode.
    name_ru: str | None = Field(default=None, max_length=600)
    name_uz: str | None = Field(default=None, max_length=600)
    category: str | None = Field(default=None, max_length=40)


def _lang(request: Request) -> str:
    return str(getattr(request.state, "lang", "uz"))


def add_shared_catalog_routes(app: FastAPI, catalog: SharedCatalogService, current_user: CurrentUser) -> None:
    async def switched_on() -> None:
        await catalog.require_on()

    # A route's own dependencies are resolved before those of its parameters: the switch is asked before
    # the caller is, so an "off" answer is the same with and without a session.
    behind_switch = [Depends(switched_on)]
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/shops/{shop_id}/shared-catalog"

    @app.get(base, name=SEARCH_SHARED.name, dependencies=behind_switch)
    async def search(
        shop_id: UUID,
        request: Request,
        user_id: user,
        q: Annotated[str | None, Query()] = None,
        category: Annotated[str | None, Query(max_length=40)] = None,
        cursor: Annotated[str | None, Query(max_length=1200)] = None,
        limit: Annotated[int, Query()] = 30,
    ) -> dict[str, Any]:
        return await catalog.search(
            user_id, shop_id, query=q, category=category, cursor=cursor, limit=limit, lang=_lang(request)
        )

    @app.get(base + "/lookup", name=LOOKUP_SHARED.name, dependencies=behind_switch)
    async def lookup(
        shop_id: UUID, request: Request, user_id: user, code: Annotated[str, Query(max_length=100)]
    ) -> dict[str, Any]:
        return await catalog.lookup(user_id, shop_id, code, lang=_lang(request))

    @app.post(base + "/{item_id}/pick", name=PICK_SHARED.name, dependencies=behind_switch)
    async def pick(
        shop_id: UUID,
        item_id: UUID,
        body: PickBody,
        request: Request,
        user_id: user,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        return await catalog.pick(
            user_id,
            shop_id,
            item_id,
            price=body.price,
            unit=body.unit,
            lang=_lang(request),
            request_key=idempotency_key,
        )


def add_shared_image_route(app: FastAPI, catalog: SharedCatalogService) -> None:
    """Where a photo of the catalogue is served: by this host, never by the address it was copied from.
    Not an operation of the API: a product's photo is nobody's data, and whoever asks is given it."""

    @app.get(IMAGE_PATH + "/{key}", include_in_schema=False)
    async def serve_image(key: str) -> Response:
        mime, content = await catalog.image(key)
        return Response(
            content,
            media_type=mime,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": IMAGE_CACHE},
        )


def add_admin_shared_catalog_routes(app: FastAPI, service: AdminSharedCatalogService, admin_user: CurrentUser) -> None:
    async def switched_on() -> None:
        await service.require_on()

    behind_switch = [Depends(switched_on)]
    admin = Annotated[UUID, Depends(admin_user)]
    base = "/api/admin/v1/catalog/suggestions"

    @app.get(base, name=LIST_SUGGESTIONS.name, dependencies=behind_switch)
    async def list_suggestions(
        user_id: admin, status: str | None = None, cursor: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        return await service.list_suggestions(user_id, status=status, cursor=cursor, limit=limit)

    @app.post(base + "/{suggestion_id}/approve", name=APPROVE_SUGGESTION.name, dependencies=behind_switch)
    async def approve(
        suggestion_id: UUID, request: Request, user_id: admin, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        # Read here, after the caller is known to be an administrator: see admin_api. A barcode needs
        # no body at all.
        raw = await request.body()
        given = ApproveSuggestionBody() if not raw.strip() else await read_body(request, ApproveSuggestionBody)
        return await service.approve(
            user_id,
            suggestion_id,
            name_ru=given.name_ru,
            name_uz=given.name_uz,
            category=given.category,
            request_key=idempotency_key,
        )

    @app.post(base + "/{suggestion_id}/reject", name=REJECT_SUGGESTION.name, dependencies=behind_switch)
    async def reject(suggestion_id: UUID, user_id: admin, idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
        return await service.reject(user_id, suggestion_id, idempotency_key)
