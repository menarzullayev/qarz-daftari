"""Support access through the API: the administrator's side, what it opens, and what the owner sees
(REQ-059; BR-31; story S18.2)."""

import logging
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.admin_access import AdminAccess
from qarz.application.auth import AuthService
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app

from .conftest import (
    ADMIN_API,
    TEST_BOT_TOKEN,
    AdminEnv,
    HeaderAuthenticator,
    World,
    as_user,
    elevate,
    make_admin,
)

pytestmark = pytest.mark.db

AUDIT = f"{ADMIN_API}/audit"


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"support-{uuid.uuid4().hex}"}


def _admin_path(world: World, tail: str = "") -> str:
    return f"{ADMIN_API}/shops/{world.shop_a}/support-access{tail}"


def _owner_path(world: World, tail: str = "") -> str:
    return f"/api/v1/shops/{world.shop_a}/support-access{tail}"


@pytest.fixture
def admin(client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv) -> dict[str, str]:
    """Headers of an administrator who passed the second factor. The clock stands still until moved."""
    admin_env.clock.freeze()
    return elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))


def _open(client: TestClient, admin: dict[str, str], world: World, **body: Any) -> Any:
    return client.post(_admin_path(world), json={"reason": "Egasi yordam so'radi", **body}, headers={**admin, **_key()})


def _customers(client: TestClient, admin: dict[str, str], world: World, tail: str = "") -> Any:
    return client.get(f"{ADMIN_API}/shops/{world.shop_a}/customers{tail}", headers=admin)


def _audit(owner: psycopg.Connection, world: World) -> list[tuple[str, Any, Any]]:
    rows = owner.execute(
        "SELECT action, reason, detail FROM admin_audit WHERE admin_id = %s AND action LIKE 'support.%%' "
        "ORDER BY at, id",
        (world.admin,),
    ).fetchall()
    return [(row[0], row[1], row[2]) for row in rows]


def _activity(owner: psycopg.Connection, shop: uuid.UUID) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT actor_kind, actor_id, action, subject_type, subject_id FROM activity WHERE shop_id = %s "
        "AND action LIKE 'support_access.%%' ORDER BY at, action",
        (shop,),
    ).fetchall()


def _notices(owner: psycopg.Connection, user: uuid.UUID) -> list[str]:
    rows = owner.execute(
        "SELECT o.payload->>'text' FROM outbox_message o JOIN app_user u ON o.recipient = u.tg_id::text "
        "WHERE u.id = %s ORDER BY o.created_at",
        (user,),
    ).fetchall()
    return [text for (text,) in rows]


# --- opening --------------------------------------------------------------------------------------------


def test_opening_gives_the_administrator_the_shops_customers_and_tells_the_owner(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.owner_a,))
    assert _customers(client, admin, world).status_code == 403

    now = admin_env.clock.now()
    response = _open(client, admin, world, hours=3)
    assert response.status_code == 201, response.text
    body = response.json()
    access_id = uuid.UUID(body["id"])
    assert body == {
        "id": str(access_id),
        "shop_id": str(world.shop_a),
        "admin_id": str(world.admin),
        "reason": "Egasi yordam so'radi",
        "state": "active",
        "starts_at": now.isoformat(),
        "ends_at": (now + timedelta(hours=3)).isoformat(),
        "closed_at": None,
        "closed_by": None,
    }
    stored = owner.execute(
        "SELECT shop_id, admin_id, reason, starts_at, ends_at, closed_at FROM support_access WHERE id = %s",
        (access_id,),
    ).fetchone()
    assert stored == (world.shop_a, world.admin, "Egasi yordam so'radi", now, now + timedelta(hours=3), None)

    # Logged: the admin audit with the shop and the reason, and the shop's own activity log.
    assert [row for row in _audit(owner, world) if row[0] == "support.opened"] == [
        (
            "support.opened",
            "Egasi yordam so'radi",
            {"access_id": str(access_id), "ends_at": (now + timedelta(hours=3)).isoformat()},
        )
    ]
    assert _activity(owner, world.shop_a) == [
        ("admin", world.admin, "support_access.opened", "support_access", access_id)
    ]
    # Visible to the owner: told at once, in their language, why and until when.
    told = _notices(owner, world.owner_a)
    assert len(told) == 1
    assert told[0].startswith("«Shop A»: администратор сервиса открыл доступ")
    assert "Egasi yordam so'radi" in told[0]
    assert (now + timedelta(hours=3, minutes=0) + timedelta(hours=5)).strftime("%d.%m.%Y %H:%M") in told[0]

    listed = _customers(client, admin, world)
    assert listed.status_code == 200, listed.text
    assert {item["display_name"] for item in listed.json()["items"]} == {"Ali", "Vali"}
    # Only this shop: the other one stays shut.
    assert client.get(f"{ADMIN_API}/shops/{world.shop_b}/customers", headers=admin).status_code == 403


def test_what_the_administrator_reads_under_it_is_what_the_staff_see_and_each_look_is_recorded(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str]
) -> None:
    access_id = uuid.UUID(_open(client, admin, world).json()["id"])
    staff_list = client.get(f"/api/v1/shops/{world.shop_a}/customers", headers=as_user(world.owner_a)).json()
    staff_one = client.get(
        f"/api/v1/shops/{world.shop_a}/customers/{world.customer_a}", headers=as_user(world.owner_a)
    ).json()

    assert _customers(client, admin, world).json() == staff_list
    assert _customers(client, admin, world, "?q=ali").json()["items"][0]["id"] == str(world.customer_a)
    assert _customers(client, admin, world, "?status=archived").json()["items"][0]["display_name"] == "Sobir"
    one = _customers(client, admin, world, f"/{world.customer_a}")
    assert one.status_code == 200, one.text
    assert one.json() == staff_one
    assert one.json()["balance"] == 50000
    assert one.json()["entries"][0]["id"] == str(world.entry_a)

    # The clock of this test stands still, so the rows have one time and no order among themselves.
    looks = [(action, detail) for action, _, detail in _audit(owner, world) if action != "support.opened"]
    assert sorted(looks, key=str) == [
        ("support.customer_viewed", {"access_id": str(access_id), "customer_id": str(world.customer_a)}),
        ("support.customers_listed", {"access_id": str(access_id)}),
        ("support.customers_listed", {"access_id": str(access_id)}),
        ("support.customers_listed", {"access_id": str(access_id)}),
    ]
    # And in the shop's own activity log, where the owner reads it.
    seen = client.get(f"/api/v1/shops/{world.shop_a}/activity", headers=as_user(world.owner_a)).json()["items"]
    by_admin = [(item["action"], item["subject_id"]) for item in seen if item["actor_kind"] == "admin"]
    assert sorted(by_admin) == sorted(
        [
            ("support_access.opened", str(access_id)),
            ("support_access.customers_listed", str(access_id)),
            ("support_access.customers_listed", str(access_id)),
            ("support_access.customers_listed", str(access_id)),
            ("support_access.customer_viewed", str(world.customer_a)),
        ]
    )
    assert {item["actor_id"] for item in seen if item["actor_kind"] == "admin"} == {str(world.admin)}


def test_under_support_access_unknown_things_are_not_found_and_bad_requests_are_refused(
    client: TestClient, world: World, admin: dict[str, str]
) -> None:
    _open(client, admin, world)
    assert _customers(client, admin, world, f"/{uuid.uuid4()}").status_code == 404
    assert _customers(client, admin, world, "/not-a-uuid").status_code == 404
    for query, field in (
        ("?limit=0", "limit"),
        ("?limit=101", "limit"),
        ("?status=all", "status"),
        ("?cursor=x", "cursor"),
    ):
        refused = _customers(client, admin, world, query)
        assert refused.status_code == 422, query
        assert field in refused.json()["error"]["fields"]


def test_a_refused_look_is_audited_counted_and_tells_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str]
) -> None:
    """PRD, acceptance of FEAT-021: an attempt without support access is refused and logged."""
    for tail in ("", f"/{world.customer_a}", f"/{uuid.uuid4()}"):
        response = _customers(client, admin, world, tail)
        assert response.status_code == 403
        assert response.json()["error"] == {
            "code": "SUPPORT_ACCESS_REQUIRED",
            "message": "Do'kon ma'lumotlarini ko'rish uchun avval sabab ko'rsatib ruxsat oching.",
            "fields": {},
        }
    assert sorted(_audit(owner, world), key=str) == [
        ("support.refused", None, {"attempted": "support.customer_viewed"}),
        ("support.refused", None, {"attempted": "support.customer_viewed"}),
        ("support.refused", None, {"attempted": "support.customers_listed"}),
    ]
    assert _activity(owner, world.shop_a) == []
    # A shop that does not exist answers the same: nothing to tell apart.
    unknown = client.get(f"{ADMIN_API}/shops/{uuid.uuid4()}/customers", headers=admin)
    assert unknown.status_code == 403


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "reason"),
        ({"reason": "ab"}, "reason"),
        ({"reason": "x" * 501}, "reason"),
        ({"reason": "Yordam", "hours": 0}, "hours"),
        ({"reason": "Yordam", "hours": 25}, "hours"),
        ({"reason": "Yordam", "hours": "2"}, "hours"),
        ({"reason": "Yordam", "hours": True}, "hours"),
        ({"reason": "Yordam", "forever": True}, "forever"),
    ],
)
def test_an_access_needs_a_reason_and_lasts_at_most_a_day(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin: dict[str, str],
    body: dict[str, Any],
    field: str,
) -> None:
    response = client.post(_admin_path(world), json=body, headers={**admin, **_key()})
    assert response.status_code == 422, response.text
    assert list(response.json()["error"]["fields"]) == [field]
    assert owner.execute("SELECT count(*) FROM support_access WHERE shop_id = %s", (world.shop_a,)).fetchone() == (0,)
    assert _audit(owner, world) == []


def test_the_length_is_one_hour_unless_asked_and_a_day_at_most(
    client: TestClient, world: World, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    now = admin_env.clock.now()
    default = client.post(_admin_path(world), json={"reason": "  Qisqa   ko'rik "}, headers={**admin, **_key()})
    assert default.status_code == 201, default.text
    assert default.json()["ends_at"] == (now + timedelta(hours=1)).isoformat()
    assert default.json()["reason"] == "Qisqa ko'rik"
    assert client.post(_admin_path(world, "/close"), headers={**admin, **_key()}).status_code == 200
    longest = _open(client, admin, world, hours=24)
    assert longest.status_code == 201, longest.text
    assert longest.json()["ends_at"] == (now + timedelta(hours=24)).isoformat()


def test_opening_is_idempotent_and_one_at_a_time(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str]
) -> None:
    key = _key()
    body = {"reason": "Egasi yordam so'radi", "hours": 2}
    first = client.post(_admin_path(world), json=body, headers={**admin, **key})
    assert first.status_code == 201
    again = client.post(_admin_path(world), json=body, headers={**admin, **key})
    assert (again.status_code, again.json()) == (201, first.json())
    other = client.post(_admin_path(world), json={**body, "hours": 3}, headers={**admin, **key})
    assert (other.status_code, other.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    second = _open(client, admin, world)
    assert (second.status_code, second.json()["error"]["code"]) == (409, "SUPPORT_ACCESS_ALREADY_OPEN")
    assert owner.execute("SELECT count(*) FROM support_access WHERE shop_id = %s", (world.shop_a,)).fetchone() == (1,)
    assert [action for action, _, _ in _audit(owner, world)] == ["support.opened"]
    assert len(_notices(owner, world.owner_a)) == 1
    without_key = client.post(_admin_path(world), json=body, headers=admin)
    assert without_key.status_code == 422
    assert "Idempotency-Key" in without_key.json()["error"]["fields"]


def test_no_access_is_opened_to_a_shop_that_does_not_exist_or_was_erased(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str]
) -> None:
    unknown = client.post(
        f"{ADMIN_API}/shops/{uuid.uuid4()}/support-access", json={"reason": "Kirish"}, headers={**admin, **_key()}
    )
    assert unknown.status_code == 404
    owner.execute("UPDATE shop SET status = 'erased' WHERE id = %s", (world.shop_a,))
    assert _open(client, admin, world).status_code == 404
    assert _audit(owner, world) == []


@pytest.mark.parametrize("condition", ["suspended", "waiting to be deleted"])
def test_a_suspended_shop_and_one_waiting_to_be_deleted_can_still_be_helped(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], condition: str
) -> None:
    if condition == "suspended":
        owner.execute(
            "UPDATE subscription SET state = 'suspended', prior_state = 'trial' WHERE shop_id = %s", (world.shop_a,)
        )
    else:
        owner.execute(
            "UPDATE shop SET status = 'deletion_pending', deletion_due = now() + interval '20 days' WHERE id = %s",
            (world.shop_a,),
        )
    opened = _open(client, admin, world)
    assert opened.status_code == 201, opened.text
    assert _customers(client, admin, world).status_code == 200
    # The owner sees it and can end it there too.
    seen = client.get(_owner_path(world), headers=as_user(world.owner_a))
    assert [item["state"] for item in seen.json()["items"]] == ["active"]
    ended = client.post(_owner_path(world, f"/{opened.json()['id']}/end"), headers={**as_user(world.owner_a), **_key()})
    assert ended.status_code == 200, ended.text
    assert _customers(client, admin, world).status_code == 403


# --- it ends --------------------------------------------------------------------------------------------


def test_it_ends_by_itself_at_its_time(
    client: TestClient, world: World, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    opened = _open(client, admin, world, hours=1)
    admin_env.clock.offset += timedelta(hours=1) - timedelta(microseconds=1)
    assert _customers(client, admin, world).status_code == 200
    admin_env.clock.offset += timedelta(microseconds=1)
    assert _customers(client, admin, world).status_code == 403
    assert _customers(client, admin, world, f"/{world.customer_a}").status_code == 403
    seen = client.get(_owner_path(world), headers=as_user(world.owner_a)).json()["items"]
    assert [(item["id"], item["state"]) for item in seen] == [(opened.json()["id"], "expired")]
    # Nothing is left to close, and a new one can be opened.
    closing = client.post(_admin_path(world, "/close"), headers={**admin, **_key()})
    assert (closing.status_code, closing.json()["error"]["code"]) == (409, "SUPPORT_ACCESS_NOT_OPEN")
    assert _open(client, admin, world).status_code == 201


def test_the_administrator_closes_it_early_and_the_owner_is_told(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    opened = _open(client, admin, world).json()
    admin_env.clock.offset += timedelta(minutes=10)
    key = _key()
    closed = client.post(_admin_path(world, "/close"), headers={**admin, **key})
    assert closed.status_code == 200, closed.text
    assert closed.json() == {"id": opened["id"], "shop_id": str(world.shop_a), "state": "closed"}
    assert client.post(_admin_path(world, "/close"), headers={**admin, **key}).json() == closed.json()
    assert _customers(client, admin, world).status_code == 403

    stored = owner.execute("SELECT closed_at, closed_by FROM support_access WHERE id = %s", (opened["id"],)).fetchone()
    assert stored == (admin_env.clock.now(), "admin")
    assert sorted(action for action, _, _ in _audit(owner, world)) == [
        "support.closed",
        "support.opened",
        "support.refused",
    ]
    assert [row[2] for row in _activity(owner, world.shop_a)] == ["support_access.opened", "support_access.closed"]
    told = _notices(owner, world.owner_a)
    assert len(told) == 2
    assert told[1] == "«Shop A»: xizmat ma'muri do'kon ma'lumotlarini ko'rish ruxsatini yopdi."
    again = client.post(_admin_path(world, "/close"), headers={**admin, **_key()})
    assert (again.status_code, again.json()["error"]["code"]) == (409, "SUPPORT_ACCESS_NOT_OPEN")
    assert len(_notices(owner, world.owner_a)) == 2
    without_key = client.post(_admin_path(world, "/close"), headers=admin)
    assert without_key.status_code == 422


def test_one_administrator_cannot_use_or_close_anothers_access(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    _open(client, admin, world)
    second = elevate(client, admin_env, world.owner_b, make_admin(owner, admin_env, world.owner_b))
    assert _customers(client, second, world).status_code == 403
    closing = client.post(_admin_path(world, "/close"), headers={**second, **_key()})
    assert (closing.status_code, closing.json()["error"]["code"]) == (409, "SUPPORT_ACCESS_NOT_OPEN")
    assert _customers(client, admin, world).status_code == 200
    # But they see that it is open: administrators see each other's accesses.
    listed = client.get(f"{ADMIN_API}/support-access", params={"shop_id": str(world.shop_a)}, headers=second)
    assert [(item["admin_id"], item["state"]) for item in listed.json()["items"]] == [(str(world.admin), "active")]


def test_a_disabled_administrator_loses_the_shop_with_everything_else(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str]
) -> None:
    _open(client, admin, world)
    assert _customers(client, admin, world).status_code == 200
    owner.execute("UPDATE admin_account SET status = 'disabled' WHERE user_id = %s", (world.admin,))
    assert _customers(client, admin, world).status_code == 404


# --- the owner's side -----------------------------------------------------------------------------------


def test_the_owner_sees_the_open_access_and_the_history_and_ends_it(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    owner_headers = as_user(world.owner_a)
    assert client.get(_owner_path(world), headers=owner_headers).json() == {"items": [], "next_cursor": None}

    first = _open(client, admin, world).json()
    admin_env.clock.offset += timedelta(minutes=5)
    client.post(_admin_path(world, "/close"), headers={**admin, **_key()})
    admin_env.clock.offset += timedelta(minutes=5)
    second = _open(client, admin, world, reason="Ikkinchi marta", hours=4).json()

    seen = client.get(_owner_path(world), headers=owner_headers)
    assert seen.status_code == 200, seen.text
    items = seen.json()["items"]
    assert [(item["id"], item["state"], item["closed_by"]) for item in items] == [
        (second["id"], "active", None),
        (first["id"], "closed", "admin"),
    ]
    assert items[0] == second
    assert set(items[0]) == {
        "id",
        "shop_id",
        "admin_id",
        "reason",
        "state",
        "starts_at",
        "ends_at",
        "closed_at",
        "closed_by",
    }
    page = client.get(_owner_path(world), params={"limit": 1}, headers=owner_headers).json()
    assert [item["id"] for item in page["items"]] == [second["id"]]
    rest = client.get(_owner_path(world), params={"limit": 1, "cursor": page["next_cursor"]}, headers=owner_headers)
    assert ([item["id"] for item in rest.json()["items"]], rest.json()["next_cursor"]) == ([first["id"]], None)
    for params, field in (({"limit": 0}, "limit"), ({"limit": 101}, "limit"), ({"cursor": "x"}, "cursor")):
        refused = client.get(_owner_path(world), params=params, headers=owner_headers)
        assert (refused.status_code, list(refused.json()["error"]["fields"])) == (422, [field])

    # The owner ends it: the administrator is out at once.
    admin_env.clock.offset += timedelta(minutes=5)
    key = _key()
    ended = client.post(_owner_path(world, f"/{second['id']}/end"), headers={**owner_headers, **key})
    assert ended.status_code == 200, ended.text
    assert ended.json() == {
        **second,
        "state": "closed",
        "closed_at": admin_env.clock.now().isoformat(),
        "closed_by": "owner",
    }
    assert _customers(client, admin, world).status_code == 403
    assert (
        client.post(_owner_path(world, f"/{second['id']}/end"), headers={**owner_headers, **key}).json() == ended.json()
    )
    activity = owner.execute(
        "SELECT actor_kind, actor_id, subject_id FROM activity WHERE shop_id = %s AND action = 'support_access.ended'",
        (world.shop_a,),
    ).fetchall()
    assert activity == [("staff", world.owner_a_membership, uuid.UUID(second["id"]))]

    # What is over cannot be ended, by a new request or for another access.
    for access in (second["id"], first["id"]):
        again = client.post(_owner_path(world, f"/{access}/end"), headers={**owner_headers, **_key()})
        assert (again.status_code, again.json()["error"]["code"]) == (409, "SUPPORT_ACCESS_NOT_OPEN")
    assert (
        client.post(_owner_path(world, f"/{uuid.uuid4()}/end"), headers={**owner_headers, **_key()}).status_code == 404
    )
    # The administrators' list shows who ended it.
    listed = client.get(f"{ADMIN_API}/support-access", params={"shop_id": str(world.shop_a)}, headers=admin)
    assert [(item["closed_by"], item["shop_name"]) for item in listed.json()["items"]] == [
        ("owner", "Shop A"),
        ("admin", "Shop A"),
    ]


def test_an_owner_cannot_end_an_access_that_ran_out_or_belongs_to_another_shop(
    client: TestClient, world: World, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    opened = _open(client, admin, world, hours=1).json()
    other = client.post(
        f"/api/v1/shops/{world.shop_b}/support-access/{opened['id']}/end", headers={**as_user(world.owner_b), **_key()}
    )
    assert other.status_code == 404
    assert _customers(client, admin, world).status_code == 200
    admin_env.clock.offset += timedelta(hours=1)
    late = client.post(_owner_path(world, f"/{opened['id']}/end"), headers={**as_user(world.owner_a), **_key()})
    assert (late.status_code, late.json()["error"]["code"]) == (409, "SUPPORT_ACCESS_NOT_OPEN")


def test_the_administrators_list_filters_the_open_ones_and_pages(
    client: TestClient, world: World, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    first = _open(client, admin, world).json()
    admin_env.clock.offset += timedelta(minutes=1)
    client.post(_admin_path(world, "/close"), headers={**admin, **_key()})
    admin_env.clock.offset += timedelta(minutes=1)
    second = _open(client, admin, world).json()
    path, shop = f"{ADMIN_API}/support-access", {"shop_id": str(world.shop_a)}

    everything = client.get(path, params=shop, headers=admin).json()
    assert [item["id"] for item in everything["items"]] == [second["id"], first["id"]]
    only_open = client.get(path, params={**shop, "open": "true"}, headers=admin).json()
    assert [item["id"] for item in only_open["items"]] == [second["id"]]
    page = client.get(path, params={**shop, "limit": 1}, headers=admin).json()
    rest = client.get(path, params={**shop, "limit": 1, "cursor": page["next_cursor"]}, headers=admin).json()
    assert [page["items"][0]["id"], rest["items"][0]["id"], rest["next_cursor"]] == [second["id"], first["id"], None]
    of_b = client.get(path, params={"shop_id": str(world.shop_b)}, headers=admin).json()
    assert of_b == {"items": [], "next_cursor": None}
    for params, field in (({"limit": 0}, "limit"), ({"limit": 101}, "limit"), ({"cursor": "x"}, "cursor")):
        refused = client.get(path, params=params, headers=admin)
        assert (refused.status_code, list(refused.json()["error"]["fields"])) == (422, [field])


def test_the_audit_can_be_read_by_shop_by_action_and_by_administrator(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    _open(client, admin, world)
    admin_env.clock.offset += timedelta(seconds=1)
    _customers(client, admin, world)
    second = elevate(client, admin_env, world.owner_b, make_admin(owner, admin_env, world.owner_b))
    admin_env.clock.offset += timedelta(seconds=1)
    assert _customers(client, second, world).status_code == 403

    of_shop = client.get(AUDIT, params={"shop_id": str(world.shop_a), "action": "support."}, headers=admin).json()
    assert [(item["action"], item["admin_id"]) for item in of_shop["items"]] == [
        ("support.refused", str(world.owner_b)),
        ("support.customers_listed", str(world.admin)),
        ("support.opened", str(world.admin)),
    ]
    mine = client.get(AUDIT, params={"shop_id": str(world.shop_a), "admin_id": str(world.admin)}, headers=admin).json()
    assert {item["admin_id"] for item in mine["items"]} == {str(world.admin)}
    assert len(mine["items"]) == 2
    bad = client.get(AUDIT, params={"admin_id": "not-a-uuid"}, headers=admin)
    assert (bad.status_code, list(bad.json()["error"]["fields"])) == (422, ["admin_id"])


# --- erasure --------------------------------------------------------------------------------------------


def test_when_a_shop_is_erased_its_support_accesses_go_with_it(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str]
) -> None:
    opened = _open(client, admin, world).json()
    stored = owner.execute(
        "SELECT response->'body'->>'id' FROM admin_request_key WHERE about_shop = %s", (world.shop_a,)
    ).fetchall()
    assert stored == [(opened["id"],)], "the stored answer is kept under its shop"
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() - interval '1 minute' WHERE id = %s",
        (world.shop_a,),
    )
    assert owner.execute("SELECT erase_shop(%s)", (world.shop_a,)).fetchone() == (True,)

    assert owner.execute("SELECT count(*) FROM support_access WHERE shop_id = %s", (world.shop_a,)).fetchone() == (0,)
    assert _customers(client, admin, world).status_code == 403
    listed = client.get(f"{ADMIN_API}/support-access", params={"shop_id": str(world.shop_a)}, headers=admin)
    assert listed.json()["items"] == []
    kept = owner.execute("SELECT count(*) FROM admin_request_key WHERE about_shop = %s", (world.shop_a,)).fetchone()
    assert kept == (0,), "the stored answer of the opening is erased with the shop"
    # The audit keeps that it happened, by the shop's identifier, with the administrator's reason.
    audit = owner.execute(
        "SELECT action, reason, detail FROM admin_audit WHERE target_shop = %s AND action = 'support.opened'",
        (world.shop_a,),
    ).fetchall()
    assert [(row[0], row[2]["access_id"]) for row in audit] == [("support.opened", opened["id"])]
    assert "Shop A" not in str(audit)


# --- security events ------------------------------------------------------------------------------------

METRICS_TOKEN = "a-metrics-token-for-tests"


@pytest.fixture
def observed(app_database_url: str, admin_database_url: str, admin_env: AdminEnv) -> Iterator[TestClient]:
    database, admin_database = Database(app_database_url), Database(admin_database_url)
    access = AdminAccess(
        admin_database, allowed_tg_ids=admin_env.allowed, cipher=admin_env.box, now=admin_env.clock.now
    )
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        admin=access,
        admin_storage=admin_database,
        authenticator=HeaderAuthenticator(),
        now=admin_env.clock.now,
        metrics_token=METRICS_TOKEN,
    )
    with TestClient(app) as test_client:
        yield test_client
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]
        test_client.portal.call(admin_database.dispose)  # type: ignore[union-attr]


def _events(client: TestClient, kind: str) -> int:
    text = client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"}).text
    for line in text.splitlines():
        if line.startswith(f'qd_security_events_total{{kind="{kind}"}}'):
            return int(line.split()[-1])
    return 0


def test_opening_and_a_refused_look_are_security_events(
    observed: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="qarz.request")
    admin = elevate(observed, admin_env, world.admin, make_admin(owner, admin_env, world.admin))

    assert _customers(observed, admin, world).status_code == 403
    assert (_events(observed, "admin_without_support_access"), _events(observed, "support_access_opened")) == (1, 0)
    # A request that opens nothing is no "opened" event.
    assert observed.post(_admin_path(world), json={"reason": "ab"}, headers={**admin, **_key()}).status_code == 422
    assert _events(observed, "support_access_opened") == 0

    assert _open(observed, admin, world).status_code == 201
    assert _events(observed, "support_access_opened") == 1
    assert _open(observed, admin, world).status_code == 409
    assert _events(observed, "support_access_opened") == 1
    # A look that is allowed is not an event.
    assert _customers(observed, admin, world).status_code == 200
    assert _events(observed, "admin_without_support_access") == 1

    events = [record.__dict__ for record in caplog.records if record.getMessage() == "security"]
    assert [(event["kind"], str(event["user_id"]), str(event["shop_id"])) for event in events] == [
        ("admin_without_support_access", str(world.admin), str(world.shop_a)),
        ("support_access_opened", str(world.admin), str(world.shop_a)),
    ]
    # Identifiers only: neither the reason nor anything of the shop's customers.
    logged = " ".join(str(record.__dict__) for record in caplog.records)
    assert "Egasi yordam" not in logged
    assert "Ali" not in logged
