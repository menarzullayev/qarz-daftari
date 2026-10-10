"""HTTP routes of the territory reference (the owner's decisions of 2026-10-10).

Every route here depends on `switched_on` before anything else, so while the platform switch
`address_on` is off each of them answers as a route that does not exist: to a member of staff, to a
stranger and to someone who is not signed in, alike. Each route is bound to a registered operation.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request

from qarz.application.territories import (
    LIST_DISTRICTS,
    LIST_MAHALLAS,
    LIST_REGIONS,
    LIST_STREETS,
    READ_LAST_ADDRESS,
    TerritoryService,
)

CurrentUser = Callable[..., Awaitable[UUID]]


def _lang(request: Request) -> str:
    return str(getattr(request.state, "lang", "uz"))


def add_territory_routes(app: FastAPI, territories: TerritoryService, current_user: CurrentUser) -> None:
    async def switched_on() -> None:
        await territories.require_on()

    # A route's own dependencies are resolved before those of its parameters: the switch is asked before
    # the caller is, so an "off" answer is the same with and without a session.
    behind_switch = [Depends(switched_on)]
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/shops/{shop_id}/territories"

    @app.get(base + "/regions", name=LIST_REGIONS.name, dependencies=behind_switch)
    async def regions(shop_id: UUID, request: Request, user_id: user) -> dict[str, Any]:
        return await territories.regions(user_id, shop_id, lang=_lang(request))

    @app.get(base + "/districts", name=LIST_DISTRICTS.name, dependencies=behind_switch)
    async def districts(shop_id: UUID, request: Request, user_id: user, region: UUID) -> dict[str, Any]:
        return await territories.districts(user_id, shop_id, region, lang=_lang(request))

    @app.get(base + "/mahallas", name=LIST_MAHALLAS.name, dependencies=behind_switch)
    async def mahallas(
        shop_id: UUID,
        request: Request,
        user_id: user,
        region: UUID,
        # Left out, every mahalla of the region. Given, the district's own and those whose district the
        # reference does not know.
        district: UUID | None = None,
        q: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query()] = 30,
    ) -> dict[str, Any]:
        return await territories.mahallas(
            user_id, shop_id, region_id=region, district_id=district, query=q, limit=limit, lang=_lang(request)
        )

    @app.get(base + "/streets", name=LIST_STREETS.name, dependencies=behind_switch)
    async def streets(
        shop_id: UUID,
        request: Request,
        user_id: user,
        mahalla: UUID,
        q: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query()] = 30,
    ) -> dict[str, Any]:
        return await territories.streets(
            user_id, shop_id, mahalla_id=mahalla, query=q, limit=limit, lang=_lang(request)
        )

    @app.get(base + "/last", name=READ_LAST_ADDRESS.name, dependencies=behind_switch)
    async def last(shop_id: UUID, request: Request, user_id: user) -> dict[str, Any]:
        return await territories.last(user_id, shop_id, lang=_lang(request))
