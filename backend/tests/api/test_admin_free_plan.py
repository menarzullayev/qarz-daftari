"""The free plan as the administrator sees it (BR-33; module A, behind the platform switch `free_plan_on`).

The search of shops computes a shop's state in SQL from its stored subscription row, and to that a shop
without a period is `limited`. With the plan on the administrator's side says what applies to the shop
itself: `free` while the plan holds its active customers, and how many of the plan they use. With the
switch off every answer is what it was.
"""

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import ADMIN_API, AdminEnv, World, as_user, elevate, make_admin
from .test_admin_shops import _post, _set, _tagged, _today

pytestmark = pytest.mark.db

SHOPS = f"{ADMIN_API}/shops"
FreePlan = Callable[[int], None]
SHOP_KEYS = {"id", "name", "status", "created_at", "subscription", "owner_tg_id", "staff_count", "customer_count"}
SUBSCRIPTION_KEYS = {"state", "stored_state", "trial_ends", "paid_through", "prior_state"}
# Shop A of the world: Ali and Vali are active, Sobir is archived.
ACTIVE_IN_A = 2


@pytest.fixture
def admin(client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv) -> dict[str, str]:
    """Headers of an administrator who passed the second factor."""
    return elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))


def _customers(owner: psycopg.Connection, shop: uuid.UUID, count: int) -> None:
    for _ in range(count):
        name = uuid.uuid4().hex[:12]
        owner.execute(
            "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, %s, %s)",
            (uuid.uuid4(), shop, name, name),
        )


def _shop(owner: psycopg.Connection, tag: str, customers: int) -> str:
    """A shop without a period, named with the tag, with this many active customers."""
    shop = uuid.uuid4()
    owner.execute("INSERT INTO shop (id, name) VALUES (%s, %s)", (shop, f"Extra {tag}"))
    owner.execute("INSERT INTO subscription (shop_id, state) VALUES (%s, 'limited')", (shop,))
    _customers(owner, shop, customers)
    return str(shop)


def _listed(client: TestClient, admin: dict[str, str], **params: Any) -> dict[str, Any]:
    response = client.get(SHOPS, params=params, headers=admin)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _found(client: TestClient, admin: dict[str, str], tag: str, state: str) -> set[str]:
    return {item["id"] for item in _listed(client, admin, q=tag, state=state)["items"]}


def _one(client: TestClient, admin: dict[str, str], tag: str, shop: uuid.UUID) -> dict[str, Any]:
    return next(item for item in _listed(client, admin, q=tag)["items"] if item["id"] == str(shop))


# --- the switch off: what was true before ------------------------------------------------------------------


@pytest.mark.parametrize("switch", ["false", None, '"true"', "1"])
def test_with_the_switch_off_the_administrator_is_answered_as_before(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], switch: str | None
) -> None:
    """Only a stored `true` is on. Anything else: no `plan`, no `free`, and `free` is no filter."""
    owner.execute("DELETE FROM platform_setting WHERE key IN ('free_plan_on', 'free_plan_customers')")
    owner.execute("INSERT INTO platform_setting (key, value, updated_by) VALUES ('free_plan_customers', '30', 'test')")
    if switch is not None:
        owner.execute(
            "INSERT INTO platform_setting (key, value, updated_by) VALUES ('free_plan_on', %s::jsonb, 'test')",
            (switch,),
        )
    try:
        tag = _tagged(owner, world)
        _set(owner, world.shop_a, "limited")

        listed = _one(client, admin, tag, world.shop_a)
        assert set(listed) == SHOP_KEYS
        assert set(listed["subscription"]) == SUBSCRIPTION_KEYS
        assert listed["subscription"]["state"] == "limited"
        assert _found(client, admin, tag, "limited") == {str(world.shop_a)}

        read = client.get(f"{SHOPS}/{world.shop_a}", headers=admin).json()
        assert set(read) == SHOP_KEYS | {"lang", "deletion_due", "receipts", "changes"}
        assert read["subscription"] == listed["subscription"]

        changed = _post(client, admin, world.shop_a, "suspend", {"reason": "tekshiruv"})
        assert changed.status_code == 200, changed.text
        assert set(changed.json()) == SHOP_KEYS

        refused = client.get(SHOPS, params={"q": tag, "state": "free"}, headers=admin)
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["fields"] == {"state": "must be trial, active, limited or suspended"}
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key IN ('free_plan_on', 'free_plan_customers')")


# --- the switch on -----------------------------------------------------------------------------------------


def test_a_shop_the_plan_holds_is_listed_and_shown_as_free_with_how_much_of_the_plan_it_uses(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], free_plan: FreePlan
) -> None:
    tag = _tagged(owner, world)
    _set(owner, world.shop_a, "limited")
    free_plan(ACTIVE_IN_A)

    listed = _one(client, admin, tag, world.shop_a)
    assert set(listed) == SHOP_KEYS | {"plan"}
    assert listed["subscription"]["state"] == "free"
    assert listed["subscription"]["stored_state"] == "limited", "nothing is stored: the row says no period runs"
    assert listed["plan"] == {"free_customers": ACTIVE_IN_A, "customers": ACTIVE_IN_A}
    assert listed["customer_count"] == 3, "the archived customer is a customer, but takes no place in the plan"

    read = client.get(f"{SHOPS}/{world.shop_a}", headers=admin).json()
    assert read["subscription"] == listed["subscription"]
    assert read["plan"] == listed["plan"]
    # REQ-059 still holds: a number, and nothing of the customers.
    for hidden in ("Ali", "Vali", "Sobir", str(world.customer_a)):
        assert hidden not in client.get(SHOPS, params={"q": tag}, headers=admin).text


def test_one_customer_over_the_number_and_the_shop_is_limited(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], free_plan: FreePlan
) -> None:
    tag = _tagged(owner, world)
    _set(owner, world.shop_a, "limited")
    free_plan(ACTIVE_IN_A - 1)
    listed = _one(client, admin, tag, world.shop_a)
    assert listed["subscription"]["state"] == "limited"
    assert listed["plan"] == {"free_customers": ACTIVE_IN_A - 1, "customers": ACTIVE_IN_A}

    # A customer put in the archive leaves the count, and the plan holds the shop again.
    owner.execute("UPDATE customer SET status = 'archived' WHERE id = %s", (world.settled_customer_a,))
    assert _one(client, admin, tag, world.shop_a)["subscription"]["state"] == "free"


def test_a_running_period_and_a_suspension_are_what_they_were(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    free_plan: FreePlan,
) -> None:
    """The plan is for a shop without a period. A trial, a paid period and a suspension keep their names,
    however few customers the shop has; the count is given all the same."""
    tag = _tagged(owner, world)
    today = _today(admin_env)
    free_plan(100)
    for stored, dates, shown in (
        ("trial", {"trial_ends": today}, "trial"),
        ("active", {"paid_through": today + timedelta(days=3)}, "active"),
        ("suspended", {"prior_state": "limited"}, "suspended"),
        ("trial", {"trial_ends": today - timedelta(days=1)}, "free"),
    ):
        _set(owner, world.shop_a, stored, **dates)
        listed = _one(client, admin, tag, world.shop_a)
        assert (listed["subscription"]["state"], listed["subscription"]["stored_state"]) == (shown, stored)
        assert listed["plan"] == {"free_customers": 100, "customers": ACTIVE_IN_A}


def test_the_answer_to_a_change_names_the_state_the_same_way(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], free_plan: FreePlan
) -> None:
    _set(owner, world.shop_a, "limited")
    free_plan(ACTIVE_IN_A)
    suspended = _post(client, admin, world.shop_a, "suspend", {"reason": "tekshiruv"})
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["subscription"]["state"] == "suspended"
    lifted = _post(client, admin, world.shop_a, "unsuspend", {"reason": "tekshirildi"})
    assert lifted.status_code == 200, lifted.text
    assert lifted.json()["subscription"]["state"] == "free"
    assert lifted.json()["plan"] == {"free_customers": ACTIVE_IN_A, "customers": ACTIVE_IN_A}


def test_the_limited_filter_leaves_out_free_shops_and_free_is_a_filter_of_its_own(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], free_plan: FreePlan
) -> None:
    tag = _tagged(owner, world)
    _set(owner, world.shop_a, "limited")
    _set(owner, world.shop_b, "limited")
    _customers(owner, world.shop_b, ACTIVE_IN_A + 1)
    free_plan(ACTIVE_IN_A)

    assert _found(client, admin, tag, "free") == {str(world.shop_a)}
    assert _found(client, admin, tag, "limited") == {str(world.shop_b)}, "a free shop is not a limited one"
    assert _found(client, admin, tag, "trial") == set()
    for state in ("free", "limited"):
        for item in _listed(client, admin, q=tag, state=state)["items"]:
            assert item["subscription"]["state"] == state

    refused = client.get(SHOPS, params={"state": "paused"}, headers=admin)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["fields"] == {"state": "must be trial, active, free, limited or suspended"}


def test_the_two_filters_are_paged_without_losing_or_repeating_a_shop(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], free_plan: FreePlan
) -> None:
    """Free and limited shops lie mixed in the order of the list, so a page of one kind is gathered from
    several pages of the search."""
    tag = uuid.uuid4().hex[:10]
    free_plan(2)
    kinds = ["free", "limited", "limited", "free", "limited", "limited", "limited", "free"]
    shops = [(_shop(owner, tag, 1 if kind == "free" else 3), kind) for kind in kinds]

    for kind in ("free", "limited"):
        expected = [shop for shop, held in reversed(shops) if held == kind]
        seen: list[str] = []
        cursor: str | None = None
        for _ in range(len(shops) + 1):
            params: dict[str, Any] = {"q": tag, "state": kind, "limit": 1}
            page = _listed(client, admin, **(params if cursor is None else {**params, "cursor": cursor}))
            seen += [item["id"] for item in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert seen == expected, f"{kind}: newest first, each once"

    whole = _listed(client, admin, q=tag, state="limited", limit=5)
    assert len(whole["items"]) == 5
    assert whole["next_cursor"] is None, "nothing follows the last limited shop"
    assert _listed(client, admin, q=tag, limit=3)["next_cursor"] is not None, "unfiltered, the list pages as before"


def test_the_shop_itself_and_the_administrator_are_told_the_same_state(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], free_plan: FreePlan
) -> None:
    """Both sides count the same customers: what the owner's `/subscription` says is what the list says."""
    tag = _tagged(owner, world)
    _set(owner, world.shop_a, "limited")
    for held in (ACTIVE_IN_A, ACTIVE_IN_A - 1):
        free_plan(held)
        own = client.get(f"/api/v1/shops/{world.shop_a}/subscription", headers=as_user(world.owner_a)).json()
        listed = _one(client, admin, tag, world.shop_a)
        assert listed["subscription"]["state"] == own["state"]
        assert listed["plan"] == {
            "free_customers": own["plan"]["free_customers"],
            "customers": own["plan"]["customers"],
        }
