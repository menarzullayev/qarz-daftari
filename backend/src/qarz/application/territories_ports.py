"""What the territory reference and a customer's address ask of storage (the owner's decisions of
2026-10-10).

In a file of its own, like the shared catalogue's: `TenantSession` and `PlatformSession` inherit the two
protocols below.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from qarz.domain.territories import Address, Chain


@dataclass(frozen=True)
class Place:
    """A region or a district of the reference."""

    place_id: UUID
    name_uz: str
    name_ru: str | None
    name_en: str | None


@dataclass(frozen=True)
class Mahalla:
    mahalla_id: UUID
    name_uz: str
    # None where the reference does not know the mahalla's district.
    district_id: UUID | None
    # The seed's own grouping code: a hint that tells two mahallas of one name apart, and links nothing.
    source_group: str | None


@dataclass(frozen=True)
class Street:
    street_id: UUID
    name: str
    kind: str
    road_type: str | None


@dataclass(frozen=True)
class AddressRow:
    """A customer's address with the names of the places it points at."""

    address: Address
    region: Place
    district: Place | None
    mahalla_name: str | None
    street_name: str | None


@dataclass(frozen=True)
class SeedRegion:
    soato: str
    name_uz: str
    name_ru: str | None
    name_en: str | None
    name_norm: str


@dataclass(frozen=True)
class SeedDistrict:
    soato: str
    region_soato: str
    name_uz: str
    name_ru: str | None
    name_en: str | None
    name_norm: str
    population: int | None
    families: int | None
    households: int | None


@dataclass(frozen=True)
class SeedMahalla:
    source_key: str
    code: str | None
    region_soato: str
    district_soato: str | None
    source_group: str | None
    name_uz: str
    name_norm: str


@dataclass(frozen=True)
class SeedStreet:
    mahalla_id: UUID
    name: str
    name_norm: str
    kind: str
    road_type: str | None


class TerritorySession(Protocol):
    """Reading the reference, and the address of the shop's own customers."""

    async def geo_regions(self) -> list[Place]: ...

    async def geo_districts(self, region_id: UUID) -> list[Place]: ...

    async def geo_mahallas(
        self, *, region_id: UUID, district_id: UUID | None, terms: Sequence[str], limit: int
    ) -> list[Mahalla]:
        """Mahallas of the region whose name holds every one of `terms`, by name. With a district: those of that
        district and those whose district the reference does not know."""
        ...

    async def geo_streets(self, *, mahalla_id: UUID, terms: Sequence[str], limit: int) -> list[Street]: ...

    async def geo_chain(self, address: Address) -> Chain:
        """What the reference says about the places the address names."""
        ...

    async def customer_addresses(self, customer_ids: Sequence[UUID]) -> dict[UUID, AddressRow]:
        """The address of each of the customers that has one."""
        ...

    async def set_customer_address(self, customer_id: UUID, address: Address | None, now: datetime) -> None:
        """Replace the customer's address as a whole; None removes it."""
        ...

    async def last_address(self) -> AddressRow | None:
        """The address the shop set last, of a customer who still has it."""
        ...


class TerritoryAdminSession(Protocol):
    """Loading the reference: the import command, as the administrators' role."""

    async def upsert_geo_regions(self, rows: Sequence[SeedRegion]) -> int:
        """Add or bring up to date; returns how many were added. The same for the three below."""
        ...

    async def upsert_geo_districts(self, rows: Sequence[SeedDistrict]) -> int: ...

    async def upsert_geo_mahallas(self, rows: Sequence[SeedMahalla]) -> int: ...

    async def upsert_geo_streets(self, rows: Sequence[SeedStreet]) -> int: ...

    async def geo_mahallas_by_district(self) -> list[tuple[UUID, str, str]]:
        """Every active mahalla whose district is known: its identifier, the district's code and the
        mahalla's matching name. What a street of the seed is matched to."""
        ...

    async def retire_geo_regions(self, keep: Sequence[str]) -> int:
        """Mark as retired every active row whose key is not in `keep`; returns how many. No row is
        deleted. The same for the three below."""
        ...

    async def retire_geo_districts(self, keep: Sequence[str]) -> int: ...

    async def retire_geo_mahallas(self, keep: Sequence[str]) -> int: ...

    async def retire_geo_streets(self, keep_mahallas: Sequence[UUID], keep_names: Sequence[str]) -> int:
        """`keep_mahallas[i]` and `keep_names[i]` are one street to keep."""
        ...
