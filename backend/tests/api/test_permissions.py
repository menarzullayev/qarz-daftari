"""Separate permissions per member of staff, through the API and the bot (expansion module G).

Three parts:

- the matrix: every shop operation, as every role, with nothing changed, with its permissions granted and
  with them denied. With the switch on the API answers as the catalogue says; with it off it answers as
  the role table always did, whatever is stored for the member;
- the owner's three routes: they do not exist while the switch is off, they validate, they are
  idempotent, and each change is in the activity log with what was there before and after;
- the ways someone could try to get more than they were given, each refused.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application import texts_en, texts_kaa, texts_tg
from qarz.application.chat_texts import say
from qarz.domain import permissions
from qarz.domain.access import Role
from qarz.domain.languages import LANGUAGES
from qarz.domain.promise import tashkent_date

from .conftest import World, as_user, set_overrides, switch_permissions_on
from .test_authorization_suite import (
    ALLOWED_ROLES,
    CALLS,
    ROLE_ORDER,
    SHOP_OPS,
    _invoke,
    _permissions_on,
    _prepare,
    _snapshot,
)
from .test_chat import chat_of, entries
from .test_customer_account import link_of
from .test_disputes import staff_notices, tg
from .test_payment_notices import send

pytestmark = pytest.mark.db

MEMBERS = {"manager_a": Role.MANAGER, "seller_a": Role.SELLER}
STATES = ["none", "granted", "denied"]
OWN_ROUTES = [name for name in SHOP_OPS if CALLS[name].prepare is _permissions_on]
BEYOND = (403, "BEYOND_OWN_PERMISSIONS")

# A member who was given `staff.manage` is let through the gate and then held to their own rights: the
# suite's request for these operations changes a role, or names the seller who is calling.
HELD_BACK: dict[tuple[str, str], tuple[int, str]] = {
    ("staff.update", "manager_a"): BEYOND,  # makes the seller a manager: a role is the owner's to give
    ("staff.update", "seller_a"): BEYOND,  # the seller's own membership
    ("staff.remove", "seller_a"): BEYOND,  # the seller's own membership
}


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"perm-{uuid.uuid4().hex}"}


def membership_of(world: World, caller: str) -> uuid.UUID:
    return uuid.UUID(str(getattr(world, f"{caller}_membership")))


def movable(op_name: str) -> list[str]:
    return [p.key for p in permissions.permissions_of_operation(op_name) if not p.fixed]


def store(owner: psycopg.Connection, world: World, caller: str, op_name: str, state: str) -> None:
    keys = movable(op_name)
    set_overrides(
        owner,
        membership_of(world, caller),
        granted=keys if state == "granted" else [],
        denied=keys if state == "denied" else [],
    )


def expected(op_name: str, role: Role, state: str) -> bool:
    """Written from the rules, not from the code: the role decides unless the permission was moved."""
    if not movable(op_name) or state == "none":
        return role in ALLOWED_ROLES[op_name]
    return state == "granted"


def put(client: TestClient, world: World, caller: uuid.UUID, member: uuid.UUID, **body: Any) -> Any:
    path = f"/api/v1/shops/{world.shop_a}/staff/{member}/permissions"
    return client.put(path, json={"granted": [], "denied": [], **body}, headers={**as_user(caller), **key()})


def my_permissions(client: TestClient, world: World, caller: uuid.UUID) -> Any:
    response = client.get(f"/api/v1/shops/{world.shop_a}/permissions/mine", headers=as_user(caller))
    assert response.status_code == 200, response.text
    return response.json()["permissions"]


def record(client: TestClient, world: World, caller: uuid.UUID, kind: str, amount: int = 1000) -> Any:
    path = f"/api/v1/shops/{world.shop_a}/customers/{world.customer_a}/entries"
    return client.post(path, json={"kind": kind, "amount": amount}, headers={**as_user(caller), **key()})


def refused_for(response: Any, permission: str) -> None:
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert (error["code"], error["fields"]) == ("FORBIDDEN_PERMISSION", {"permission": permission}), response.text


# --- the matrix, switch on ------------------------------------------------------------------------------


@pytest.mark.parametrize("op_name", SHOP_OPS)
@pytest.mark.parametrize("caller", list(MEMBERS))
@pytest.mark.parametrize("state", STATES)
def test_with_the_switch_on_the_api_answers_as_the_catalogue_says(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, caller: str, state: str
) -> None:
    call = CALLS[op_name]
    switch_permissions_on(owner)
    store(owner, world, caller, op_name, state)
    _prepare(owner, world, call)
    before = _snapshot(owner, world.shop_a)
    response = _invoke(client, world, call, world.shop_a, as_user(getattr(world, caller)))

    allowed = expected(op_name, MEMBERS[caller], state)
    stated = {who: (status, code) for who, status, code in call.refused}
    if allowed and (op_name, caller) in HELD_BACK and state == "granted":
        assert (response.status_code, response.json()["error"]["code"]) == HELD_BACK[(op_name, caller)], response.text
        assert _snapshot(owner, world.shop_a) == before, "a refused call must change nothing"
    elif allowed and caller in stated:
        assert (response.status_code, response.json()["error"]["code"]) == stated[caller], response.text
    elif allowed:
        assert response.status_code == call.ok_status, response.text
    else:
        refused_for(response, permissions.permissions_of_operation(op_name)[0].key)
        assert _snapshot(owner, world.shop_a) == before, "a refused call must change nothing"


@pytest.mark.parametrize("op_name", SHOP_OPS)
def test_with_the_switch_on_the_owner_still_does_everything(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str
) -> None:
    call = CALLS[op_name]
    switch_permissions_on(owner)
    _prepare(owner, world, call)
    response = _invoke(client, world, call, world.shop_a, as_user(world.owner_a))
    stated = {who: (status, code) for who, status, code in call.refused}
    if "owner_a" in stated:
        assert (response.status_code, response.json()["error"]["code"]) == stated["owner_a"], response.text
    else:
        assert response.status_code == call.ok_status, response.text


# --- the matrix, switch off: exactly as before, whatever is stored ---------------------------------------


@pytest.mark.parametrize("op_name", [name for name in SHOP_OPS if name not in OWN_ROUTES])
@pytest.mark.parametrize("caller", list(MEMBERS))
@pytest.mark.parametrize("state", ["granted", "denied"])
def test_with_the_switch_off_stored_changes_are_ignored(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, caller: str, state: str
) -> None:
    call = CALLS[op_name]
    store(owner, world, caller, op_name, state)
    _prepare(owner, world, call)
    before = _snapshot(owner, world.shop_a)
    response = _invoke(client, world, call, world.shop_a, as_user(getattr(world, caller)))

    if MEMBERS[caller] in ALLOWED_ROLES[op_name]:
        assert response.status_code == call.ok_status, response.text
    else:
        assert response.status_code == 403, response.text
        error = response.json()["error"]
        assert error["code"] == "FORBIDDEN_ROLE"
        assert error["fields"] == {"needed_role": min(ALLOWED_ROLES[op_name], key=ROLE_ORDER.index).value}
        assert _snapshot(owner, world.shop_a) == before


@pytest.mark.parametrize("op_name", OWN_ROUTES)
@pytest.mark.parametrize("caller", ["owner_a", "manager_a", "seller_a", "owner_b", "stranger"])
def test_with_the_switch_off_the_matrix_routes_do_not_exist(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, caller: str
) -> None:
    call = CALLS[op_name]
    before = _snapshot(owner, world.shop_a)
    response = _invoke(client, world, call, world.shop_a, as_user(getattr(world, caller)))
    missing = _invoke(client, world, call, uuid.uuid4(), as_user(getattr(world, caller)))
    assert response.status_code == 404, response.text
    assert response.json() == missing.json(), "the same answer as for a shop that does not exist"
    assert _snapshot(owner, world.shop_a) == before


@pytest.mark.parametrize("value", ["false", '"true"', "1", "null"])
def test_only_the_json_true_turns_the_switch_on(
    client: TestClient, world: World, owner: psycopg.Connection, value: str
) -> None:
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('permissions_on', %s::jsonb, %s)",
        (value, str(uuid.uuid4())),
    )
    set_overrides(owner, world.seller_a_membership, granted=["reports.view"])
    assert client.get(f"/api/v1/shops/{world.shop_a}/permissions", headers=as_user(world.owner_a)).status_code == 404
    overdue = client.get(f"/api/v1/shops/{world.shop_a}/reports/overdue", headers=as_user(world.seller_a))
    assert overdue.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_with_the_switch_off_no_answer_changes_shape(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    set_overrides(owner, world.seller_a_membership, granted=["reports.view"], denied=["payments.record"])
    shops = client.get("/api/v1/me/shops", headers=as_user(world.seller_a)).json()
    assert [set(item) for item in shops["items"]] == [{"shop_id", "name", "role", "membership_id"}]
    staff = client.get(f"/api/v1/shops/{world.shop_a}/staff", headers=as_user(world.owner_a)).json()
    assert all(set(item) == {"id", "user_id", "role", "status"} for item in staff["items"])
    client.patch(
        f"/api/v1/shops/{world.shop_a}/staff/{world.seller_a_membership}",
        json={"status": "suspended"},
        headers={**as_user(world.owner_a), **key()},
    )
    activity = client.get(f"/api/v1/shops/{world.shop_a}/activity", headers=as_user(world.owner_a)).json()
    assert activity["items"]
    for item in activity["items"]:
        assert set(item) == {"id", "at", "actor_kind", "actor_id", "action", "subject_type", "subject_id"}


def test_the_bot_ignores_stored_changes_with_the_switch_off(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    set_overrides(owner, world.seller_a_membership, granted=["entries.cancel"], denied=["credits.record"])
    said = chat_of(client, owner, world.seller_a).say("Ali 1000")
    assert entries(owner, world.shop_a)[-1] == ("Ali", "credit", 1000, world.seller_a_membership)
    assert "↩️ Bekor qilish" not in said.buttons


def test_a_client_is_told_in_a_header_that_permissions_are_kept_and_only_then(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    off = client.get("/api/v1/me/shops", headers=as_user(world.seller_a))
    assert "x-qarz-permissions" not in off.headers
    switch_permissions_on(owner)
    on = client.get("/api/v1/me/shops", headers=as_user(world.seller_a))
    assert on.headers["x-qarz-permissions"] == "on"
    assert on.json() == off.json(), "the body is the same either way"


# --- the owner's routes ---------------------------------------------------------------------------------


def test_the_catalogue_is_read_by_the_owner(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    switch_permissions_on(owner)
    body = client.get(f"/api/v1/shops/{world.shop_a}/permissions", headers=as_user(world.owner_a)).json()
    listed = [item for group in body["groups"] for item in group["permissions"]]
    assert {item["key"] for item in listed} == permissions.ALL_KEYS
    assert [group["key"] for group in body["groups"]] == [group.key for group in permissions.GROUPS]
    for item in (*body["groups"], *listed):
        assert set(item["label"]) == set(LANGUAGES) and all(item["label"].values())
    by_key = {item["key"]: item for item in listed}
    assert by_key["entries.cancel"] == {
        "key": "entries.cancel",
        "label": {
            "uz": "Yozuvni bekor qilish",
            "uz-Cyrl": "Ёзувни бекор қилиш",
            "ru": "Отменять запись",
            "tg": texts_tg.PERMISSIONS["entries.cancel"],
            "kaa": texts_kaa.PERMISSIONS["entries.cancel"],
            "en": texts_en.PERMISSIONS["entries.cancel"],
        },
        "roles": ["manager", "owner"],
        "fixed": False,
    }
    assert by_key["shop.delete"]["fixed"] is True and by_key["shop.delete"]["roles"] == ["owner"]


def test_a_members_permissions_say_where_each_answer_comes_from(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    set_overrides(owner, world.seller_a_membership, granted=["reports.view"], denied=["payments.record"])
    path = f"/api/v1/shops/{world.shop_a}/staff/{world.seller_a_membership}/permissions"
    body = client.get(path, headers=as_user(world.owner_a)).json()
    assert (body["membership_id"], body["role"], body["status"]) == (str(world.seller_a_membership), "seller", "active")
    assert (body["granted"], body["denied"]) == (["reports.view"], ["payments.record"])
    items = {item["key"]: item for item in body["permissions"]}
    assert set(items) == permissions.ALL_KEYS
    assert items["reports.view"] == {
        "key": "reports.view", "allowed": True, "source": "granted", "default": False, "fixed": False
    }  # fmt: skip
    assert items["payments.record"] == {
        "key": "payments.record", "allowed": False, "source": "denied", "default": True, "fixed": False
    }  # fmt: skip
    assert items["credits.record"]["source"] == "role" and items["credits.record"]["allowed"] is True
    assert items["entries.cancel"]["source"] == "role" and items["entries.cancel"]["allowed"] is False
    assert items["shop.delete"] == {
        "key": "shop.delete", "allowed": False, "source": "role", "default": False, "fixed": True
    }  # fmt: skip


def test_the_owner_reads_as_holding_everything(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    switch_permissions_on(owner)
    path = f"/api/v1/shops/{world.shop_a}/staff/{world.owner_a_membership}/permissions"
    body = client.get(path, headers=as_user(world.owner_a)).json()
    assert all(item["allowed"] and item["source"] == "role" for item in body["permissions"])


def test_setting_takes_effect_on_the_members_next_request_both_ways(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """No cache anywhere: the request after the owner's change is already decided by it."""
    switch_permissions_on(owner)
    overdue = f"/api/v1/shops/{world.shop_a}/reports/overdue"
    seller = as_user(world.seller_a)
    refused_for(client.get(overdue, headers=seller), "reports.view")
    assert record(client, world, world.seller_a, "payment").status_code == 201

    changed = put(
        client, world, world.owner_a, world.seller_a_membership, granted=["reports.view"], denied=["payments.record"]
    )
    assert changed.status_code == 200, changed.text
    assert (changed.json()["granted"], changed.json()["denied"]) == (["reports.view"], ["payments.record"])
    assert client.get(overdue, headers=seller).status_code == 200
    refused_for(record(client, world, world.seller_a, "payment"), "payments.record")
    assert "reports.view" in my_permissions(client, world, world.seller_a)
    assert "payments.record" not in my_permissions(client, world, world.seller_a)

    reset = put(client, world, world.owner_a, world.seller_a_membership)
    assert (reset.json()["granted"], reset.json()["denied"]) == ([], [])
    refused_for(client.get(overdue, headers=seller), "reports.view")
    assert record(client, world, world.seller_a, "payment").status_code == 201
    assert my_permissions(client, world, world.seller_a) == sorted(permissions.role_defaults(Role.SELLER))


def test_every_change_is_in_the_activity_log_with_before_and_after(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    put(client, world, world.owner_a, world.seller_a_membership, granted=["reports.view"])
    put(client, world, world.owner_a, world.seller_a_membership, granted=["reports.export"], denied=["payments.record"])
    rows = owner.execute(
        "SELECT actor_kind, actor_id, subject_type, subject_id, detail FROM activity "
        "WHERE shop_id = %s AND action = 'staff.permissions_changed' ORDER BY at, id",
        (world.shop_a,),
    ).fetchall()
    who = ("staff", world.owner_a_membership, "membership", world.seller_a_membership)
    assert [row[:4] for row in rows] == [who, who]
    assert [row[4] for row in rows] == [
        {"before": {"granted": [], "denied": []}, "after": {"granted": ["reports.view"], "denied": []}},
        {
            "before": {"granted": ["reports.view"], "denied": []},
            "after": {"granted": ["reports.export"], "denied": ["payments.record"]},
        },
    ]
    listed = client.get(
        f"/api/v1/shops/{world.shop_a}/activity?action=staff.permissions_changed", headers=as_user(world.owner_a)
    ).json()["items"]
    assert [item["detail"] for item in listed] == [rows[1][4], rows[0][4]], "newest first, with what changed"


def test_setting_is_idempotent(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    switch_permissions_on(owner)
    path = f"/api/v1/shops/{world.shop_a}/staff/{world.seller_a_membership}/permissions"
    headers = {**as_user(world.owner_a), **key()}
    body = {"granted": ["reports.view"], "denied": []}
    first = client.put(path, json=body, headers=headers)
    again = client.put(path, json=body, headers=headers)
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    # The same matrix under a new key: nothing changes, and nothing is logged a second time.
    assert client.put(path, json=body, headers={**as_user(world.owner_a), **key()}).json() == first.json()
    logged = owner.execute(
        "SELECT count(*) FROM activity WHERE shop_id = %s AND action = 'staff.permissions_changed'", (world.shop_a,)
    ).fetchone()
    assert logged == (1,)
    # The same key for a different matrix is refused, as for every write.
    other = client.put(path, json={"granted": [], "denied": ["payments.record"]}, headers=headers)
    assert (other.status_code, other.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")


def test_only_real_changes_are_stored(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    switch_permissions_on(owner)
    response = put(
        client,
        world,
        world.owner_a,
        world.seller_a_membership,
        granted=["ledger.view", "reports.view"],  # the seller's role already gives ledger.view
        denied=["entries.cancel"],  # and does not give entries.cancel
    )
    assert (response.json()["granted"], response.json()["denied"]) == (["reports.view"], [])
    stored = owner.execute(
        "SELECT permissions_granted, permissions_denied FROM membership WHERE id = %s", (world.seller_a_membership,)
    ).fetchone()
    assert stored == (["reports.view"], [])


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"granted": ["cash.record"]}, "granted"),
        ({"denied": ["no.such.permission"]}, "denied"),
        ({"granted": ["permissions.manage"]}, "granted"),
        ({"granted": ["shop.delete"]}, "granted"),
        ({"granted": ["ownership.transfer"]}, "granted"),
        ({"granted": ["subscription.manage"]}, "granted"),
        ({"granted": ["support.manage"]}, "granted"),
        ({"granted": ["ownership.receive"]}, "granted"),
        ({"denied": ["ownership.receive"]}, "denied"),
        ({"granted": ["reports.view", "reports.view"]}, "granted"),
        ({"granted": ["reports.view"], "denied": ["reports.view"]}, "denied"),
    ],
)
def test_a_matrix_that_cannot_be_stored_is_refused_and_changes_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, body: dict[str, Any], field: str
) -> None:
    switch_permissions_on(owner)
    before = _snapshot(owner, world.shop_a)
    response = put(client, world, world.owner_a, world.seller_a_membership, **body)
    assert response.status_code == 422, response.text
    assert field in response.json()["error"]["fields"]
    assert _snapshot(owner, world.shop_a) == before


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"granted": ["reports.view"]},
        {"granted": "reports.view", "denied": []},
        {"granted": [1], "denied": []},
        {"granted": [""], "denied": []},
        {"granted": [], "denied": [], "role": "owner"},
        {"granted": [f"reports.view{n}" for n in range(65)], "denied": []},
    ],
)
def test_a_malformed_matrix_is_refused(
    client: TestClient, world: World, owner: psycopg.Connection, body: dict[str, Any]
) -> None:
    switch_permissions_on(owner)
    path = f"/api/v1/shops/{world.shop_a}/staff/{world.seller_a_membership}/permissions"
    before = _snapshot(owner, world.shop_a)
    assert client.put(path, json=body, headers={**as_user(world.owner_a), **key()}).status_code == 422
    assert _snapshot(owner, world.shop_a) == before


def test_the_owners_own_permissions_cannot_be_changed(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    before = _snapshot(owner, world.shop_a)
    response = put(client, world, world.owner_a, world.owner_a_membership, denied=["reports.view"])
    assert (response.status_code, response.json()["error"]["code"]) == (409, "OWNER_MEMBERSHIP_FIXED")
    assert _snapshot(owner, world.shop_a) == before


def test_a_member_of_another_shop_or_a_removed_one_is_not_found(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    other = owner.execute("SELECT id FROM membership WHERE shop_id = %s", (world.shop_b,)).fetchone()
    assert other is not None
    owner.execute("UPDATE membership SET status = 'removed' WHERE id = %s", (world.manager_a_membership,))
    for member in (other[0], world.manager_a_membership, uuid.uuid4()):
        assert put(client, world, world.owner_a, member, granted=["reports.view"]).status_code == 404
        path = f"/api/v1/shops/{world.shop_a}/staff/{member}/permissions"
        assert client.get(path, headers=as_user(world.owner_a)).status_code == 404


def test_what_the_client_is_told_is_what_the_server_allows(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    set_overrides(owner, world.manager_a_membership, denied=["entries.cancel"])
    assert my_permissions(client, world, world.owner_a) == sorted(permissions.ALL_KEYS)
    assert my_permissions(client, world, world.manager_a) == sorted(
        permissions.role_defaults(Role.MANAGER) - {"entries.cancel"}
    )
    assert my_permissions(client, world, world.seller_a) == sorted(permissions.role_defaults(Role.SELLER))
    mine = client.get(f"/api/v1/shops/{world.shop_a}/permissions/mine", headers=as_user(world.manager_a)).json()
    assert (mine["role"], mine["membership_id"]) == ("manager", str(world.manager_a_membership))
    # Someone who was denied everything that can be denied still learns that.
    everything = sorted(permissions.ALL_KEYS - permissions.FIXED_KEYS)
    set_overrides(owner, world.seller_a_membership, denied=everything)
    assert my_permissions(client, world, world.seller_a) == ["membership.own"]
    # A suspended member, and a member of another shop, learn nothing.
    for outsider in (world.suspended_a, world.owner_b, world.stranger):
        path = f"/api/v1/shops/{world.shop_a}/permissions/mine"
        assert client.get(path, headers=as_user(outsider)).status_code == 404


# --- one operation, two permissions; and the permissions asked for inside a service ---------------------


def test_a_credit_sale_and_a_payment_are_separate_permissions(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    set_overrides(owner, world.seller_a_membership, denied=["credits.record"])
    before = entries(owner, world.shop_a)
    refused_for(record(client, world, world.seller_a, "credit"), "credits.record")
    assert entries(owner, world.shop_a) == before
    assert record(client, world, world.seller_a, "payment").status_code == 201

    set_overrides(owner, world.seller_a_membership, denied=["payments.record"])
    before = entries(owner, world.shop_a)
    refused_for(record(client, world, world.seller_a, "payment"), "payments.record")
    assert entries(owner, world.shop_a) == before
    assert record(client, world, world.seller_a, "credit").status_code == 201

    set_overrides(owner, world.seller_a_membership, denied=["payments.record", "credits.record"])
    refused_for(record(client, world, world.seller_a, "credit"), "credits.record")
    refused_for(record(client, world, world.seller_a, "payment"), "credits.record")


def test_accepting_a_customers_payment_notice_needs_its_own_permission_only(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    set_overrides(owner, world.seller_a_membership, denied=["payments.record"])
    link = link_of(owner, world.customer_a)
    notice = send(client, world.customer_of_a, link, 20000).json()
    path = f"/api/v1/shops/{world.shop_a}/payment-notices/{notice['id']}/accept"
    assert client.post(path, headers={**as_user(world.seller_a), **key()}).status_code == 200


def promise_choice(client: TestClient, world: World, caller: uuid.UUID, entry: uuid.UUID) -> Any:
    chosen = (tashkent_date(datetime.now(UTC)) + timedelta(days=3)).isoformat()
    path = f"/api/v1/shops/{world.shop_a}/entries/{entry}/promise-choice"
    return client.post(path, json={"promised_date": chosen}, headers={**as_user(caller), **key()})


def test_another_members_sale_needs_its_own_permission(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    theirs = uuid.UUID(record(client, world, world.owner_a, "credit").json()["entry"]["id"])
    refused_for(promise_choice(client, world, world.seller_a, theirs), "entries.others")
    set_overrides(owner, world.manager_a_membership, denied=["entries.others"])
    refused_for(promise_choice(client, world, world.manager_a, theirs), "entries.others")
    assert owner.execute("SELECT count(*) FROM promise WHERE entry_id = %s", (theirs,)).fetchone() == (1,)

    set_overrides(owner, world.seller_a_membership, granted=["entries.others"])
    assert promise_choice(client, world, world.seller_a, theirs).status_code == 200
    # One's own sale never needs it.
    mine = uuid.UUID(record(client, world, world.manager_a, "credit").json()["entry"]["id"])
    assert promise_choice(client, world, world.manager_a, mine).status_code == 200


def test_selling_above_the_limit_needs_its_own_permission(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    owner.execute(
        "UPDATE shop SET default_credit_limit = 60000, sellers_may_exceed = false WHERE id = %s", (world.shop_a,)
    )
    over = record(client, world, world.seller_a, "credit", 45000)
    assert (over.status_code, over.json()["error"]["code"]) == (409, "LIMIT_REACHED")
    set_overrides(owner, world.manager_a_membership, denied=["entries.over_limit"])
    assert record(client, world, world.manager_a, "credit", 45000).json()["error"]["code"] == "LIMIT_REACHED"

    set_overrides(owner, world.seller_a_membership, granted=["entries.over_limit"])
    allowed = record(client, world, world.seller_a, "credit", 45000)
    assert allowed.status_code == 201 and allowed.json()["limit_warning"] == {"limit": 60000, "balance": 95000}


# --- nobody gets more than they were given ---------------------------------------------------------------


def staff_manager(owner: psycopg.Connection, world: World, caller: str = "manager_a", **more: Any) -> uuid.UUID:
    switch_permissions_on(owner)
    granted = ["staff.manage", *more.get("granted", [])]
    set_overrides(owner, membership_of(world, caller), granted=granted, denied=more.get("denied", []))
    return uuid.UUID(str(getattr(world, caller)))


def beyond(response: Any) -> None:
    assert (response.status_code, response.json()["error"]["code"]) == BEYOND, response.text


@pytest.mark.parametrize("caller", ["manager_a", "seller_a"])
def test_only_the_owner_reads_or_sets_permissions_even_with_staff_manage(
    client: TestClient, world: World, owner: psycopg.Connection, caller: str
) -> None:
    user = staff_manager(owner, world, caller)
    before = _snapshot(owner, world.shop_a)
    for member in (world.seller_a_membership, world.manager_a_membership):
        refused_for(put(client, world, user, member, granted=["reports.view"]), "permissions.manage")
        path = f"/api/v1/shops/{world.shop_a}/staff/{member}/permissions"
        refused_for(client.get(path, headers=as_user(user)), "permissions.manage")
    refused_for(client.get(f"/api/v1/shops/{world.shop_a}/permissions", headers=as_user(user)), "permissions.manage")
    assert _snapshot(owner, world.shop_a) == before


@pytest.mark.parametrize("fixed", sorted(permissions.FIXED_KEYS - {"ownership.receive", "membership.own"}))
def test_what_is_the_owners_cannot_be_reached_by_a_grant_put_straight_into_the_database(
    client: TestClient, world: World, owner: psycopg.Connection, fixed: str
) -> None:
    """Were a fixed key ever stored (by a fault, or by hand), it would still open nothing."""
    switch_permissions_on(owner)
    set_overrides(owner, world.manager_a_membership, granted=[fixed])
    assert fixed not in my_permissions(client, world, world.manager_a)
    for op_name in permissions.get(fixed).operations:
        call = CALLS[op_name]
        _prepare(owner, world, call)
        before = _snapshot(owner, world.shop_a)
        refused_for(_invoke(client, world, call, world.shop_a, as_user(world.manager_a)), fixed)
        assert _snapshot(owner, world.shop_a) == before


def invite(client: TestClient, world: World, caller: uuid.UUID, role: str) -> Any:
    path = f"/api/v1/shops/{world.shop_a}/staff/invitations"
    return client.post(path, json={"role": role}, headers={**as_user(caller), **key()})


def patch_member(client: TestClient, world: World, caller: uuid.UUID, member: uuid.UUID, **body: Any) -> Any:
    path = f"/api/v1/shops/{world.shop_a}/staff/{member}"
    return client.patch(path, json=body, headers={**as_user(caller), **key()})


def remove_member(client: TestClient, world: World, caller: uuid.UUID, member: uuid.UUID) -> Any:
    path = f"/api/v1/shops/{world.shop_a}/staff/{member}"
    return client.delete(path, headers={**as_user(caller), **key()})


def test_a_staff_manager_invites_suspends_and_removes_sellers(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    manager = staff_manager(owner, world)
    assert invite(client, world, manager, "seller").status_code == 201
    suspended = patch_member(client, world, manager, world.seller_a_membership, status="suspended")
    assert suspended.status_code == 200 and suspended.json()["status"] == "suspended"
    assert patch_member(client, world, manager, world.seller_a_membership, status="active").status_code == 200
    assert remove_member(client, world, manager, world.seller_a_membership).json()["status"] == "removed"


def test_a_staff_manager_cannot_invite_a_manager_or_change_a_role(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    manager = staff_manager(owner, world)
    before = _snapshot(owner, world.shop_a)
    beyond(invite(client, world, manager, "manager"))
    beyond(patch_member(client, world, manager, world.seller_a_membership, role="manager"))
    assert _snapshot(owner, world.shop_a) == before


def test_a_staff_manager_cannot_touch_a_manager_the_owner_or_themselves(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    seller = staff_manager(owner, world, "seller_a")
    before = _snapshot(owner, world.shop_a)
    for member in (world.manager_a_membership, world.seller_a_membership):
        beyond(patch_member(client, world, seller, member, status="suspended"))
        beyond(remove_member(client, world, seller, member))
    for response in (
        patch_member(client, world, seller, world.owner_a_membership, status="suspended"),
        remove_member(client, world, seller, world.owner_a_membership),
    ):
        assert (response.status_code, response.json()["error"]["code"]) == (409, "OWNER_MEMBERSHIP_FIXED")
    assert _snapshot(owner, world.shop_a) == before


def test_a_staff_manager_cannot_bring_back_a_seller_who_holds_more_than_they_do(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """Reactivating a suspended seller who was granted something would hand that grant out again."""
    manager = staff_manager(owner, world)
    owner.execute(
        "UPDATE membership SET status = 'suspended', permissions_granted = '{shop.edit}' WHERE id = %s",
        (world.seller_a_membership,),
    )
    before = _snapshot(owner, world.shop_a)
    beyond(patch_member(client, world, manager, world.seller_a_membership, status="active"))
    assert _snapshot(owner, world.shop_a) == before
    # What the manager holds as well is theirs to bring back.
    set_overrides(owner, world.seller_a_membership, granted=["reports.view"])
    assert patch_member(client, world, manager, world.seller_a_membership, status="active").status_code == 200


def test_a_staff_manager_who_was_denied_something_cannot_invite_someone_who_would_have_it(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    manager = staff_manager(owner, world, denied=["payments.record"])
    before = _snapshot(owner, world.shop_a)
    beyond(invite(client, world, manager, "seller"))  # a seller takes payments by default
    assert _snapshot(owner, world.shop_a) == before


def test_a_staff_manager_cannot_cancel_the_owners_invitation_of_a_manager(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    manager = staff_manager(owner, world)
    issued = invite(client, world, world.owner_a, "manager").json()
    before = _snapshot(owner, world.shop_a)
    path = f"/api/v1/shops/{world.shop_a}/staff/invitations/"
    beyond(client.delete(path + issued["id"], headers={**as_user(manager), **key()}))
    assert _snapshot(owner, world.shop_a) == before
    assert client.delete(path + world.invitation_a, headers={**as_user(manager), **key()}).status_code == 200


def test_a_suspended_member_holds_nothing_whatever_was_granted(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    member = owner.execute(
        "UPDATE membership SET permissions_granted = %s WHERE user_id = %s RETURNING id",
        (sorted(permissions.ALL_KEYS - permissions.FIXED_KEYS), world.suspended_a),
    ).fetchone()
    assert member is not None
    for op_name in ("customers.list", "reports.overdue", "staff.list", "shop.update", "ledger.entry.create"):
        response = _invoke(client, world, CALLS[op_name], world.shop_a, as_user(world.suspended_a))
        assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")
    said = chat_of(client, owner, world.suspended_a).say("Ali 1000")
    assert "Ali" not in [row[0] for row in entries(owner, world.shop_a)[1:]], said.payloads


def test_suspending_takes_everything_away_at_once(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    switch_permissions_on(owner)
    set_overrides(owner, world.seller_a_membership, granted=["reports.view"])
    overdue = f"/api/v1/shops/{world.shop_a}/reports/overdue"
    assert client.get(overdue, headers=as_user(world.seller_a)).status_code == 200
    patch_member(client, world, world.owner_a, world.seller_a_membership, status="suspended")
    assert client.get(overdue, headers=as_user(world.seller_a)).status_code == 404


def stored(owner: psycopg.Connection, member: uuid.UUID) -> Any:
    return owner.execute(
        "SELECT role, status, permissions_granted, permissions_denied FROM membership WHERE id = %s", (member,)
    ).fetchone()


def test_a_removed_member_who_is_invited_back_does_not_find_old_grants(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    put(client, world, world.owner_a, world.seller_a_membership, granted=["reports.view", "staff.manage"])
    remove_member(client, world, world.owner_a, world.seller_a_membership)
    assert stored(owner, world.seller_a_membership) == ("seller", "removed", [], [])

    joined = client.post(
        "/api/v1/staff-invitations/accept", json={"token": world.invitation_a_token}, headers=as_user(world.seller_a)
    )
    assert joined.status_code == 200, joined.text
    assert stored(owner, world.seller_a_membership) == ("seller", "active", [], [])
    refused_for(
        client.get(f"/api/v1/shops/{world.shop_a}/reports/overdue", headers=as_user(world.seller_a)), "reports.view"
    )


def test_a_change_of_role_starts_from_the_new_roles_defaults(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    put(client, world, world.owner_a, world.manager_a_membership, granted=["shop.edit"], denied=["entries.cancel"])
    assert patch_member(client, world, world.owner_a, world.manager_a_membership, role="seller").status_code == 200
    assert stored(owner, world.manager_a_membership) == ("seller", "active", [], [])
    assert my_permissions(client, world, world.manager_a) == sorted(permissions.role_defaults(Role.SELLER))


def test_a_former_owner_does_not_find_changes_from_before(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """Ownership moves to the manager; whatever was stored for either of them is gone with the roles."""
    switch_permissions_on(owner)
    put(client, world, world.owner_a, world.manager_a_membership, denied=["entries.cancel"])
    base = f"/api/v1/shops/{world.shop_a}/ownership-transfer"
    started = client.post(
        base, json={"membership_id": str(world.manager_a_membership)}, headers={**as_user(world.owner_a), **key()}
    )
    assert started.status_code == 201, started.text
    assert client.post(base + "/accept", headers={**as_user(world.manager_a), **key()}).status_code == 200
    assert stored(owner, world.manager_a_membership) == ("owner", "active", [], [])
    assert stored(owner, world.owner_a_membership) == ("manager", "active", [], [])
    assert my_permissions(client, world, world.manager_a) == sorted(permissions.ALL_KEYS)
    assert my_permissions(client, world, world.owner_a) == sorted(permissions.role_defaults(Role.MANAGER))


# --- the database holds to it as well ---------------------------------------------------------------------


def test_the_database_refuses_changes_for_the_owner(world: World, owner: psycopg.Connection) -> None:
    for column in ("permissions_granted", "permissions_denied"):
        with pytest.raises(psycopg.errors.CheckViolation):
            owner.execute(
                f"UPDATE membership SET {column} = '{{reports.view}}' WHERE id = %s",
                (world.owner_a_membership,),
            )


@pytest.mark.parametrize(
    ("granted", "denied"),
    [
        (["reports.view"], ["reports.view"]),
        (["Reports.View"], []),
        (["reports"], []),
        (["reports.view,shop.delete"], []),
        ([], ["reports .view"]),
        ([""], []),
        ([None], []),
        ([f"area.key_{'x' * n}" for n in range(65)], []),
    ],
)
def test_the_database_refuses_keys_that_are_not_keys(
    world: World, owner: psycopg.Connection, granted: list[Any], denied: list[Any]
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        set_overrides(owner, world.seller_a_membership, granted=granted, denied=denied)


def test_the_database_clears_the_changes_with_the_role_and_on_removal(world: World, owner: psycopg.Connection) -> None:
    member = world.seller_a_membership
    set_overrides(owner, member, granted=["reports.view"], denied=["payments.record"])
    owner.execute("UPDATE membership SET status = 'suspended' WHERE id = %s", (member,))
    assert stored(owner, member) == ("seller", "suspended", ["reports.view"], ["payments.record"]), "kept while away"
    owner.execute("UPDATE membership SET role = 'manager', status = 'active' WHERE id = %s", (member,))
    assert stored(owner, member) == ("manager", "active", [], [])
    set_overrides(owner, member, denied=["entries.cancel"])
    owner.execute("UPDATE membership SET status = 'removed' WHERE id = %s", (member,))
    assert stored(owner, member) == ("manager", "removed", [], [])
    # Nothing can be stored for someone who is not in the shop, to wait for their return.
    set_overrides(owner, member, granted=["shop.edit"])
    assert stored(owner, member) == ("manager", "removed", [], [])


def test_another_shops_owner_cannot_see_or_change_a_members_permissions(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    before = _snapshot(owner, world.shop_a)
    assert put(client, world, world.owner_b, world.seller_a_membership, granted=["reports.view"]).status_code == 404
    through_own = client.put(
        f"/api/v1/shops/{world.shop_b}/staff/{world.seller_a_membership}/permissions",
        json={"granted": ["reports.view"], "denied": []},
        headers={**as_user(world.owner_b), **key()},
    )
    assert through_own.status_code == 404
    assert _snapshot(owner, world.shop_a) == before


# --- the bot ---------------------------------------------------------------------------------------------


def test_the_bot_refuses_an_entry_as_the_api_does(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    switch_permissions_on(owner)
    set_overrides(owner, world.seller_a_membership, denied=["credits.record"])
    seller = chat_of(client, owner, world.seller_a)
    before = entries(owner, world.shop_a)
    assert seller.say("Ali 30000 non").text == say("uz", "forbidden_permission")
    assert entries(owner, world.shop_a) == before
    # A payment is another permission, and the seller still holds it.
    seller.say("Ali 20000 berdi")
    assert entries(owner, world.shop_a)[-1] == ("Ali", "payment", 20000, world.seller_a_membership)

    set_overrides(owner, world.seller_a_membership, denied=["payments.record"])
    before = entries(owner, world.shop_a)
    assert seller.say("Ali 1000 berdi").text == say("uz", "forbidden_permission")
    assert entries(owner, world.shop_a) == before


def test_the_bot_does_not_create_a_customer_for_someone_who_may_not(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    set_overrides(owner, world.seller_a_membership, denied=["customers.create"])
    seller = chat_of(client, owner, world.seller_a)
    asked = seller.say("Yangi Odam 5000")
    pressed = seller.press(asked.button("➕"), seller.last_message_id)
    assert pressed.text == say("uz", "forbidden_permission")
    assert owner.execute(
        "SELECT count(*) FROM customer WHERE shop_id = %s AND display_name = 'Yangi Odam'", (world.shop_a,)
    ).fetchone() == (0,)


def test_the_bot_offers_reversal_to_whoever_holds_it_and_refuses_the_rest(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    set_overrides(owner, world.seller_a_membership, granted=["entries.cancel"])
    set_overrides(owner, world.manager_a_membership, denied=["entries.cancel"])
    seller, manager = chat_of(client, owner, world.seller_a), chat_of(client, owner, world.manager_a)
    saved = seller.say("Ali 4000")
    assert "↩️ Bekor qilish" in saved.buttons
    assert "↩️ Bekor qilish" not in manager.say("Ali 1000").buttons

    before = entries(owner, world.shop_a)
    forged = manager.press(f"v2:rvok:{world.entry_a.hex}")
    assert forged.text == say("uz", "forbidden_permission")
    assert entries(owner, world.shop_a) == before

    asked = seller.press(saved.button("↩️"), seller.last_message_id)
    seller.press(asked.button("Ha"), seller.last_message_id)
    assert entries(owner, world.shop_a)[-1] == ("Ali", "reversal", 4000, world.seller_a_membership)


def test_only_those_who_may_decide_are_told(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    switch_permissions_on(owner)
    set_overrides(owner, world.seller_a_membership, denied=["payment_notices.decide"])
    notice = send(client, world.customer_of_a, link_of(owner, world.customer_a), 20000).json()
    messages = staff_notices(owner, f"notice:{notice['id']}:sent")
    assert sorted(recipient for recipient, _ in messages) == sorted(
        tg(owner, user) for user in (world.owner_a, world.manager_a)
    )
    # And the button is refused to the one who was not told, should they press an old one.
    pressed = chat_of(client, owner, world.seller_a).press(f"v2:pna:{uuid.UUID(notice['id']).hex}")
    assert pressed.text == say("uz", "forbidden_permission")
    assert owner.execute("SELECT status FROM payment_notice WHERE id = %s", (notice["id"],)).fetchone() == ("sent",)
