"""Ownership transfer (REQ-036), a user's own shops (REQ-064) and the activity log (REQ-047)."""

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import World, as_user

pytestmark = pytest.mark.db


def writing(user: uuid.UUID) -> dict[str, str]:
    return {**as_user(user), "Idempotency-Key": f"test-{uuid.uuid4().hex}"}


def roles(owner: psycopg.Connection, shop: uuid.UUID) -> dict[uuid.UUID, tuple[str, str]]:
    rows = owner.execute("SELECT user_id, role, status FROM membership WHERE shop_id = %s", (shop,)).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def transfer_path(world: World, suffix: str = "") -> str:
    return f"/api/v1/shops/{world.shop_a}/ownership-transfer{suffix}"


def start(client: TestClient, world: World, to: uuid.UUID | None = None) -> Any:
    return client.post(
        transfer_path(world),
        json={"membership_id": str(to or world.manager_a_membership)},
        headers=writing(world.owner_a),
    )


# --- ownership transfer ---------------------------------------------------------------------------------


def test_ownership_moves_only_after_the_manager_accepts(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    started = start(client, world)
    assert started.status_code == 201, started.text
    assert roles(owner, world.shop_a)[world.owner_a] == ("owner", "active"), "nothing changes until acceptance"
    pending = client.get(transfer_path(world), headers=as_user(world.manager_a)).json()["pending"]
    assert pending["to_membership"] == str(world.manager_a_membership)

    accepted = client.post(transfer_path(world, "/accept"), headers=writing(world.manager_a))
    assert accepted.status_code == 200, accepted.text
    after = roles(owner, world.shop_a)
    assert after[world.manager_a] == ("owner", "active")
    assert after[world.owner_a] == ("manager", "active"), "the former owner becomes a manager (BR-22)"
    assert [role for role, _ in after.values()].count("owner") == 1

    # rights follow at once
    staff = f"/api/v1/shops/{world.shop_a}/staff"
    assert client.get(staff, headers=as_user(world.manager_a)).status_code == 200
    assert client.get(staff, headers=as_user(world.owner_a)).status_code == 403
    assert client.get(transfer_path(world), headers=as_user(world.manager_a)).json() == {"pending": None}
    actions = [
        r[0] for r in owner.execute("SELECT action FROM activity WHERE shop_id = %s ORDER BY at, id", (world.shop_a,))
    ]
    assert actions == ["ownership.transfer_started", "ownership.transferred"]


@pytest.mark.parametrize(
    ("target", "why"),
    [
        ("seller_a_membership", "a seller"),
        ("owner_a_membership", "the owner themself"),
        ("suspended", "a suspended manager"),
        ("other_shop", "a member of another shop"),
        ("unknown", "nobody"),
    ],
)
def test_the_shop_can_be_offered_only_to_an_active_manager(
    client: TestClient, world: World, owner: psycopg.Connection, target: str, why: str
) -> None:
    if target == "suspended":
        row = owner.execute(
            "SELECT id FROM membership WHERE shop_id = %s AND user_id = %s", (world.shop_a, world.suspended_a)
        ).fetchone()
    elif target == "other_shop":
        row = owner.execute("SELECT id FROM membership WHERE shop_id = %s", (world.shop_b,)).fetchone()
    elif target == "unknown":
        row = (uuid.uuid4(),)
    else:
        row = (getattr(world, target),)
    assert row is not None
    response = start(client, world, row[0])
    assert response.status_code == 409, why
    assert response.json()["error"]["code"] == "TRANSFER_TARGET_INVALID"
    assert owner.execute("SELECT count(*) FROM ownership_transfer WHERE shop_id = %s", (world.shop_a,)).fetchone() == (
        0,
    )


def test_only_one_transfer_can_be_pending(client: TestClient, world: World) -> None:
    assert start(client, world).status_code == 201
    second = start(client, world)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "TRANSFER_PENDING"


def test_only_the_offered_manager_can_answer(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    other_manager = uuid.uuid4()
    owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (other_manager, 880_000_001))
    owner.execute(
        "INSERT INTO membership (id, shop_id, user_id, role) VALUES (gen_random_uuid(), %s, %s, 'manager')",
        (world.shop_a, other_manager),
    )
    start(client, world)
    before = roles(owner, world.shop_a)
    for caller in (other_manager, world.owner_a):
        for action in ("/accept", "/decline"):
            response = client.post(transfer_path(world, action), headers=writing(caller))
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == "NOT_TRANSFER_TARGET"
    assert client.post(transfer_path(world, "/accept"), headers=writing(world.seller_a)).status_code == 403
    assert roles(owner, world.shop_a) == before


def test_declining_or_cancelling_leaves_ownership_as_it_was(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = roles(owner, world.shop_a)
    start(client, world)
    assert client.post(transfer_path(world, "/decline"), headers=writing(world.manager_a)).status_code == 200
    assert roles(owner, world.shop_a) == before
    # nothing left to answer
    assert client.post(transfer_path(world, "/accept"), headers=writing(world.manager_a)).status_code == 404

    start(client, world)
    assert client.delete(transfer_path(world), headers=writing(world.owner_a)).status_code == 200
    assert client.post(transfer_path(world, "/accept"), headers=writing(world.manager_a)).status_code == 404
    assert roles(owner, world.shop_a) == before
    statuses = [
        r[0]
        for r in owner.execute(
            "SELECT status FROM ownership_transfer WHERE shop_id = %s ORDER BY created_at", (world.shop_a,)
        )
    ]
    assert statuses == ["declined", "cancelled"]


def test_an_expired_offer_cannot_be_accepted(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    start(client, world)
    owner.execute(
        "UPDATE ownership_transfer SET created_at = now() - interval '3 days', expires_at = now() - interval '1 day' "
        "WHERE shop_id = %s",
        (world.shop_a,),
    )
    before = roles(owner, world.shop_a)
    assert client.post(transfer_path(world, "/accept"), headers=writing(world.manager_a)).status_code == 404
    assert roles(owner, world.shop_a) == before
    # The refused call rolled back, so the row is marked expired by the next call that succeeds.
    assert start(client, world).status_code == 201, "a new offer can be made after the old one expired"
    statuses = sorted(
        row[0] for row in owner.execute("SELECT status FROM ownership_transfer WHERE shop_id = %s", (world.shop_a,))
    )
    assert statuses == ["expired", "pending"]


def test_acceptance_is_refused_if_the_manager_was_suspended_meanwhile(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    start(client, world)
    owner.execute("UPDATE membership SET role = 'seller' WHERE id = %s", (world.manager_a_membership,))
    before = roles(owner, world.shop_a)
    response = client.post(transfer_path(world, "/accept"), headers=writing(world.manager_a))
    # as a seller they no longer hold the capability at all
    assert response.status_code == 403
    assert roles(owner, world.shop_a) == before


# --- my shops and the active shop -----------------------------------------------------------------------


def test_my_shops_lists_only_active_memberships(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    owner.execute(
        "INSERT INTO membership (id, shop_id, user_id, role) VALUES (gen_random_uuid(), %s, %s, 'seller')",
        (world.shop_b, world.manager_a),
    )
    mine = client.get("/api/v1/me/shops", headers=as_user(world.manager_a)).json()
    assert {(s["name"], s["role"]) for s in mine["items"]} == {("Shop A", "manager"), ("Shop B", "seller")}
    assert mine["active_shop"] is None

    suspended = client.get("/api/v1/me/shops", headers=as_user(world.suspended_a)).json()
    assert suspended == {"items": [], "active_shop": None}
    assert client.get("/api/v1/me/shops", headers=as_user(world.customer_of_a)).json()["items"] == []


def test_the_active_shop_can_be_set_only_to_a_shop_one_belongs_to(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    ok = client.put("/api/v1/me/active-shop", json={"shop_id": str(world.shop_a)}, headers=as_user(world.seller_a))
    assert ok.status_code == 200
    assert client.get("/api/v1/me/shops", headers=as_user(world.seller_a)).json()["active_shop"] == str(world.shop_a)

    for shop in (world.shop_b, uuid.uuid4()):
        refused = client.put("/api/v1/me/active-shop", json={"shop_id": str(shop)}, headers=as_user(world.seller_a))
        assert refused.status_code == 404
    assert owner.execute("SELECT active_shop FROM app_user WHERE id = %s", (world.seller_a,)).fetchone() == (
        world.shop_a,
    )


def test_an_active_shop_one_was_removed_from_is_no_longer_reported(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    client.put("/api/v1/me/active-shop", json={"shop_id": str(world.shop_a)}, headers=as_user(world.seller_a))
    owner.execute("UPDATE membership SET status = 'removed' WHERE id = %s", (world.seller_a_membership,))
    assert client.get("/api/v1/me/shops", headers=as_user(world.seller_a)).json() == {"items": [], "active_shop": None}


# --- activity log ---------------------------------------------------------------------------------------


def _make_activity(client: TestClient, world: World, count: int) -> None:
    for n in range(count):
        response = client.patch(
            f"/api/v1/shops/{world.shop_a}", json={"name": f"Name {n}"}, headers=writing(world.owner_a)
        )
        assert response.status_code == 200


def test_the_owner_pages_through_the_activity_log_newest_first(client: TestClient, world: World) -> None:
    _make_activity(client, world, 5)
    path = f"/api/v1/shops/{world.shop_a}/activity"
    first = client.get(path, params={"limit": 2}, headers=as_user(world.owner_a)).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    seen = list(first["items"])
    cursor = first["next_cursor"]
    while cursor:
        page = client.get(path, params={"limit": 2, "cursor": cursor}, headers=as_user(world.owner_a)).json()
        seen += page["items"]
        cursor = page["next_cursor"]
    assert len(seen) == 5
    assert len({item["id"] for item in seen}) == 5, "no item repeats across pages"
    assert [item["at"] for item in seen] == sorted((item["at"] for item in seen), reverse=True)
    assert {item["action"] for item in seen} == {"shop.settings_changed"}
    assert {item["actor_id"] for item in seen} == {str(world.owner_a_membership)}


def test_the_activity_log_filters_by_staff_member_action_and_subject(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    _make_activity(client, world, 2)
    client.delete(f"/api/v1/shops/{world.shop_a}/staff/{world.seller_a_membership}", headers=writing(world.owner_a))
    path = f"/api/v1/shops/{world.shop_a}/activity"

    def actions(**params: Any) -> list[str]:
        response = client.get(path, params=params, headers=as_user(world.owner_a))
        assert response.status_code == 200, response.text
        return [item["action"] for item in response.json()["items"]]

    assert actions(action="staff") == ["staff.removed"]
    assert actions(action="shop.settings_changed") == ["shop.settings_changed"] * 2
    assert actions(subject=str(world.seller_a_membership)) == ["staff.removed"]
    assert len(actions(actor=str(world.owner_a_membership))) == 3
    assert actions(actor=str(world.manager_a_membership)) == []
    assert actions(action="nothing.like.this") == []


def test_the_activity_log_never_shows_another_shop(client: TestClient, world: World) -> None:
    _make_activity(client, world, 2)
    client.patch(f"/api/v1/shops/{world.shop_b}", json={"name": "B renamed"}, headers=writing(world.owner_b))
    in_b = client.get(f"/api/v1/shops/{world.shop_b}/activity", headers=as_user(world.owner_b)).json()["items"]
    assert len(in_b) == 1
    # a cursor taken in shop A is just a position; used in shop B it reveals nothing of A
    cursor = client.get(
        f"/api/v1/shops/{world.shop_a}/activity", params={"limit": 1}, headers=as_user(world.owner_a)
    ).json()["next_cursor"]
    crossed = client.get(
        f"/api/v1/shops/{world.shop_b}/activity", params={"cursor": cursor}, headers=as_user(world.owner_b)
    ).json()["items"]
    assert all(item["id"] in {i["id"] for i in in_b} for item in crossed)


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"cursor": "not-a-cursor"}, {"action": "bad action!"}, {"action": "a%"}],
)
def test_invalid_activity_queries_are_rejected(client: TestClient, world: World, params: dict[str, Any]) -> None:
    response = client.get(f"/api/v1/shops/{world.shop_a}/activity", params=params, headers=as_user(world.owner_a))
    assert response.status_code == 422, response.text
