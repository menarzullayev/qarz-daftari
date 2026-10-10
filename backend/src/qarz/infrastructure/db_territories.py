"""PostgreSQL storage of the territory reference and of a customer's address (migration 0051).

`PgTenantSession` inherits `TerritoryQueries` and `PgPlatformSession` inherits `TerritoryAdminQueries`.
SQL here is composed only from the module-level constants below, with every value bound as a parameter
(tests/test_sql_composition.py).
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from qarz.application.territories_ports import (
    AddressRow,
    Mahalla,
    Place,
    SeedDistrict,
    SeedMahalla,
    SeedRegion,
    SeedStreet,
    Street,
)
from qarz.domain.territories import Address, Chain

_REGIONS = (
    "SELECT r.id, r.name_uz, r.name_ru, r.name_en FROM geo_region r "
    "WHERE r.status = 'active' ORDER BY r.name_norm, r.id"
)
_DISTRICTS = (
    "SELECT d.id, d.name_uz, d.name_ru, d.name_en FROM geo_district d "
    "WHERE d.region_id = :region AND d.status = 'active' ORDER BY d.name_norm, d.id"
)
# With a district: its own mahallas, and those the reference cannot place in any district.
_MAHALLAS = (
    "SELECT m.id, m.name_uz, m.district_id, m.source_group FROM geo_mahalla m "
    "WHERE m.region_id = :region AND m.status = 'active' "
    "AND (CAST(:district AS uuid) IS NULL OR m.district_id IS NULL OR m.district_id = CAST(:district AS uuid)) "
    "AND (CAST(:parts AS text[]) IS NULL OR m.name_norm LIKE ALL (CAST(:parts AS text[]))) "
    "ORDER BY m.name_norm, m.source_group NULLS FIRST, m.id LIMIT :limit"
)
_STREETS = (
    "SELECT s.id, s.name, s.kind, s.road_type FROM geo_street s "
    "WHERE s.mahalla_id = :mahalla AND s.status = 'active' "
    "AND (CAST(:parts AS text[]) IS NULL OR s.name_norm LIKE ALL (CAST(:parts AS text[]))) "
    "ORDER BY s.name_norm, s.id LIMIT :limit"
)
_CHAIN = (
    "SELECT EXISTS (SELECT 1 FROM geo_region r WHERE r.id = :region) AS region, "
    "(SELECT d.region_id FROM geo_district d WHERE d.id = CAST(:district AS uuid)) AS district_region, "
    "(SELECT m.region_id FROM geo_mahalla m WHERE m.id = CAST(:mahalla AS uuid)) AS mahalla_region, "
    "(SELECT m.district_id FROM geo_mahalla m WHERE m.id = CAST(:mahalla AS uuid)) AS mahalla_district, "
    "(SELECT s.mahalla_id FROM geo_street s WHERE s.id = CAST(:street AS uuid)) AS street_mahalla"
)
# A retired place is still named here: it is no longer offered, and a customer who has it keeps it.
_ADDRESS = (
    "SELECT c.id, c.geo_region_id, c.geo_district_id, c.geo_mahalla_id, c.geo_street_id, c.street_text, "
    "r.name_uz AS region_uz, r.name_ru AS region_ru, r.name_en AS region_en, "
    "d.name_uz AS district_uz, d.name_ru AS district_ru, d.name_en AS district_en, "
    "m.name_uz AS mahalla_name, s.name AS street_name "
    "FROM customer c JOIN geo_region r ON r.id = c.geo_region_id "
    "LEFT JOIN geo_district d ON d.id = c.geo_district_id "
    "LEFT JOIN geo_mahalla m ON m.id = c.geo_mahalla_id "
    "LEFT JOIN geo_street s ON s.id = c.geo_street_id "
)
_ADDRESSES_OF = _ADDRESS + "WHERE c.id = ANY(CAST(:ids AS uuid[]))"
_LAST_ADDRESS = _ADDRESS + "WHERE c.address_at IS NOT NULL ORDER BY c.address_at DESC, c.id LIMIT 1"
_SET_ADDRESS = (
    "UPDATE customer SET geo_region_id = CAST(:region AS uuid), geo_district_id = CAST(:district AS uuid), "
    "geo_mahalla_id = CAST(:mahalla AS uuid), geo_street_id = CAST(:street AS uuid), "
    "street_text = CAST(:street_text AS text), "
    "address_at = CASE WHEN CAST(:region AS uuid) IS NULL THEN NULL ELSE CAST(:now AS timestamptz) END "
    "WHERE id = :id"
)

_UPSERT_REGIONS = (
    "INSERT INTO geo_region AS g (id, soato, name_uz, name_ru, name_en, name_norm) "
    "SELECT gen_random_uuid(), r.soato, r.name_uz, r.name_ru, r.name_en, r.name_norm "
    "FROM unnest(CAST(:soatos AS text[]), CAST(:names_uz AS text[]), CAST(:names_ru AS text[]), "
    "            CAST(:names_en AS text[]), CAST(:norms AS text[])) "
    "  AS r(soato, name_uz, name_ru, name_en, name_norm) "
    "ON CONFLICT (soato) DO UPDATE SET name_uz = EXCLUDED.name_uz, name_ru = EXCLUDED.name_ru, "
    "  name_en = EXCLUDED.name_en, name_norm = EXCLUDED.name_norm, status = 'active' "
    "RETURNING (xmax = 0) AS added"
)
_UPSERT_DISTRICTS = (
    "INSERT INTO geo_district AS g "
    "  (id, soato, region_id, name_uz, name_ru, name_en, name_norm, population, families, households) "
    "SELECT gen_random_uuid(), r.soato, p.id, r.name_uz, r.name_ru, r.name_en, r.name_norm, r.population, "
    "       r.families, r.households "
    "FROM unnest(CAST(:soatos AS text[]), CAST(:regions AS text[]), CAST(:names_uz AS text[]), "
    "            CAST(:names_ru AS text[]), CAST(:names_en AS text[]), CAST(:norms AS text[]), "
    "            CAST(:populations AS bigint[]), CAST(:families AS bigint[]), CAST(:households AS bigint[])) "
    "  AS r(soato, region_soato, name_uz, name_ru, name_en, name_norm, population, families, households) "
    "JOIN geo_region p ON p.soato = r.region_soato "
    "ON CONFLICT (soato) DO UPDATE SET region_id = EXCLUDED.region_id, name_uz = EXCLUDED.name_uz, "
    "  name_ru = EXCLUDED.name_ru, name_en = EXCLUDED.name_en, name_norm = EXCLUDED.name_norm, "
    "  population = EXCLUDED.population, families = EXCLUDED.families, households = EXCLUDED.households, "
    "  status = 'active' "
    "RETURNING (xmax = 0) AS added"
)
_UPSERT_MAHALLAS = (
    "INSERT INTO geo_mahalla AS g (id, source_key, code, region_id, district_id, source_group, name_uz, name_norm) "
    "SELECT gen_random_uuid(), r.source_key, r.code, p.id, d.id, r.source_group, r.name_uz, r.name_norm "
    "FROM unnest(CAST(:keys AS text[]), CAST(:codes AS text[]), CAST(:regions AS text[]), "
    "            CAST(:districts AS text[]), CAST(:groups AS text[]), CAST(:names AS text[]), "
    "            CAST(:norms AS text[])) "
    "  AS r(source_key, code, region_soato, district_soato, source_group, name_uz, name_norm) "
    "JOIN geo_region p ON p.soato = r.region_soato "
    "LEFT JOIN geo_district d ON d.soato = r.district_soato "
    # A row that names a district the reference does not have is left out rather than loaded without one.
    "WHERE r.district_soato IS NULL OR d.id IS NOT NULL "
    "ON CONFLICT (source_key) DO UPDATE SET code = EXCLUDED.code, region_id = EXCLUDED.region_id, "
    "  district_id = EXCLUDED.district_id, source_group = EXCLUDED.source_group, name_uz = EXCLUDED.name_uz, "
    "  name_norm = EXCLUDED.name_norm, status = 'active' "
    "RETURNING (xmax = 0) AS added"
)
_UPSERT_STREETS = (
    "INSERT INTO geo_street AS g (id, mahalla_id, name, name_norm, kind, road_type) "
    "SELECT gen_random_uuid(), r.mahalla_id, r.name, r.name_norm, r.kind, r.road_type "
    "FROM unnest(CAST(:mahallas AS uuid[]), CAST(:names AS text[]), CAST(:norms AS text[]), "
    "            CAST(:kinds AS text[]), CAST(:road_types AS text[])) "
    "  AS r(mahalla_id, name, name_norm, kind, road_type) "
    "ON CONFLICT (mahalla_id, name_norm) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind, "
    "  road_type = EXCLUDED.road_type, status = 'active' "
    "RETURNING (xmax = 0) AS added"
)
_MAHALLAS_BY_DISTRICT = (
    "SELECT m.id, d.soato, m.name_norm FROM geo_mahalla m JOIN geo_district d ON d.id = m.district_id "
    "WHERE m.status = 'active'"
)
_RETIRE_REGIONS = (
    "UPDATE geo_region SET status = 'retired' WHERE status = 'active' AND NOT (soato = ANY(CAST(:keep AS text[])))"
)
_RETIRE_DISTRICTS = (
    "UPDATE geo_district SET status = 'retired' WHERE status = 'active' AND NOT (soato = ANY(CAST(:keep AS text[])))"
)
_RETIRE_MAHALLAS = (
    "UPDATE geo_mahalla SET status = 'retired' "
    "WHERE status = 'active' AND NOT (source_key = ANY(CAST(:keep AS text[])))"
)
_RETIRE_STREETS = (
    "UPDATE geo_street s SET status = 'retired' WHERE s.status = 'active' AND NOT EXISTS ("
    "  SELECT 1 FROM unnest(CAST(:mahallas AS uuid[]), CAST(:names AS text[])) AS k(mahalla_id, name_norm) "
    "  WHERE k.mahalla_id = s.mahalla_id AND k.name_norm = s.name_norm)"
)


def _like(terms: Sequence[str]) -> list[str] | None:
    """Each word as a pattern that finds it anywhere in a name; None for no search."""
    if not terms:
        return None
    return ["%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for term in terms]


def _place(row: Any) -> Place:
    return Place(row.id, row.name_uz, row.name_ru, row.name_en)


def _address_row(row: Any) -> AddressRow:
    return AddressRow(
        Address(row.geo_region_id, row.geo_district_id, row.geo_mahalla_id, row.geo_street_id, row.street_text),
        Place(row.geo_region_id, row.region_uz, row.region_ru, row.region_en),
        None
        if row.geo_district_id is None
        else Place(row.geo_district_id, row.district_uz, row.district_ru, row.district_en),
        row.mahalla_name,
        row.street_name,
    )


def _added(rows: Sequence[Any]) -> int:
    return sum(1 for row in rows if row.added)


class TerritoryQueries:
    _conn: AsyncConnection
    _shop_id: UUID

    async def geo_regions(self) -> list[Place]:
        return [_place(row) for row in (await self._conn.execute(text(_REGIONS))).all()]

    async def geo_districts(self, region_id: UUID) -> list[Place]:
        return [_place(row) for row in (await self._conn.execute(text(_DISTRICTS), {"region": region_id})).all()]

    async def geo_mahallas(
        self, *, region_id: UUID, district_id: UUID | None, terms: Sequence[str], limit: int
    ) -> list[Mahalla]:
        rows = (
            await self._conn.execute(
                text(_MAHALLAS), {"region": region_id, "district": district_id, "parts": _like(terms), "limit": limit}
            )
        ).all()
        return [Mahalla(row.id, row.name_uz, row.district_id, row.source_group) for row in rows]

    async def geo_streets(self, *, mahalla_id: UUID, terms: Sequence[str], limit: int) -> list[Street]:
        rows = (
            await self._conn.execute(text(_STREETS), {"mahalla": mahalla_id, "parts": _like(terms), "limit": limit})
        ).all()
        return [Street(row.id, row.name, row.kind, row.road_type) for row in rows]

    async def geo_chain(self, address: Address) -> Chain:
        row = (
            await self._conn.execute(
                text(_CHAIN),
                {
                    "region": address.region_id,
                    "district": address.district_id,
                    "mahalla": address.mahalla_id,
                    "street": address.street_id,
                },
            )
        ).one()
        return Chain(
            bool(row.region), row.district_region, row.mahalla_region, row.mahalla_district, row.street_mahalla
        )

    async def customer_addresses(self, customer_ids: Sequence[UUID]) -> dict[UUID, AddressRow]:
        if not customer_ids:
            return {}
        rows = (await self._conn.execute(text(_ADDRESSES_OF), {"ids": list(customer_ids)})).all()
        return {row.id: _address_row(row) for row in rows}

    async def set_customer_address(self, customer_id: UUID, address: Address | None, now: datetime) -> None:
        await self._conn.execute(
            text(_SET_ADDRESS),
            {
                "id": customer_id,
                "region": None if address is None else address.region_id,
                "district": None if address is None else address.district_id,
                "mahalla": None if address is None else address.mahalla_id,
                "street": None if address is None else address.street_id,
                "street_text": None if address is None else address.street_text,
                "now": now,
            },
        )

    async def last_address(self) -> AddressRow | None:
        row = (await self._conn.execute(text(_LAST_ADDRESS))).first()
        return None if row is None else _address_row(row)


class TerritoryAdminQueries:
    _conn: AsyncConnection

    async def upsert_geo_regions(self, rows: Sequence[SeedRegion]) -> int:
        if not rows:
            return 0
        result = await self._conn.execute(
            text(_UPSERT_REGIONS),
            {
                "soatos": [row.soato for row in rows],
                "names_uz": [row.name_uz for row in rows],
                "names_ru": [row.name_ru for row in rows],
                "names_en": [row.name_en for row in rows],
                "norms": [row.name_norm for row in rows],
            },
        )
        return _added(result.all())

    async def upsert_geo_districts(self, rows: Sequence[SeedDistrict]) -> int:
        if not rows:
            return 0
        result = await self._conn.execute(
            text(_UPSERT_DISTRICTS),
            {
                "soatos": [row.soato for row in rows],
                "regions": [row.region_soato for row in rows],
                "names_uz": [row.name_uz for row in rows],
                "names_ru": [row.name_ru for row in rows],
                "names_en": [row.name_en for row in rows],
                "norms": [row.name_norm for row in rows],
                "populations": [row.population for row in rows],
                "families": [row.families for row in rows],
                "households": [row.households for row in rows],
            },
        )
        return _added(result.all())

    async def upsert_geo_mahallas(self, rows: Sequence[SeedMahalla]) -> int:
        if not rows:
            return 0
        result = await self._conn.execute(
            text(_UPSERT_MAHALLAS),
            {
                "keys": [row.source_key for row in rows],
                "codes": [row.code for row in rows],
                "regions": [row.region_soato for row in rows],
                "districts": [row.district_soato for row in rows],
                "groups": [row.source_group for row in rows],
                "names": [row.name_uz for row in rows],
                "norms": [row.name_norm for row in rows],
            },
        )
        return _added(result.all())

    async def upsert_geo_streets(self, rows: Sequence[SeedStreet]) -> int:
        if not rows:
            return 0
        result = await self._conn.execute(
            text(_UPSERT_STREETS),
            {
                "mahallas": [row.mahalla_id for row in rows],
                "names": [row.name for row in rows],
                "norms": [row.name_norm for row in rows],
                "kinds": [row.kind for row in rows],
                "road_types": [row.road_type for row in rows],
            },
        )
        return _added(result.all())

    async def geo_mahallas_by_district(self) -> list[tuple[UUID, str, str]]:
        rows = (await self._conn.execute(text(_MAHALLAS_BY_DISTRICT))).all()
        return [(row.id, str(row.soato), str(row.name_norm)) for row in rows]

    async def retire_geo_regions(self, keep: Sequence[str]) -> int:
        return int((await self._conn.execute(text(_RETIRE_REGIONS), {"keep": list(keep)})).rowcount)

    async def retire_geo_districts(self, keep: Sequence[str]) -> int:
        return int((await self._conn.execute(text(_RETIRE_DISTRICTS), {"keep": list(keep)})).rowcount)

    async def retire_geo_mahallas(self, keep: Sequence[str]) -> int:
        return int((await self._conn.execute(text(_RETIRE_MAHALLAS), {"keep": list(keep)})).rowcount)

    async def retire_geo_streets(self, keep_mahallas: Sequence[UUID], keep_names: Sequence[str]) -> int:
        result = await self._conn.execute(
            text(_RETIRE_STREETS), {"mahallas": list(keep_mahallas), "names": list(keep_names)}
        )
        return int(result.rowcount)
