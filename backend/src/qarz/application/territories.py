"""The territory reference a shop picks a customer's address from (the owner's decisions of 2026-10-10).

Everything here is behind the platform switch `address_on`: while it is off every operation answers as a
route that does not exist, a customer has no `address` anywhere, and a request that names one is
answered as any request with a field the API does not know.

The reference holds places and never people. A customer's address is the shop's own data, on the shop's
own customer row: it is read and written by whoever may read and write the customer, and by nobody else.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from qarz.application.customer_address import address_body, place_body, switched_on
from qarz.application.customers import require_viewable
from qarz.application.errors import NotFound, ValidationFailed
from qarz.application.operations import operation
from qarz.application.ports import Storage
from qarz.application.shops import require_member
from qarz.domain import territories
from qarz.domain.access import Capability
from qarz.domain.promise import tashkent_date

# Whoever may add a customer may look a place up for them.
LIST_REGIONS = operation("territories.regions", Capability.RECORD)
LIST_DISTRICTS = operation("territories.districts", Capability.RECORD)
LIST_MAHALLAS = operation("territories.mahallas", Capability.RECORD)
LIST_STREETS = operation("territories.streets", Capability.RECORD)
READ_LAST_ADDRESS = operation("territories.last", Capability.RECORD)


class TerritoryService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def require_on(self) -> None:
        """Called for every route of the reference before anything else: off is "no such route", to
        everyone, before the caller is asked who they are."""
        async with self._storage.platform() as session:
            if not await switched_on(session):
                raise NotFound()

    async def switched_on(self) -> bool:
        async with self._storage.platform() as session:
            return await switched_on(session)

    async def regions(self, user_id: UUID, shop_id: UUID, *, lang: str) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_REGIONS)
            await require_viewable(session, actor, self._today())
            return {"items": [place_body(place, lang) for place in await session.geo_regions()]}

    async def districts(self, user_id: UUID, shop_id: UUID, region_id: UUID, *, lang: str) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_DISTRICTS)
            await require_viewable(session, actor, self._today())
            return {"items": [place_body(place, lang) for place in await session.geo_districts(region_id)]}

    async def mahallas(
        self,
        user_id: UUID,
        shop_id: UUID,
        *,
        region_id: UUID,
        district_id: UUID | None,
        query: str | None,
        limit: int,
        lang: str,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_MAHALLAS)
            await require_viewable(session, actor, self._today())
            terms = _search(query, limit)
            rows = await session.geo_mahallas(
                region_id=region_id, district_id=district_id, terms=terms, limit=limit + 1
            )
            return {
                "items": [
                    {
                        "id": str(row.mahalla_id),
                        "name": territories.local_name(lang, row.name_uz),
                        # Null where the reference does not know the mahalla's district.
                        "district_id": None if row.district_id is None else str(row.district_id),
                        # The seed's grouping code: tells two mahallas of one name apart, links nothing.
                        "group": row.source_group,
                    }
                    for row in rows[:limit]
                ],
                "more": len(rows) > limit,
            }

    async def streets(
        self, user_id: UUID, shop_id: UUID, *, mahalla_id: UUID, query: str | None, limit: int, lang: str
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_STREETS)
            await require_viewable(session, actor, self._today())
            terms = _search(query, limit)
            rows = await session.geo_streets(mahalla_id=mahalla_id, terms=terms, limit=limit + 1)
            return {
                "items": [
                    {
                        "id": str(row.street_id),
                        "name": territories.local_name(lang, row.name),
                        "kind": row.kind,
                        "road_type": row.road_type,
                    }
                    for row in rows[:limit]
                ],
                "more": len(rows) > limit,
            }

    async def last(self, user_id: UUID, shop_id: UUID, *, lang: str) -> dict[str, Any]:
        """The region, district and mahalla of the address the shop set last, to start the next one
        from. Never a street: that is one customer's."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_LAST_ADDRESS)
            await require_viewable(session, actor, self._today())
            body = address_body(await session.last_address(), lang)
            if body is None:
                return {"address": None}
            return {"address": {key: body[key] for key in ("region", "district", "mahalla")}}


def _search(query: str | None, limit: int) -> list[str]:
    fields: dict[str, str] = {}
    if not 1 <= limit <= territories.MAX_LIST:
        fields["limit"] = f"must be between 1 and {territories.MAX_LIST}"
    terms: list[str] = []
    try:
        terms = territories.search_terms(query)
    except ValueError as error:
        fields["q"] = str(error)
    if fields:
        raise ValidationFailed(fields)
    return terms
