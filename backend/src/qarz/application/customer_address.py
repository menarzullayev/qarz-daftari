"""A customer's address: reading it, checking it against the territory reference, giving it back
(the owner's decisions of 2026-10-10).

Behind the platform switch `address_on`. The address is the shop's own data on the shop's own customer
row; the reference it points into holds places and never people (`qarz.domain.territories`).
"""

from typing import Any
from uuid import UUID

from qarz.application.errors import ValidationFailed
from qarz.application.ports import PlatformSession, TenantSession
from qarz.application.territories_ports import AddressRow, Place
from qarz.domain import territories
from qarz.domain.territories import Address

_ADDRESS_FIELDS = frozenset({"region_id", "district_id", "mahalla_id", "street_id", "street_text"})


async def switched_on(session: TenantSession | PlatformSession) -> bool:
    # Only the JSON value `true` turns it on: absent, null, "true" and 1 all mean off.
    return await session.platform_setting(territories.SWITCH) is True


def place_body(place: Place, lang: str) -> dict[str, Any]:
    return {
        "id": str(place.place_id),
        "name": territories.local_name(lang, place.name_uz, place.name_ru, place.name_en),
    }


def address_body(row: AddressRow | None, lang: str) -> dict[str, Any] | None:
    """A customer's address as the API gives it; null for a customer without one."""
    if row is None:
        return None
    address = row.address
    return {
        "region": place_body(row.region, lang),
        "district": None if row.district is None else place_body(row.district, lang),
        "mahalla": None
        if address.mahalla_id is None or row.mahalla_name is None
        else {"id": str(address.mahalla_id), "name": territories.local_name(lang, row.mahalla_name)},
        # A street of the reference, or what the shop typed: never both.
        "street": None
        if address.street_id is None or row.street_name is None
        else {"id": str(address.street_id), "name": territories.local_name(lang, row.street_name)},
        "street_text": address.street_text,
    }


def address_line(row: AddressRow | None, lang: str) -> str | None:
    """The address in one line, widest place first: what the owner's export writes in a cell."""
    body = address_body(row, lang)
    if body is None:
        return None
    parts = [body[key]["name"] for key in ("region", "district", "mahalla", "street") if body[key] is not None]
    if body["street_text"]:
        parts.append(body["street_text"])
    return ", ".join(parts)


def _identifier(raw: Any, field: str, fields: dict[str, str]) -> UUID | None:
    if raw is None:
        return None
    try:
        if not isinstance(raw, str):
            raise ValueError
        return UUID(raw)
    except ValueError:
        fields[field] = "not an identifier"
        return None


def parse_address(raw: Any) -> Address | None:
    """The address a request names, before the reference is asked about it; None for null, which
    removes the address. Raises ValidationFailed naming each field that cannot be read."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationFailed({"address": "must be an object or null"})
    fields = {f"address.{name}": "unknown field" for name in raw if name not in _ADDRESS_FIELDS}
    found: dict[str, str] = {}
    region = _identifier(raw.get("region_id"), "region_id", found)
    district = _identifier(raw.get("district_id"), "district_id", found)
    mahalla = _identifier(raw.get("mahalla_id"), "mahalla_id", found)
    street = _identifier(raw.get("street_id"), "street_id", found)
    typed: str | None = None
    try:
        typed = territories.street_text(raw.get("street_text"))
    except ValueError as error:
        found["street_text"] = str(error)
    if region is None and "region_id" not in found:
        found["region_id"] = "an address starts at a region"
    fields.update({f"address.{name}": message for name, message in found.items()})
    if fields or region is None:
        raise ValidationFailed(fields)
    return Address(region, district, mahalla, street, typed)


async def checked_address(session: TenantSession, address: Address | None) -> Address | None:
    """The address as it may be stored. Raises ValidationFailed when its chain does not hold."""
    if address is None:
        return None
    checked, fields = territories.check_address(address, await session.geo_chain(address))
    if checked is None:
        raise ValidationFailed({f"address.{name}": message for name, message in fields.items()})
    return checked


def address_fingerprint(address: Address | None) -> dict[str, str | None] | None:
    """The address as it enters the fingerprint of an idempotent request."""
    if address is None:
        return None
    return {
        "region": str(address.region_id),
        "district": None if address.district_id is None else str(address.district_id),
        "mahalla": None if address.mahalla_id is None else str(address.mahalla_id),
        "street": None if address.street_id is None else str(address.street_id),
        "street_text": address.street_text,
    }


async def address_of(session: TenantSession, customer_id: UUID, lang: str) -> dict[str, Any] | None:
    return address_body((await session.customer_addresses([customer_id])).get(customer_id), lang)
