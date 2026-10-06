"""The shop's catalog, learned items and their review (story S6.1; REQ-039, REQ-040, REQ-041)."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import errors

from qarz.application.catalog import register_learned
from qarz.application.errors import ValidationFailed
from qarz.application.ports import Membership, TenantSession
from qarz.domain.access import Role
from qarz.domain.catalog import MAX_PRICE
from qarz.infrastructure.db import Database
from qarz.interface.errors import _MESSAGES, _STATUS

from .conftest import World, as_user

pytestmark = pytest.mark.db

ITEM_COLUMNS = "id, name, name_norm, unit, price, learned, status, merged_into"


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"test-{uuid.uuid4().hex}"}


def catalog(world: World, shop_id: uuid.UUID | None = None) -> str:
    return f"/api/v1/shops/{shop_id or world.shop_a}/catalog"


def write(client: TestClient, user: uuid.UUID, method: str, path: str, body: Any = None) -> Any:
    return client.request(method, path, json=body, headers={**as_user(user), **key()})


def read(client: TestClient, user: uuid.UUID, path: str, **params: Any) -> Any:
    return client.get(path, params=params, headers=as_user(user))


def add_item(client: TestClient, world: World, name: str, price: int = 5000, unit: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "price": price}
    if unit is not None:
        body["unit"] = unit
    response = write(client, world.manager_a, "POST", catalog(world), body)
    assert response.status_code == 201, response.text
    return dict(response.json())


def act(client: TestClient, world: World, item: Any, action: str, body: Any = None) -> Any:
    return write(client, world.manager_a, "POST", f"{catalog(world)}/{item}/{action}", body)


def names(client: TestClient, world: World, **params: Any) -> list[str]:
    response = read(client, world.seller_a, catalog(world), **params)
    assert response.status_code == 200, response.text
    return [item["name"] for item in response.json()["items"]]


def rows(owner: psycopg.Connection, shop_id: uuid.UUID) -> list[Any]:
    return owner.execute(
        f"SELECT {ITEM_COLUMNS} FROM catalog_item WHERE shop_id = %s ORDER BY id", (shop_id,)
    ).fetchall()


def row(owner: psycopg.Connection, item: Any) -> Any:
    return owner.execute(f"SELECT {ITEM_COLUMNS} FROM catalog_item WHERE id = %s", (item,)).fetchone()


def activity(owner: psycopg.Connection, shop_id: uuid.UUID) -> list[Any]:
    return owner.execute(
        "SELECT action, subject_type, subject_id, actor_id FROM activity WHERE shop_id = %s ORDER BY at, id", (shop_id,)
    ).fetchall()


def stored(owner: psycopg.Connection, world: World) -> tuple[Any, ...]:
    """Everything a refused catalog call could have changed in shop A."""
    keys = owner.execute("SELECT count(*) FROM request_key WHERE shop_id = %s", (world.shop_a,)).fetchone()
    return rows(owner, world.shop_a), activity(owner, world.shop_a), keys


def seed_item(
    owner: psycopg.Connection,
    shop_id: uuid.UUID,
    name: str,
    *,
    price: int = 5000,
    learned: bool = False,
    status: str = "active",
) -> uuid.UUID:
    item_id = uuid.uuid4()
    owner.execute(
        "INSERT INTO catalog_item (id, shop_id, name, name_norm, unit, price, learned, status) "
        "VALUES (%s, %s, %s, %s, 'dona', %s, %s, %s)",
        (item_id, shop_id, name, name.lower(), price, learned, status),
    )
    return item_id


def refused(response: Any, status: int, code: str) -> dict[str, str]:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    return dict(error["fields"])


# --- adding an item (REQ-039) -------------------------------------------------------------------------


def test_a_manager_adds_an_item_with_a_name_a_unit_and_a_price(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    response = write(
        client, world.manager_a, "POST", catalog(world), {"name": "  Shakar   oq ", "unit": "КГ", "price": 14000}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body == {
        "id": body["id"],
        "name": "Shakar oq",
        "unit": "kg",
        "price": 14000,
        "learned": False,
        "status": "active",
        "merged_into": None,
    }
    item_id = uuid.UUID(body["id"])
    assert row(owner, item_id) == (item_id, "Shakar oq", "shakar oq", "kg", 14000, False, "active", None)
    assert activity(owner, world.shop_a) == [
        ("catalog.item.created", "catalog_item", item_id, world.manager_a_membership)
    ]


def test_an_item_without_a_unit_is_sold_by_the_piece(client: TestClient, world: World) -> None:
    assert add_item(client, world, "Gugurt", 1000)["unit"] == "dona"
    assert add_item(client, world, "Tuz", 3000, unit="  ")["unit"] == "dona"
    assert add_item(client, world, "Sigaret", 30000, unit="Blok")["unit"] == "blok"


def test_the_price_bounds_are_inclusive(client: TestClient, world: World) -> None:
    assert add_item(client, world, "Eng arzon", 1)["price"] == 1
    assert add_item(client, world, "Eng qimmat", MAX_PRICE)["price"] == MAX_PRICE


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"name": "", "price": 5000}, "name"),
        ({"name": "   ", "price": 5000}, "name"),
        ({"name": "x" * 81, "price": 5000}, "name"),
        ({"name": "ь", "price": 5000}, "name"),
        ({"name": 5, "price": 5000}, "name"),
        ({"price": 5000}, "name"),
        ({"name": "Yog'", "price": 0}, "price"),
        ({"name": "Yog'", "price": -5000}, "price"),
        ({"name": "Yog'", "price": MAX_PRICE + 1}, "price"),
        ({"name": "Yog'", "price": "5000"}, "price"),
        ({"name": "Yog'", "price": 5000.0}, "price"),
        ({"name": "Yog'", "price": 49.5}, "price"),
        ({"name": "Yog'", "price": True}, "price"),
        ({"name": "Yog'", "price": None}, "price"),
        ({"name": "Yog'"}, "price"),
        ({"name": "Yog'", "price": 5000, "unit": "kg/m"}, "unit"),
        ({"name": "Yog'", "price": 5000, "unit": "x" * 13}, "unit"),
        ({"name": "Yog'", "price": 5000, "unit": "12"}, "unit"),
        ({"name": "Yog'", "price": 5000, "unit": 5}, "unit"),
        ({"name": "Yog'", "price": 5000, "learned": True}, "learned"),
        ({"name": "Yog'", "price": 5000, "status": "hidden"}, "status"),
        ({"name": "Yog'", "price": 5000, "stock": 10}, "stock"),
    ],
)
def test_an_invalid_item_is_refused_and_not_stored(
    client: TestClient, world: World, owner: psycopg.Connection, body: dict[str, Any], field: str
) -> None:
    before = stored(owner, world)
    fields = refused(write(client, world.manager_a, "POST", catalog(world), body), 422, "VALIDATION")
    assert list(fields) == [field]
    assert stored(owner, world) == before


def test_every_invalid_part_of_an_item_is_reported_at_once(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = stored(owner, world)
    response = write(client, world.manager_a, "POST", catalog(world), {"name": " ", "unit": "a/b", "price": 0})
    assert sorted(refused(response, 422, "VALIDATION")) == ["name", "price", "unit"]
    assert stored(owner, world) == before


@pytest.mark.parametrize("taken", ["Non", "NON", "  non ", "Нон"])
def test_a_name_the_catalog_already_has_in_any_spelling_is_refused(
    client: TestClient, world: World, owner: psycopg.Connection, taken: str
) -> None:
    before = stored(owner, world)
    response = write(client, world.manager_a, "POST", catalog(world), {"name": taken, "price": 4500})
    assert refused(response, 409, "CATALOG_NAME_TAKEN") == {
        "existing_id": str(world.catalog_item_a),
        "existing_status": "active",
    }
    assert stored(owner, world) == before


def test_a_hidden_item_still_holds_its_name(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    hidden = seed_item(owner, world.shop_a, "Kefir", status="hidden")
    response = write(client, world.manager_a, "POST", catalog(world), {"name": "кефир", "price": 9000})
    assert refused(response, 409, "CATALOG_NAME_TAKEN") == {"existing_id": str(hidden), "existing_status": "hidden"}


def test_two_shops_may_have_items_with_the_same_name(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    theirs = seed_item(owner, world.shop_b, "Shakar", price=1)
    mine = add_item(client, world, "Shakar", 14000)
    assert mine["id"] != str(theirs)
    assert row(owner, theirs)[4] == 1


def test_a_repeated_create_request_makes_one_item(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    headers = {**as_user(world.manager_a), **key()}
    before = len(rows(owner, world.shop_a))
    first = client.post(catalog(world), json={"name": "Bir marta", "price": 5000}, headers=headers)
    second = client.post(catalog(world), json={"name": "Bir marta", "price": 5000}, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert len(rows(owner, world.shop_a)) == before + 1
    assert len(activity(owner, world.shop_a)) == 1

    # The same key with any part of the item changed is a different request.
    for changed in (
        {"name": "Boshqa", "price": 5000},
        {"name": "Bir marta", "price": 5001},
        {"name": "Bir marta", "price": 5000, "unit": "kg"},
    ):
        refused(client.post(catalog(world), json=changed, headers=headers), 409, "IDEMPOTENCY_KEY_REUSED")
    assert len(rows(owner, world.shop_a)) == before + 1
    assert len(activity(owner, world.shop_a)) == 1


# --- changing an item (REQ-039, REQ-041) ----------------------------------------------------------------


def test_each_part_of_an_item_can_be_changed_on_its_own(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    item = world.catalog_item_a
    path = f"{catalog(world)}/{item}"
    priced = write(client, world.manager_a, "PATCH", path, {"price": 4500})
    assert priced.status_code == 200, priced.text
    assert priced.json() == {
        "id": str(item),
        "name": "Non",
        "unit": "dona",
        "price": 4500,
        "learned": False,
        "status": "active",
        "merged_into": None,
    }
    assert row(owner, item) == (item, "Non", "non", "dona", 4500, False, "active", None)

    assert write(client, world.manager_a, "PATCH", path, {"unit": "ШТ."}).json()["unit"] == "dona"
    assert write(client, world.manager_a, "PATCH", path, {"unit": "buxanka"}).json()["unit"] == "buxanka"
    assert row(owner, item) == (item, "Non", "non", "buxanka", 4500, False, "active", None)

    renamed = write(client, world.owner_a, "PATCH", path, {"name": " Буханка  нон "})
    assert renamed.json()["name"] == "Буханка нон"
    assert row(owner, item) == (item, "Буханка нон", "buhanka non", "buxanka", 4500, False, "active", None)

    everything = write(client, world.manager_a, "PATCH", path, {"name": "Non", "unit": "dona", "price": 4000})
    assert everything.status_code == 200
    assert row(owner, item) == (item, "Non", "non", "dona", 4000, False, "active", None)
    actors = [world.manager_a_membership] * 3 + [world.owner_a_membership, world.manager_a_membership]
    assert activity(owner, world.shop_a) == [("catalog.item.updated", "catalog_item", item, actor) for actor in actors]


def test_an_item_may_be_respelled_without_colliding_with_itself(client: TestClient, world: World) -> None:
    response = write(client, world.manager_a, "PATCH", f"{catalog(world)}/{world.catalog_item_a}", {"name": "НОН"})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "НОН"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "_"),
        ({"name": None, "unit": None, "price": None}, "_"),
        ({"name": ""}, "name"),
        ({"name": "x" * 81}, "name"),
        ({"name": "ь"}, "name"),
        ({"price": 0}, "price"),
        ({"price": -1}, "price"),
        ({"price": MAX_PRICE + 1}, "price"),
        ({"price": "4500"}, "price"),
        ({"price": 4500.0}, "price"),
        ({"price": False}, "price"),
        ({"unit": "kg/m"}, "unit"),
        ({"unit": "x" * 13}, "unit"),
        ({"price": 4500, "learned": True}, "learned"),
        ({"price": 4500, "status": "hidden"}, "status"),
        ({"price": 4500, "merged_into": None}, "merged_into"),
    ],
)
def test_an_invalid_change_is_refused_and_nothing_changes(
    client: TestClient, world: World, owner: psycopg.Connection, body: dict[str, Any], field: str
) -> None:
    before = stored(owner, world)
    response = write(client, world.manager_a, "PATCH", f"{catalog(world)}/{world.catalog_item_a}", body)
    assert list(refused(response, 422, "VALIDATION")) == [field]
    assert stored(owner, world) == before


def test_an_item_cannot_take_the_name_of_another(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    before = stored(owner, world)
    response = write(
        client, world.manager_a, "PATCH", f"{catalog(world)}/{world.catalog_item_a}", {"name": "ҚАТИҚ", "price": 1}
    )
    assert refused(response, 409, "CATALOG_NAME_TAKEN") == {
        "existing_id": str(world.learned_item_a),
        "existing_status": "active",
    }
    assert stored(owner, world) == before


def test_a_repeated_change_is_applied_once(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    headers = {**as_user(world.manager_a), **key()}
    path = f"{catalog(world)}/{world.catalog_item_a}"
    first = client.patch(path, json={"price": 4500}, headers=headers)
    second = client.patch(path, json={"price": 4500}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(activity(owner, world.shop_a)) == 1
    # The same key with another value, another part or another item is a different request.
    before = stored(owner, world)
    for changed in ({"price": 4600}, {"price": 4500, "name": "Patir"}, {"price": 4500, "unit": "kg"}):
        refused(client.patch(path, json=changed, headers=headers), 409, "IDEMPOTENCY_KEY_REUSED")
    elsewhere = client.patch(f"{catalog(world)}/{world.learned_item_a}", json={"price": 4500}, headers=headers)
    refused(elsewhere, 409, "IDEMPOTENCY_KEY_REUSED")
    assert stored(owner, world) == before
    assert row(owner, world.catalog_item_a)[4] == 4500


def test_nothing_done_to_the_catalog_alters_a_saved_goods_line(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """REQ-041, INV-17: a line keeps the name and the price it was sold at."""
    owner.execute(
        "INSERT INTO goods_line (id, shop_id, entry_id, line_no, catalog_item_id, name, qty, unit, unit_price, "
        "line_total) VALUES (%s, %s, %s, 1, %s, 'Qatiq', 2, 'dona', 9000, 18000), "
        "(%s, %s, %s, 2, %s, 'Non', 8, 'dona', 4000, 32000)",
        (
            uuid.uuid4(),
            world.shop_a,
            world.entry_a,
            world.learned_item_a,
            uuid.uuid4(),
            world.shop_a,
            world.entry_a,
            world.catalog_item_a,
        ),
    )

    def lines() -> list[Any]:
        return owner.execute(
            "SELECT line_no, catalog_item_id, name, qty, unit, unit_price, line_total FROM goods_line "
            "WHERE entry_id = %s ORDER BY line_no",
            (world.entry_a,),
        ).fetchall()

    def entry() -> Any:
        return owner.execute("SELECT amount FROM ledger_entry WHERE id = %s", (world.entry_a,)).fetchone()

    before = lines()
    changes = [
        ("PATCH", f"{catalog(world)}/{world.catalog_item_a}", {"name": "Patir", "unit": "kg", "price": 7000}),
        ("POST", f"{catalog(world)}/{world.catalog_item_a}/hide", None),
        ("POST", f"{catalog(world)}/{world.catalog_item_a}/unhide", None),
        ("PATCH", f"{catalog(world)}/{world.learned_item_a}", {"price": 12000}),
        ("POST", f"{catalog(world)}/{world.learned_item_a}/merge", {"into": str(world.catalog_item_a)}),
    ]
    for method, path, body in changes:
        assert write(client, world.manager_a, method, path, body).status_code == 200, path
        assert lines() == before, path
        assert entry() == (50000,)
    assert row(owner, world.catalog_item_a)[4] == 7000


# --- hiding and showing ---------------------------------------------------------------------------------


def test_a_hidden_item_leaves_the_list_and_can_be_shown_again(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    item = world.catalog_item_a
    hidden = act(client, world, item, "hide")
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["status"] == "hidden"
    assert row(owner, item) == (item, "Non", "non", "dona", 4000, False, "hidden", None)
    assert names(client, world) == ["Qatiq"]
    assert names(client, world, status="hidden") == ["Non"]

    shown = act(client, world, item, "unhide")
    assert shown.json()["status"] == "active"
    assert row(owner, item) == (item, "Non", "non", "dona", 4000, False, "active", None)
    assert names(client, world) == ["Non", "Qatiq"]
    assert names(client, world, status="hidden") == []
    assert [entry[0] for entry in activity(owner, world.shop_a)] == ["catalog.item.hidden", "catalog.item.unhidden"]


def test_hiding_a_learned_item_does_not_review_it(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    assert act(client, world, world.learned_item_a, "hide").json() == {
        "id": str(world.learned_item_a),
        "name": "Qatiq",
        "unit": "dona",
        "price": 9000,
        "learned": True,
        "status": "hidden",
        "merged_into": None,
    }
    assert act(client, world, world.learned_item_a, "unhide").json()["learned"] is True


def test_a_repeated_hide_is_applied_once(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    headers = {**as_user(world.manager_a), **key()}
    path = f"{catalog(world)}/{world.catalog_item_a}/hide"
    first, second = client.post(path, headers=headers), client.post(path, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(activity(owner, world.shop_a)) == 1
    # The same key cannot hide another item.
    other = client.post(f"{catalog(world)}/{world.learned_item_a}/hide", headers=headers)
    refused(other, 409, "IDEMPOTENCY_KEY_REUSED")
    assert row(owner, world.learned_item_a)[6] == "active"


# --- items that do not exist for the caller -------------------------------------------------------------

ITEM_WRITES = [
    ("PATCH", "", {"price": 4500}),
    ("POST", "/hide", None),
    ("POST", "/unhide", None),
    ("POST", "/accept", None),
    ("POST", "/dismiss", None),
    ("POST", "/merge", "target"),
]


@pytest.mark.parametrize(("method", "suffix", "body"), ITEM_WRITES)
def test_an_unknown_or_foreign_item_is_not_found(
    client: TestClient, world: World, owner: psycopg.Connection, method: str, suffix: str, body: Any
) -> None:
    theirs = seed_item(owner, world.shop_b, "Begona", learned=True)
    payload = {"into": str(world.catalog_item_a)} if body == "target" else body
    before = (stored(owner, world), rows(owner, world.shop_b))
    for item in (uuid.uuid4(), theirs, "not-a-uuid"):
        response = write(client, world.owner_a, method, f"{catalog(world)}/{item}{suffix}", payload)
        assert refused(response, 404, "NOT_FOUND") == {}
    assert (stored(owner, world), rows(owner, world.shop_b)) == before


# --- listing and search ---------------------------------------------------------------------------------


def test_search_ignores_case_and_script_and_matches_part_of_a_name(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    add_item(client, world, "Shakar oq", 14000, unit="kg")
    add_item(client, world, "Яхна чой", 8000)
    add_item(client, world, "100% sharbat", 16000)
    add_item(client, world, "Sut_1l", 11000)
    seed_item(owner, world.shop_b, "Shakar boshqa")  # another shop's item with a matching name

    assert names(client, world, q="шакар") == ["Shakar oq"]
    assert names(client, world, q="SHAK") == ["Shakar oq"]
    assert names(client, world, q="  kar o ") == ["Shakar oq"]
    assert names(client, world, q="yahna") == ["Яхна чой"]
    assert names(client, world, q="choy") == ["Яхна чой"]
    assert names(client, world, q="zzz") == []
    # Search text is matched literally: wildcard characters are not wildcards.
    assert names(client, world, q="%") == ["100% sharbat"]
    assert names(client, world, q="_") == ["Sut_1l"]
    assert names(client, world, q="s%r") == []
    assert names(client, world, q="sut 1l") == []
    assert names(client, world, q="\\") == []
    # No search text: every shown item of this shop and none of another shop's, in name order.
    assert names(client, world) == ["100% sharbat", "Non", "Qatiq", "Shakar oq", "Sut_1l", "Яхна чой"]
    assert names(client, world, q="   ") == ["100% sharbat", "Non", "Qatiq", "Shakar oq", "Sut_1l", "Яхна чой"]


def test_the_list_can_be_narrowed_to_learned_or_reviewed_items(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    seed_item(owner, world.shop_a, "Yashirin", learned=True, status="hidden")
    seed_item(owner, world.shop_a, "Eski", status="hidden")
    assert names(client, world) == ["Non", "Qatiq"]
    assert names(client, world, learned="true") == ["Qatiq"]
    assert names(client, world, learned="false") == ["Non"]
    assert names(client, world, status="hidden") == ["Eski", "Yashirin"]
    assert names(client, world, status="hidden", learned="true") == ["Yashirin"]
    assert names(client, world, status="hidden", learned="false") == ["Eski"]
    assert names(client, world, learned="true", q="non") == []

    listed = read(client, world.seller_a, catalog(world), learned="true").json()
    assert listed == {
        "items": [
            {
                "id": str(world.learned_item_a),
                "name": "Qatiq",
                "unit": "dona",
                "price": 9000,
                "learned": True,
                "status": "active",
                "merged_into": None,
            }
        ],
        "next_cursor": None,
    }


def test_the_catalog_is_paged_by_cursor(client: TestClient, world: World) -> None:
    created = [f"Sahifa {letter}" for letter in "ABCDE"]
    for name in reversed(created):
        add_item(client, world, name)

    seen: list[str] = []
    cursor: str | None = None
    for expected_size in (2, 2, 1):
        params: dict[str, Any] = {"q": "sahifa", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = read(client, world.seller_a, catalog(world), **params).json()
        assert len(page["items"]) == expected_size
        seen += [item["name"] for item in page["items"]]
        cursor = page["next_cursor"]
    assert cursor is None
    assert seen == created

    assert len(read(client, world.seller_a, catalog(world), limit=1).json()["items"]) == 1
    everything = read(client, world.seller_a, catalog(world), limit=100).json()
    assert len(everything["items"]) == 7
    assert everything["next_cursor"] is None
    exact = read(client, world.seller_a, catalog(world), limit=7).json()
    assert (len(exact["items"]), exact["next_cursor"]) == (7, None)


@pytest.mark.parametrize(
    "params",
    [
        {"cursor": "abc"},
        {"cursor": "WyJhIl0"},
        {"cursor": "WyJub24iLCAibm90LWEtdXVpZCJd"},  # ["non", "not-a-uuid"]
        {"limit": 0},
        {"limit": 101},
        {"limit": "ten"},
        {"status": "all"},
        {"status": "archived"},
        {"learned": "maybe"},
        {"q": "x" * 81},
    ],
)
def test_bad_list_parameters_are_refused(client: TestClient, world: World, params: dict[str, Any]) -> None:
    response = read(client, world.seller_a, catalog(world), **params)
    assert list(refused(response, 422, "VALIDATION")) == [next(iter(params))]


# --- subscription state (BR-29, BR-30) ------------------------------------------------------------------


def _subscription(owner: psycopg.Connection, world: World, sql: str) -> None:
    owner.execute(f"UPDATE subscription SET {sql} WHERE shop_id = %s", (world.shop_a,))


def _all_writes(world: World) -> list[tuple[str, str, Any]]:
    base = catalog(world)
    return [
        ("POST", base, {"name": "Yangi", "price": 5000}),
        ("PATCH", f"{base}/{world.catalog_item_a}", {"price": 4500}),
        ("POST", f"{base}/{world.catalog_item_a}/hide", None),
        ("POST", f"{base}/{world.catalog_item_a}/unhide", None),
        ("POST", f"{base}/{world.learned_item_a}/accept", None),
        ("POST", f"{base}/{world.learned_item_a}/dismiss", None),
        ("POST", f"{base}/{world.learned_item_a}/merge", {"into": str(world.catalog_item_a)}),
    ]


@pytest.mark.parametrize("state_sql", ["state = 'trial', trial_ends = current_date - 1", "state = 'limited'"])
def test_in_limited_mode_the_catalog_still_works(
    client: TestClient, world: World, owner: psycopg.Connection, state_sql: str
) -> None:
    """BR-29 refuses only new credit sales; keeping the catalog is not one."""
    _subscription(owner, world, state_sql)
    second, third = (seed_item(owner, world.shop_a, name, learned=True) for name in ("Ikkinchi", "Uchinchi"))
    for position, (method, path, body) in enumerate(_all_writes(world)):
        if path.endswith("/dismiss"):
            path = path.replace(str(world.learned_item_a), str(second))
        if path.endswith("/merge"):
            path = path.replace(str(world.learned_item_a), str(third))
        response = write(client, world.manager_a, method, path, body)
        assert response.status_code == (201 if position == 0 else 200), response.text
    assert names(client, world) == ["Non", "Qatiq", "Yangi"]
    assert names(client, world, status="hidden") == ["Ikkinchi", "Uchinchi"]


def test_a_suspended_shop_accepts_no_catalog_writes_and_only_the_owner_may_look(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    _subscription(owner, world, "state = 'suspended'")
    before = stored(owner, world)
    for method, path, body in _all_writes(world):
        for user in (world.owner_a, world.manager_a):
            assert refused(write(client, user, method, path, body), 403, "SHOP_SUSPENDED") == {}, path
    assert stored(owner, world) == before

    assert read(client, world.owner_a, catalog(world)).status_code == 200
    for staff in (world.manager_a, world.seller_a):
        refused(read(client, staff, catalog(world)), 403, "SHOP_SUSPENDED")


# --- review of learned items (REQ-040, domain lifecycle of the catalog item) ------------------------------


def test_accepting_a_learned_item_keeps_it_and_clears_the_flag(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    item = world.learned_item_a
    response = act(client, world, item, "accept")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "id": str(item),
        "name": "Qatiq",
        "unit": "dona",
        "price": 9000,
        "learned": False,
        "status": "active",
        "merged_into": None,
    }
    assert row(owner, item) == (item, "Qatiq", "qatiq", "dona", 9000, False, "active", None)
    assert activity(owner, world.shop_a) == [
        ("catalog.learned.accepted", "catalog_item", item, world.manager_a_membership)
    ]
    assert names(client, world, learned="true") == []
    assert names(client, world) == ["Non", "Qatiq"]


def test_accepting_a_hidden_learned_item_leaves_it_hidden(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    item = seed_item(owner, world.shop_a, "Yashirin", learned=True, status="hidden")
    assert act(client, world, item, "accept").status_code == 200
    assert row(owner, item)[5:] == (False, "hidden", None)


def test_dismissing_a_learned_item_hides_it_and_clears_the_flag(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    item = world.learned_item_a
    response = act(client, world, item, "dismiss")
    assert response.status_code == 200, response.text
    assert (response.json()["learned"], response.json()["status"], response.json()["merged_into"]) == (
        False,
        "hidden",
        None,
    )
    assert row(owner, item) == (item, "Qatiq", "qatiq", "dona", 9000, False, "hidden", None)
    assert activity(owner, world.shop_a) == [
        ("catalog.learned.dismissed", "catalog_item", item, world.manager_a_membership)
    ]
    assert names(client, world) == ["Non"]
    assert names(client, world, status="hidden") == ["Qatiq"]


def test_merging_a_learned_item_makes_it_a_hidden_alias_of_the_target(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    item, target = world.learned_item_a, world.catalog_item_a
    response = act(client, world, item, "merge", {"into": str(target)})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "id": str(item),
        "name": "Qatiq",
        "unit": "dona",
        "price": 9000,
        "learned": False,
        "status": "hidden",
        "merged_into": str(target),
    }
    assert row(owner, item) == (item, "Qatiq", "qatiq", "dona", 9000, False, "hidden", target)
    assert row(owner, target) == (target, "Non", "non", "dona", 4000, False, "active", None)
    assert activity(owner, world.shop_a) == [
        ("catalog.learned.merged", "catalog_item", item, world.manager_a_membership)
    ]
    assert names(client, world) == ["Non"]
    assert names(client, world, status="hidden") == ["Qatiq"]


def test_showing_a_merged_item_again_undoes_the_merge(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    item = world.learned_item_a
    act(client, world, item, "merge", {"into": str(world.catalog_item_a)})
    assert act(client, world, item, "hide").json()["merged_into"] == str(world.catalog_item_a)
    shown = act(client, world, item, "unhide")
    assert shown.status_code == 200, shown.text
    assert row(owner, item) == (item, "Qatiq", "qatiq", "dona", 9000, False, "active", None)


@pytest.mark.parametrize("action", ["accept", "dismiss", "merge"])
def test_only_an_item_still_flagged_as_learned_can_be_reviewed(
    client: TestClient, world: World, owner: psycopg.Connection, action: str
) -> None:
    other = seed_item(owner, world.shop_a, "Boshqa")
    before = stored(owner, world)
    body = {"into": str(other)} if action == "merge" else None
    assert refused(act(client, world, world.catalog_item_a, action, body), 409, "CATALOG_ITEM_NOT_LEARNED") == {}
    assert stored(owner, world) == before


@pytest.mark.parametrize("first", ["accept", "dismiss", "merge"])
@pytest.mark.parametrize("second", ["accept", "dismiss", "merge"])
def test_a_learned_item_is_reviewed_once(
    client: TestClient, world: World, owner: psycopg.Connection, first: str, second: str
) -> None:
    body = {"into": str(world.catalog_item_a)}
    assert act(client, world, world.learned_item_a, first, body if first == "merge" else None).status_code == 200
    before = stored(owner, world)
    again = act(client, world, world.learned_item_a, second, body if second == "merge" else None)
    refused(again, 409, "CATALOG_ITEM_NOT_LEARNED")
    assert stored(owner, world) == before


def test_a_repeated_review_request_is_applied_once(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    headers = {**as_user(world.manager_a), **key()}
    path = f"{catalog(world)}/{world.learned_item_a}/merge"
    body = {"into": str(world.catalog_item_a)}
    first = client.post(path, json=body, headers=headers)
    second = client.post(path, json=body, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(activity(owner, world.shop_a)) == 1

    # The same key with another target is a different request.
    other = seed_item(owner, world.shop_a, "Boshqa")
    refused(client.post(path, json={"into": str(other)}, headers=headers), 409, "IDEMPOTENCY_KEY_REUSED")
    assert row(owner, world.learned_item_a)[7] == world.catalog_item_a


def test_an_item_cannot_be_merged_into_itself(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    before = stored(owner, world)
    response = act(client, world, world.learned_item_a, "merge", {"into": str(world.learned_item_a)})
    assert list(refused(response, 422, "VALIDATION")) == ["into"]
    assert stored(owner, world) == before


@pytest.mark.parametrize("body", [None, {}, {"into": None}, {"into": "non"}, {"into": 5}, {"target": "x"}])
def test_a_merge_needs_a_target(client: TestClient, world: World, owner: psycopg.Connection, body: Any) -> None:
    before = stored(owner, world)
    refused(act(client, world, world.learned_item_a, "merge", body), 422, "VALIDATION")
    assert stored(owner, world) == before


def test_a_merge_body_accepts_nothing_but_the_target(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = stored(owner, world)
    body = {"into": str(world.catalog_item_a), "price": 1}
    assert list(refused(act(client, world, world.learned_item_a, "merge", body), 422, "VALIDATION")) == ["price"]
    assert stored(owner, world) == before


def test_a_merge_target_must_exist_in_the_same_shop(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    theirs = seed_item(owner, world.shop_b, "Begona")
    before = (stored(owner, world), rows(owner, world.shop_b))
    for target in (uuid.uuid4(), theirs):
        response = act(client, world, world.learned_item_a, "merge", {"into": str(target)})
        assert refused(response, 404, "NOT_FOUND") == {}
    assert (stored(owner, world), rows(owner, world.shop_b)) == before


@pytest.mark.parametrize("kind", ["hidden", "learned", "alias"])
def test_a_merge_target_must_be_a_shown_reviewed_item(
    client: TestClient, world: World, owner: psycopg.Connection, kind: str
) -> None:
    if kind == "hidden":
        target = seed_item(owner, world.shop_a, "Yashirin", status="hidden")
    elif kind == "learned":
        target = seed_item(owner, world.shop_a, "Yangi", learned=True)
    else:
        target = seed_item(owner, world.shop_a, "Taxallus", status="hidden")
        owner.execute("UPDATE catalog_item SET merged_into = %s WHERE id = %s", (world.catalog_item_a, target))
    before = stored(owner, world)
    response = act(client, world, world.learned_item_a, "merge", {"into": str(target)})
    assert refused(response, 409, "CATALOG_MERGE_TARGET_INVALID") == {}
    assert stored(owner, world) == before


# --- learning an item from a typed line (BR-6): the function the itemized sale calls ----------------------

SELLER = Role.SELLER


def in_shop(url: str, shop_id: uuid.UUID, work: Callable[[TenantSession], Awaitable[Any]], times: int = 1) -> Any:
    """Run `work` inside its own tenant transaction, as the application role, `times` times at once."""

    async def one(database: Database) -> Any:
        async with database.tenant(shop_id) as session:
            return await work(session)

    async def main() -> Any:
        database = Database(url, pool_size=times)
        try:
            results = await asyncio.gather(*(one(database) for _ in range(times)))
        finally:
            await database.dispose()
        return results[0] if times == 1 else results

    return asyncio.run(main())


def learn(
    url: str, world: World, name: str, price: Any = 7000, unit: str | None = None, shop_id: uuid.UUID | None = None
) -> uuid.UUID:
    actor = Membership(world.seller_a_membership, SELLER)

    async def work(session: TenantSession) -> uuid.UUID:
        return await register_learned(session, actor, name=name, unit=unit, price=price)

    result = in_shop(url, shop_id or world.shop_a, work)
    assert isinstance(result, uuid.UUID)
    return result


def test_a_typed_good_the_catalog_does_not_know_is_learned(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    item = learn(app_database_url, world, "  Tvorog   5% ", 23000, "КГ")
    assert row(owner, item) == (item, "Tvorog 5%", "tvorog 5%", "kg", 23000, True, "active", None)
    assert activity(owner, world.shop_a) == [("catalog.item.learned", "catalog_item", item, world.seller_a_membership)]
    # Shown to sellers at once, and waiting for a manager's review.
    assert names(client, world) == ["Non", "Qatiq", "Tvorog 5%"]
    assert names(client, world, learned="true") == ["Qatiq", "Tvorog 5%"]
    assert act(client, world, item, "accept").status_code == 200


def test_a_learned_good_without_a_unit_is_a_piece(
    world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    assert row(owner, learn(app_database_url, world, "Saqich"))[3] == "dona"


@pytest.mark.parametrize("typed", ["Non", "нон", " NON "])
def test_a_typed_good_the_catalog_knows_is_not_learned_again_and_keeps_its_price(
    world: World, owner: psycopg.Connection, app_database_url: str, typed: str
) -> None:
    before = stored(owner, world)
    assert learn(app_database_url, world, typed, 99000, "kg") == world.catalog_item_a
    assert stored(owner, world) == before


def test_a_typed_good_that_was_hidden_or_dismissed_stays_hidden(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    assert act(client, world, world.learned_item_a, "dismiss").status_code == 200
    before = rows(owner, world.shop_a)
    assert learn(app_database_url, world, "қатиқ") == world.learned_item_a
    assert rows(owner, world.shop_a) == before
    assert names(client, world) == ["Non"]


def test_a_typed_good_that_was_merged_refers_to_the_item_it_was_merged_into(
    client: TestClient, world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    assert act(client, world, world.learned_item_a, "merge", {"into": str(world.catalog_item_a)}).status_code == 200
    before = rows(owner, world.shop_a)
    assert learn(app_database_url, world, "QATIQ") == world.catalog_item_a
    assert rows(owner, world.shop_a) == before


@pytest.mark.parametrize(
    ("name", "unit", "price", "field"),
    [
        ("", None, 7000, "name"),
        ("x" * 81, None, 7000, "name"),
        ("ь", None, 7000, "name"),
        ("Saqich", None, 0, "price"),
        ("Saqich", None, MAX_PRICE + 1, "price"),
        ("Saqich", None, 7000.5, "price"),
        ("Saqich", None, True, "price"),
        ("Saqich", "kg/m", 7000, "unit"),
    ],
)
def test_an_unusable_typed_good_is_refused_and_not_learned(
    world: World, owner: psycopg.Connection, app_database_url: str, name: str, unit: str | None, price: Any, field: str
) -> None:
    before = stored(owner, world)
    with pytest.raises(ValidationFailed) as caught:
        learn(app_database_url, world, name, price, unit)
    assert list(caught.value.fields) == [field]
    assert stored(owner, world) == before


def test_learning_is_part_of_the_callers_transaction(
    world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    actor = Membership(world.seller_a_membership, SELLER)

    async def work(session: TenantSession) -> None:
        await register_learned(session, actor, name="Bekor bo'ladi", unit=None, price=7000)
        raise LookupError("the sale failed after the good was learned")

    before = stored(owner, world)
    with pytest.raises(LookupError):
        in_shop(app_database_url, world.shop_a, work)
    assert stored(owner, world) == before


def test_each_shop_learns_its_own_goods(world: World, owner: psycopg.Connection, app_database_url: str) -> None:
    before = rows(owner, world.shop_a)
    theirs = learn(app_database_url, world, "Non", 3500, shop_id=world.shop_b)
    assert theirs != world.catalog_item_a
    assert rows(owner, world.shop_b) == [(theirs, "Non", "non", "dona", 3500, True, "active", None)]
    assert rows(owner, world.shop_a) == before


def test_the_same_good_typed_in_two_sales_at_once_is_learned_once(
    world: World, owner: psycopg.Connection, app_database_url: str
) -> None:
    actor = Membership(world.seller_a_membership, SELLER)

    async def work(session: TenantSession) -> uuid.UUID:
        return await register_learned(session, actor, name="Bir vaqtda", unit=None, price=7000)

    results = in_shop(app_database_url, world.shop_a, work, times=6)
    assert len(set(results)) == 1
    found = owner.execute(
        "SELECT id FROM catalog_item WHERE shop_id = %s AND name_norm = 'bir vaqtda'", (world.shop_a,)
    ).fetchall()
    assert found == [(results[0],)]
    assert [entry[0] for entry in activity(owner, world.shop_a)] == ["catalog.item.learned"]


# --- the alias column (migration 0007) -------------------------------------------------------------------


def test_the_database_refuses_an_alias_that_is_shown_flagged_or_its_own_target(
    world: World, owner: psycopg.Connection
) -> None:
    item, target = world.learned_item_a, world.catalog_item_a
    for sql in (
        "UPDATE catalog_item SET merged_into = %(target)s, learned = false WHERE id = %(item)s",  # still shown
        "UPDATE catalog_item SET merged_into = %(target)s, status = 'hidden' WHERE id = %(item)s",  # still flagged
        "UPDATE catalog_item SET merged_into = %(item)s, status = 'hidden', learned = false WHERE id = %(item)s",
    ):
        with pytest.raises(errors.CheckViolation, match="catalog_item_alias_is_hidden"):
            owner.execute(sql, {"item": item, "target": target})
    owner.execute(
        "UPDATE catalog_item SET merged_into = %s, status = 'hidden', learned = false WHERE id = %s", (target, item)
    )


def test_the_database_refuses_an_alias_of_another_shops_item(world: World, owner: psycopg.Connection) -> None:
    theirs = seed_item(owner, world.shop_b, "Begona")
    with pytest.raises(errors.ForeignKeyViolation, match="catalog_item_merged_into_fkey"):
        owner.execute(
            "UPDATE catalog_item SET merged_into = %s, status = 'hidden', learned = false WHERE id = %s",
            (theirs, world.learned_item_a),
        )
    with pytest.raises(errors.ForeignKeyViolation, match="catalog_item_merged_into_fkey"):
        owner.execute(
            "UPDATE catalog_item SET merged_into = %s, status = 'hidden', learned = false WHERE id = %s",
            (uuid.uuid4(), world.learned_item_a),
        )


# --- error texts ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["CATALOG_NAME_TAKEN", "CATALOG_ITEM_NOT_LEARNED", "CATALOG_MERGE_TARGET_INVALID"])
def test_every_catalog_error_has_a_status_and_a_message_in_both_languages(code: str) -> None:
    assert _STATUS[code] == 409
    assert _MESSAGES["uz"][code] != _MESSAGES["ru"][code]
    assert all(_MESSAGES[lang][code] not in ("", _MESSAGES[lang]["ERROR"]) for lang in ("uz", "ru"))


def test_a_catalog_error_is_worded_in_the_callers_language(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.manager_a,))
    response = write(client, world.manager_a, "POST", catalog(world), {"name": "Non", "price": 4500})
    assert response.json()["error"]["message"] == "В каталоге уже есть товар с таким названием (возможно, он скрыт)."
    response = write(client, world.owner_a, "POST", catalog(world), {"name": "Non", "price": 4500})
    assert response.json()["error"]["message"] == "Katalogda shu nomli mahsulot bor (yashirilgan bo'lishi ham mumkin)."
