"""The pure rules of the territory reference and of a customer's address (qarz.domain.territories) and how
a row of the seed is read (qarz.application.territories_import). Each rule has the case that must work
and the case that must be refused. The places are invented.
"""

import uuid

import pytest

from qarz.application.customer_address import parse_address
from qarz.application.errors import ValidationFailed
from qarz.application.territories_import import seed_district, seed_mahalla, seed_region
from qarz.domain import territories
from qarz.domain.territories import Address, Chain, check_address

REGION, OTHER_REGION = uuid.uuid4(), uuid.uuid4()
DISTRICT, OTHER_DISTRICT = uuid.uuid4(), uuid.uuid4()
MAHALLA, OTHER_MAHALLA = uuid.uuid4(), uuid.uuid4()
STREET = uuid.uuid4()


@pytest.mark.parametrize(
    "typed",
    ["Qo'shrabot", "Qoʻshrabot", "Qo’shrabot", "QO`SHRABOT", "  qo'shrabot ", "Қўшработ", "қўшработ"],
)
def test_a_name_is_found_whatever_apostrophe_or_script_it_is_typed_in(typed: str) -> None:
    assert territories.search_terms(typed) == [territories.name_norm("Qo'shrabot")] == ["qo'shrabot"]


def test_a_different_name_is_a_different_name() -> None:
    # The counterpart: the matching form does not fold everything together.
    assert territories.name_norm("Qoshrabot") != territories.name_norm("Qo'shrabot")
    assert territories.search_terms("Oqdaryo") != [territories.name_norm("Qo'shrabot")]


def test_a_search_is_nothing_when_blank_and_refused_when_too_long() -> None:
    assert territories.search_terms(None) == [] and territories.search_terms("  ") == []
    # Every typed word is looked for, each once, and no more of them than a person would type.
    assert territories.search_terms("Bog' bog\u02bb  ko'cha") == ["bog'", "ko'cha"]
    assert len(territories.search_terms("a b c d e f")) == territories.MAX_TERMS
    with pytest.raises(ValueError, match="at most"):
        territories.search_terms("a" * (territories.MAX_QUERY + 1))


def test_a_mahalla_is_keyed_by_its_code_and_without_one_by_where_it_is_and_its_name() -> None:
    assert territories.mahalla_key("101-0085", "1726", None, "Abdulla Qodiriy") == "c:101-0085"
    assert territories.mahalla_key(None, "1718", "1718216", "Qo'ralos") == "n:1718:1718216:qo'ralos"
    # The same name in another district, or with no district known, is another mahalla.
    assert territories.mahalla_key(None, "1718", None, "Qo'ralos") == "n:1718:-:qo'ralos"
    assert territories.mahalla_key(None, "1718", "1718216", "Qoʻralos") == "n:1718:1718216:qo'ralos"


def test_a_place_is_named_in_the_readers_language_where_the_reference_has_it() -> None:
    names = ("Samarqand viloyati", "Самаркандская область", "Samarkand region")
    assert territories.local_name("ru", *names) == "Самаркандская область"
    assert territories.local_name("en", *names) == "Samarkand region"
    assert territories.local_name("uz", *names) == "Samarqand viloyati"
    assert territories.local_name("tg", *names) == territories.local_name("kaa", *names) == "Samarqand viloyati"
    assert territories.local_name("uz-Cyrl", *names) == "Самарқанд вилояти"
    # A mahalla has an Uzbek name only: nobody is shown an empty one.
    assert territories.local_name("ru", "Oqchobsoy") == territories.local_name("en", "Oqchobsoy") == "Oqchobsoy"


def test_a_typed_street_is_trimmed_and_a_long_or_wrong_one_refused() -> None:
    assert territories.street_text("  Navro'z   ko'chasi 12 ") == "Navro'z ko'chasi 12"
    assert territories.street_text("   ") is None and territories.street_text(None) is None
    with pytest.raises(ValueError, match="at most"):
        territories.street_text("k" * (territories.MAX_STREET_TEXT + 1))
    with pytest.raises(ValueError, match="text"):
        territories.street_text(12)


FULL = Chain(True, district_region=REGION, mahalla_region=REGION, mahalla_district=DISTRICT, street_mahalla=MAHALLA)


def test_an_address_whose_chain_holds_is_kept_as_given() -> None:
    address = Address(REGION, DISTRICT, MAHALLA, STREET)
    assert check_address(address, FULL) == (address, {})
    assert check_address(Address(REGION), Chain(True)) == (Address(REGION), {})
    typed = Address(REGION, DISTRICT, None, None, "Bog' ko'chasi")
    assert check_address(typed, Chain(True, district_region=REGION)) == (typed, {})


def test_a_mahalla_whose_district_is_known_brings_it_and_one_whose_is_not_invents_none() -> None:
    assert check_address(Address(REGION, None, MAHALLA), FULL)[0] == Address(REGION, DISTRICT, MAHALLA)
    unknown = Chain(True, district_region=REGION, mahalla_region=REGION, mahalla_district=None)
    # No district named: none is made up. A district the shop named itself: kept as the shop's word.
    assert check_address(Address(REGION, None, MAHALLA), unknown)[0] == Address(REGION, None, MAHALLA)
    assert check_address(Address(REGION, DISTRICT, MAHALLA), unknown)[0] == Address(REGION, DISTRICT, MAHALLA)


@pytest.mark.parametrize(
    ("address", "chain", "field"),
    [
        (Address(REGION), Chain(False), "region_id"),
        # A district of another region, and one the reference does not have.
        (Address(REGION, DISTRICT), Chain(True, district_region=OTHER_REGION), "district_id"),
        (Address(REGION, DISTRICT), Chain(True), "district_id"),
        # A mahalla of another region, of another district, and one the reference does not have.
        (Address(REGION, None, MAHALLA), Chain(True, mahalla_region=OTHER_REGION), "mahalla_id"),
        (
            Address(REGION, DISTRICT, MAHALLA),
            Chain(True, district_region=REGION, mahalla_region=REGION, mahalla_district=OTHER_DISTRICT),
            "mahalla_id",
        ),
        (Address(REGION, None, MAHALLA), Chain(True), "mahalla_id"),
        # A street of another mahalla, one without a mahalla, and one the reference does not have.
        (
            Address(REGION, DISTRICT, MAHALLA, STREET),
            Chain(True, REGION, REGION, DISTRICT, OTHER_MAHALLA),
            "street_id",
        ),
        (Address(REGION, None, None, STREET), Chain(True, street_mahalla=MAHALLA), "street_id"),
        (Address(REGION, DISTRICT, MAHALLA, STREET), Chain(True, REGION, REGION, DISTRICT, None), "street_id"),
        # A street both picked and typed.
        (Address(REGION, DISTRICT, MAHALLA, STREET, "Bog'"), FULL, "street_text"),
    ],
)
def test_an_address_whose_chain_does_not_hold_is_refused(address: Address, chain: Chain, field: str) -> None:
    checked, fields = check_address(address, chain)
    assert checked is None and field in fields


def test_a_request_names_an_address_by_identifiers_and_anything_else_is_refused() -> None:
    assert parse_address(None) is None
    assert parse_address({"region_id": str(REGION), "street_text": " Bog'  ko'chasi "}) == Address(
        REGION, None, None, None, "Bog' ko'chasi"
    )
    for wrong, field in [
        ({}, "address.region_id"),
        ({"district_id": str(DISTRICT)}, "address.region_id"),
        ({"region_id": "not-a-uuid"}, "address.region_id"),
        ({"region_id": str(REGION), "mahalla_id": 7}, "address.mahalla_id"),
        ({"region_id": str(REGION), "street_text": "k" * 121}, "address.street_text"),
        ({"region_id": str(REGION), "resident": "Ali Valiyev"}, "address.resident"),
        ("Samarqand", "address"),
    ]:
        with pytest.raises(ValidationFailed) as refused:
            parse_address(wrong)
        assert field in refused.value.fields, wrong


def test_a_row_of_the_seed_is_read_or_left_out_and_never_half_read() -> None:
    region = seed_region({"soato": "1718", "name_uz": " Samarqand  viloyati ", "name_ru": "", "name_en": "S"})
    assert region is not None and (region.name_uz, region.name_ru, region.name_norm) == (
        "Samarqand viloyati",
        None,
        "samarqand viloyati",
    )
    assert seed_region({"soato": "17x8", "name_uz": "Samarqand"}) is None
    assert seed_region({"soato": "1718", "name_uz": "  "}) is None

    district = seed_district(
        {"soato": "1718216", "region_soato": "1718", "name_uz": "Qo'shrabot tumani", "population": "128527",
         "families": "", "households": "-4"}
    )  # fmt: skip
    assert district is not None
    assert (district.population, district.families, district.households) == (128527, None, None)
    assert seed_district({"soato": "1718216", "region_soato": "", "name_uz": "Qo'shrabot tumani"}) is None

    known = seed_mahalla({"code": "101-0085", "name_uz": "Abdulla Qodiriy", "region_soato": "1726",
                          "district_soato": "", "source_group": "101-"})  # fmt: skip
    assert known is not None
    assert (known.source_key, known.district_soato, known.source_group) == ("c:101-0085", None, "101-")
    # A district that is named and cannot be read: the row is left out, not loaded as if it named none.
    assert seed_mahalla({"code": "1", "name_uz": "Bog'", "region_soato": "1726", "district_soato": "17x"}) is None
    assert seed_mahalla({"code": "1", "name_uz": "", "region_soato": "1726", "district_soato": ""}) is None
