"""The administrator's side together with what landed beside it: the rate limits, the request size limit,
online payment behind the platform switch, and the one reading of platform settings (story S18.1)."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.admin_access import AdminAccess
from qarz.application.auth import AuthService
from qarz.application.errors import NotFound
from qarz.application.online_payment import PaymentKeys
from qarz.application.ports import AdminAccount, SecretCipher, Storage
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app
from qarz.interface.rate_limit import Limit, RateLimits

from .conftest import (
    ADMIN_API,
    TEST_BOT_TOKEN,
    AdminEnv,
    HeaderAuthenticator,
    World,
    as_user,
    elevate,
    fresh_code,
    make_admin,
)

pytestmark = pytest.mark.db

SETTINGS = f"{ADMIN_API}/settings"
KEYS = PaymentKeys(
    payme_merchant_id="test-merchant",
    payme_key="payme-test-key-not-a-real-one",
    click_service_id="70001",
    click_merchant_id="50001",
    click_key="click-test-key-not-a-real-one",
)


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"admin-{uuid.uuid4().hex}"}


class Ticks:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


def _app(app_database_url: str, env: AdminEnv, **more: Any) -> Iterator[TestClient]:
    database = Database(app_database_url)
    admin = AdminAccess(database, allowed_tg_ids=env.allowed, cipher=env.box, now=env.clock.now)
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        admin=admin,
        authenticator=HeaderAuthenticator(),
        now=env.clock.now,
        **more,
    )
    with TestClient(app) as client:
        yield client
        client.portal.call(database.dispose)  # type: ignore[union-attr]


# --- rate limits ----------------------------------------------------------------------------------------


@pytest.fixture
def ticks() -> Ticks:
    return Ticks()


@pytest.fixture
def limited(app_database_url: str, admin_env: AdminEnv, ticks: Ticks) -> Iterator[TestClient]:
    """Five requests at once per user, then one a minute; the shop limit is out of the way."""
    limits = RateLimits(user=Limit(per_minute=1, burst=5), shop=Limit(per_minute=6000, burst=6000))
    yield from _app(app_database_url, admin_env, rate_limits=limits, monotonic=ticks)


def test_the_per_user_rate_limit_covers_the_administrators_routes(
    limited: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, ticks: Ticks
) -> None:
    secret = make_admin(owner, admin_env, world.admin)
    headers = elevate(limited, admin_env, world.admin, secret)  # the first of the five
    for _ in range(4):
        assert limited.get(SETTINGS, headers=headers).status_code == 200
    before = owner.execute("SELECT count(*) FROM admin_audit").fetchone()

    over = limited.get(f"{ADMIN_API}/shops/{world.shop_a}", headers=headers)
    assert (over.status_code, over.json()["error"]["code"]) == (429, "RATE_LIMITED")
    assert int(over.headers["retry-after"]) >= 1
    assert owner.execute("SELECT count(*) FROM admin_audit").fetchone() == before, "nothing was done for it"
    change = limited.patch(SETTINGS, json={"changes": {"trial_days": 9}}, headers={**headers, **_key()})
    assert change.status_code == 429
    assert owner.execute("SELECT count(*) FROM platform_setting").fetchone() == (0,)

    # The same bucket as the rest of the API: an administrator has one rate, not two.
    assert limited.get("/api/v1/me", headers=headers).status_code == 429
    # Another user is not held back, and a minute later neither is this one.
    assert limited.get("/api/v1/me", headers=as_user(world.owner_a)).status_code == 200
    ticks.now += 60
    assert limited.get(SETTINGS, headers=headers).status_code == 200


def test_wrong_codes_are_held_to_the_rate_as_well_as_to_the_lock(
    limited: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    make_admin(owner, admin_env, world.admin)

    def attempt() -> str:
        response = limited.post(f"{ADMIN_API}/auth/session", json={"code": "000000"}, headers=as_user(world.admin))
        return str(response.json()["error"]["code"])

    def state() -> Any:
        return owner.execute(
            "SELECT failed_codes, locked_until FROM admin_account WHERE user_id = %s", (world.admin,)
        ).fetchone()

    within = [attempt() for _ in range(5)]
    assert "RATE_LIMITED" not in within
    counted = state()
    assert [attempt(), attempt()] == ["RATE_LIMITED", "RATE_LIMITED"]
    assert state() == counted, "a request over the rate is not even looked at"


def test_requests_without_a_sign_in_are_not_counted_on_the_administrators_routes(
    limited: TestClient, world: World
) -> None:
    for _ in range(8):
        assert limited.get(SETTINGS).status_code == 401
    assert limited.get("/api/v1/me", headers=as_user(world.stranger)).status_code == 200


# --- request size ---------------------------------------------------------------------------------------


def test_an_oversized_body_is_refused_before_the_administrators_side_reads_it(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    headers = elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))
    body = b'{"changes": {"trial_days": 9}, "reason": "' + b"x" * (1024 * 1024) + b'"}'
    for caller in (headers, as_user(world.owner_a), {}):
        response = client.patch(
            SETTINGS, content=body, headers={**caller, **_key(), "Content-Type": "application/json"}
        )
        assert response.status_code == 413, response.text
    assert owner.execute("SELECT count(*) FROM platform_setting").fetchone() == (0,)


# --- online payment behind the switch -------------------------------------------------------------------


@pytest.fixture
def with_keys(app_database_url: str, admin_env: AdminEnv) -> Iterator[TestClient]:
    yield from _app(app_database_url, admin_env, payment_keys=KEYS)


def test_turning_online_payment_on_in_the_panel_is_what_lets_an_owner_order(
    with_keys: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    """ADR-019: provider keys alone do nothing; the administrator's switch decides, without a release."""
    secret = make_admin(owner, admin_env, world.admin)
    admin = elevate(with_keys, admin_env, world.admin, secret)

    def order() -> Any:
        return with_keys.post(
            f"/api/v1/shops/{world.shop_a}/subscription/online-orders",
            json={"months": 1},
            headers={**as_user(world.owner_a), **_key()},
        )

    def switch(value: bool, **more: Any) -> Any:
        body = {"changes": {"online_pay_on": value}, **more}
        return with_keys.patch(SETTINGS, json=body, headers={**admin, **_key()})

    off = order()
    assert (off.status_code, off.json()["error"]["code"]) == (409, "ONLINE_PAY_OFF")
    assert with_keys.get(SETTINGS, headers=admin).json()["settings"]["online_pay_on"] is False

    # Without the code again the switch does not move, and the owner is still refused.
    assert switch(True).status_code == 422
    assert order().json()["error"]["code"] == "ONLINE_PAY_OFF"

    assert switch(True, code=fresh_code(admin_env, secret)).status_code == 200
    assert owner.execute("SELECT value FROM platform_setting WHERE key = 'online_pay_on'").fetchone() == (True,)
    placed = order()
    assert placed.status_code == 201, placed.text

    assert switch(False, code=fresh_code(admin_env, secret)).status_code == 200
    assert order().json()["error"]["code"] == "ONLINE_PAY_OFF"
    owner.execute("DELETE FROM online_payment WHERE shop_id = %s", (world.shop_a,))


def test_an_order_is_priced_at_what_the_administrator_set(
    with_keys: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    secret = make_admin(owner, admin_env, world.admin)
    admin = elevate(with_keys, admin_env, world.admin, secret)
    change = {"changes": {"online_pay_on": True, "price_uzs": 125_000}, "code": fresh_code(admin_env, secret)}
    assert with_keys.patch(SETTINGS, json=change, headers={**admin, **_key()}).status_code == 200
    placed = with_keys.post(
        f"/api/v1/shops/{world.shop_a}/subscription/online-orders",
        json={"months": 2},
        headers={**as_user(world.owner_a), **_key()},
    )
    assert placed.status_code == 201, placed.text
    row = owner.execute("SELECT months, amount FROM online_payment WHERE shop_id = %s", (world.shop_a,)).fetchone()
    assert row == (2, 250_000)
    owner.execute("DELETE FROM online_payment WHERE shop_id = %s", (world.shop_a,))


# --- one reading of the settings ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "stored"),
    [("price_uzs", "500"), ("price_uzs", "50000000"), ("price_uzs", '"150000"'), ("price_uzs", "true")],
)
def test_a_stored_price_outside_the_allowed_range_applies_nowhere(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, key: str, stored: str
) -> None:
    """What the panel shows as in force is what the owner is asked to pay: never two different prices."""
    admin = elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))
    owner.execute("INSERT INTO platform_setting (key, value, updated_by) VALUES (%s, %s::jsonb, 'test')", (key, stored))
    try:
        shown = client.get(SETTINGS, headers=admin).json()["settings"]["price_uzs"]
        asked = client.get(f"/api/v1/shops/{world.shop_a}/subscription", headers=as_user(world.owner_a)).json()
        assert shown == asked["price_uzs"] == 100_000
    finally:
        owner.execute("DELETE FROM platform_setting WHERE updated_by = 'test'")


@pytest.mark.parametrize(("stored", "days"), [("400", 30), ("0", 30), ('"14"', 30), ("14", 14)])
def test_a_stored_trial_length_outside_the_allowed_range_applies_nowhere(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, stored: str, days: int
) -> None:
    admin = elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('trial_days', %s::jsonb, 'test')", (stored,)
    )
    try:
        assert client.get(SETTINGS, headers=admin).json()["settings"]["trial_days"] == days
        created = client.post(
            "/api/v1/shops", json={"name": "Sinov", "lang": "uz"}, headers={**as_user(world.stranger), **_key()}
        )
        assert created.status_code == 201, created.text
        row = owner.execute(
            "SELECT trial_ends - (created_at AT TIME ZONE 'Asia/Tashkent')::date FROM subscription s "
            "JOIN shop ON shop.id = s.shop_id WHERE s.shop_id = %s",
            (created.json()["id"],),
        ).fetchone()
        assert row == (days,)
    finally:
        owner.execute("DELETE FROM platform_setting WHERE updated_by = 'test'")


# --- nothing of a shop outlives it in the administrator's tables ---------------------------------------


def test_a_stored_answer_keeps_only_the_shops_identifier(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    admin = elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))
    owner.execute("UPDATE shop SET name = 'Maxfiy Nomli Dokon' WHERE id = %s", (world.shop_a,))
    owner_tg = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (world.owner_a,)).fetchone()
    assert owner_tg is not None
    key = _key()
    path = f"{ADMIN_API}/shops/{world.shop_a}/suspend"
    first = client.post(path, json={"reason": "Tekshiruv uchun"}, headers={**admin, **key})
    assert first.status_code == 200, first.text
    assert first.json()["name"] == "Maxfiy Nomli Dokon"

    kept = owner.execute("SELECT response::text FROM admin_request_key WHERE admin_id = %s", (world.admin,)).fetchall()
    audit = owner.execute(
        "SELECT reason, detail::text FROM admin_audit WHERE target_shop = %s", (world.shop_a,)
    ).fetchall()
    written = " ".join(str(row) for row in kept + audit)
    assert str(world.shop_a) in written
    assert "Maxfiy Nomli Dokon" not in written
    assert str(owner_tg[0]) not in written

    # A repeat makes no second change and shows the shop as it is now.
    owner.execute("UPDATE shop SET name = 'Yangi Nom' WHERE id = %s", (world.shop_a,))
    again = client.post(path, json={"reason": "Tekshiruv uchun"}, headers={**admin, **key})
    assert again.status_code == 200, again.text
    assert again.json() == {**first.json(), "name": "Yangi Nom"}
    assert len(audit) == len(
        owner.execute("SELECT 1 FROM admin_audit WHERE target_shop = %s", (world.shop_a,)).fetchall()
    )


def test_after_a_shop_is_erased_the_audit_still_says_what_was_done_to_it(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    """The audit names a shop only by its identifier, so erasing the shop leaves nothing personal in it."""
    admin = elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))
    key = _key()
    path = f"{ADMIN_API}/shops/{world.shop_a}/suspend"
    assert client.post(path, json={"reason": "Yopilmoqda"}, headers={**admin, **key}).status_code == 200
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() - interval '1 minute' WHERE id = %s",
        (world.shop_a,),
    )
    assert owner.execute("SELECT erase_shop(%s)", (world.shop_a,)).fetchone() == (True,)

    rows = client.get(f"{ADMIN_API}/audit", params={"shop_id": str(world.shop_a)}, headers=admin).json()["items"]
    assert [(row["action"], row["reason"]) for row in rows] == [("subscription.suspended", "Yopilmoqda")]
    assert "Shop A" not in str(rows)
    # The repeat of the request shows what is left of the shop; nothing more can be done to it.
    repeat = client.post(path, json={"reason": "Yopilmoqda"}, headers={**admin, **key})
    assert (repeat.status_code, repeat.json()["status"]) == (200, "erased")
    assert (repeat.json()["staff_count"], repeat.json()["customer_count"]) == (0, 0)
    assert client.post(path, json={"reason": "Yana"}, headers={**admin, **_key()}).status_code == 404


# --- the account is asked for, whatever the schema guarantees -------------------------------------------


class _Session:
    """An admin session that is live although its user has no administrator account: what the schema's
    foreign key forbids, and what the application must refuse on its own as well."""

    def __init__(self, account: AdminAccount | None) -> None:
        self._account = account

    async def telegram_id(self, user_id: Any) -> int:
        return 42

    async def admin_account(self, user_id: Any, *, for_update: bool) -> AdminAccount | None:
        return self._account

    async def admin_session_expiry(self, token_hash: bytes, user_id: Any, now: datetime) -> datetime:
        return now + timedelta(hours=1)


class _Storage:
    def __init__(self, session: _Session) -> None:
        self._session = session

    @asynccontextmanager
    async def platform(self) -> AsyncIterator[_Session]:
        yield self._session


def _gate(account: AdminAccount | None) -> None:
    access = AdminAccess(
        cast(Storage, _Storage(_Session(account))),
        allowed_tg_ids={42},
        cipher=cast(SecretCipher, None),
        now=lambda: datetime(2026, 10, 7, tzinfo=UTC),
    )
    asyncio.run(access.require_admin(uuid.uuid4(), "t" * 43))


def test_a_live_admin_session_without_an_account_is_refused_by_the_application_itself() -> None:
    _gate(AdminAccount("active", b"x", True, 0, None, None))  # the control: with an account it passes
    with pytest.raises(NotFound):
        _gate(None)
    with pytest.raises(NotFound):
        _gate(AdminAccount("disabled", b"x", True, 0, None, None))
