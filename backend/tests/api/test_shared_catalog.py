"""The shared product catalogue through the API: the switch, searching, picking, what a shop proposes,
the administrators' queue, photos and the import.

Behind the platform switch `catalog_on`. Each rule has the case that must work and the case that must be
refused. Who may call what, by role and as an outsider, is in the authorization suite; what the database
itself refuses is tests/db/test_shared_catalog_schema.py. The products are invented.
"""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.domain import shared_catalog
from qarz.infrastructure.settings import Settings
from qarz.interface.import_shared_catalog import report, run

from ..receipt_samples import EXIF, JPEG, PNG, TEXT, jpeg, png
from .conftest import ADMIN_API, AdminEnv, World, as_user, elevate, make_admin
from .test_customers_ledger import key, read, shop, write
from .test_stock import switch as stock_switch

pytestmark = pytest.mark.db

NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "Topilmadi.", "fields": {}}}
QUEUE = f"{ADMIN_API}/catalog/suggestions"
ITEM_KEYS = {"id", "name", "unit", "price", "learned", "status", "merged_into"}


def switch(owner: psycopg.Connection, value: str = "true") -> None:
    """Store the switch as an administrator's change would (`value` is JSON). The row is signed with a
    user identifier, so the `admin_env` fixture behind `client` removes it after the test."""
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('catalog_on', %s::jsonb, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by",
        (value, str(uuid.uuid4())),
    )


@pytest.fixture
def on(client: TestClient, owner: psycopg.Connection) -> Iterator[None]:
    switch(owner)
    yield


@pytest.fixture
def admin(client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv) -> dict[str, str]:
    """Headers of an administrator who passed the second factor."""
    admin_env.clock.freeze()
    return elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))


def tag() -> str:
    """A word no other test's item carries: the catalogue is one for the whole session's database."""
    return "q" + uuid.uuid4().hex[:11]


def shared(world: World) -> str:
    return f"{shop(world)}/shared-catalog"


def seed(
    owner: psycopg.Connection,
    *,
    name_ru: str | None = None,
    name_uz: str | None = None,
    amount: str | None = None,
    category: str = "other",
    price_hint: int | None = None,
    image_key: str | None = None,
    status: str = "active",
) -> str:
    item = uuid.uuid4()
    owner.execute(
        "INSERT INTO shared_item (id, name_ru, name_uz, search_norm, category, amount, price_hint, image_key, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            item,
            name_ru,
            name_uz,
            shared_catalog.search_norm(name_ru, name_uz, amount),
            category,
            amount,
            price_hint,
            image_key,
            status,
        ),
    )
    return str(item)


def approved_code(owner: psycopg.Connection, item: str) -> str:
    code = f"SC-{uuid.uuid4().hex[:14]}"
    owner.execute("INSERT INTO shared_barcode (code, item_id) VALUES (%s, %s)", (code, item))
    return code


def search(client: TestClient, world: World, user: uuid.UUID | None = None, **params: Any) -> dict[str, Any]:
    response = read(client, user or world.manager_a, shared(world), **params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def pick(client: TestClient, world: World, item: str, price: int = 10_000, user: uuid.UUID | None = None) -> Any:
    return write(client, user or world.manager_a, "POST", f"{shared(world)}/{item}/pick", {"price": price})


def new_item(client: TestClient, world: World, name: str, user: uuid.UUID | None = None) -> str:
    response = write(client, user or world.manager_a, "POST", f"{shop(world)}/catalog", {"name": name, "price": 5_000})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def suggestions(owner: psycopg.Connection, shop_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT kind, name, unit, barcode, status FROM shared_suggestion WHERE shop_id = %s ORDER BY created_at, id",
        (shop_id,),
    ).fetchall()


def queue(client: TestClient, admin: dict[str, str], word: str, status: str = "pending") -> list[dict[str, Any]]:
    """The suggestions of the queue that carry `word`: the queue is every shop's and every test's."""
    found: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params = {"status": status, "limit": 100, **({} if cursor is None else {"cursor": cursor})}
        response = client.get(QUEUE, params=params, headers=admin)
        assert response.status_code == 200, response.text
        body = response.json()
        found += [row for row in body["items"] if word in json.dumps(row, ensure_ascii=False)]
        cursor = body["next_cursor"]
        if cursor is None:
            return found


def decide(client: TestClient, admin: dict[str, str], suggestion: str, action: str, body: Any = None) -> Any:
    return client.post(f"{QUEUE}/{suggestion}/{action}", json=body, headers={**admin, **key()})


def counts(owner: psycopg.Connection) -> tuple[Any, ...]:
    return tuple(
        owner.execute(f"SELECT count(*) FROM {table}").fetchone()
        for table in ("shared_item", "shared_barcode", "shared_suggestion")
    )


# --- the switch --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("stored", [None, "false", "null", '"true"', "1"])
def test_with_the_switch_off_no_route_of_the_catalogue_exists_for_anyone(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], stored: str | None
) -> None:
    """Off is the default (no row), and only the JSON value `true` is on. A manager, an administrator, a
    stranger and someone who is not signed in all get the answer of a route that was never there."""
    if stored is not None:
        switch(owner, stored)
    item = seed(owner, name_uz=f"Choy {tag()}", image_key="a" * 64)
    code = approved_code(owner, item)
    before = counts(owner)
    routes = [
        ("GET", shared(world), None),
        ("GET", f"{shared(world)}/lookup?code={code}", None),
        ("POST", f"{shared(world)}/{item}/pick", {"price": 9_000}),
        ("GET", QUEUE, None),
        ("POST", f"{QUEUE}/{uuid.uuid4()}/approve", None),
        ("POST", f"{QUEUE}/{uuid.uuid4()}/reject", None),
        ("GET", f"/files/catalog/{'a' * 64}", None),
    ]
    for method, path, body in routes:
        for headers in (as_user(world.manager_a), admin, as_user(world.stranger), {}):
            response = client.request(method, path, json=body, headers={**headers, **key()})
            assert response.status_code == 404 and response.json() == NOT_FOUND, (method, path, headers)
    assert counts(owner) == before
    assert "x-qarz-catalog" not in client.get("/api/v1/me/shops", headers=as_user(world.manager_a)).headers


def test_with_the_switch_off_adding_items_and_barcodes_answers_exactly_as_before_and_proposes_nothing(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The same requests with the switch off and with it on give the same bodies, key for key; off,
    nothing reaches the tables of the catalogue."""
    stock_switch(owner)

    def run_once(word: str) -> list[Any]:
        created = write(
            client, world.manager_a, "POST", f"{shop(world)}/catalog", {"name": f"Un {word}", "price": 7_000}
        )
        assert created.status_code == 201, created.text
        patched = write(
            client,
            world.manager_a,
            "PATCH",
            f"{shop(world)}/stock/items/{created.json()['id']}",
            {"barcodes": [f"OFF-{word}"]},
        )
        assert patched.status_code == 200, patched.text
        received = write(
            client,
            world.manager_a,
            "POST",
            f"{shop(world)}/stock/documents",
            {
                "kind": "receipt",
                "post": True,
                "lines": [
                    {
                        "new_item": {"name": f"Tuz {word}", "price": 3_000, "barcode": f"NEW-{word}"},
                        "qty": "2",
                        "unit_cost": 2_000,
                    }
                ],
            },
        )
        assert received.status_code == 201, received.text
        listed = read(client, world.manager_a, f"{shop(world)}/catalog", q=f"Un {word}").json()
        return [created.json(), patched.json(), received.json(), listed]

    def shape(value: Any) -> Any:
        if isinstance(value, dict):
            return {name: shape(inner) for name, inner in value.items()}
        if isinstance(value, list):
            return [shape(inner) for inner in value]
        return type(value).__name__

    before = counts(owner)
    off = run_once(tag())
    assert counts(owner) == before and suggestions(owner, world.shop_a) == []
    switch(owner)
    word = tag()
    on_ = run_once(word)
    assert [shape(body) for body in off] == [shape(body) for body in on_]
    assert set(off[0]) == ITEM_KEYS
    assert suggestions(owner, world.shop_a) == [
        ("item", f"Un {word}", "dona", None, "pending"),
        ("item", f"Tuz {word}", "dona", f"NEW-{word}", "pending"),
    ], "on, what was added by hand is proposed: the control for the claim above"


def test_the_shops_list_says_the_catalogue_is_on_only_when_it_is(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    assert "x-qarz-catalog" not in client.get("/api/v1/me/shops", headers=as_user(world.seller_a)).headers
    switch(owner)
    assert client.get("/api/v1/me/shops", headers=as_user(world.seller_a)).headers["x-qarz-catalog"] == "on"


# --- searching -----------------------------------------------------------------------------------------------


def test_a_search_finds_an_item_by_either_name_in_either_script(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    word = tag()
    both = seed(owner, name_ru=f"Чай зелёный {word}", name_uz=f"Ko'k choy {word}", amount="100 г", category="tea")
    only_ru = seed(owner, name_ru=f"Чай чёрный {word}", amount="250 г", category="tea")
    seed(owner, name_uz=f"Yashirin choy {word}", status="hidden")

    def found(query: str, **more: Any) -> list[str]:
        return [item["id"] for item in search(client, world, q=query, **more)["items"]]

    assert set(found(word)) == {both, only_ru}, "a hidden item is not offered"
    assert found(f"ko'k {word}") == [both], "the Uzbek name"
    assert found(f"зелёный {word}") == [both], "the Russian name"
    assert found(f"zelyoniy {word}") == [both], "the Russian name typed in Latin"
    assert found(f"кўк {word}") == [both], "the Uzbek name typed in Cyrillic"
    assert found(f"{word} 250") == [only_ru], "every word must be there, the package size among them"
    assert found(f"{word} sut") == []
    assert set(found(word, category="tea")) == {both, only_ru}
    assert found(word, category="dairy") == []


def test_an_item_is_named_in_the_readers_language_with_the_other_name_standing_in(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    word = tag()
    both = seed(owner, name_ru=f"Чай {word}", name_uz=f"Choy {word}", amount="100 г", category="tea", price_hint=12_990)
    only_ru = seed(owner, name_ru=f"Кофе {word}")

    def names(lang: str) -> dict[str, str]:
        owner.execute("UPDATE app_user SET lang = %s WHERE id = %s", (lang, world.manager_a))
        return {item["id"]: item["name"] for item in search(client, world, q=word)["items"]}

    assert names("uz") == {both: f"Choy {word}", only_ru: f"Кофе {word}"}
    assert names("ru") == {both: f"Чай {word}", only_ru: f"Кофе {word}"}
    assert names("uz-Cyrl")[both].startswith("Чой "), "Uzbek Cyrillic is made from the Uzbek name"
    assert names("uz-Cyrl")[only_ru] == f"Кофе {word}", "a Russian name is never transliterated"
    assert names("en")[both] == f"Choy {word}"
    item = next(item for item in search(client, world, q=word)["items"] if item["id"] == both)
    assert item == {
        "id": both,
        "name": f"Choy {word}",
        "name_ru": f"Чай {word}",
        "name_uz": f"Choy {word}",
        "amount": "100 г",
        "category": "tea",
        "subcategory": None,
        "unit": "dona",
        "price_hint": 12_990,
        "image": None,
        "picked": None,
    }


def test_a_search_is_paged_and_names_the_categories(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    word = tag()
    made = {seed(owner, name_uz=f"Non {word} {number}") for number in range(5)}
    first = search(client, world, q=word, limit=2)
    assert len(first["items"]) == 2 and first["next_cursor"] is not None
    assert first["categories"] == list(shared_catalog.CATEGORIES)
    seen = [item["id"] for item in first["items"]]
    cursor = first["next_cursor"]
    while cursor is not None:
        page = search(client, world, q=word, limit=2, cursor=cursor)
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]
    assert len(seen) == 5 and set(seen) == made, "every item once"


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": 500}, "limit"),
        ({"category": "weapons"}, "category"),
        ({"q": "x" * 81}, "q"),
        ({"cursor": "not-a-cursor"}, "cursor"),
    ],
)
def test_a_search_that_makes_no_sense_is_refused(
    client: TestClient, world: World, on: None, params: dict[str, Any], field: str
) -> None:
    response = read(client, world.manager_a, shared(world), **params)
    assert response.status_code == 422, response.text
    assert field in response.json()["error"]["fields"]


# --- picking -------------------------------------------------------------------------------------------------


def test_picking_makes_the_shops_own_item_at_the_shops_own_price(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    word = tag()
    item = seed(owner, name_uz=f"Choy {word}", amount="100 g", price_hint=12_990)
    response = pick(client, world, item, 15_000)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == ITEM_KEYS
    assert (body["name"], body["unit"], body["price"], body["learned"], body["status"]) == (
        f"Choy {word} 100 g",
        "dona",
        15_000,
        False,
        "active",
    ), "the price is the one typed, never the catalogue's approximate one"
    listed = read(client, world.seller_a, f"{shop(world)}/catalog", q=word).json()["items"]
    assert [row["id"] for row in listed] == [body["id"]]
    found = next(row for row in search(client, world, q=word)["items"] if row["id"] == item)
    assert found["picked"] == {"id": body["id"], "name": f"Choy {word} 100 g", "price": 15_000, "status": "active"}
    assert found["price_hint"] == 12_990
    assert suggestions(owner, world.shop_a) == [], "what came from the catalogue is not proposed back to it"


def test_picking_the_same_item_twice_adds_nothing_and_keeps_the_first_price(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    word = tag()
    item = seed(owner, name_uz=f"Shakar {word}")
    first = pick(client, world, item, 9_000).json()
    again = pick(client, world, item, 11_000)
    assert again.status_code == 200 and again.json() == first
    assert owner.execute(
        "SELECT count(*), min(price) FROM catalog_item WHERE shop_id = %s AND shared_item_id = %s", (world.shop_a, item)
    ).fetchone() == (1, 9_000)


def test_another_shop_picks_the_same_item_for_itself_and_sees_nothing_of_the_first(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    word = tag()
    item = seed(owner, name_uz=f"Guruch {word}")
    mine = pick(client, world, item, 14_000).json()
    theirs_before = read(client, world.owner_b, f"/api/v1/shops/{world.shop_b}/shared-catalog", q=word).json()
    assert theirs_before["items"][0]["picked"] is None, "shop B is not told that shop A holds it, or at what price"
    theirs = write(
        client, world.owner_b, "POST", f"/api/v1/shops/{world.shop_b}/shared-catalog/{item}/pick", {"price": 13_500}
    )
    assert theirs.status_code == 200 and theirs.json()["id"] != mine["id"] and theirs.json()["price"] == 13_500


def test_a_name_the_shop_already_has_is_refused_as_for_an_item_typed_by_hand(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    word = tag()
    own = new_item(client, world, f"Makaron {word}")
    item = seed(owner, name_uz=f"Makaron {word}")
    response = pick(client, world, item)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CATALOG_NAME_TAKEN"
    assert response.json()["error"]["fields"] == {"existing_id": own, "existing_status": "active"}
    assert owner.execute("SELECT count(*) FROM catalog_item WHERE shared_item_id = %s", (item,)).fetchone() == (0,)


@pytest.mark.parametrize(
    ("body", "field"),
    [({"price": 0}, "price"), ({"price": 100_000_001}, "price"), ({"price": 5_000, "unit": "1 2 3"}, "unit")],
)
def test_a_pick_with_a_price_or_unit_that_cannot_be_is_refused(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, body: dict[str, Any], field: str
) -> None:
    item = seed(owner, name_uz=f"Tuz {tag()}")
    response = write(client, world.manager_a, "POST", f"{shared(world)}/{item}/pick", body)
    assert response.status_code == 422, response.text
    assert field in response.json()["error"]["fields"]
    assert owner.execute("SELECT count(*) FROM catalog_item WHERE shared_item_id = %s", (item,)).fetchone() == (0,)


def test_an_item_that_is_hidden_or_does_not_exist_cannot_be_picked(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    hidden = seed(owner, name_uz=f"Yashirin {tag()}", status="hidden")
    for item in (hidden, str(uuid.uuid4())):
        assert pick(client, world, item).status_code == 404


def test_a_seller_who_may_not_add_items_may_not_pick_one(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    item = seed(owner, name_uz=f"Sut {tag()}")
    response = pick(client, world, item, user=world.seller_a)
    assert response.status_code == 403, response.text
    assert read(client, world.seller_a, shared(world)).status_code == 403
    assert pick(client, world, item).status_code == 200, "the control: a manager may"


# --- barcodes ------------------------------------------------------------------------------------------------


def test_a_scan_that_no_item_of_the_shop_has_falls_through_to_the_catalogue(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    stock_switch(owner)
    word = tag()
    item = seed(owner, name_uz=f"Kofe {word}", amount="95 g", price_hint=38_000)
    code = approved_code(owner, item)
    assert read(client, world.manager_a, f"{shop(world)}/stock/lookup", code=code).status_code == 404
    found = read(client, world.manager_a, f"{shared(world)}/lookup", code=code)
    assert found.status_code == 200, found.text
    assert (found.json()["id"], found.json()["price_hint"], found.json()["picked"]) == (item, 38_000, None)

    picked = pick(client, world, item, 41_000).json()
    own = read(client, world.manager_a, f"{shop(world)}/stock/lookup", code=code)
    assert own.status_code == 200 and own.json()["id"] == picked["id"], "the picked item carries the approved code"
    assert own.json()["barcodes"] == [code]
    assert read(client, world.manager_a, f"{shared(world)}/lookup", code=code).json()["picked"]["id"] == picked["id"]


def test_an_unknown_or_malformed_code_is_not_found_in_the_catalogue(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    hidden = seed(owner, name_uz=f"Yashirin {tag()}", status="hidden")
    assert read(client, world.manager_a, f"{shared(world)}/lookup", code=f"NO-{tag()}").status_code == 404
    assert (
        read(client, world.manager_a, f"{shared(world)}/lookup", code=approved_code(owner, hidden)).status_code == 404
    )
    assert read(client, world.manager_a, f"{shared(world)}/lookup", code="x" * 60).status_code == 422


def test_a_barcode_attached_to_a_picked_item_is_proposed_and_one_on_the_shops_own_item_is_not(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    stock_switch(owner)
    word = tag()
    item = seed(owner, name_uz=f"Yog' {word}")
    known = approved_code(owner, item)
    picked = pick(client, world, item).json()["id"]
    codes = [known, f"NEW-{word}"]
    patched = write(client, world.manager_a, "PATCH", f"{shop(world)}/stock/items/{picked}", {"barcodes": codes})
    assert patched.status_code == 200 and patched.json()["barcodes"] == codes, "it works in the shop at once"
    assert suggestions(owner, world.shop_a) == [("barcode", None, None, f"NEW-{word}", "pending")], (
        "the new code is proposed; the one the catalogue already has is not"
    )
    assert (
        read(
            client, world.owner_b, f"/api/v1/shops/{world.shop_b}/shared-catalog/lookup", code=f"NEW-{word}"
        ).status_code
        == 404
    ), "not offered to other shops until an administrator approves it"
    # Saving the same codes again proposes nothing twice.
    assert (
        write(client, world.manager_a, "PATCH", f"{shop(world)}/stock/items/{picked}", {"barcodes": codes}).status_code
        == 200
    )
    assert len(suggestions(owner, world.shop_a)) == 1

    own = new_item(client, world, f"O'zimniki {word}")
    assert (
        write(
            client, world.manager_a, "PATCH", f"{shop(world)}/stock/items/{own}", {"barcodes": [f"OWN-{word}"]}
        ).status_code
        == 200
    )
    assert [row for row in suggestions(owner, world.shop_a) if row[0] == "barcode"] == [
        ("barcode", None, None, f"NEW-{word}", "pending")
    ]


# --- what a shop proposes ------------------------------------------------------------------------------------


def test_an_item_added_by_hand_works_at_once_and_is_proposed_with_its_name_and_unit_only(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    word = tag()
    created = write(
        client,
        world.manager_a,
        "POST",
        f"{shop(world)}/catalog",
        {"name": f"Qatiq {word}", "unit": "l", "price": 9_500},
    )
    assert created.status_code == 201 and set(created.json()) == ITEM_KEYS
    row = owner.execute("SELECT * FROM shared_suggestion WHERE shop_id = %s", (world.shop_a,)).fetchone()
    assert row is not None
    assert "9500" not in json.dumps([str(value) for value in row]), "the shop's price is in no column"
    assert suggestions(owner, world.shop_a) == [("item", f"Qatiq {word}", "l", None, "pending")]
    assert search(client, world, q=word)["items"] == [], "not in the catalogue until an administrator approves it"
    assert read(client, world.owner_b, f"/api/v1/shops/{world.shop_b}/shared-catalog", q=word).json()["items"] == []


def test_a_shop_cannot_bury_the_queue(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shared_catalog, "MAX_PENDING", 2)
    word = tag()
    for number in range(4):
        new_item(client, world, f"Mahsulot {word} {number}")
    assert len(suggestions(owner, world.shop_a)) == 2, "the rest still work in the shop and are not proposed"
    assert len(read(client, world.manager_a, f"{shop(world)}/catalog", q=word).json()["items"]) == 4


# --- the administrators' queue ---------------------------------------------------------------------------


def test_the_queue_shows_what_was_proposed_and_never_whose_it_is(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, admin: dict[str, str]
) -> None:
    word = tag()
    new_item(client, world, f"Asal {word}")
    theirs = write(
        client, world.owner_b, "POST", f"/api/v1/shops/{world.shop_b}/catalog", {"name": f"Asal {word}", "price": 7_777}
    )
    assert theirs.status_code == 201, theirs.text
    rows = queue(client, admin, word)
    assert len(rows) == 2
    assert set(rows[0]) == {
        "id",
        "kind",
        "name",
        "unit",
        "barcode",
        "shared_item",
        "status",
        "created_at",
        "decided_at",
        "same",
    }
    assert (rows[0]["kind"], rows[0]["name"], rows[0]["unit"], rows[0]["status"], rows[0]["same"]) == (
        "item",
        f"Asal {word}",
        "dona",
        "pending",
        1,
    )
    told = json.dumps(rows)
    for private in (world.shop_a, world.shop_b, world.manager_a, world.owner_b, world.owner_a):
        assert str(private) not in told
    assert "Shop" not in told and "price" not in told and "7777" not in told


def test_an_approved_item_is_offered_to_every_shop_and_tied_to_the_shop_that_proposed_it(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, admin: dict[str, str]
) -> None:
    word = tag()
    own = new_item(client, world, f"Murabbo {word}")
    (waiting,) = queue(client, admin, word)
    body = {"name_ru": f"Варенье {word}", "name_uz": f"Murabbo {word}", "category": "sweets"}
    approved = decide(client, admin, waiting["id"], "approve", body)
    assert approved.status_code == 200, approved.text
    assert (approved.json()["status"], approved.json()["kind"]) == ("approved", "item")
    made = approved.json()["shared_item_id"]

    theirs = read(client, world.owner_b, f"/api/v1/shops/{world.shop_b}/shared-catalog", q=f"варенье {word}").json()
    assert [(row["id"], row["category"], row["picked"]) for row in theirs["items"]] == [(made, "sweets", None)]
    mine = search(client, world, q=word)["items"]
    assert mine[0]["picked"]["id"] == own, "the shop that proposed it already holds it"
    assert queue(client, admin, word) == []
    assert [row["status"] for row in queue(client, admin, word, "approved")] == ["approved"]


def test_an_approved_barcode_is_offered_to_any_shop_that_scans_it(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, admin: dict[str, str]
) -> None:
    stock_switch(owner)
    word = tag()
    item = seed(owner, name_uz=f"Sharbat {word}")
    picked = pick(client, world, item).json()["id"]
    code = f"APR-{word}"
    assert (
        write(client, world.manager_a, "PATCH", f"{shop(world)}/stock/items/{picked}", {"barcodes": [code]}).status_code
        == 200
    )
    (waiting,) = queue(client, admin, code)
    assert (waiting["kind"], waiting["barcode"], waiting["shared_item"]["id"]) == ("barcode", code, item)
    lookup = f"/api/v1/shops/{world.shop_b}/shared-catalog/lookup"
    assert read(client, world.owner_b, lookup, code=code).status_code == 404
    assert decide(client, admin, waiting["id"], "approve").status_code == 200
    assert read(client, world.owner_b, lookup, code=code).json()["id"] == item


def test_a_suggestion_is_decided_once_and_a_rejected_one_changes_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, admin: dict[str, str]
) -> None:
    word = tag()
    own = new_item(client, world, f"Sirka {word}")
    (waiting,) = queue(client, admin, word)
    before = counts(owner)[:2]
    rejected = decide(client, admin, waiting["id"], "reject")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    for action in ("approve", "reject"):
        again = decide(
            client, admin, waiting["id"], action, {"name_uz": f"Sirka {word}"} if action == "approve" else None
        )
        assert again.status_code == 409 and again.json()["error"]["code"] == "SUGGESTION_ALREADY_DECIDED"
    assert counts(owner)[:2] == before
    assert search(client, world, q=word)["items"] == []
    kept = read(client, world.manager_a, f"{shop(world)}/catalog", q=word).json()["items"]
    assert [row["id"] for row in kept] == [own], "the shop's own item is untouched"
    assert decide(client, admin, str(uuid.uuid4()), "reject").status_code == 404


@pytest.mark.parametrize(
    ("body", "field"),
    [
        (None, "name_uz"),
        ({"name_ru": "  ", "name_uz": ""}, "name_uz"),
        ({"name_uz": "Bor", "category": "weapons"}, "category"),
        ({"name_ru": "x" * 301}, "name_ru"),
    ],
)
def test_an_item_is_not_approved_without_a_name_or_into_a_category_we_do_not_have(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    on: None,
    admin: dict[str, str],
    body: dict[str, Any] | None,
    field: str,
) -> None:
    word = tag()
    new_item(client, world, f"Xamir {word}")
    (waiting,) = queue(client, admin, word)
    before = counts(owner)[:2]
    response = decide(client, admin, waiting["id"], "approve", body)
    assert response.status_code == 422, response.text
    assert field in response.json()["error"]["fields"]
    assert counts(owner)[:2] == before
    assert [row["id"] for row in queue(client, admin, word)] == [waiting["id"]], "it still waits"


def test_a_barcode_that_names_another_catalogue_item_is_refused(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, admin: dict[str, str]
) -> None:
    stock_switch(owner)
    word = tag()
    item, other = seed(owner, name_uz=f"Limon {word}"), seed(owner, name_uz=f"Apelsin {word}")
    picked = pick(client, world, item).json()["id"]
    code = f"TKN-{word}"
    assert (
        write(client, world.manager_a, "PATCH", f"{shop(world)}/stock/items/{picked}", {"barcodes": [code]}).status_code
        == 200
    )
    (waiting,) = queue(client, admin, code)
    owner.execute("INSERT INTO shared_barcode (code, item_id) VALUES (%s, %s)", (code, other))
    response = decide(client, admin, waiting["id"], "approve")
    assert response.status_code == 409 and response.json()["error"]["code"] == "SHARED_BARCODE_TAKEN"
    assert owner.execute("SELECT item_id::text FROM shared_barcode WHERE code = %s", (code,)).fetchone() == (other,)


@pytest.mark.parametrize("params", [{"status": "waiting"}, {"limit": 0}, {"limit": 1000}])
def test_a_queue_request_that_makes_no_sense_is_refused(
    client: TestClient, on: None, admin: dict[str, str], params: dict[str, Any]
) -> None:
    assert client.get(QUEUE, params=params, headers=admin).status_code == 422


# --- photos --------------------------------------------------------------------------------------------------


def put_photo(file_root: Path, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    path = file_root / "catalog" / digest[:2] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return digest


def test_a_photo_is_served_by_this_host_for_any_cache_to_keep(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, file_root: Path
) -> None:
    digest = put_photo(file_root, PNG)
    word = tag()
    seed(owner, name_uz=f"Olma {word}", image_key=digest)
    (item,) = search(client, world, q=word)["items"]
    assert item["image"] == f"/files/catalog/{digest}", "an address of ours, with nothing of where it came from"
    response = client.get(item["image"])  # whoever asks: a product's photo is nobody's data
    assert response.status_code == 200 and response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_what_is_not_a_photo_of_the_catalogue_is_not_served(
    client: TestClient, on: None, file_root: Path, world: World, owner: psycopg.Connection
) -> None:
    digest = put_photo(file_root, PNG)
    not_an_image = put_photo(file_root, b"%PDF-1.4 not a photo\n%%EOF")
    other = file_root / "ab" / ("ab" + "c" * 62)
    other.parent.mkdir(parents=True)
    other.write_bytes(JPEG)  # a shop's own file, kept elsewhere in the same store
    assert client.get(f"/files/catalog/{digest}").status_code == 200, "the control"
    for name in (
        "0" * 64,
        not_an_image,
        digest.upper(),
        digest[:-1],
        f"..%2F..%2Fab%2F{'ab' + 'c' * 62}",
        "ab" + "c" * 62,
    ):
        assert client.get(f"/files/catalog/{name}").status_code == 404, name


# --- the import ----------------------------------------------------------------------------------------------


def seed_directory(root: Path, word: str) -> tuple[Path, list[str]]:
    """A seed of four invented rows: two with photos (one with metadata to strip), one whose photo is
    not an image, one with no usable name."""
    keys = [uuid.uuid4().hex[:16] for _ in range(4)]
    rows = [
        {"id": keys[0], "name_ru": f"Чай зелёный {word}", "name_uz": f"Ko'k choy {word}", "amount": "100 г",
         "path": "Choy bo'limi > Ko'k choy", "shop": "x", "shops": ["x"], "price_hint": 12_990,
         "img_url": "https://example.invalid/a/400x400"},
        {"id": keys[1], "name_ru": f"Печенье {word}", "name_uz": "", "amount": "250 г", "path": "Noma'lum bo'lim",
         "shop": "x", "shops": ["x"], "price_hint": 0, "img_url": "https://example.invalid/b/400x400"},
        {"id": keys[2], "name_ru": f"Сок {word}", "name_uz": "", "amount": "1 л", "path": "Choy bo'limi",
         "shop": "x", "shops": ["x"], "price_hint": 15_000, "img_url": ""},
        {"id": keys[3], "name_ru": "", "name_uz": "", "amount": "", "path": "", "price_hint": 1, "img_url": ""},
    ]  # fmt: skip
    directory = root / "seed"
    (directory / "img").mkdir(parents=True)
    (directory / "source.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    (directory / "categories.json").write_text(
        json.dumps({"categories": {"tea": {"from": ["Choy bo'limi"]}, "other": {"from": ["*"]}}}), encoding="utf-8"
    )
    (directory / "img" / f"{keys[0]}.png").write_bytes(png(TEXT))
    (directory / "img" / f"{keys[1]}.jpg").write_bytes(jpeg(EXIF))
    (directory / "img" / f"{keys[2]}.png").write_bytes(b"<html>not found</html>")
    return directory, keys


def test_the_import_loads_rows_and_photos_and_running_it_again_adds_nothing(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    on: None,
    admin_database_url: str,
    file_root: Path,
    tmp_path: Path,
) -> None:
    word = tag()
    directory, keys = seed_directory(tmp_path, word)
    settings = Settings(admin_database_url=admin_database_url, file_store="filesystem", file_root=str(file_root))

    first = asyncio.run(run(directory, settings))
    assert (first.rows, first.added, first.updated, first.skipped) == (4, 3, 0, 1)
    assert (first.images_stored, first.images_kept, first.images_missing, first.images_refused) == (2, 0, 0, 1)
    assert "items added: 3" in report(first, with_store=True) and word not in report(first, with_store=True)

    stored = owner.execute(
        "SELECT source_key, name_ru, name_uz, category, subcategory, amount, price_hint, image_key IS NOT NULL "
        "FROM shared_item WHERE source_key = ANY(%s) ORDER BY array_position(%s, source_key)",
        (keys, keys),
    ).fetchall()
    assert stored == [
        (keys[0], f"Чай зелёный {word}", f"Ko'k choy {word}", "tea", "Ko'k choy", "100 г", 12_990, True),
        (keys[1], f"Печенье {word}", None, "other", None, "250 г", None, True),
        (keys[2], f"Сок {word}", None, "tea", None, "1 л", 15_000, False),
    ]
    everything = json.dumps(
        [list(map(str, row)) for row in owner.execute("SELECT * FROM shared_item WHERE source_key = ANY(%s)", (keys,))]
    )
    assert "example.invalid" not in everything and "http" not in everything, "no address of the source is kept"

    # The photo is kept as it is served: without the metadata it came with, under the hash of that.
    items = {item["name_ru"]: item for item in search(client, world, q=word)["items"]}
    photo = client.get(items[f"Чай зелёный {word}"]["image"])
    assert photo.status_code == 200 and photo.content == PNG and b"Ali Valiyev" not in photo.content
    assert client.get(items[f"Печенье {word}"]["image"]).content == JPEG
    assert items[f"Сок {word}"]["image"] is None

    # Again, with one name corrected and one photo gone from the directory: nothing is added, the
    # name is brought up to date, and the photo an item already has is kept.
    rows = json.loads((directory / "source.json").read_text(encoding="utf-8"))
    rows[1]["name_uz"] = f"Pechenye {word}"
    (directory / "source.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    (directory / "img" / f"{keys[0]}.png").unlink()
    files_before = sorted(path.name for path in file_root.rglob("*") if path.is_file())
    second = asyncio.run(run(directory, settings))
    assert (second.rows, second.added, second.updated, second.skipped) == (4, 0, 3, 1)
    assert (second.images_stored, second.images_kept, second.images_missing, second.images_refused) == (0, 1, 1, 1)
    assert sorted(path.name for path in file_root.rglob("*") if path.is_file()) == files_before
    assert owner.execute(
        "SELECT count(*), count(image_key), max(name_uz) FILTER (WHERE source_key = %s) FROM shared_item "
        "WHERE source_key = ANY(%s)",
        (keys[1], keys),
    ).fetchone() == (3, 2, f"Pechenye {word}")


def test_the_import_refuses_a_directory_or_a_configuration_it_cannot_use(
    tmp_path: Path, admin_database_url: str, file_root: Path
) -> None:
    directory, _ = seed_directory(tmp_path, tag())
    settings = Settings(admin_database_url=admin_database_url, file_store="filesystem", file_root=str(file_root))
    with pytest.raises(ValueError, match="QD_ADMIN_DATABASE_URL"):
        asyncio.run(run(directory, Settings(admin_database_url="")))
    (directory / "categories.json").write_text(
        json.dumps({"categories": {"weapons": {"from": ["Q"]}}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="does not have"):
        asyncio.run(run(directory, settings))
    (directory / "source.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match=r"source.json"):
        asyncio.run(run(directory, settings))


def test_the_ordinary_role_cannot_run_the_import(tmp_path: Path, app_database_url: str, file_root: Path) -> None:
    """The counterpart of "only the administrators' role may write the catalogue": given the ordinary
    application's connection, the same command is refused by the database."""
    directory, _ = seed_directory(tmp_path, tag())
    settings = Settings(admin_database_url=app_database_url, file_store="filesystem", file_root=str(file_root))
    with pytest.raises(Exception, match="permission denied"):
        asyncio.run(run(directory, settings))
