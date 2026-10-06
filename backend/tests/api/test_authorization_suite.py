"""The authorization and tenant suite (NFR-013, launch criterion 5). Blocking.

Every registered operation is exercised according to its scope:

- shop operations: as each role, as a suspended member, as the owner of another shop, as a customer, as a
  platform administrator without support access, as a stranger, and without signing in;
- self operations: without signing in, and as any signed-in user;
- public operations: without signing in, with data that is not validly signed.

An operation that is registered but not described here fails the suite, and so does an API route that is
not bound to a registered operation, so nothing can be added without being checked.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from qarz.application.operations import all_operations
from qarz.domain.access import Role, lowest_role_with

from .conftest import World, as_user

pytestmark = pytest.mark.db

ROLE_ORDER = [Role.SELLER, Role.MANAGER, Role.OWNER]


@dataclass(frozen=True)
class Call:
    method: str
    path: Callable[[uuid.UUID], str]
    json: dict[str, Any] | None = None
    changes_data: bool = False


@dataclass(frozen=True)
class PlainCall:
    method: str
    path: str
    json: dict[str, Any] | None = None
    ok_status: int = 200


# How to invoke each shop operation with a valid request.
CALLS: dict[str, Call] = {
    "shop.read": Call("GET", lambda shop: f"/api/v1/shops/{shop}"),
    "shop.update": Call("PATCH", lambda shop: f"/api/v1/shops/{shop}", {"name": "Renamed"}, changes_data=True),
}

# Written by hand from REQ-033 and the specification's authorization table; deliberately not derived
# from the code under test.
ALLOWED_ROLES: dict[str, set[Role]] = {
    "shop.read": {Role.MANAGER, Role.OWNER},
    "shop.update": {Role.OWNER},
}

SELF_CALLS: dict[str, PlainCall] = {
    "me.read": PlainCall("GET", "/api/v1/me"),
    "me.update": PlainCall("PATCH", "/api/v1/me", {"lang": "ru"}),
    "auth.sign_out": PlainCall("POST", "/api/v1/auth/sign-out", ok_status=204),
}

# Requests that are well formed but not signed by Telegram.
PUBLIC_CALLS: dict[str, PlainCall] = {
    "auth.telegram_webapp": PlainCall("POST", "/api/v1/auth/telegram-webapp", {"init_data": "user=%7B%7D&hash=00"}),
    "auth.telegram_login": PlainCall("POST", "/api/v1/auth/telegram-login", {"id": 1, "auth_date": 1, "hash": "00"}),
}

BY_SCOPE = {
    scope: sorted(op.name for op in all_operations() if op.scope == scope) for scope in ("shop", "self", "public")
}
SHOP_OPS, SELF_OPS, PUBLIC_OPS = BY_SCOPE["shop"], BY_SCOPE["self"], BY_SCOPE["public"]
STAFF = [("owner_a", Role.OWNER), ("manager_a", Role.MANAGER), ("seller_a", Role.SELLER)]
OUTSIDERS = ["suspended_a", "owner_b", "customer_of_a", "admin", "stranger"]
NO_CREDENTIALS = [{}, {"X-Test-User": "not-a-uuid"}]


def _invoke(client: TestClient, call: Call, shop: uuid.UUID, headers: dict[str, str]) -> Any:
    if call.changes_data:
        headers = {**headers, "Idempotency-Key": f"suite-{uuid.uuid4().hex}"}
    return client.request(call.method, call.path(shop), json=call.json, headers=headers)


def _snapshot(owner: psycopg.Connection, shop: uuid.UUID) -> tuple[Any, ...]:
    shop_row = owner.execute("SELECT name, lang, default_promise_days FROM shop WHERE id = %s", (shop,)).fetchone()
    activity = owner.execute("SELECT count(*) FROM activity WHERE shop_id = %s", (shop,)).fetchone()
    return (shop_row, activity)


# --- the suite covers everything --------------------------------------------------------------------


def test_every_operation_is_described_in_the_suite() -> None:
    assert set(CALLS) == set(SHOP_OPS), "add the new shop operation to CALLS"
    assert set(ALLOWED_ROLES) == set(SHOP_OPS), "add the new shop operation to ALLOWED_ROLES"
    assert set(SELF_CALLS) == set(SELF_OPS), "add the new self operation to SELF_CALLS"
    assert set(PUBLIC_CALLS) == set(PUBLIC_OPS), "add the new public operation to PUBLIC_CALLS"
    assert SHOP_OPS and SELF_OPS and PUBLIC_OPS


def test_every_api_route_is_a_registered_operation(client: TestClient) -> None:
    api_routes = [r for r in client.app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/")]  # type: ignore[attr-defined]
    names = sorted(route.name for route in api_routes)
    assert names == sorted(op.name for op in all_operations()), (
        "every /api/ route must be bound to exactly one registered operation"
    )


def test_the_code_agrees_with_the_hand_written_table() -> None:
    for op in all_operations():
        if op.scope != "shop":
            assert op.capability is None
            continue
        assert op.capability is not None
        assert lowest_role_with(op.capability) == min(ALLOWED_ROLES[op.name], key=ROLE_ORDER.index)


# --- shop operations: staff are allowed or refused by role ------------------------------------------


@pytest.mark.parametrize("op_name", SHOP_OPS)
@pytest.mark.parametrize(("caller", "role"), STAFF)
def test_staff_are_allowed_or_refused_by_role(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, caller: str, role: Role
) -> None:
    call = CALLS[op_name]
    before = _snapshot(owner, world.shop_a)
    response = _invoke(client, call, world.shop_a, as_user(getattr(world, caller)))

    if role in ALLOWED_ROLES[op_name]:
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 403, response.text
        error = response.json()["error"]
        assert error["code"] == "FORBIDDEN_ROLE"
        assert error["fields"] == {"needed_role": min(ALLOWED_ROLES[op_name], key=ROLE_ORDER.index).value}
        assert _snapshot(owner, world.shop_a) == before, "a refused call must change nothing"


# --- shop operations: for everyone else the shop does not exist ---------------------------------------


@pytest.mark.parametrize("op_name", SHOP_OPS)
@pytest.mark.parametrize("caller", OUTSIDERS)
def test_outsiders_get_not_found_and_change_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, caller: str
) -> None:
    call = CALLS[op_name]
    before = _snapshot(owner, world.shop_a)
    response = _invoke(client, call, world.shop_a, as_user(getattr(world, caller)))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.json()["error"]["fields"] == {}
    assert _snapshot(owner, world.shop_a) == before


@pytest.mark.parametrize("op_name", SHOP_OPS)
def test_a_member_of_one_shop_cannot_reach_another(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str
) -> None:
    call = CALLS[op_name]
    before = _snapshot(owner, world.shop_b)
    # owner_a is the most privileged caller there is in shop A, and nobody in shop B
    response = _invoke(client, call, world.shop_b, as_user(world.owner_a))
    assert response.status_code == 404, response.text
    assert _snapshot(owner, world.shop_b) == before


@pytest.mark.parametrize("op_name", SHOP_OPS)
def test_refusals_are_indistinguishable_from_a_missing_shop(client: TestClient, world: World, op_name: str) -> None:
    call = CALLS[op_name]
    outsider = _invoke(client, call, world.shop_a, as_user(world.owner_b))
    missing = _invoke(client, call, uuid.uuid4(), as_user(world.owner_b))
    malformed = client.request(
        call.method,
        "/api/v1/shops/not-a-uuid",
        json=call.json,
        headers={**as_user(world.owner_b), "Idempotency-Key": "suite-malformed-path"},
    )
    assert outsider.status_code == missing.status_code == malformed.status_code == 404
    assert outsider.json() == missing.json() == malformed.json()


@pytest.mark.parametrize("op_name", SHOP_OPS)
@pytest.mark.parametrize("headers", NO_CREDENTIALS, ids=["no credentials", "bad credentials"])
def test_unauthenticated_shop_calls_are_refused(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, headers: dict[str, str]
) -> None:
    call = CALLS[op_name]
    before = _snapshot(owner, world.shop_a)
    response = _invoke(client, call, world.shop_a, headers)
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert _snapshot(owner, world.shop_a) == before


# --- self operations: need a signed-in user, and act only on that user --------------------------------


@pytest.mark.parametrize("op_name", SELF_OPS)
@pytest.mark.parametrize("headers", NO_CREDENTIALS, ids=["no credentials", "bad credentials"])
def test_unauthenticated_self_calls_are_refused(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, headers: dict[str, str]
) -> None:
    call = SELF_CALLS[op_name]
    languages = owner.execute("SELECT id, lang FROM app_user ORDER BY id").fetchall()
    response = client.request(call.method, call.path, json=call.json, headers=headers)
    assert response.status_code == 401, response.text
    assert owner.execute("SELECT id, lang FROM app_user ORDER BY id").fetchall() == languages


@pytest.mark.parametrize("op_name", SELF_OPS)
@pytest.mark.parametrize("caller", ["owner_a", "seller_a", "customer_of_a", "stranger"])
def test_any_signed_in_user_may_act_on_their_own_account_only(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, caller: str
) -> None:
    call = SELF_CALLS[op_name]
    me = getattr(world, caller)
    others = owner.execute("SELECT id, lang FROM app_user WHERE id <> %s ORDER BY id", (me,)).fetchall()
    response = client.request(call.method, call.path, json=call.json, headers=as_user(me))
    assert response.status_code == call.ok_status, response.text
    if response.content:
        assert response.json()["id"] == str(me)
    assert owner.execute("SELECT id, lang FROM app_user WHERE id <> %s ORDER BY id", (me,)).fetchall() == others


# --- public operations: callable without a session, but only Telegram's signature signs anyone in -----


@pytest.mark.parametrize("op_name", PUBLIC_OPS)
def test_unsigned_data_signs_nobody_in(client: TestClient, owner: psycopg.Connection, op_name: str) -> None:
    call = PUBLIC_CALLS[op_name]
    sessions = owner.execute("SELECT count(*) FROM user_session").fetchone()
    users = owner.execute("SELECT count(*) FROM app_user").fetchone()
    response = client.request(call.method, call.path, json=call.json)
    assert response.status_code == 401, response.text
    assert response.json()["error"] == {"code": "UNAUTHENTICATED", "message": "Avval tizimga kiring.", "fields": {}}
    assert "set-cookie" not in response.headers
    assert owner.execute("SELECT count(*) FROM user_session").fetchone() == sessions
    assert owner.execute("SELECT count(*) FROM app_user").fetchone() == users
