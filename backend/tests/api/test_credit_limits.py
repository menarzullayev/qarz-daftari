"""Credit limits: a customer's own, the shop default, and what a seller may do above them (REQ-044, BR-8)."""

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import money, say
from qarz.domain.credit import LimitOutcome, check_limit, effective_limit, valid_limit

from .conftest import World, as_user
from .test_chat import chat_of
from .test_customers_ledger import key, shop

pytestmark = pytest.mark.db


# --- the rule ------------------------------------------------------------------------------------------


def test_a_customers_own_limit_comes_before_the_shop_default() -> None:
    assert effective_limit(100_000, 500_000) == 100_000
    assert effective_limit(None, 500_000) == 500_000
    assert effective_limit(100_000, None) == 100_000
    assert effective_limit(None, None) is None


def test_a_balance_up_to_the_limit_is_within_it() -> None:
    for may_manage in (True, False):
        for sellers_may_exceed in (True, False):
            rules = {"may_manage": may_manage, "sellers_may_exceed": sellers_may_exceed}
            assert check_limit(100_000, 100_000, **rules) is LimitOutcome.WITHIN
            assert check_limit(100_000, 99_999, **rules) is LimitOutcome.WITHIN
            assert check_limit(None, 10**12, **rules) is LimitOutcome.WITHIN


def test_above_the_limit_a_manager_is_warned_and_a_seller_is_warned_or_stopped() -> None:
    assert check_limit(100_000, 100_001, may_manage=True, sellers_may_exceed=False) is LimitOutcome.WARN
    assert check_limit(100_000, 100_001, may_manage=True, sellers_may_exceed=True) is LimitOutcome.WARN
    assert check_limit(100_000, 100_001, may_manage=False, sellers_may_exceed=True) is LimitOutcome.WARN
    assert check_limit(100_000, 100_001, may_manage=False, sellers_may_exceed=False) is LimitOutcome.REFUSE


@pytest.mark.parametrize("value", [999, 0, -1, 10_000_000_001, True, 1000.0, "5000", None])
def test_what_is_not_a_limit(value: Any) -> None:
    assert valid_limit(value) is False


def test_limits_at_the_bounds() -> None:
    assert valid_limit(1_000) and valid_limit(10_000_000_000)


# --- through the API -----------------------------------------------------------------------------------


def sell(client: TestClient, world: World, user: uuid.UUID, amount: int, customer: Any = None) -> Any:
    return client.post(
        f"{shop(world)}/customers/{customer or world.customer_a}/entries",
        json={"kind": "credit", "amount": amount},
        headers={**as_user(user), **key()},
    )


def set_limit(client: TestClient, world: World, limit: Any, customer: Any = None) -> Any:
    return client.patch(
        f"{shop(world)}/customers/{customer or world.customer_a}",
        json={"credit_limit": limit},
        headers={**as_user(world.manager_a), **key()},
    )


def credit_settings(client: TestClient, world: World, body: dict[str, Any], user: uuid.UUID | None = None) -> Any:
    return client.patch(
        f"{shop(world)}/credit-settings", json=body, headers={**as_user(user or world.manager_a), **key()}
    )


def count_entries(owner: psycopg.Connection, world: World) -> int:
    row = owner.execute("SELECT count(*) FROM ledger_entry WHERE shop_id = %s", (world.shop_a,)).fetchone()
    assert row is not None
    return int(row[0])


def test_without_a_limit_nothing_is_said(client: TestClient, world: World) -> None:
    settings = client.get(f"{shop(world)}/credit-settings", headers=as_user(world.seller_a)).json()
    assert settings == {
        "default_credit_limit": None,
        "sellers_may_exceed": True,
        "limit_bounds": [1000, 10_000_000_000],
        "accept_advances": False,
    }
    sale = sell(client, world, world.seller_a, 90_000_000)
    assert sale.status_code == 201
    assert "limit_warning" not in sale.json()
    assert sale.json()["customer"]["credit_limit"] is None


def test_a_sale_above_the_limit_warns_with_balance_and_limit(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    assert set_limit(client, world, 100_000).json()["credit_limit"] == 100_000
    # Ali owes 50 000. Up to the limit: nothing is said.
    within = sell(client, world, world.seller_a, 50_000)
    assert (within.status_code, "limit_warning" in within.json()) == (201, False)
    # One so'm more is above it. By default a seller may proceed, and is warned.
    above = sell(client, world, world.seller_a, 100)
    assert above.status_code == 201, above.text
    assert above.json()["limit_warning"] == {"limit": 100_000, "balance": 100_100}
    # The customer's message says nothing of limits.
    told = owner.execute(
        "SELECT payload->>'text' FROM outbox_message WHERE dedupe_key = %s",
        (f"entry:{above.json()['entry']['id']}:notify",),
    ).fetchone()
    assert told is not None and "limit" not in told[0].lower()


def test_when_the_shop_forbids_it_a_seller_is_stopped_and_a_manager_is_not(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    set_limit(client, world, 60_000)
    assert credit_settings(client, world, {"sellers_may_exceed": False}).json()["sellers_may_exceed"] is False
    before = count_entries(owner, world)

    refused = sell(client, world, world.seller_a, 10_001)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "LIMIT_REACHED")
    assert refused.json()["error"]["fields"] == {"limit": "60000", "balance": "60001"}
    assert count_entries(owner, world) == before, "a refused sale writes nothing"

    # Exactly up to the limit is still allowed for the seller.
    assert sell(client, world, world.seller_a, 10_000).status_code == 201
    for user in (world.manager_a, world.owner_a):
        allowed = sell(client, world, user, 5_000)
        assert allowed.status_code == 201, allowed.text
        assert allowed.json()["limit_warning"]["limit"] == 60_000
    # A payment is never stopped by a limit.
    paid = client.post(
        f"{shop(world)}/customers/{world.customer_a}/entries",
        json={"kind": "payment", "amount": 1000},
        headers={**as_user(world.seller_a), **key()},
    )
    assert paid.status_code == 201 and "limit_warning" not in paid.json()


def test_the_shop_default_applies_to_customers_without_their_own_limit(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    done = credit_settings(client, world, {"default_credit_limit": 20_000, "sellers_may_exceed": False})
    assert (done.status_code, done.json()["default_credit_limit"]) == (200, 20_000)
    refused = sell(client, world, world.seller_a, 20_001, world.settled_customer_a)
    assert refused.json()["error"]["code"] == "LIMIT_REACHED"

    # A customer's own, higher limit wins over the default.
    set_limit(client, world, 500_000, world.settled_customer_a)
    assert sell(client, world, world.seller_a, 20_001, world.settled_customer_a).status_code == 201

    # Removing the customer's limit brings the default back; removing the default leaves none.
    assert set_limit(client, world, None, world.settled_customer_a).json()["credit_limit"] is None
    assert sell(client, world, world.seller_a, 100, world.settled_customer_a).json()["error"]["code"] == "LIMIT_REACHED"
    assert credit_settings(client, world, {"default_credit_limit": None}).json()["default_credit_limit"] is None
    assert sell(client, world, world.seller_a, 100, world.settled_customer_a).status_code == 201
    assert owner.execute(
        "SELECT default_credit_limit, sellers_may_exceed FROM shop WHERE id = %s", (world.shop_a,)
    ).fetchone() == (None, False)


@pytest.mark.parametrize("limit", [999, 0, -5, 10_000_000_001, "50000", 50000.5, True])
def test_an_invalid_limit_is_refused(client: TestClient, world: World, owner: psycopg.Connection, limit: Any) -> None:
    assert set_limit(client, world, limit).status_code == 422
    assert credit_settings(client, world, {"default_credit_limit": limit}).status_code == 422
    assert owner.execute("SELECT credit_limit FROM customer WHERE id = %s", (world.customer_a,)).fetchone() == (None,)
    assert owner.execute("SELECT default_credit_limit FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == (None,)


def test_credit_settings_need_something_to_change_and_keep_what_is_not_sent(client: TestClient, world: World) -> None:
    assert credit_settings(client, world, {}).status_code == 422
    assert credit_settings(client, world, {"x": 1}).status_code == 422
    credit_settings(client, world, {"default_credit_limit": 30_000})
    kept = credit_settings(client, world, {"sellers_may_exceed": False}).json()
    assert (kept["default_credit_limit"], kept["sellers_may_exceed"]) == (30_000, False)


def test_a_customers_limit_is_changed_by_managers_only_and_leaves_other_fields_alone(
    client: TestClient, world: World
) -> None:
    by_seller = client.patch(
        f"{shop(world)}/customers/{world.customer_a}",
        json={"credit_limit": 70_000},
        headers={**as_user(world.seller_a), **key()},
    )
    assert by_seller.status_code == 403
    set_limit(client, world, 70_000)
    renamed = client.patch(
        f"{shop(world)}/customers/{world.customer_a}",
        json={"display_name": "Ali aka"},
        headers={**as_user(world.manager_a), **key()},
    ).json()
    assert (renamed["display_name"], renamed["credit_limit"]) == ("Ali aka", 70_000)


# --- in the chat ---------------------------------------------------------------------------------------


def test_the_chat_reply_warns_or_refuses(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    set_limit(client, world, 60_000)
    seller, manager = chat_of(client, owner, world.seller_a), chat_of(client, owner, world.manager_a)

    warned = seller.say("Ali 20000")
    assert warned.text.endswith(say("uz", "limit_warning", limit=money("uz", 60_000), balance=money("uz", 70_000)))
    assert warned.text.startswith("✅ Shop A\nAli: +20 000")

    credit_settings(client, world, {"sellers_may_exceed": False})
    before = count_entries(owner, world)
    assert seller.say("Ali 1000").text == say("uz", "LIMIT_REACHED")
    assert count_entries(owner, world) == before
    assert manager.say("Ali 1000").text.startswith("✅ Shop A\nAli: +1 000")
