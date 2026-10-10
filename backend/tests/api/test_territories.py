"""The territory reference and a customer's address through the API: the switch, the lists, the address
on a customer, what follows it (removal of a customer's data, the owner's export), and the import.

Behind the platform switch `address_on`. Each rule has the case that must work and the case that must be
refused. Who may call what, by role and as an outsider, is in the authorization suite; what the database
itself refuses is tests/db/test_territories_schema.py. The places are invented, and no test names a
real person.
"""

import asyncio
import csv
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.domain import territories
from qarz.infrastructure.settings import Settings
from qarz.interface.import_territories import report, run

from .conftest import World, as_user
from .test_customers_ledger import key, read, record, shop, write
from .test_exports import ask, work, workbook

pytestmark = pytest.mark.db

NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "Topilmadi.", "fields": {}}}
GEO_TABLES = ("geo_region", "geo_district", "geo_mahalla", "geo_street")
ADDRESS_COLUMNS = "geo_region_id, geo_district_id, geo_mahalla_id, geo_street_id, street_text"


def switch(owner: psycopg.Connection, value: str = "true") -> None:
    """Store the switch as an administrator's change would (`value` is JSON). The row is signed with a
    user identifier, so the `admin_env` fixture behind `client` removes it after the test."""
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('address_on', %s::jsonb, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by",
        (value, str(uuid.uuid4())),
    )


@pytest.fixture
def on(client: TestClient, owner: psycopg.Connection) -> Iterator[None]:
    switch(owner)
    yield


def code(digits: int = 10) -> str:
    """A state code no other test's place carries: the reference is one for the whole session's database."""
    return str(uuid.uuid4().int % 10**digits).zfill(digits)


def tag() -> str:
    return "q" + uuid.uuid4().hex[:11]


def add_region(owner: psycopg.Connection, name: str, name_ru: str | None = None, name_en: str | None = None) -> str:
    region = uuid.uuid4()
    owner.execute(
        "INSERT INTO geo_region (id, soato, name_uz, name_ru, name_en, name_norm) VALUES (%s, %s, %s, %s, %s, %s)",
        (region, code(), name, name_ru, name_en, territories.name_norm(name)),
    )
    return str(region)


def add_district(owner: psycopg.Connection, region: str, name: str, name_ru: str | None = None) -> str:
    district = uuid.uuid4()
    owner.execute(
        "INSERT INTO geo_district (id, soato, region_id, name_uz, name_ru, name_norm) VALUES (%s, %s, %s, %s, %s, %s)",
        (district, code(), region, name, name_ru, territories.name_norm(name)),
    )
    return str(district)


def add_mahalla(
    owner: psycopg.Connection,
    region: str,
    district: str | None,
    name: str,
    group: str | None = None,
    status: str = "active",
) -> str:
    mahalla = uuid.uuid4()
    owner.execute(
        "INSERT INTO geo_mahalla (id, source_key, region_id, district_id, source_group, name_uz, name_norm, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (mahalla, f"c:{mahalla.hex}", region, district, group, name, territories.name_norm(name), status),
    )
    return str(mahalla)


def add_street(owner: psycopg.Connection, mahalla: str, name: str, kind: str = "street") -> str:
    street = uuid.uuid4()
    owner.execute(
        "INSERT INTO geo_street (id, mahalla_id, name, name_norm, kind, road_type) "
        "VALUES (%s, %s, %s, %s, %s, 'straight')",
        (street, mahalla, name, territories.name_norm(name), kind),
    )
    return str(street)


@dataclass(frozen=True)
class Places:
    word: str
    region: str
    district: str
    mahalla: str
    street: str
    other_region: str
    other_district: str  # of the other region
    loose_mahalla: str  # of the first region; the reference does not know its district
    other_street: str  # of the loose mahalla


@pytest.fixture
def places(owner: psycopg.Connection) -> Places:
    word = tag()
    region = add_region(owner, f"Sinov viloyati {word}", f"Пробная область {word}", f"Trial region {word}")
    other_region = add_region(owner, f"Boshqa viloyat {word}")
    district = add_district(owner, region, f"Qo'rg'on tumani {word}", f"Курганский район {word}")
    mahalla = add_mahalla(owner, region, district, f"Oqchobsoy {word}")
    loose = add_mahalla(owner, region, None, f"Bo'ston {word}", "101-")
    return Places(
        word,
        region,
        district,
        mahalla,
        add_street(owner, mahalla, f"Beklarsoy {word}"),
        other_region,
        add_district(owner, other_region, f"Boshqa tuman {word}"),
        loose,
        add_street(owner, loose, f"Yangi {word}"),
    )


def geo(world: World) -> str:
    return f"{shop(world)}/territories"


def items(client: TestClient, world: World, path: str, user: uuid.UUID | None = None, **params: Any) -> Any:
    response = read(client, user or world.seller_a, f"{geo(world)}/{path}", **params)
    assert response.status_code == 200, response.text
    return response.json()


def names(body: dict[str, Any]) -> list[str]:
    return [item["name"] for item in body["items"]]


def create(client: TestClient, world: World, body: dict[str, Any], user: uuid.UUID | None = None) -> Any:
    return write(client, user or world.seller_a, "POST", f"{shop(world)}/customers", body)


def patch(client: TestClient, world: World, customer: Any, body: dict[str, Any], user: uuid.UUID | None = None) -> Any:
    return write(client, user or world.manager_a, "PATCH", f"{shop(world)}/customers/{customer}", body)


def speak(owner: psycopg.Connection, user: uuid.UUID, lang: str) -> None:
    """The language the member of staff reads in: what names a place for them."""
    owner.execute("UPDATE app_user SET lang = %s WHERE id = %s", (lang, user))


def detail(client: TestClient, world: World, customer: Any, user: uuid.UUID | None = None) -> Any:
    response = client.get(f"{shop(world)}/customers/{customer}", headers=as_user(user or world.seller_a))
    assert response.status_code == 200, response.text
    return response.json()


def stored(owner: psycopg.Connection, customer: Any) -> tuple[Any, ...]:
    row = owner.execute(f"SELECT {ADDRESS_COLUMNS}, address_at FROM customer WHERE id = %s", (customer,)).fetchone()
    assert row is not None
    return (*(None if value is None else str(value) for value in row[:5]), row[5] is not None)


def full(places: Places) -> dict[str, Any]:
    return {
        "region_id": places.region,
        "district_id": places.district,
        "mahalla_id": places.mahalla,
        "street_id": places.street,
    }


# --- the switch ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "false", '"true"', "1", "null"])
def test_with_the_switch_off_no_route_of_the_reference_exists_for_anyone(
    client: TestClient, world: World, owner: psycopg.Connection, places: Places, value: str | None
) -> None:
    """Off is the default (no row), and only the JSON value `true` is on. A manager, a stranger and
    someone who is not signed in all get the answer of a route that was never there."""
    if value is not None:
        switch(owner, value)
    routes = [
        "regions",
        f"districts?region={places.region}",
        f"mahallas?region={places.region}",
        f"streets?mahalla={places.mahalla}",
        "last",
    ]
    for path in routes:
        for headers in (as_user(world.manager_a), as_user(world.stranger), {}):
            response = client.get(f"{geo(world)}/{path}", headers=headers)
            assert response.status_code == 404 and response.json() == NOT_FOUND, (path, headers)
    assert "x-qarz-address" not in client.get("/api/v1/me/shops", headers=as_user(world.manager_a)).headers


def test_with_the_switch_off_a_customer_is_exactly_what_it_was(
    client: TestClient, world: World, owner: psycopg.Connection, places: Places
) -> None:
    """The same requests with the switch off and with it on, addresses left out, give the same bodies key
    for key but for the one key the switch adds; off, an address is a field the API does not know and
    nothing reaches the customer's address columns."""

    def run_once(name: str) -> list[dict[str, Any]]:
        created = create(client, world, {"display_name": name, "phone": "+998901112233"})
        assert created.status_code == 201, created.text
        customer = created.json()["id"]
        patched = patch(client, world, customer, {"reminders_off": True})
        assert patched.status_code == 200, patched.text
        listed = read(client, world.seller_a, f"{shop(world)}/customers", q=name).json()["items"]
        return [created.json(), patched.json(), detail(client, world, customer), *listed]

    off = run_once(f"Sinov {tag()}")
    assert all("address" not in body for body in off)
    for body in ({"display_name": "Sinov", "address": full(places)}, {"display_name": "Sinov", "address": None}):
        refused = create(client, world, body)
        assert refused.status_code == 422 and refused.json()["error"]["fields"] == {"address": "unknown field"}
    for change in ({"address": full(places)}, {"address": None}):
        refused = patch(client, world, world.settled_customer_a, change)
        assert refused.status_code == 422 and refused.json()["error"]["fields"] == {"address": "unknown field"}
    assert owner.execute(
        "SELECT count(*) FROM customer WHERE shop_id = %s AND (geo_region_id IS NOT NULL OR address_at IS NOT NULL "
        "OR street_text IS NOT NULL)",
        (world.shop_a,),
    ).fetchone() == (0,)

    switch(owner)
    on = run_once(f"Sinov {tag()}")
    created, patched, read_back, listed = on
    assert created["address"] is None and patched["address"] is None and read_back["address"] is None
    assert "address" not in listed, "a list of customers never carries addresses"
    for before, after in zip(off, on, strict=True):
        assert set(after) - set(before) <= {"address"} and set(before) <= set(after)
    assert client.get("/api/v1/me/shops", headers=as_user(world.manager_a)).headers["x-qarz-address"] == "on"


# --- the lists -----------------------------------------------------------------------------------------


def test_regions_and_districts_are_listed_by_name_in_the_readers_language(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, places: Places
) -> None:
    regions = {item["id"]: item["name"] for item in items(client, world, "regions")["items"]}
    assert regions[places.region] == f"Sinov viloyati {places.word}"
    assert regions[places.other_region] == f"Boshqa viloyat {places.word}"
    listed = [item["name"] for item in items(client, world, "regions")["items"]]
    assert listed.index(f"Boshqa viloyat {places.word}") < listed.index(f"Sinov viloyati {places.word}")

    def in_language(lang: str) -> dict[str, str]:
        speak(owner, world.seller_a, lang)
        return {item["id"]: item["name"] for item in items(client, world, "regions")["items"]}

    assert in_language("ru")[places.region] == f"Пробная область {places.word}"
    assert in_language("en")[places.region] == f"Trial region {places.word}"
    # A place the reference has no Russian name for is still named.
    assert in_language("ru")[places.other_region] == f"Boshqa viloyat {places.word}"
    assert in_language("uz-Cyrl")[places.other_region] == f"Бошқа вилоят {places.word}"
    speak(owner, world.seller_a, "uz")

    districts = items(client, world, "districts", region=places.region)
    assert districts == {"items": [{"id": places.district, "name": f"Qo'rg'on tumani {places.word}"}]}
    # The counterpart: a district of another region is not among them.
    assert places.other_district not in str(districts)
    assert items(client, world, "districts", region=str(uuid.uuid4())) == {"items": []}


@pytest.mark.parametrize("typed", ["oqchob", "OQCHOBSOY", "Оқчобсой", "  oqchobsoy "])
def test_a_mahalla_is_found_by_a_part_of_its_name_in_either_script(
    client: TestClient, world: World, on: None, places: Places, typed: str
) -> None:
    found = items(client, world, "mahallas", region=places.region, q=f"{typed} {places.word}".strip())
    assert found == {
        "items": [
            {"id": places.mahalla, "name": f"Oqchobsoy {places.word}", "district_id": places.district, "group": None}
        ],
        "more": False,
    }


@pytest.mark.parametrize("typed", ["Bo'ston", "Boʻston", "Bo’ston", "bo`ston", "Бўстон"])
def test_every_apostrophe_a_phone_types_finds_the_same_mahalla(
    client: TestClient, world: World, on: None, places: Places, typed: str
) -> None:
    found = items(client, world, "mahallas", region=places.region, q=f"{typed} {places.word}")
    assert [item["id"] for item in found["items"]] == [places.loose_mahalla]
    # The counterpart: without the apostrophe it is another name, and a `%` is a character, not a wildcard.
    assert items(client, world, "mahallas", region=places.region, q=f"Boston {places.word}")["items"] == []
    assert items(client, world, "mahallas", region=places.region, q="%" + places.word)["items"] == []


def test_a_mahalla_whose_district_is_unknown_is_offered_under_every_district_of_its_region_and_linked_to_none(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, places: Places
) -> None:
    second = add_district(owner, places.region, f"Ikkinchi tuman {places.word}")
    foreign = add_mahalla(owner, places.region, second, f"Chetdagi {places.word}")
    elsewhere = add_mahalla(owner, places.other_region, None, f"Uzoq {places.word}")
    gone = add_mahalla(owner, places.region, places.district, f"Eski {places.word}", status="retired")

    whole = items(client, world, "mahallas", region=places.region, q=places.word)
    assert {item["id"] for item in whole["items"]} == {places.mahalla, places.loose_mahalla, foreign}

    of_district = items(client, world, "mahallas", region=places.region, district=places.district, q=places.word)
    assert {item["id"]: item["district_id"] for item in of_district["items"]} == {
        places.mahalla: places.district,
        # Offered, because nobody knows it is not of this district; and still linked to none.
        places.loose_mahalla: None,
    }
    assert next(item for item in of_district["items"] if item["id"] == places.loose_mahalla)["group"] == "101-"
    # Not another district's, not another region's, not a retired one.
    assert not {foreign, elsewhere, gone} & {item["id"] for item in of_district["items"]}
    assert owner.execute("SELECT district_id FROM geo_mahalla WHERE id = %s", (places.loose_mahalla,)).fetchone() == (
        None,
    )


def test_a_long_list_says_there_is_more_and_a_wrong_limit_or_search_is_refused(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, places: Places
) -> None:
    for number in range(4):
        add_mahalla(owner, places.region, places.district, f"Guliston {number} {places.word}")
    page = items(client, world, "mahallas", region=places.region, q=f"guliston {places.word}"[:8], limit=3)
    assert len(page["items"]) == 3 and page["more"] is True
    assert items(client, world, "mahallas", region=places.region, q="guliston", limit=50)["more"] is False
    for params in ({"limit": 0}, {"limit": 51}, {"q": "a" * 81}):
        refused = read(client, world.seller_a, f"{geo(world)}/mahallas", region=places.region, **params)
        assert refused.status_code == 422, params
    assert read(client, world.seller_a, f"{geo(world)}/mahallas").status_code == 422, "a region is required"


def test_streets_are_those_of_one_mahalla(client: TestClient, world: World, on: None, places: Places) -> None:
    assert items(client, world, "streets", mahalla=places.mahalla) == {
        "items": [{"id": places.street, "name": f"Beklarsoy {places.word}", "kind": "street", "road_type": "straight"}],
        "more": False,
    }
    assert names(items(client, world, "streets", mahalla=places.mahalla, q="Бекларсой")) == [f"Beklarsoy {places.word}"]
    assert items(client, world, "streets", mahalla=places.mahalla, q="yangi")["items"] == []
    assert items(client, world, "streets", mahalla=str(uuid.uuid4()))["items"] == []


def test_the_lists_hold_places_and_nothing_of_any_shop_or_person(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, places: Places
) -> None:
    """The boundary the owner drew. A customer with an address in shop A changes nothing another shop is
    given, and no list carries a customer, a count of them, or a district's totals."""
    path_b = f"/api/v1/shops/{world.shop_b}/territories"

    def given_to_shop_b() -> list[Any]:
        return [
            read(client, world.owner_b, f"{path_b}/regions").json(),
            read(client, world.owner_b, f"{path_b}/districts", region=places.region).json(),
            read(client, world.owner_b, f"{path_b}/mahallas", region=places.region, q=places.word).json(),
            read(client, world.owner_b, f"{path_b}/streets", mahalla=places.mahalla).json(),
            read(client, world.owner_b, f"{path_b}/last").json(),
        ]

    before = given_to_shop_b()
    created = create(client, world, {"display_name": "Sinov Mijoz", "address": full(places)})
    assert created.status_code == 201, created.text
    owner.execute("UPDATE geo_district SET population = 128527, families = 32155 WHERE id = %s", (places.district,))
    after = given_to_shop_b()
    assert after == before
    assert [set(item) for item in after[2]["items"]] == [{"id", "name", "district_id", "group"}] * 2
    assert {key for item in after[1]["items"] for key in item} == {"id", "name"}
    assert after[4] == {"address": None}, "shop B set no address"
    everything = str(after)
    assert "Sinov Mijoz" not in everything and "128527" not in everything and "32155" not in everything


# --- the address on a customer -------------------------------------------------------------------------


def test_a_customer_is_added_with_an_address_and_read_back_with_the_places_named(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, places: Places
) -> None:
    created = create(client, world, {"display_name": "Sinov Mijoz", "address": full(places)})
    assert created.status_code == 201, created.text
    expected = {
        "region": {"id": places.region, "name": f"Sinov viloyati {places.word}"},
        "district": {"id": places.district, "name": f"Qo'rg'on tumani {places.word}"},
        "mahalla": {"id": places.mahalla, "name": f"Oqchobsoy {places.word}"},
        "street": {"id": places.street, "name": f"Beklarsoy {places.word}"},
        "street_text": None,
    }
    assert created.json()["address"] == expected
    customer = created.json()["id"]
    assert detail(client, world, customer)["address"] == expected
    assert stored(owner, customer) == (places.region, places.district, places.mahalla, places.street, None, True)

    speak(owner, world.seller_a, "ru")
    russian = detail(client, world, customer)["address"]
    assert russian["region"]["name"] == f"Пробная область {places.word}"
    assert russian["district"]["name"] == f"Курганский район {places.word}"
    assert russian["mahalla"]["name"] == f"Oqchobsoy {places.word}", "a mahalla has an Uzbek name only"


def test_everything_about_an_address_is_optional_and_the_street_may_be_typed(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, places: Places
) -> None:
    without = create(client, world, {"display_name": "Manzilsiz"})
    assert without.status_code == 201 and without.json()["address"] is None
    assert stored(owner, without.json()["id"]) == (None, None, None, None, None, False)

    only_region = create(client, world, {"display_name": "Viloyat", "address": {"region_id": places.region}})
    assert only_region.status_code == 201, only_region.text
    assert only_region.json()["address"] == {
        "region": {"id": places.region, "name": f"Sinov viloyati {places.word}"},
        "district": None,
        "mahalla": None,
        "street": None,
        "street_text": None,
    }

    typed = create(
        client,
        world,
        {
            "display_name": "Yozilgan",
            "address": {
                "region_id": places.region,
                "mahalla_id": places.mahalla,
                "street_text": "  Bog'  ko'chasi 12 ",
            },
        },
    )
    assert typed.status_code == 201, typed.text
    assert typed.json()["address"]["street"] is None
    assert typed.json()["address"]["street_text"] == "Bog' ko'chasi 12"
    # The mahalla's district is known to the reference, so it came with the mahalla.
    assert typed.json()["address"]["district"]["id"] == places.district


def test_a_mahalla_without_a_known_district_is_stored_without_one_and_none_is_invented(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, places: Places
) -> None:
    loose = create(
        client,
        world,
        {"display_name": "Tumansiz", "address": {"region_id": places.region, "mahalla_id": places.loose_mahalla}},
    )
    assert loose.status_code == 201, loose.text
    assert loose.json()["address"]["district"] is None
    assert stored(owner, loose.json()["id"])[:3] == (places.region, None, places.loose_mahalla)

    # A district the shop names itself is the shop's word about its customer, kept on the customer alone.
    said = create(
        client,
        world,
        {
            "display_name": "Tumani aytilgan",
            "address": {
                "region_id": places.region,
                "district_id": places.district,
                "mahalla_id": places.loose_mahalla,
                "street_id": places.other_street,
            },
        },
    )
    assert said.status_code == 201, said.text
    assert said.json()["address"]["district"]["id"] == places.district
    assert owner.execute("SELECT district_id FROM geo_mahalla WHERE id = %s", (places.loose_mahalla,)).fetchone() == (
        None,
    ), "the reference learns nothing from what a shop says"


def test_an_address_whose_chain_does_not_hold_is_refused_and_nothing_is_stored(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, places: Places
) -> None:
    second = add_district(owner, places.region, f"Ikkinchi tuman {places.word}")
    wrong: list[tuple[dict[str, Any], str]] = [
        ({"region_id": str(uuid.uuid4())}, "address.region_id"),
        ({"region_id": places.region, "district_id": places.other_district}, "address.district_id"),
        ({"region_id": places.other_region, "mahalla_id": places.mahalla}, "address.mahalla_id"),
        ({"region_id": places.region, "district_id": second, "mahalla_id": places.mahalla}, "address.mahalla_id"),
        ({**full(places), "street_id": places.other_street}, "address.street_id"),
        ({"region_id": places.region, "street_id": places.street}, "address.street_id"),
        ({**full(places), "street_text": "Bog' 12"}, "address.street_text"),
        ({"region_id": places.region, "street_text": "k" * 121}, "address.street_text"),
        ({"region_id": "samarqand"}, "address.region_id"),
        ({"district_id": places.district}, "address.region_id"),
    ]
    count = owner.execute("SELECT count(*) FROM customer WHERE shop_id = %s", (world.shop_a,)).fetchone()
    for address, field in wrong:
        refused = create(client, world, {"display_name": "Sinov", "address": address})
        assert refused.status_code == 422, (address, refused.text)
        assert field in refused.json()["error"]["fields"] or field == "address.region_id", (address, refused.text)
        changed = patch(client, world, world.settled_customer_a, {"address": address})
        assert changed.status_code == 422, (address, changed.text)
    unknown = create(client, world, {"display_name": "Sinov", "address": {**full(places), "resident": "Ali"}})
    assert unknown.status_code == 422, "an address holds places and has no other field"
    assert owner.execute("SELECT count(*) FROM customer WHERE shop_id = %s", (world.shop_a,)).fetchone() == count
    assert stored(owner, world.settled_customer_a) == (None, None, None, None, None, False)


def test_an_address_is_replaced_as_a_whole_left_alone_when_not_named_and_removed_by_null(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, places: Places
) -> None:
    customer = world.settled_customer_a
    first = patch(client, world, customer, {"address": full(places)})
    assert first.status_code == 200, first.text
    assert stored(owner, customer) == (places.region, places.district, places.mahalla, places.street, None, True)

    # Another field changes; the address is not named and stays.
    renamed = patch(client, world, customer, {"display_name": "Yangi Ism"})
    assert renamed.status_code == 200 and renamed.json()["address"]["street"]["id"] == places.street
    assert stored(owner, customer)[:4] == (places.region, places.district, places.mahalla, places.street)

    # Replaced as a whole: what the new one does not name is gone, not kept from the old one.
    second = patch(client, world, customer, {"address": {"region_id": places.other_region, "street_text": "Bog' 3"}})
    assert second.status_code == 200, second.text
    assert stored(owner, customer) == (places.other_region, None, None, None, "Bog' 3", True)

    cleared = patch(client, world, customer, {"address": None})
    assert cleared.status_code == 200 and cleared.json()["address"] is None
    assert stored(owner, customer) == (None, None, None, None, None, False)
    assert detail(client, world, customer)["address"] is None


def test_only_who_may_edit_a_customer_changes_the_address_and_only_in_their_own_shop(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, places: Places
) -> None:
    customer = world.settled_customer_a
    assert patch(client, world, customer, {"address": full(places)}).status_code == 200
    kept = stored(owner, customer)
    # A seller adds customers and does not edit them; another shop's owner and a stranger are nobody here.
    assert patch(client, world, customer, {"address": None}, user=world.seller_a).status_code == 403
    for outsider in (world.owner_b, world.stranger):
        assert patch(client, world, customer, {"address": None}, user=outsider).status_code in (403, 404)
        response = client.get(f"{shop(world)}/customers/{customer}", headers=as_user(outsider))
        assert response.status_code in (403, 404) and places.word not in response.text
    assert stored(owner, customer) == kept
    # Through its own shop, another shop's owner cannot reach the customer either.
    other = write(
        client, world.owner_b, "PATCH", f"/api/v1/shops/{world.shop_b}/customers/{customer}", {"address": None}
    )
    assert other.status_code == 404 and stored(owner, customer) == kept


def test_a_repeated_request_sets_the_address_once_and_the_same_key_with_another_address_is_refused(
    client: TestClient, world: World, on: None, places: Places
) -> None:
    headers = {**as_user(world.seller_a), **key()}
    body = {"display_name": "Bir marta", "address": full(places)}
    first = client.post(f"{shop(world)}/customers", json=body, headers=headers)
    again = client.post(f"{shop(world)}/customers", json=body, headers=headers)
    assert first.status_code == 201 and again.json() == first.json()
    other = {"display_name": "Bir marta", "address": {"region_id": places.other_region}}
    assert client.post(f"{shop(world)}/customers", json=other, headers=headers).status_code == 409


def test_the_next_customer_is_offered_the_place_the_shop_used_last_and_never_a_street(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, places: Places
) -> None:
    assert items(client, world, "last") == {"address": None}
    assert create(client, world, {"display_name": "Birinchi", "address": {"region_id": places.other_region}})
    assert create(client, world, {"display_name": "Ikkinchi", "address": full(places)}).status_code == 201
    last = items(client, world, "last")["address"]
    assert last == {
        "region": {"id": places.region, "name": f"Sinov viloyati {places.word}"},
        "district": {"id": places.district, "name": f"Qo'rg'on tumani {places.word}"},
        "mahalla": {"id": places.mahalla, "name": f"Oqchobsoy {places.word}"},
    }
    # It is this shop's own: another shop has set nothing.
    other = read(client, world.owner_b, f"/api/v1/shops/{world.shop_b}/territories/last")
    assert other.json() == {"address": None}


# --- what follows the customer -------------------------------------------------------------------------


def test_removing_a_customers_data_removes_where_they_live(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, places: Places
) -> None:
    customer = world.customer_a
    typed = {"region_id": places.region, "mahalla_id": places.mahalla, "street_text": "Bog' ko'chasi 12"}
    assert patch(client, world, customer, {"address": typed}).status_code == 200
    assert stored(owner, customer)[5] is True
    assert record(client, world, customer, "payment", 50000).status_code == 201
    link = owner.execute("SELECT id FROM customer_link WHERE customer_id = %s", (customer,)).fetchone()
    assert link is not None
    removed = client.post(f"/api/v1/me/accounts/{link[0]}/removal", headers=as_user(world.customer_of_a))
    assert removed.json()["removed"] is True

    assert stored(owner, customer) == (None, None, None, None, None, False)
    assert owner.execute("SELECT status FROM customer WHERE id = %s", (customer,)).fetchone() == ("anonymized",)
    # Nor is it what the shop is offered for its next customer.
    assert items(client, world, "last") == {"address": None}


def test_the_owners_export_says_where_each_customer_lives_only_while_the_switch_is_on(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    places: Places,
    worker_database_url: str,
    file_root: Path,
) -> None:
    switch(owner)
    typed = {"region_id": places.region, "mahalla_id": places.loose_mahalla, "street_text": "Bog' ko'chasi 12"}
    assert patch(client, world, world.customer_a, {"address": full(places)}).status_code == 200
    assert patch(client, world, world.settled_customer_a, {"address": typed}).status_code == 200

    job = ask(client, world).json()["id"]
    work(worker_database_url, file_root)
    sheet = workbook(client, world, job)["Mijozlar"]
    assert sheet[0][-1] == "Manzil"
    by_customer = {row[6]: row[-1] if len(row) == len(sheet[0]) else None for row in sheet[1:]}
    assert by_customer[str(world.customer_a)] == (
        f"Sinov viloyati {places.word}, Qo'rg'on tumani {places.word}, Oqchobsoy {places.word}, Beklarsoy {places.word}"
    )
    assert by_customer[str(world.settled_customer_a)] == (
        f"Sinov viloyati {places.word}, Bo'ston {places.word}, Bog' ko'chasi 12"
    )
    assert by_customer[str(world.archived_customer_a)] is None

    # Off again: the column is not there, and nothing of an address is anywhere in the workbook.
    switch(owner, "false")
    owner.execute(
        "UPDATE export_job SET created_at = created_at - interval '2 days' WHERE shop_id = %s", (world.shop_a,)
    )
    second = ask(client, world).json()["id"]
    work(worker_database_url, file_root)
    book = workbook(client, world, second)
    assert "Manzil" not in book["Mijozlar"][0] and places.word not in str(book)


# --- the import ----------------------------------------------------------------------------------------


def seed_directory(root: Path, word: str) -> tuple[Path, dict[str, str]]:
    """A seed of two regions, two districts, five mahallas and three streets, with the gaps and the
    faults the real one has: a mahalla without a code, mahallas whose district the seed does not say,
    two of one name in one region, a row that names a district the seed does not have, and a street
    whose mahalla cannot be told."""
    codes = {"region": code(4), "other": code(4)}
    codes |= {"district": codes["region"] + "216", "far": codes["other"] + "201"}
    codes |= {name: f"{code(3)}-{code(4)}" for name in ("m1", "m2", "m3", "m4")}
    files: dict[str, list[list[str]]] = {
        "regions.csv": [
            ["soato", "name_uz", "name_ru", "name_en"],
            [codes["region"], f"Sinov viloyati {word}", f"Пробная область {word}", f"Trial region {word}"],
            [codes["other"], f"Boshqa viloyat {word}", "", ""],
            ["", "Kodsiz viloyat", "", ""],
        ],
        "districts.csv": [
            ["soato", "region_soato", "name_uz", "name_ru", "name_en", "population", "families", "households"],
            [codes["district"], codes["region"], f"Qo'rg'on tumani {word}", "", "", "128527", "32155", "24701"],
            [codes["far"], codes["other"], f"Uzoq tuman {word}", "", "", "", "", ""],
            [code(7), "0000", "Viloyatsiz tuman", "", "", "", "", ""],
        ],
        "mahallas.csv": [
            ["code", "name_uz", "region_soato", "district_soato", "source_group"],
            [codes["m1"], f"Oqchobsoy {word}", codes["region"], codes["district"], "1404"],
            ["", f"Qo'ralos {word}", codes["region"], codes["district"], "1404"],
            [codes["m2"], f"Bo'ston {word}", codes["other"], "", "101-"],
            [codes["m3"], f"Bo'ston {word}", codes["other"], "", "102-"],
            # Its district is of another region than the one it names, and one names no known district.
            [codes["m4"], f"Adashgan {word}", codes["other"], codes["district"], "9"],
            [f"{code(3)}-{code(4)}", f"Tumani yo'q {word}", codes["region"], "9999999", "9"],
        ],
        "streets.csv": [
            ["district_soato", "mahalla", "street", "kind", "road_type"],
            [codes["district"], f"Oqchobsoy {word}", f"Beklarsoy {word}", "ko'cha", "to'g'ri"],
            [codes["district"], f"Qo'ralos {word}", f"Qo'ralos {word}", "qishloq", "tor"],
            [codes["district"], f"Yo'q mahalla {word}", "Hech qayer", "ko'cha", "berk"],
            [codes["district"], f"Oqchobsoy {word}", "Turi noma'lum", "xiyobon", "shoh"],
        ],
    }
    directory = root / "seed"
    directory.mkdir(parents=True)
    for name, rows in files.items():
        # As the real seed is written: UTF-8 with a byte order mark.
        with (directory / name).open("w", encoding="utf-8-sig", newline="") as file:
            csv.writer(file).writerows(rows)
    return directory, codes


def counted(part: Any) -> tuple[int, int, int, int, int]:
    return (part.rows, part.added, part.updated, part.skipped, part.retired)


def retire_nothing_else(owner: psycopg.Connection) -> dict[str, set[str]]:
    """Every place other tests left active. The import treats its files as the whole reference, so on
    the session's one database it would retire them: they are put back after it."""
    return {
        table: {str(row[0]) for row in owner.execute(f"SELECT id FROM {table} WHERE status = 'active'")}
        for table in GEO_TABLES
    }


def put_back(owner: psycopg.Connection, kept: dict[str, set[str]]) -> None:
    for table, ids in kept.items():
        owner.execute(f"UPDATE {table} SET status = 'active' WHERE id = ANY(%s::uuid[])", (list(ids),))


def test_the_import_loads_the_four_files_and_running_it_again_adds_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, admin_database_url: str, tmp_path: Path
) -> None:
    word = tag()
    directory, codes = seed_directory(tmp_path, word)
    settings = Settings(admin_database_url=admin_database_url)
    kept = retire_nothing_else(owner)
    try:
        first = asyncio.run(run(directory, settings))
        assert counted(first.regions)[:4] == (3, 2, 0, 1)
        assert counted(first.districts)[:4] == (3, 2, 0, 1)
        assert counted(first.mahallas)[:4] == (6, 4, 0, 2)
        assert first.mahallas_without_district == 2
        assert counted(first.streets)[:4] == (4, 2, 0, 2)
        said = report(first)
        assert "mahallas: 6 read, 4 added" in said and word not in said, "counts and no row"

        assert owner.execute(
            "SELECT m.name_uz, m.code, m.source_group, d.soato FROM geo_mahalla m "
            "LEFT JOIN geo_district d ON d.id = m.district_id WHERE m.name_uz LIKE %s "
            "ORDER BY m.name_uz, m.source_group",
            (f"% {word}",),
        ).fetchall() == [
            (f"Bo'ston {word}", codes["m2"], "101-", None),
            (f"Bo'ston {word}", codes["m3"], "102-", None),
            (f"Oqchobsoy {word}", codes["m1"], "1404", codes["district"]),
            (f"Qo'ralos {word}", None, "1404", codes["district"]),
        ], "a district only where the seed says one; none invented from the group"
        assert owner.execute(
            "SELECT population, families, households FROM geo_district WHERE soato = %s", (codes["district"],)
        ).fetchone() == (128527, 32155, 24701)
        assert owner.execute(
            "SELECT s.name, s.kind, s.road_type FROM geo_street s WHERE s.name LIKE %s ORDER BY s.name", (f"% {word}",)
        ).fetchall() == [(f"Beklarsoy {word}", "street", "straight"), (f"Qo'ralos {word}", "village", "narrow")]

        # A customer lives in a mahalla of the first run.
        region = owner.execute("SELECT id FROM geo_region WHERE soato = %s", (codes["region"],)).fetchone()
        mahalla = owner.execute("SELECT id FROM geo_mahalla WHERE code = %s", (codes["m1"],)).fetchone()
        assert region is not None and mahalla is not None
        address = {"region_id": str(region[0]), "mahalla_id": str(mahalla[0])}
        assert patch(client, world, world.settled_customer_a, {"address": address}).status_code == 200

        # Again, unchanged: nothing is added, nothing retired, every identifier is what it was.
        ids = owner.execute("SELECT array_agg(id ORDER BY id) FROM geo_mahalla WHERE status = 'active'").fetchone()
        second = asyncio.run(run(directory, settings))
        assert [counted(part) for part in (second.regions, second.districts, second.mahallas, second.streets)] == [
            (3, 0, 2, 1, 0),
            (3, 0, 2, 1, 0),
            (6, 0, 4, 2, 0),
            (4, 0, 2, 2, 0),
        ]
        assert (
            owner.execute("SELECT array_agg(id ORDER BY id) FROM geo_mahalla WHERE status = 'active'").fetchone() == ids
        )

        # A later seed without the customer's mahalla, with a name corrected and a district now known.
        rows = list(csv.reader((directory / "mahallas.csv").open(encoding="utf-8-sig", newline="")))
        rows = [row for row in rows if row[0] != codes["m1"]]
        for row in rows:
            if row[0] == codes["m2"]:
                row[1], row[3] = f"Bo'ston yangi {word}", codes["far"]
        with (directory / "mahallas.csv").open("w", encoding="utf-8-sig", newline="") as file:
            csv.writer(file).writerows(rows)
        third = asyncio.run(run(directory, settings))
        assert counted(third.mahallas) == (5, 0, 3, 2, 1)
        assert third.mahallas_without_district == 1
        # Its street's mahalla is gone from the seed, so the street cannot be matched and is retired too.
        assert counted(third.streets) == (4, 0, 1, 3, 1)
        assert owner.execute(
            "SELECT m.name_uz, d.soato FROM geo_mahalla m LEFT JOIN geo_district d ON d.id = m.district_id "
            "WHERE m.code = %s",
            (codes["m2"],),
        ).fetchone() == (f"Bo'ston yangi {word}", codes["far"])

        # The mahalla the customer points at is retired, not deleted: no longer offered, still shown.
        assert owner.execute("SELECT status FROM geo_mahalla WHERE id = %s", (mahalla[0],)).fetchone() == ("retired",)
        offered = items(client, world, "mahallas", region=str(region[0]), q=word)
        assert str(mahalla[0]) not in {item["id"] for item in offered["items"]}
        shown = detail(client, world, world.settled_customer_a)["address"]
        assert shown["mahalla"] == {"id": str(mahalla[0]), "name": f"Oqchobsoy {word}"}

        # And a seed that has it again brings it back, under the identifier it always had.
        with (directory / "mahallas.csv").open("w", encoding="utf-8-sig", newline="") as file:
            csv.writer(file).writerows(
                [
                    ["code", "name_uz", "region_soato", "district_soato", "source_group"],
                    [codes["m1"], f"Oqchobsoy {word}", codes["region"], codes["district"], "1404"],
                ]
            )
        fourth = asyncio.run(run(directory, settings))
        assert (fourth.mahallas.added, fourth.mahallas.updated) == (0, 1)
        assert owner.execute("SELECT status FROM geo_mahalla WHERE id = %s", (mahalla[0],)).fetchone() == ("active",)
    finally:
        put_back(owner, kept)


def test_the_import_refuses_a_directory_or_a_configuration_it_cannot_use_and_changes_nothing(
    owner: psycopg.Connection, admin_database_url: str, tmp_path: Path
) -> None:
    directory, _ = seed_directory(tmp_path, tag())
    settings = Settings(admin_database_url=admin_database_url)
    before = [
        owner.execute(f"SELECT count(*), count(*) FILTER (WHERE status = 'active') FROM {t}").fetchone()
        for t in GEO_TABLES
    ]
    with pytest.raises(ValueError, match="QD_ADMIN_DATABASE_URL"):
        asyncio.run(run(directory, Settings(admin_database_url="")))
    # A missing file would otherwise read as "the reference has no streets any more".
    (directory / "streets.csv").rename(directory / "streets.txt")
    with pytest.raises(ValueError, match=r"streets\.csv"):
        asyncio.run(run(directory, settings))
    (directory / "streets.txt").rename(directory / "streets.csv")
    (directory / "mahallas.csv").write_text("name;region\nBo'ston;1\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"mahallas\.csv has no column"):
        asyncio.run(run(directory, settings))
    directory, _ = seed_directory(tmp_path / "second", tag())
    (directory / "regions.csv").write_text("soato,name_uz\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"regions\.csv has no rows"):
        asyncio.run(run(directory, settings))
    after = [
        owner.execute(f"SELECT count(*), count(*) FILTER (WHERE status = 'active') FROM {t}").fetchone()
        for t in GEO_TABLES
    ]
    assert after == before


def test_the_ordinary_role_cannot_run_the_import(tmp_path: Path, app_database_url: str) -> None:
    """The counterpart of "only the administrators' role may write the reference": given the ordinary
    application's connection, the same command is refused by the database."""
    directory, _ = seed_directory(tmp_path, tag())
    with pytest.raises(Exception, match="permission denied"):
        asyncio.run(run(directory, Settings(admin_database_url=app_database_url)))
