"""Loading the seed of the territory reference: regions, districts, mahallas and streets.

For the import command (`qarz.interface.import_territories`), run by hand by whoever operates the
platform; it is not a migration and nothing runs it by itself. It can be run again at any time: a region
and a district are known by their state code, a mahalla by the seed's code (or, without one, by where it
is and what it is called), a street by its mahalla and name. A second run adds nothing and brings names
up to date.

Each of the four files is the whole of its table. A place that was loaded before and is in the file no
longer is marked retired: it is not offered any more, and it is never deleted, so a customer whose
address points at it keeps it and is still shown it.

The seed says a mahalla's district for a part of the country only. Where it does not, the mahalla is
loaded with its region alone: nothing here guesses a district. A street is matched to its mahalla by the
district's code and the mahalla's name; one that matches no mahalla, or more than one, is left out and
counted.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from qarz.application.ports import Storage
from qarz.application.territories_ports import SeedDistrict, SeedMahalla, SeedRegion, SeedStreet
from qarz.domain import territories

BATCH = 1000
_MAX_COUNT = 10**9


@dataclass
class Loaded:
    rows: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0  # rows that cannot be used: no key, no name, or a parent the seed does not have
    retired: int = 0  # loaded before, not in the file any more


@dataclass
class ImportResult:
    regions: Loaded = field(default_factory=Loaded)
    districts: Loaded = field(default_factory=Loaded)
    mahallas: Loaded = field(default_factory=Loaded)
    streets: Loaded = field(default_factory=Loaded)
    # Mahallas loaded with a region and no district: what the seed does not say.
    mahallas_without_district: int = 0


def _code(raw: Any) -> str | None:
    code = raw.strip() if isinstance(raw, str) else ""
    return code if code.isascii() and code.isdigit() and 2 <= len(code) <= 12 else None


def _text(raw: Any, limit: int) -> str | None:
    text = " ".join(raw.split()) if isinstance(raw, str) else ""
    return text if 1 <= len(text) <= limit else None


def _count(raw: Any) -> int | None:
    digits = raw.strip() if isinstance(raw, str) else ""
    if not (digits.isascii() and digits.isdigit()):
        return None
    number = int(digits)
    return number if 0 < number < _MAX_COUNT else None


def seed_region(row: Mapping[str, Any]) -> SeedRegion | None:
    soato, name = _code(row.get("soato")), territories.place_name(row.get("name_uz"))
    if soato is None or name is None:
        return None
    return SeedRegion(
        soato,
        name,
        territories.place_name(row.get("name_ru")),
        territories.place_name(row.get("name_en")),
        territories.name_norm(name),
    )


def seed_district(row: Mapping[str, Any]) -> SeedDistrict | None:
    soato, region = _code(row.get("soato")), _code(row.get("region_soato"))
    name = territories.place_name(row.get("name_uz"))
    if soato is None or region is None or name is None:
        return None
    return SeedDistrict(
        soato,
        region,
        name,
        territories.place_name(row.get("name_ru")),
        territories.place_name(row.get("name_en")),
        territories.name_norm(name),
        _count(row.get("population")),
        _count(row.get("families")),
        _count(row.get("households")),
    )


def seed_mahalla(row: Mapping[str, Any]) -> SeedMahalla | None:
    region, name = _code(row.get("region_soato")), territories.place_name(row.get("name_uz"))
    if region is None or name is None:
        return None
    given = row.get("district_soato")
    district = _code(given)
    if district is None and isinstance(given, str) and given.strip():
        # A district is named and cannot be read: the row is not loaded as if it named none.
        return None
    code = _text(row.get("code"), 24)
    return SeedMahalla(
        territories.mahalla_key(code, region, district, name),
        code,
        region,
        district,
        _text(row.get("source_group"), 12),
        name,
        territories.name_norm(name),
    )


def _loaded(part: Loaded, given: int, added: int) -> None:
    part.added, part.updated = added, given - added


async def import_territories(
    storage: Storage,
    regions: Iterable[Mapping[str, Any]],
    districts: Iterable[Mapping[str, Any]],
    mahallas: Iterable[Mapping[str, Any]],
    streets: Iterable[Mapping[str, Any]],
) -> ImportResult:
    """Store the four files. `storage` connects as the administrators' role, the only one that may
    write the reference. All of it is one transaction: a file that fails leaves the reference as it was."""
    result = ImportResult()

    seed_regions: dict[str, SeedRegion] = {}
    for row in regions:
        result.regions.rows += 1
        region = seed_region(row)
        if region is None or region.soato in seed_regions:
            result.regions.skipped += 1
        else:
            seed_regions[region.soato] = region

    seed_districts: dict[str, SeedDistrict] = {}
    for row in districts:
        result.districts.rows += 1
        district = seed_district(row)
        if district is None or district.soato in seed_districts or district.region_soato not in seed_regions:
            result.districts.skipped += 1
        else:
            seed_districts[district.soato] = district

    seed_mahallas: dict[str, SeedMahalla] = {}
    for row in mahallas:
        result.mahallas.rows += 1
        mahalla = seed_mahalla(row)
        if (
            mahalla is None
            or mahalla.source_key in seed_mahallas
            or mahalla.region_soato not in seed_regions
            or (
                mahalla.district_soato is not None
                # The district a mahalla names is one the seed has, and of the region the mahalla names.
                and (
                    mahalla.district_soato not in seed_districts
                    or seed_districts[mahalla.district_soato].region_soato != mahalla.region_soato
                )
            )
        ):
            result.mahallas.skipped += 1
        else:
            seed_mahallas[mahalla.source_key] = mahalla
    result.mahallas_without_district = sum(1 for kept in seed_mahallas.values() if kept.district_soato is None)

    async with storage.platform() as session:
        _loaded(result.regions, len(seed_regions), await session.upsert_geo_regions(list(seed_regions.values())))
        _loaded(
            result.districts, len(seed_districts), await session.upsert_geo_districts(list(seed_districts.values()))
        )
        added = 0
        batch = list(seed_mahallas.values())
        for start in range(0, len(batch), BATCH):
            added += await session.upsert_geo_mahallas(batch[start : start + BATCH])
        _loaded(result.mahallas, len(batch), added)
        result.regions.retired = await session.retire_geo_regions(list(seed_regions))
        result.districts.retired = await session.retire_geo_districts(list(seed_districts))
        result.mahallas.retired = await session.retire_geo_mahallas(list(seed_mahallas))

        # A street names its mahalla by the district's code and the mahalla's name.
        found: dict[tuple[str, str], list[UUID]] = {}
        for mahalla_id, district_soato, norm in await session.geo_mahallas_by_district():
            found.setdefault((district_soato, norm), []).append(mahalla_id)
        seed_streets: dict[tuple[UUID, str], SeedStreet] = {}
        for row in streets:
            result.streets.rows += 1
            district_code, name = _code(row.get("district_soato")), territories.place_name(row.get("street"))
            mahalla_name = territories.place_name(row.get("mahalla"))
            kind = territories.STREET_KINDS.get(str(row.get("kind") or "").strip())
            matches = (
                found.get((district_code, territories.name_norm(mahalla_name)), [])
                if district_code is not None and mahalla_name is not None
                else []
            )
            if name is None or kind is None or len(matches) != 1:
                result.streets.skipped += 1
                continue
            street = SeedStreet(
                matches[0],
                name,
                territories.name_norm(name),
                kind,
                territories.ROAD_TYPES.get(str(row.get("road_type") or "").strip()),
            )
            if (street.mahalla_id, street.name_norm) in seed_streets:
                result.streets.skipped += 1
            else:
                seed_streets[(street.mahalla_id, street.name_norm)] = street
        _loaded(result.streets, len(seed_streets), await session.upsert_geo_streets(list(seed_streets.values())))
        result.streets.retired = await session.retire_geo_streets(
            [mahalla_id for mahalla_id, _ in seed_streets], [norm for _, norm in seed_streets]
        )
    return result
