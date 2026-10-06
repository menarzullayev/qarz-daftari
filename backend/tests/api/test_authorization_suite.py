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
    """A valid request for a shop operation, addressed to the given shop using shop A's resources."""

    method: str
    path: Callable[[World, uuid.UUID], str]
    json: dict[str, Any] | None = None
    changes_data: bool = False
    ok_status: int = 200


@dataclass(frozen=True)
class PlainCall:
    method: str
    path: str
    json: dict[str, Any] | None = None
    ok_status: int = 200
    needs_key: bool = False
    returns_own_id: bool = True


STAFF_BASE = "/api/v1/shops/{shop}/staff"

CALLS: dict[str, Call] = {
    "shop.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}"),
    "shop.update": Call("PATCH", lambda w, shop: f"/api/v1/shops/{shop}", {"name": "Renamed"}, changes_data=True),
    "staff.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/staff"),
    "staff.invite": Call(
        "POST", lambda w, shop: f"/api/v1/shops/{shop}/staff/invitations", {"role": "seller"}, True, ok_status=201
    ),
    "staff.invitations.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/staff/invitations"),
    "staff.invitations.cancel": Call(
        "DELETE", lambda w, shop: f"/api/v1/shops/{shop}/staff/invitations/{w.invitation_a}", None, True
    ),
    "staff.update": Call(
        "PATCH", lambda w, shop: f"/api/v1/shops/{shop}/staff/{w.seller_a_membership}", {"role": "manager"}, True
    ),
    "staff.remove": Call("DELETE", lambda w, shop: f"/api/v1/shops/{shop}/staff/{w.seller_a_membership}", None, True),
}

# Written by hand from REQ-033 and the specification's authorization table; deliberately not derived
# from the code under test.
ALLOWED_ROLES: dict[str, set[Role]] = {
    "shop.read": {Role.MANAGER, Role.OWNER},
    "shop.update": {Role.OWNER},
    "staff.list": {Role.OWNER},
    "staff.invite": {Role.OWNER},
    "staff.invitations.list": {Role.OWNER},
    "staff.invitations.cancel": {Role.OWNER},
    "staff.update": {Role.OWNER},
    "staff.remove": {Role.OWNER},
}

SELF_CALLS: dict[str, PlainCall] = {
    "me.read": PlainCall("GET", "/api/v1/me"),
    "me.update": PlainCall("PATCH", "/api/v1/me", {"lang": "ru"}),
    "auth.sign_out": PlainCall("POST", "/api/v1/auth/sign-out", ok_status=204),
    "shop.create": PlainCall(
        "POST", "/api/v1/shops", {"name": "My shop", "lang": "uz"}, 201, needs_key=True, returns_own_id=False
    ),
    # An unknown token: for any signed-in user the invitation simply does not exist.
    "staff.invitations.accept": PlainCall(
        "POST", "/api/v1/staff-invitations/accept", {"token": "unknown-token-0123456789abcdef"}, 404
    ),
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


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"suite-{uuid.uuid4().hex}"}


def _invoke(client: TestClient, world: World, call: Call, shop: uuid.UUID, headers: dict[str, str]) -> Any:
    if call.changes_data:
        headers = {**headers, **_key()}
    return client.request(call.method, call.path(world, shop), json=call.json, headers=headers)


def _snapshot(owner: psycopg.Connection, shop: uuid.UUID) -> tuple[Any, ...]:
    """Everything a refused call could have changed in a shop."""
    return (
        owner.execute("SELECT name, lang, default_promise_days, status FROM shop WHERE id = %s", (shop,)).fetchone(),
        owner.execute(
            "SELECT id, user_id, role, status FROM membership WHERE shop_id = %s ORDER BY id", (shop,)
        ).fetchall(),
        owner.execute(
            "SELECT token_hash, status, role FROM invitation WHERE shop_id = %s ORDER BY token_hash", (shop,)
        ).fetchall(),
        owner.execute("SELECT count(*) FROM activity WHERE shop_id = %s", (shop,)).fetchone(),
        owner.execute("SELECT count(*) FROM request_key WHERE shop_id = %s", (shop,)).fetchone(),
    )


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


def test_every_write_route_is_marked_as_changing_data(client: TestClient) -> None:
    """So that the "a refused call must change nothing" checks are not skipped for a new write."""
    for route in client.app.routes:  # type: ignore[attr-defined]
        if isinstance(route, APIRoute) and route.name in CALLS:
            writes = bool(route.methods - {"GET", "HEAD"})
            assert CALLS[route.name].changes_data is writes, route.name


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
    response = _invoke(client, world, call, world.shop_a, as_user(getattr(world, caller)))

    if role in ALLOWED_ROLES[op_name]:
        assert response.status_code == call.ok_status, response.text
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
    response = _invoke(client, world, call, world.shop_a, as_user(getattr(world, caller)))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.json()["error"]["fields"] == {}
    assert _snapshot(owner, world.shop_a) == before


@pytest.mark.parametrize("op_name", SHOP_OPS)
def test_a_member_of_one_shop_cannot_reach_another(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str
) -> None:
    """owner_b, the most privileged caller in shop B, addresses shop A; then shop B using A's resources."""
    call = CALLS[op_name]
    before_a, before_b = _snapshot(owner, world.shop_a), _snapshot(owner, world.shop_b)

    into_a = _invoke(client, world, call, world.shop_a, as_user(world.owner_b))
    assert into_a.status_code == 404, into_a.text

    # Through their own shop, naming a member or invitation that belongs to shop A.
    through_b = _invoke(client, world, call, world.shop_b, as_user(world.owner_b))
    if call.path(world, world.shop_b) != call.path(world, world.shop_a).replace(str(world.shop_a), str(world.shop_b)):
        raise AssertionError("the path must differ only by the shop identifier")
    uses_foreign_resource = str(world.seller_a_membership) in call.path(world, world.shop_b) or (
        world.invitation_a in call.path(world, world.shop_b)
    )
    if uses_foreign_resource:
        assert through_b.status_code == 404, through_b.text
        assert _snapshot(owner, world.shop_b)[:3] == before_b[:3], "shop B's own data must be untouched"
    assert _snapshot(owner, world.shop_a) == before_a, "shop A must be untouched either way"


@pytest.mark.parametrize("op_name", SHOP_OPS)
def test_refusals_are_indistinguishable_from_a_missing_shop(client: TestClient, world: World, op_name: str) -> None:
    call = CALLS[op_name]
    outsider = _invoke(client, world, call, world.shop_a, as_user(world.owner_b))
    missing = _invoke(client, world, call, uuid.uuid4(), as_user(world.owner_b))
    malformed = client.request(
        call.method,
        call.path(world, world.shop_a).replace(str(world.shop_a), "not-a-uuid"),
        json=call.json,
        headers={**as_user(world.owner_b), **_key()},
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
    response = _invoke(client, world, call, world.shop_a, headers)
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert _snapshot(owner, world.shop_a) == before


@pytest.mark.parametrize("op_name", [name for name in SHOP_OPS if CALLS[name].changes_data])
def test_writes_need_an_idempotency_key_but_outsiders_still_see_not_found(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str
) -> None:
    call = CALLS[op_name]
    before = _snapshot(owner, world.shop_a)
    path = call.path(world, world.shop_a)
    member = client.request(call.method, path, json=call.json, headers=as_user(world.owner_a))
    assert member.status_code == 422, member.text
    assert "Idempotency-Key" in member.json()["error"]["fields"]
    outsider = client.request(call.method, path, json=call.json, headers=as_user(world.owner_b))
    assert outsider.status_code == 404
    assert _snapshot(owner, world.shop_a) == before


# --- self operations: need a signed-in user, and act only on that user --------------------------------


def _users(owner: psycopg.Connection, except_for: uuid.UUID | None = None) -> list[Any]:
    return owner.execute(
        "SELECT id, lang FROM app_user WHERE id IS DISTINCT FROM %s ORDER BY id", (except_for,)
    ).fetchall()


@pytest.mark.parametrize("op_name", SELF_OPS)
@pytest.mark.parametrize("headers", NO_CREDENTIALS, ids=["no credentials", "bad credentials"])
def test_unauthenticated_self_calls_are_refused(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, headers: dict[str, str]
) -> None:
    call = SELF_CALLS[op_name]
    users, shops = _users(owner), owner.execute("SELECT count(*) FROM shop").fetchone()
    extra = _key() if call.needs_key else {}
    response = client.request(call.method, call.path, json=call.json, headers={**headers, **extra})
    assert response.status_code == 401, response.text
    assert _users(owner) == users
    assert owner.execute("SELECT count(*) FROM shop").fetchone() == shops


@pytest.mark.parametrize("op_name", SELF_OPS)
@pytest.mark.parametrize("caller", ["owner_a", "seller_a", "customer_of_a", "stranger"])
def test_any_signed_in_user_may_act_on_their_own_account_only(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, caller: str
) -> None:
    call = SELF_CALLS[op_name]
    me = getattr(world, caller)
    others = _users(owner, except_for=me)
    shops = (_snapshot(owner, world.shop_a), _snapshot(owner, world.shop_b))
    extra = _key() if call.needs_key else {}
    response = client.request(call.method, call.path, json=call.json, headers={**as_user(me), **extra})
    assert response.status_code == call.ok_status, response.text
    if response.content and call.returns_own_id and response.status_code < 300:
        assert response.json()["id"] == str(me)
    assert _users(owner, except_for=me) == others, "nobody else's account may change"
    assert (_snapshot(owner, world.shop_a), _snapshot(owner, world.shop_b)) == shops, "no existing shop may change"


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
