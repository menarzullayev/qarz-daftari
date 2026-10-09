"""Shop creation and staff management (REQ-001, REQ-031 to REQ-035, REQ-052)."""

import hashlib
import uuid
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import World, as_user

pytestmark = pytest.mark.db


def writing(user: uuid.UUID, key: str | None = None) -> dict[str, str]:
    return {**as_user(user), "Idempotency-Key": key or f"test-{uuid.uuid4().hex}"}


def staff_of(owner: psycopg.Connection, shop: uuid.UUID) -> dict[uuid.UUID, tuple[str, str]]:
    rows = owner.execute("SELECT user_id, role, status FROM membership WHERE shop_id = %s", (shop,)).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def actions(owner: psycopg.Connection, shop: Any) -> list[str]:
    return [row[0] for row in owner.execute("SELECT action FROM activity WHERE shop_id = %s ORDER BY at, id", (shop,))]


def invite(client: TestClient, world: World, role: str = "seller") -> dict[str, Any]:
    response = client.post(
        f"/api/v1/shops/{world.shop_a}/staff/invitations", json={"role": role}, headers=writing(world.owner_a)
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def accept(client: TestClient, user: uuid.UUID, token: str):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/staff-invitations/accept", json={"token": token}, headers=as_user(user))


# --- creating a shop ------------------------------------------------------------------------------------


def test_any_user_can_create_a_shop_and_becomes_its_owner_on_trial(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    response = client.post("/api/v1/shops", json={"name": "  Baraka  ", "lang": "ru"}, headers=writing(world.stranger))
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["name"], body["lang"], body["default_promise_days"]) == ("Baraka", "ru", 30)
    shop = uuid.UUID(body["id"])

    assert staff_of(owner, shop) == {world.stranger: ("owner", "active")}
    subscription = owner.execute("SELECT state, trial_ends FROM subscription WHERE shop_id = %s", (shop,)).fetchone()
    assert subscription is not None
    assert subscription[0] == "trial"
    assert date.today() + timedelta(days=29) <= subscription[1] <= date.today() + timedelta(days=31)
    assert owner.execute("SELECT active_shop FROM app_user WHERE id = %s", (world.stranger,)).fetchone() == (shop,)
    assert actions(owner, shop) == ["shop.created"]

    # the new owner can use it at once; nobody else can see it
    assert client.get(f"/api/v1/shops/{shop}", headers=as_user(world.stranger)).status_code == 200
    assert client.get(f"/api/v1/shops/{shop}", headers=as_user(world.owner_a)).status_code == 404


def test_a_repeated_create_request_makes_one_shop(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    before = owner.execute("SELECT count(*) FROM shop").fetchone()
    headers = writing(world.stranger, "create-shop-0001")
    first = client.post("/api/v1/shops", json={"name": "Once", "lang": "uz"}, headers=headers)
    second = client.post("/api/v1/shops", json={"name": "Once", "lang": "uz"}, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    after = owner.execute("SELECT count(*) FROM shop").fetchone()
    assert before is not None and after is not None
    assert after[0] == before[0] + 1

    different = client.post("/api/v1/shops", json={"name": "Twice", "lang": "uz"}, headers=headers)
    assert different.status_code == 409
    assert owner.execute("SELECT count(*) FROM shop").fetchone() == after


def test_the_same_key_from_two_users_makes_two_shops(client: TestClient, world: World) -> None:
    body = {"name": "Same key", "lang": "uz"}
    one = client.post("/api/v1/shops", json=body, headers=writing(world.stranger, "shared-key-0001"))
    two = client.post("/api/v1/shops", json=body, headers=writing(world.customer_of_a, "shared-key-0001"))
    assert one.status_code == two.status_code == 201
    assert one.json()["id"] != two.json()["id"]


@pytest.mark.parametrize("trial_on", [False, True])
def test_the_trial_follows_the_platform_switch(
    client: TestClient, world: World, owner: psycopg.Connection, trial_on: bool
) -> None:
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('trial_on', %s::jsonb, 'test'), "
        "('trial_days', '10'::jsonb, 'test') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        ("true" if trial_on else "false",),
    )
    try:
        response = client.post("/api/v1/shops", json={"name": "Switch", "lang": "uz"}, headers=writing(world.stranger))
        row = owner.execute(
            "SELECT state, trial_ends FROM subscription WHERE shop_id = %s", (response.json()["id"],)
        ).fetchone()
        assert row is not None
        if trial_on:
            assert row[0] == "trial"
            assert date.today() + timedelta(days=9) <= row[1] <= date.today() + timedelta(days=11)
        else:
            assert row == ("limited", None)
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key IN ('trial_on', 'trial_days')")


@pytest.mark.parametrize("body", [{"name": "", "lang": "uz"}, {"name": "x" * 81}, {"name": "Ok", "lang": "de"}, {}])
def test_invalid_shops_are_not_created(
    client: TestClient, world: World, owner: psycopg.Connection, body: dict[str, Any]
) -> None:
    before = owner.execute("SELECT count(*) FROM shop").fetchone()
    response = client.post("/api/v1/shops", json=body, headers=writing(world.stranger))
    assert response.status_code == 422
    assert owner.execute("SELECT count(*) FROM shop").fetchone() == before


# --- inviting and joining -------------------------------------------------------------------------------


def test_an_invited_person_joins_with_the_invited_role(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    invitation = invite(client, world, "manager")
    assert set(invitation) == {"id", "token", "role", "expires_at"}

    joined = accept(client, world.stranger, invitation["token"])
    assert joined.status_code == 200, joined.text
    assert joined.json() == {"shop_id": str(world.shop_a)}
    assert staff_of(owner, world.shop_a)[world.stranger] == ("manager", "active")
    assert actions(owner, world.shop_a) == ["staff.invited", "staff.joined"]

    # a manager's rights, at once
    assert client.get(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.stranger)).status_code == 200
    assert client.get(f"/api/v1/shops/{world.shop_a}/staff", headers=as_user(world.stranger)).status_code == 403


def test_only_the_hash_of_an_invitation_token_is_stored(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    key = "invite-once-0001"
    path = f"/api/v1/shops/{world.shop_a}/staff/invitations"
    first = client.post(path, json={"role": "seller"}, headers=writing(world.owner_a, key))
    token = first.json()["token"]
    digest = hashlib.sha256(token.encode()).digest()
    assert owner.execute("SELECT count(*) FROM invitation WHERE token_hash = %s", (digest,)).fetchone() == (1,)

    # the token appears nowhere in the database, including the stored idempotent response
    dump = owner.execute("SELECT string_agg(response::text, ' ') FROM request_key").fetchone()
    assert dump is not None
    assert token not in dump[0]
    replay = client.post(path, json={"role": "seller"}, headers=writing(world.owner_a, key))
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["token"] is None, "a repeat must not hand the token out a second time"
    assert owner.execute(
        "SELECT count(*) FROM invitation WHERE shop_id = %s AND status = 'issued'", (world.shop_a,)
    ).fetchone() == (2,), "one new invitation besides the fixture's, not two"


def test_an_invitation_works_once(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    token = invite(client, world)["token"]
    assert accept(client, world.stranger, token).status_code == 200
    again = accept(client, world.customer_of_a, token)
    assert again.status_code == 404
    assert world.customer_of_a not in staff_of(owner, world.shop_a)


def test_unusable_invitations_all_look_the_same(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    expired = invite(client, world)
    owner.execute(
        "UPDATE invitation SET expires_at = now() - interval '1 minute' WHERE token_hash = %s",
        (bytes.fromhex(expired["id"]),),
    )
    cancelled = invite(client, world)
    done = client.delete(
        f"/api/v1/shops/{world.shop_a}/staff/invitations/{cancelled['id']}", headers=writing(world.owner_a)
    )
    assert done.status_code == 200

    before = staff_of(owner, world.shop_a)
    responses = [
        accept(client, world.stranger, expired["token"]),
        accept(client, world.stranger, cancelled["token"]),
        accept(client, world.stranger, "never-issued-token-0123456789"),
        accept(client, world.stranger, "short"),
    ]
    assert [r.status_code for r in responses] == [404, 404, 404, 404]
    assert len({r.text for r in responses}) == 1
    assert staff_of(owner, world.shop_a) == before


def test_a_customer_link_token_cannot_be_used_as_a_staff_invitation(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    token = f"customer-link-{uuid.uuid4().hex}"
    customer = owner.execute("SELECT id FROM customer WHERE shop_id = %s LIMIT 1", (world.shop_a,)).fetchone()
    assert customer is not None
    owner.execute(
        "INSERT INTO invitation (token_hash, shop_id, kind, customer_id) VALUES (%s, %s, 'customer', %s)",
        (hashlib.sha256(token.encode()).digest(), world.shop_a, customer[0]),
    )
    assert accept(client, world.stranger, token).status_code == 404
    assert world.stranger not in staff_of(owner, world.shop_a)


def test_an_existing_member_cannot_join_again(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    token = invite(client, world, "manager")["token"]
    before = staff_of(owner, world.shop_a)
    response = accept(client, world.seller_a, token)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ALREADY_MEMBER"
    assert staff_of(owner, world.shop_a) == before, "the seller must not have been promoted by the manager invitation"
    # the invitation is still usable by the person it was meant for
    assert accept(client, world.stranger, token).status_code == 200


def test_only_manager_and_seller_can_be_invited(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    for role in ("owner", "admin", "", "SELLER"):
        response = client.post(
            f"/api/v1/shops/{world.shop_a}/staff/invitations", json={"role": role}, headers=writing(world.owner_a)
        )
        assert response.status_code == 422, role
    assert owner.execute(
        "SELECT count(*) FROM invitation WHERE shop_id = %s AND role = 'owner'", (world.shop_a,)
    ).fetchone() == (0,)


def test_listing_shows_members_and_open_invitations_without_tokens(client: TestClient, world: World) -> None:
    invitation = invite(client, world, "manager")
    members = client.get(f"/api/v1/shops/{world.shop_a}/staff", headers=as_user(world.owner_a)).json()["items"]
    assert [m["role"] for m in members] == ["owner", "manager", "manager", "seller"]
    assert {m["status"] for m in members} == {"active", "suspended"}

    listed = client.get(f"/api/v1/shops/{world.shop_a}/staff/invitations", headers=as_user(world.owner_a))
    assert invitation["token"] not in listed.text
    assert {item["id"] for item in listed.json()["items"]} == {invitation["id"], world.invitation_a}


# --- changing and removing ------------------------------------------------------------------------------


def test_suspending_takes_access_away_at_once_and_reactivating_restores_it(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    manager = owner.execute(
        "SELECT id FROM membership WHERE shop_id = %s AND user_id = %s", (world.shop_a, world.manager_a)
    ).fetchone()
    assert manager is not None
    path = f"/api/v1/shops/{world.shop_a}/staff/{manager[0]}"
    shop = f"/api/v1/shops/{world.shop_a}"

    assert client.get(shop, headers=as_user(world.manager_a)).status_code == 200
    assert client.patch(path, json={"status": "suspended"}, headers=writing(world.owner_a)).status_code == 200
    assert client.get(shop, headers=as_user(world.manager_a)).status_code == 404
    assert client.patch(path, json={"status": "active"}, headers=writing(world.owner_a)).status_code == 200
    assert client.get(shop, headers=as_user(world.manager_a)).status_code == 200
    assert actions(owner, world.shop_a) == ["staff.updated", "staff.updated"]


def test_a_removed_member_loses_access_and_stays_on_record(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    path = f"/api/v1/shops/{world.shop_a}/staff/{world.seller_a_membership}"
    response = client.delete(path, headers=writing(world.owner_a))
    assert response.status_code == 200
    assert response.json()["status"] == "removed"
    assert staff_of(owner, world.shop_a)[world.seller_a] == ("seller", "removed")

    listed = client.get(f"/api/v1/shops/{world.shop_a}/staff", headers=as_user(world.owner_a)).json()["items"]
    assert str(world.seller_a_membership) not in {m["id"] for m in listed}
    # removed is final through this route
    assert client.patch(path, json={"status": "active"}, headers=writing(world.owner_a)).status_code == 404
    assert client.delete(path, headers=writing(world.owner_a)).status_code == 404


def test_a_removed_member_invited_again_keeps_the_same_membership(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    client.delete(f"/api/v1/shops/{world.shop_a}/staff/{world.seller_a_membership}", headers=writing(world.owner_a))
    token = invite(client, world, "manager")["token"]
    assert accept(client, world.seller_a, token).status_code == 200
    rows = owner.execute(
        "SELECT id, role, status FROM membership WHERE shop_id = %s AND user_id = %s", (world.shop_a, world.seller_a)
    ).fetchall()
    assert rows == [(world.seller_a_membership, "manager", "active")]


def test_the_owner_membership_cannot_be_changed_or_removed(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner_membership = owner.execute(
        "SELECT id FROM membership WHERE shop_id = %s AND role = 'owner'", (world.shop_a,)
    ).fetchone()
    assert owner_membership is not None
    path = f"/api/v1/shops/{world.shop_a}/staff/{owner_membership[0]}"
    for response in (
        client.patch(path, json={"role": "seller"}, headers=writing(world.owner_a)),
        client.patch(path, json={"status": "suspended"}, headers=writing(world.owner_a)),
        client.delete(path, headers=writing(world.owner_a)),
    ):
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "OWNER_MEMBERSHIP_FIXED"
    assert staff_of(owner, world.shop_a)[world.owner_a] == ("owner", "active")


@pytest.mark.parametrize(
    "body", [{}, {"role": "owner"}, {"status": "removed"}, {"status": "invited"}, {"role": "manager", "extra": 1}]
)
def test_invalid_member_changes_are_rejected(
    client: TestClient, world: World, owner: psycopg.Connection, body: dict[str, Any]
) -> None:
    before = staff_of(owner, world.shop_a)
    response = client.patch(
        f"/api/v1/shops/{world.shop_a}/staff/{world.seller_a_membership}", json=body, headers=writing(world.owner_a)
    )
    assert response.status_code == 422, response.text
    assert staff_of(owner, world.shop_a) == before


def test_nobody_can_be_made_a_second_owner(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    """REQ-031 / INV-11: no route promotes to owner, and the database refuses a second active owner."""
    response = client.patch(
        f"/api/v1/shops/{world.shop_a}/staff/{world.seller_a_membership}",
        json={"role": "owner"},
        headers=writing(world.owner_a),
    )
    assert response.status_code == 422
    with pytest.raises(psycopg.errors.UniqueViolation):
        owner.execute("UPDATE membership SET role = 'owner' WHERE id = %s", (world.seller_a_membership,))
