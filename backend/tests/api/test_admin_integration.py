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


def _app(app_database_url: str, admin_database_url: str, env: AdminEnv, **more: Any) -> Iterator[TestClient]:
    database, admin_database = Database(app_database_url), Database(admin_database_url)
    admin = AdminAccess(admin_database, allowed_tg_ids=env.allowed, cipher=env.box, now=env.clock.now)
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        admin=admin,
        admin_storage=admin_database,
        authenticator=HeaderAuthenticator(),
        now=env.clock.now,
        **more,
    )
    with TestClient(app) as client:
        yield client
        client.portal.call(database.dispose)  # type: ignore[union-attr]
        client.portal.call(admin_database.dispose)  # type: ignore[union-attr]


# --- rate limits ----------------------------------------------------------------------------------------


@pytest.fixture
def ticks() -> Ticks:
    return Ticks()


@pytest.fixture
def limited(app_database_url: str, admin_database_url: str, admin_env: AdminEnv, ticks: Ticks) -> Iterator[TestClient]:
    """Five requests at once per user, then one a minute; the shop limit is out of the way."""
    limits = RateLimits(user=Limit(per_minute=1, burst=5), shop=Limit(per_minute=6000, burst=6000))
    yield from _app(app_database_url, admin_database_url, admin_env, rate_limits=limits, monotonic=ticks)


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
def with_keys(app_database_url: str, admin_database_url: str, admin_env: AdminEnv) -> Iterator[TestClient]:
    yield from _app(app_database_url, admin_database_url, admin_env, payment_keys=KEYS)


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


def test_a_stored_answer_is_kept_under_its_shop_and_the_audit_names_the_shop_only_by_identifier(
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
    # A change that is about no shop is kept under none.
    assert client.patch(SETTINGS, json={"changes": {"trial_days": 9}}, headers={**admin, **_key()}).status_code == 200

    kept = owner.execute(
        "SELECT about_shop, response->'body'->>'name' FROM admin_request_key WHERE admin_id = %s ORDER BY about_shop",
        (world.admin,),
    ).fetchall()
    assert kept == [(world.shop_a, "Maxfiy Nomli Dokon"), (None, None)]
    audit = owner.execute(
        "SELECT reason, detail::text FROM admin_audit WHERE target_shop = %s", (world.shop_a,)
    ).fetchall()
    assert len(audit) == 1
    assert "Maxfiy Nomli Dokon" not in str(audit)
    assert str(owner_tg[0]) not in str(audit)


def test_erasing_a_shop_erases_the_stored_answers_about_it_and_leaves_the_audit(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    admin = elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))
    key = _key()
    path = f"{ADMIN_API}/shops/{world.shop_a}/suspend"
    first = client.post(path, json={"reason": "Yopilmoqda"}, headers={**admin, **key})
    assert first.status_code == 200
    other = client.post(
        f"{ADMIN_API}/shops/{world.shop_b}/suspend", json={"reason": "Boshqa"}, headers={**admin, **_key()}
    )
    assert other.status_code == 200
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() - interval '1 minute' WHERE id = %s",
        (world.shop_a,),
    )
    assert owner.execute("SELECT erase_shop(%s)", (world.shop_a,)).fetchone() == (True,)

    kept = owner.execute("SELECT about_shop FROM admin_request_key WHERE admin_id = %s", (world.admin,)).fetchall()
    assert kept == [(world.shop_b,)]
    # The audit still says what was done, naming the shop only by its identifier.
    rows = client.get(f"{ADMIN_API}/audit", params={"shop_id": str(world.shop_a)}, headers=admin).json()["items"]
    assert [(row["action"], row["reason"]) for row in rows] == [("subscription.suspended", "Yopilmoqda")]
    assert "Shop A" not in str(rows)
    # With its stored answer gone the same request is a new one, and there is nothing left to change.
    assert client.post(path, json={"reason": "Yopilmoqda"}, headers={**admin, **key}).status_code == 404
    listed = client.get(f"{ADMIN_API}/shops/{world.shop_a}", headers=admin).json()
    assert (listed["name"], listed["status"], listed["owner_tg_id"]) == ("erased", "erased", None)


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


# --- observability --------------------------------------------------------------------------------------

METRICS_TOKEN = "a-metrics-token-for-tests"


@pytest.fixture
def observed(app_database_url: str, admin_database_url: str, admin_env: AdminEnv, ticks: Ticks) -> Iterator[TestClient]:
    limits = RateLimits(user=Limit(per_minute=1, burst=3), shop=Limit(per_minute=6000, burst=6000))
    yield from _app(
        app_database_url,
        admin_database_url,
        admin_env,
        metrics_token=METRICS_TOKEN,
        rate_limits=limits,
        monotonic=ticks,
    )


def _second_factor_events(client: TestClient) -> int:
    text = client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"}).text
    for line in text.splitlines():
        if line.startswith('qd_security_events_total{kind="bad_second_factor"}'):
            return int(line.split()[-1])
    return 0


def test_a_refused_second_factor_is_a_security_event_and_a_rate_limit_is_not(
    observed: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    ticks: Ticks,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import json
    import logging

    caplog.set_level(logging.INFO, logger="qarz.request")
    secret = make_admin(owner, admin_env, world.admin)
    session = f"{ADMIN_API}/auth/session"

    def attempt(code: str) -> Any:
        return observed.post(session, json={"code": code}, headers=as_user(world.admin))

    assert _second_factor_events(observed) == 0
    assert attempt("000000").status_code == 403
    assert _second_factor_events(observed) == 1
    events = [record for record in caplog.records if record.getMessage() == "security"]
    assert [(record.__dict__["kind"], record.__dict__["route"]) for record in events] == [
        ("bad_second_factor", session)
    ]
    assert str(events[0].__dict__["user_id"]) == str(world.admin)
    assert "000000" not in json.dumps([str(record.__dict__) for record in caplog.records])

    # The right code is no event, and its request is logged with the caller.
    opened = attempt(fresh_code(admin_env, secret))
    assert opened.status_code == 201
    assert _second_factor_events(observed) == 1
    logged = [r for r in caplog.records if r.getMessage() == "request" and r.__dict__.get("route") == session]
    assert str(logged[-1].__dict__["user_id"]) == str(world.admin)

    # A wrong code on a change that asks for the code again counts too.
    headers = {
        **as_user(world.admin),
        "Cookie": f"qd_admin={opened.headers['set-cookie'].split('qd_admin=')[1].split(';')[0]}",
    }
    change = {"changes": {"price_uzs": 2_000}, "code": "000000"}
    assert observed.patch(SETTINGS, json=change, headers={**headers, **_key()}).status_code == 403
    assert _second_factor_events(observed) == 2

    # Over the rate now: 429 on the same route, which is not a refused code.
    over = attempt("000000")
    assert (over.status_code, over.json()["error"]["code"]) == (429, "RATE_LIMITED")
    assert _second_factor_events(observed) == 2

    # A locked factor answers 429 as well, and that one is an event.
    owner.execute(
        "UPDATE admin_account SET locked_until = %s WHERE user_id = %s",
        (admin_env.clock.now() + timedelta(minutes=5), world.admin),
    )
    ticks.now += 600
    locked = attempt("000000")
    assert (locked.status_code, locked.json()["error"]["code"]) == (429, "SECOND_FACTOR_LOCKED")
    assert _second_factor_events(observed) == 3


def test_a_refused_administrator_route_is_logged_with_the_caller(
    observed: TestClient, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="qarz.request")
    assert observed.get(SETTINGS, headers=as_user(world.owner_a)).status_code == 404
    logged = [r for r in caplog.records if r.getMessage() == "request" and r.__dict__.get("route") == SETTINGS]
    assert [(str(r.__dict__["user_id"]), r.__dict__["status"]) for r in logged] == [(str(world.owner_a), 404)]
