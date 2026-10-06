"""The authorization and tenant suite (NFR-013, launch criterion 5). Blocking.

Every registered staff operation is attempted as each role, as a suspended member, as the owner of another
shop, as a customer, as a platform administrator without support access, as a stranger, and without
signing in. An operation that is registered but not described here fails the suite, so nothing can be
added without being checked.
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


@dataclass(frozen=True)
class Call:
    method: str
    path: Callable[[uuid.UUID], str]
    json: dict[str, Any] | None = None
    changes_data: bool = False


# How to invoke each operation with a valid request.
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

OPERATION_NAMES = sorted(op.name for op in all_operations())
STAFF = [("owner_a", Role.OWNER), ("manager_a", Role.MANAGER), ("seller_a", Role.SELLER)]
OUTSIDERS = ["suspended_a", "owner_b", "customer_of_a", "admin", "stranger"]


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
    assert set(CALLS) == set(OPERATION_NAMES), "add the new operation to CALLS"
    assert set(ALLOWED_ROLES) == set(OPERATION_NAMES), "add the new operation to ALLOWED_ROLES"


def test_every_api_route_is_a_registered_operation(client: TestClient) -> None:
    api_routes = [r for r in client.app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/")]  # type: ignore[attr-defined]
    names = [route.name for route in api_routes]
    assert sorted(names) == OPERATION_NAMES, "every /api/ route must be bound to exactly one registered operation"


def test_the_code_agrees_with_the_hand_written_table() -> None:
    for op in all_operations():
        allowed = ALLOWED_ROLES[op.name]
        assert lowest_role_with(op.capability) == min(allowed, key=[Role.SELLER, Role.MANAGER, Role.OWNER].index)


# --- staff of the shop: allowed or refused by role --------------------------------------------------


@pytest.mark.parametrize("op_name", OPERATION_NAMES)
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
        needed = min(ALLOWED_ROLES[op_name], key=[Role.SELLER, Role.MANAGER, Role.OWNER].index)
        assert error["fields"] == {"needed_role": needed.value}
        assert _snapshot(owner, world.shop_a) == before, "a refused call must change nothing"


# --- everyone else: the shop does not exist for them ------------------------------------------------


@pytest.mark.parametrize("op_name", OPERATION_NAMES)
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


@pytest.mark.parametrize("op_name", OPERATION_NAMES)
def test_a_member_of_one_shop_cannot_reach_another(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str
) -> None:
    call = CALLS[op_name]
    before = _snapshot(owner, world.shop_b)
    # owner_a is the most privileged caller there is in shop A, and nobody in shop B
    response = _invoke(client, call, world.shop_b, as_user(world.owner_a))
    assert response.status_code == 404, response.text
    assert _snapshot(owner, world.shop_b) == before


@pytest.mark.parametrize("op_name", OPERATION_NAMES)
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


# --- not signed in -----------------------------------------------------------------------------------


@pytest.mark.parametrize("op_name", OPERATION_NAMES)
@pytest.mark.parametrize("headers", [{}, {"X-Test-User": "not-a-uuid"}], ids=["no credentials", "bad credentials"])
def test_unauthenticated_calls_are_refused(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, headers: dict[str, str]
) -> None:
    call = CALLS[op_name]
    before = _snapshot(owner, world.shop_a)
    response = _invoke(client, call, world.shop_a, headers)
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert _snapshot(owner, world.shop_a) == before
