"""The one-tap promised date after a sale, through the API (REQ-008)."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.domain.promise import tashkent_date

from .conftest import World, as_user
from .test_customers_ledger import key, record, reverse, seed_customer, seed_entry, shop

pytestmark = pytest.mark.db


def today() -> date:
    return tashkent_date(datetime.now(UTC))


def choose(client: TestClient, world: World, user: uuid.UUID, entry: Any, chosen: Any) -> Any:
    body = {"promised_date": chosen.isoformat() if isinstance(chosen, date) else chosen}
    return client.post(f"{shop(world)}/entries/{entry}/promise-choice", json=body, headers={**as_user(user), **key()})


def promise_rows(owner: psycopg.Connection, entry: Any) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT actor, promised_date FROM promise WHERE entry_id = %s ORDER BY created_at, id", (entry,)
    ).fetchall()


def test_the_author_chooses_once(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    entry = record(client, world, world.customer_a, "credit", 20000).json()["entry"]["id"]
    assert promise_rows(owner, entry) == [("default", today() + timedelta(days=30))]

    chosen = today() + timedelta(days=5)
    response = choose(client, world, world.seller_a, entry, chosen)
    assert response.status_code == 200, response.text
    assert response.json()["entry"] == {"id": entry, "amount": 20000, "promised_date": chosen.isoformat()}
    assert response.json()["customer"]["balance"] == 70000
    assert promise_rows(owner, entry) == [("default", today() + timedelta(days=30)), ("staff", chosen)]
    detail = client.get(f"{shop(world)}/customers/{world.customer_a}", headers=as_user(world.seller_a)).json()
    assert detail["entries"][0]["promised_date"] == chosen.isoformat()
    logged = owner.execute(
        "SELECT count(*) FROM activity WHERE shop_id = %s AND action = 'ledger.promise_chosen'", (world.shop_a,)
    ).fetchone()
    assert logged == (1,)

    again = choose(client, world, world.seller_a, entry, today() + timedelta(days=6))
    assert (again.status_code, again.json()["error"]["code"]) == (409, "PROMISE_ALREADY_SET")
    # Not even a manager: after the first choice a change is a different operation (REQ-067).
    by_manager = choose(client, world, world.manager_a, entry, today() + timedelta(days=6))
    assert by_manager.status_code == 409
    assert len(promise_rows(owner, entry)) == 2


def test_a_date_chosen_at_the_sale_closes_the_choice(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    at_sale = (today() + timedelta(days=2)).isoformat()
    entry = record(client, world, world.customer_a, "credit", 20000, promised_date=at_sale).json()["entry"]["id"]
    assert promise_rows(owner, entry) == [("staff", today() + timedelta(days=2))]
    response = choose(client, world, world.seller_a, entry, today() + timedelta(days=5))
    assert (response.status_code, response.json()["error"]["code"]) == (409, "PROMISE_ALREADY_SET")
    assert len(promise_rows(owner, entry)) == 1


def test_another_seller_is_refused_and_a_manager_is_not(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    other = uuid.uuid4()
    owner.execute("INSERT INTO app_user (id, tg_id, lang) VALUES (%s, %s, 'uz')", (other, uuid.uuid4().int % 10**15))
    owner.execute(
        "INSERT INTO membership (id, shop_id, user_id, role, status) VALUES (%s, %s, %s, 'seller', 'active')",
        (uuid.uuid4(), world.shop_a, other),
    )
    entry = record(client, world, world.customer_a, "credit", 20000).json()["entry"]["id"]

    refused = choose(client, world, other, entry, today() + timedelta(days=5))
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "FORBIDDEN_ROLE")
    assert refused.json()["error"]["fields"] == {"needed_role": "manager"}
    assert len(promise_rows(owner, entry)) == 1
    assert choose(client, world, world.manager_a, entry, today() + timedelta(days=5)).status_code == 200


def test_the_choice_closes_a_day_after_the_sale(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    customer = seed_customer(owner, world.shop_a, "Kechagi")
    fresh = seed_entry(owner, world, customer, 1, "credit", 5000, days_ago=0.9)
    stale = seed_entry(owner, world, customer, 2, "credit", 5000, days_ago=1.1)
    for entry in (fresh, stale):
        owner.execute(
            "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor) VALUES (%s, %s, %s, %s, 'default')",
            (uuid.uuid4(), world.shop_a, entry, today() + timedelta(days=30)),
        )
    assert choose(client, world, world.seller_a, fresh, today() + timedelta(days=5)).status_code == 200
    late = choose(client, world, world.seller_a, stale, today() + timedelta(days=5))
    assert (late.status_code, late.json()["error"]["code"]) == (409, "PROMISE_ALREADY_SET")
    assert len(promise_rows(owner, stale)) == 1


def test_only_a_live_credit_sale_has_a_choice(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    payment = record(client, world, world.customer_a, "payment", 1000).json()["entry"]["id"]
    refused = choose(client, world, world.seller_a, payment, today() + timedelta(days=5))
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "PROMISE_ALREADY_SET")

    sale = record(client, world, world.settled_customer_a, "credit", 20000).json()["entry"]["id"]
    reversal = reverse(client, world, sale).json()["entry"]["id"]
    for entry in (sale, reversal):
        response = choose(client, world, world.manager_a, entry, today() + timedelta(days=5))
        assert (response.status_code, response.json()["error"]["code"]) == (409, "PROMISE_ALREADY_SET")
    assert owner.execute("SELECT count(*) FROM promise WHERE shop_id = %s", (world.shop_a,)).fetchone() == (2,)


@pytest.mark.parametrize(
    ("offset", "reason"), [(-1, "PROMISE_BEFORE_SALE"), (366, "PROMISE_TOO_FAR")], ids=["before the sale", "too far"]
)
def test_the_chosen_date_must_be_in_range(
    client: TestClient, world: World, owner: psycopg.Connection, offset: int, reason: str
) -> None:
    entry = record(client, world, world.customer_a, "credit", 20000).json()["entry"]["id"]
    response = choose(client, world, world.seller_a, entry, today() + timedelta(days=offset))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["fields"] == {"promised_date": reason}
    assert len(promise_rows(owner, entry)) == 1
    # The edges themselves are allowed.
    edge = today() if offset < 0 else today() + timedelta(days=365)
    assert choose(client, world, world.seller_a, entry, edge).status_code == 200


@pytest.mark.parametrize(
    "body", [{}, {"promised_date": "tomorrow"}, {"promised_date": None}, {"promised_date": 5, "x": 1}]
)
def test_a_malformed_choice_is_refused(client: TestClient, world: World, owner: psycopg.Connection, body: Any) -> None:
    response = client.post(
        f"{shop(world)}/entries/{world.entry_a}/promise-choice", json=body, headers={**as_user(world.seller_a), **key()}
    )
    assert response.status_code == 422
    assert len(promise_rows(owner, world.entry_a)) == 1


def test_a_suspended_shop_takes_no_choice_and_a_limited_one_does(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE subscription SET state = 'suspended' WHERE shop_id = %s", (world.shop_a,))
    refused = choose(client, world, world.owner_a, world.entry_a, today() + timedelta(days=5))
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "SHOP_SUSPENDED")
    owner.execute("UPDATE subscription SET state = 'limited' WHERE shop_id = %s", (world.shop_a,))
    assert choose(client, world, world.seller_a, world.entry_a, today() + timedelta(days=5)).status_code == 200
