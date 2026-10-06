"""Rate limits of the API per signed-in user and per shop (technical specification, "Abuse"; REQ-N13)."""

from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.auth import AuthService
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app
from qarz.interface.rate_limit import Limit, RateLimits

from .conftest import TEST_BOT_TOKEN, HeaderAuthenticator, World, as_user

pytestmark = pytest.mark.db


class Clock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Limited:
    client: TestClient
    clock: Clock


def limited(app_database_url: str, user: Limit, shop: Limit) -> Iterator[Limited]:
    database, clock = Database(app_database_url), Clock()
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        authenticator=HeaderAuthenticator(),
        rate_limits=RateLimits(user=user, shop=shop),
        monotonic=clock,
    )
    with TestClient(app) as client:
        yield Limited(client, clock)
        client.portal.call(database.dispose)  # type: ignore[union-attr]


@pytest.fixture
def per_user(app_database_url: str) -> Iterator[Limited]:
    yield from limited(app_database_url, user=Limit(60, 3), shop=Limit(6000, 1000))


@pytest.fixture
def per_shop(app_database_url: str) -> Iterator[Limited]:
    yield from limited(app_database_url, user=Limit(6000, 1000), shop=Limit(60, 4))


def test_a_user_over_the_rate_is_told_to_wait_and_nothing_is_done(
    per_user: Limited, world: World, owner: psycopg.Connection
) -> None:
    client, me = per_user.client, as_user(world.owner_a)
    path = f"/api/v1/shops/{world.shop_a}"
    assert [client.get(path, headers=me).status_code for _ in range(3)] == [200, 200, 200]

    refused = client.patch(path, json={"name": "Flood"}, headers={**me, "Idempotency-Key": "flood-key-1"})
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "1"
    assert refused.json() == {
        "error": {
            "code": "RATE_LIMITED",
            "message": "So'rovlar juda ko'p. Biroz kutib, qayta urinib ko'ring.",
            "fields": {},
        }
    }
    assert owner.execute("SELECT name FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == ("Shop A",)
    assert client.get("/api/v1/me", headers=me).status_code == 429, "the limit is the user's, whatever they ask for"

    # Other people are not held back, in the same shop or elsewhere.
    assert client.get(path, headers=as_user(world.manager_a)).status_code == 200
    assert client.get(f"/api/v1/shops/{world.shop_b}", headers=as_user(world.owner_b)).status_code == 200

    per_user.clock.now += 1
    assert client.get(path, headers=me).status_code == 200
    assert client.get(path, headers=me).status_code == 429


def test_requests_without_a_valid_sign_in_are_not_counted_for_anyone(per_user: Limited, world: World) -> None:
    client = per_user.client
    for _ in range(10):
        assert client.get("/api/v1/me").status_code == 401
        assert client.get("/api/v1/me", headers={"X-Test-User": "not-a-uuid"}).status_code == 401
    assert client.get("/api/v1/me", headers=as_user(world.owner_a)).status_code == 200
    for _ in range(10):
        assert client.get("/healthz").status_code == 200


def test_a_shop_over_its_rate_holds_back_its_staff_together(per_shop: Limited, world: World) -> None:
    client = per_shop.client
    path = f"/api/v1/shops/{world.shop_a}"
    owner_a, manager, seller = (as_user(user) for user in (world.owner_a, world.manager_a, world.seller_a))
    assert [client.get(path, headers=who).status_code for who in (owner_a, manager, owner_a, manager)] == [200] * 4

    for who in (owner_a, manager):
        refused = client.get(path, headers=who)
        assert (refused.status_code, refused.json()["error"]["code"]) == (429, "RATE_LIMITED")
        assert refused.headers["Retry-After"] == "1"
    # What the role may not do is still counted: a refusal by role is an answer to a member.
    assert client.get(f"{path}/subscription", headers=seller).status_code == 403
    assert client.get(f"{path}/subscription", headers=seller).status_code == 429

    # The other shop works, and so does what is not about a shop.
    assert client.get(f"/api/v1/shops/{world.shop_b}", headers=as_user(world.owner_b)).status_code == 200
    assert client.get("/api/v1/me", headers=owner_a).status_code == 200

    per_shop.clock.now += 1
    assert client.get(path, headers=manager).status_code == 200
    assert client.get(path, headers=owner_a).status_code == 429


@pytest.mark.parametrize("caller", ["owner_b", "stranger", "customer_of_a", "suspended_a", "admin"])
def test_a_busy_shop_answers_a_stranger_as_it_always_does(per_shop: Limited, world: World, caller: str) -> None:
    """Otherwise "too many requests" would tell an outsider that the shop exists and is in use."""
    client = per_shop.client
    path = f"/api/v1/shops/{world.shop_a}"
    outsider = as_user(getattr(world, caller))
    before = client.get(path, headers=outsider)
    assert before.status_code == 404
    for _ in range(4):
        assert client.get(path, headers=as_user(world.owner_a)).status_code == 200
    assert client.get(path, headers=as_user(world.owner_a)).status_code == 429
    for _ in range(6):
        after = client.get(path, headers=outsider)
        assert (after.status_code, after.json(), "retry-after" in after.headers) == (404, before.json(), False)


def test_strangers_cannot_use_up_a_shops_rate(per_shop: Limited, world: World) -> None:
    client = per_shop.client
    path = f"/api/v1/shops/{world.shop_a}"
    for _ in range(30):
        assert client.get(path, headers=as_user(world.owner_b)).status_code == 404
        assert client.get(path, headers=as_user(world.stranger)).status_code == 404
    assert [client.get(path, headers=as_user(world.owner_a)).status_code for _ in range(4)] == [200] * 4


def test_strangers_cannot_use_up_a_shops_rate_with_malformed_requests(per_shop: Limited, world: World) -> None:
    """A malformed body is refused with 422 before membership is looked at, so it must not count either."""
    client = per_shop.client
    path = f"/api/v1/shops/{world.shop_a}"
    for who in (world.owner_b, world.stranger):
        for _ in range(15):
            refused = client.patch(path, json={"name": 5}, headers={**as_user(who), "Idempotency-Key": "malformed-1"})
            assert refused.status_code == 422
    assert [client.get(path, headers=as_user(world.owner_a)).status_code for _ in range(4)] == [200] * 4
    assert client.get(path, headers=as_user(world.owner_a)).status_code == 429
    # And having sent them does not make a stranger known as a member.
    assert client.get(path, headers=as_user(world.owner_b)).status_code == 404


def test_a_malformed_shop_identifier_is_not_found_and_limits_nothing(per_shop: Limited, world: World) -> None:
    client = per_shop.client
    for _ in range(10):
        assert client.get("/api/v1/shops/not-a-shop", headers=as_user(world.owner_a)).status_code == 404
    assert client.get(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.owner_a)).status_code == 200


def test_without_limits_nothing_is_refused(client: TestClient, world: World) -> None:
    """The application of the other tests has no limits; this is what keeps them independent of timing."""
    statuses = {client.get("/api/v1/me", headers=as_user(world.owner_a)).status_code for _ in range(150)}
    assert statuses == {200}
