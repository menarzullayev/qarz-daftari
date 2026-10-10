"""Pure rules of the territory reference and of a customer's address (the owner's decisions of 2026-10-10).

One reference for the whole platform: region, district, mahalla, street. A shop may say where a customer
lives by picking from it; the street may also be what the shop typed, because most of the country has no
streets in the reference. Everything about an address is optional.

The reference holds places, never people. Nothing here knows a shop or a customer's name.
"""

from dataclasses import dataclass
from uuid import UUID

from qarz.domain.languages import UZ_CYRILLIC
from qarz.domain.names import normalize_name
from qarz.domain.uz_cyrillic import to_cyrillic

# The platform setting that turns the address on (off by default).
SWITCH = "address_on"

MAX_NAME = 120
MAX_STREET_TEXT = 120
MAX_QUERY = 80
# A search is "every typed word is in the name"; more words than this add nothing a person would type.
MAX_TERMS = 4
# A list a person picks from by typing: past this many the answer says there is more, and they type on.
MAX_LIST = 50

ACTIVE, RETIRED = "active", "retired"

# How the seed writes the kind of a street and the kind of road, and what is stored for each.
STREET_KINDS = {"ko'cha": "street", "qishloq": "village"}
ROAD_TYPES = {"to'g'ri": "straight", "tor": "narrow", "berk": "dead_end", "shoh": "main"}


def place_name(raw: object) -> str | None:
    """A name of the reference as stored: trimmed, single spaces; None for one that cannot be used."""
    name = " ".join(raw.split()) if isinstance(raw, str) else ""
    if not name or len(name) > MAX_NAME or not normalize_name(name):
        return None
    return name


def name_norm(name: str) -> str:
    """What a search reads and a key is made of: the matching form of qarz.domain.names, so that the
    apostrophe a phone types and a name typed in Cyrillic find the row written in Latin."""
    return normalize_name(name)


def search_terms(query: str | None) -> list[str]:
    """The words a search must all find in a name, in the matching form; none for no search.

    Raises ValueError for a query that is too long.
    """
    if query is None:
        return []
    if len(query) > MAX_QUERY:
        raise ValueError(f"at most {MAX_QUERY} characters")
    terms: list[str] = []
    for term in normalize_name(query).split():
        if term not in terms:
            terms.append(term)
    return terms[:MAX_TERMS]


def mahalla_key(code: str | None, region_soato: str, district_soato: str | None, name: str) -> str:
    """The stable key of a mahalla of the seed: its code when it has one, otherwise where it is and
    what it is called."""
    if code:
        return f"c:{code}"
    return f"n:{region_soato}:{district_soato or '-'}:{name_norm(name)}"


def local_name(lang: str, name_uz: str, name_ru: str | None = None, name_en: str | None = None) -> str:
    """A place's name for the reader: Russian and English where the reference has them, Uzbek for
    everyone else, in Cyrillic for who reads Uzbek in Cyrillic."""
    if lang == "ru" and name_ru:
        return name_ru
    if lang == "en" and name_en:
        return name_en
    return to_cyrillic(name_uz) if lang == UZ_CYRILLIC else name_uz


def street_text(raw: object) -> str | None:
    """A street the shop typed, as stored; None for a blank one.

    Raises ValueError for one that is not text or is too long.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("must be text")
    text = " ".join(raw.split())
    if len(text) > MAX_STREET_TEXT:
        raise ValueError(f"at most {MAX_STREET_TEXT} characters")
    return text or None


@dataclass(frozen=True)
class Chain:
    """What the reference says about the places an address names. A place it does not have is absent:
    `region` is False, and the identifiers of a district, a mahalla or a street it does not have are None.
    """

    region: bool
    district_region: UUID | None = None
    mahalla_region: UUID | None = None
    mahalla_district: UUID | None = None
    street_mahalla: UUID | None = None


@dataclass(frozen=True)
class Address:
    """A customer's address as it is stored."""

    region_id: UUID
    district_id: UUID | None = None
    mahalla_id: UUID | None = None
    street_id: UUID | None = None
    street_text: str | None = None


def check_address(address: Address, chain: Chain) -> tuple[Address | None, dict[str, str]]:
    """The address as it may be stored, or what is wrong with it, by field.

    The chain must hold: the district is of the region, the mahalla is of the region and, where the
    reference knows the mahalla's district, of that district; the street is of the mahalla. A mahalla
    whose district the reference knows brings that district with it when none was named. Where the
    reference does not know a mahalla's district, the district is the shop's own word and is kept as
    given: nothing here invents the link.
    """
    fields: dict[str, str] = {}
    if not chain.region:
        fields["region_id"] = "no such region"
    district = address.district_id
    if district is not None and chain.district_region != address.region_id:
        fields["district_id"] = "not a district of this region"
    if address.mahalla_id is not None:
        if chain.mahalla_region != address.region_id:
            fields["mahalla_id"] = "not a mahalla of this region"
        elif chain.mahalla_district is not None:
            if district is None:
                district = chain.mahalla_district
            elif district != chain.mahalla_district:
                fields["mahalla_id"] = "not a mahalla of this district"
    if address.street_id is not None:
        if address.street_text is not None:
            fields["street_text"] = "a street is picked or typed, not both"
        if address.mahalla_id is None:
            fields["street_id"] = "a street is picked within a mahalla"
        elif chain.street_mahalla != address.mahalla_id:
            fields["street_id"] = "not a street of this mahalla"
    if fields:
        return None, fields
    return Address(address.region_id, district, address.mahalla_id, address.street_id, address.street_text), {}
