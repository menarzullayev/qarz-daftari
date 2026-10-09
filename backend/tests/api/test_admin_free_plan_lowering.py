"""Lowering how many customers the free plan holds (BR-33; module A, behind `free_plan_on`).

A lower number limits the shops the plan holds today that are over it. The administrator is told how
many they are before saving, and their owners are told once, through the outbox, what happened and how
to leave the limited mode. No shop loses anything it wrote or the right to read it. Raising the number,
a shop in a trial or paid period, and the switch off: nobody is counted and nobody is told.
"""

import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import say

from .conftest import ADMIN_API, AdminEnv, World, as_user, elevate, fresh_code, make_admin
from .test_admin_free_plan import ACTIVE_IN_A, _customers
from .test_admin_shops import _credit, _set, _today

pytestmark = pytest.mark.db

SETTINGS = f"{ADMIN_API}/settings"
SHOPS = f"{ADMIN_API}/shops"
FreePlan = Callable[[int], None]
SETTINGS_KEYS = {"settings", "needs_code", "changed"}


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"admin-{uuid.uuid4().hex}"}


@pytest.fixture
def secret(world: World, owner: psycopg.Connection, admin_env: AdminEnv) -> bytes:
    admin_env.clock.freeze()
    return make_admin(owner, admin_env, world.admin)


@pytest.fixture
def admin(client: TestClient, world: World, admin_env: AdminEnv, secret: bytes) -> dict[str, str]:
    """Headers of an administrator who passed the second factor."""
    return elevate(client, admin_env, world.admin, secret)


def _save(client: TestClient, admin: dict[str, str], changes: dict[str, Any], **more: Any) -> dict[str, Any]:
    response = client.patch(SETTINGS, json={"changes": changes, **more}, headers={**admin, **_key()})
    assert response.status_code == 200, response.text
    return dict(response.json())


def _lower(client: TestClient, admin: dict[str, str], customers: int) -> dict[str, Any]:
    return _save(client, admin, {"free_plan_customers": customers})


def _preview(client: TestClient, admin: dict[str, str], customers: object) -> Any:
    return client.get(SETTINGS, params={"free_plan_customers": customers}, headers=admin)


def _told(owner: psycopg.Connection, shop: uuid.UUID) -> list[tuple[str, str, str]]:
    """What the outbox holds for the shop about a lowered plan: who, the text, and the key it is kept under."""
    rows = owner.execute(
        "SELECT recipient, payload->>'text', dedupe_key FROM outbox_message "
        "WHERE shop_id = %s AND channel = 'telegram' AND dedupe_key LIKE 'free_plan:%%' ORDER BY created_at, id",
        (shop,),
    ).fetchall()
    return [(str(recipient), str(text), str(key)) for recipient, text, key in rows]


def _chat(owner: psycopg.Connection, user: uuid.UUID) -> str:
    row = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (user,)).fetchone()
    assert row is not None
    return str(row[0])


def _leaving(owner: psycopg.Connection, today: date, *, held: int, lowered_to: int) -> set[uuid.UUID]:
    """The shops of the whole database (it is shared by every test) that have no running period and more
    than `lowered_to` but no more than `held` active customers, straight from the tables."""
    rows = owner.execute(
        "SELECT s.id FROM shop s LEFT JOIN subscription sub ON sub.shop_id = s.id "
        "CROSS JOIN LATERAL (SELECT count(*) AS n FROM customer c WHERE c.shop_id = s.id AND c.status = 'active') c "
        "WHERE c.n > %(low)s AND c.n <= %(held)s AND coalesce(sub.state, 'limited') <> 'suspended' AND NOT "
        "  coalesce((sub.state = 'trial' AND sub.trial_ends >= %(today)s) "
        "        OR (sub.state = 'active' AND sub.paid_through >= %(today)s), false)",
        {"low": lowered_to, "held": held, "today": today},
    ).fetchall()
    return {row[0] for row in rows}


def _state(client: TestClient, admin: dict[str, str], shop: uuid.UUID) -> str:
    return str(client.get(f"{SHOPS}/{shop}", headers=admin).json()["subscription"]["state"])


# --- before saving: how many shops a lower number would limit ----------------------------------------------


def test_the_settings_say_how_many_shops_a_lower_number_would_limit_and_change_nothing(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    free_plan: FreePlan,
) -> None:
    today = _today(admin_env)
    _set(owner, world.shop_a, "limited")
    _set(owner, world.shop_b, "limited")
    _customers(owner, world.shop_b, 4)
    free_plan(5)

    plain = client.get(SETTINGS, headers=admin)
    assert set(plain.json()) == SETTINGS_KEYS, "unasked, the answer is the settings and nothing else"

    asked = _preview(client, admin, 3)
    assert asked.status_code == 200, asked.text
    leaving = _leaving(owner, today, held=5, lowered_to=3)
    assert world.shop_b in leaving and world.shop_a not in leaving, "four customers do not fit in three; two do"
    assert asked.json() == {**plain.json(), "free_plan_preview": {"customers": 3, "shops_limited": len(leaving)}}

    lower = _preview(client, admin, 1).json()["free_plan_preview"]
    assert lower == {"customers": 1, "shops_limited": len(_leaving(owner, today, held=5, lowered_to=1))}
    assert lower["shops_limited"] >= asked.json()["free_plan_preview"]["shops_limited"] + 1, "shop A as well"

    # The same number, or a higher one, limits nobody.
    assert _preview(client, admin, 5).json()["free_plan_preview"] == {"customers": 5, "shops_limited": 0}
    assert _preview(client, admin, 9).json()["free_plan_preview"] == {"customers": 9, "shops_limited": 0}

    # Asking is not saving: nothing stored, nobody told, both shops still free.
    assert client.get(SETTINGS, headers=admin).json() == plain.json()
    assert _told(owner, world.shop_a) == _told(owner, world.shop_b) == []
    assert (_state(client, admin, world.shop_a), _state(client, admin, world.shop_b)) == ("free", "free")


@pytest.mark.parametrize("asked", ["0", "10001", "abc", "1.5", "-3", "", " 4", "٤"])
def test_a_number_the_plan_cannot_hold_is_refused(
    client: TestClient, admin: dict[str, str], free_plan: FreePlan, asked: str
) -> None:
    free_plan(5)
    response = _preview(client, admin, asked)
    assert response.status_code == 422, response.text
    assert list(response.json()["error"]["fields"]) == ["free_plan_customers"]


def test_a_shop_in_a_trial_or_paid_period_or_suspended_is_not_counted(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    free_plan: FreePlan,
) -> None:
    today = _today(admin_env)
    free_plan(5)

    def counted() -> int:
        return int(_preview(client, admin, 1).json()["free_plan_preview"]["shops_limited"])

    _set(owner, world.shop_a, "trial", trial_ends=today)
    _set(owner, world.shop_b, "active", paid_through=today + timedelta(days=30))
    _customers(owner, world.shop_b, 3)
    without = counted()
    _set(owner, world.shop_a, "suspended", prior_state="limited")
    assert counted() == without
    _set(owner, world.shop_a, "limited")
    assert counted() == without + 1, "the same shop, once no period runs"
    _set(owner, world.shop_b, "active", paid_through=today - timedelta(days=1))
    assert counted() == without + 2, "a period that ended yesterday is no period"


# --- saving a lower number ---------------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["uz", "ru", "tg", "kaa", "en"])
def test_lowering_tells_the_owner_once_and_the_shop_keeps_its_data_and_reads_it(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    free_plan: FreePlan,
    lang: str,
) -> None:
    today = _today(admin_env)
    owner.execute("UPDATE app_user SET lang = %s WHERE id = %s", (lang, world.owner_a))
    _set(owner, world.shop_a, "limited")
    free_plan(5)
    assert _state(client, admin, world.shop_a) == "free"
    assert _credit(client, world).status_code == 201, "a free shop works in full"
    before = owner.execute(
        "SELECT (SELECT count(*) FROM customer WHERE shop_id = %(s)s), "
        "(SELECT count(*) FROM ledger_entry WHERE shop_id = %(s)s)",
        {"s": world.shop_a},
    ).fetchone()
    expected = len(_leaving(owner, today, held=5, lowered_to=1))

    body = _lower(client, admin, 1)
    assert body["settings"]["free_plan_customers"] == 1
    assert body["free_plan_lowered"] == {"customers": 1, "shops_limited": expected}
    assert set(body) == SETTINGS_KEYS | {"free_plan_lowered"}

    text = say(lang, "free_plan_lowered", shop="Shop A", limit=1, used=ACTIVE_IN_A)
    assert "/obuna" in text and "Shop A" in text
    assert _told(owner, world.shop_a) == [
        (_chat(owner, world.owner_a), text, f"free_plan:lowered:{world.shop_a}:1:{today.isoformat()}")
    ]
    assert _state(client, admin, world.shop_a) == "limited"

    # The limited mode: no new credit sale, and everything written is still there and still read.
    refused = _credit(client, world)
    assert (refused.status_code, refused.json()["error"]["code"]) == (402, "SUBSCRIPTION_LIMITED")
    after = owner.execute(
        "SELECT (SELECT count(*) FROM customer WHERE shop_id = %(s)s), "
        "(SELECT count(*) FROM ledger_entry WHERE shop_id = %(s)s)",
        {"s": world.shop_a},
    ).fetchone()
    assert after == before, "nothing of the shop was removed"
    customers = client.get(f"/api/v1/shops/{world.shop_a}/customers", headers=as_user(world.owner_a))
    assert customers.status_code == 200, customers.text
    assert {item["id"] for item in customers.json()["items"]} >= {str(world.customer_a), str(world.settled_customer_a)}
    one = client.get(f"/api/v1/shops/{world.shop_a}/customers/{world.customer_a}", headers=as_user(world.seller_a))
    assert one.status_code == 200, one.text
    payment = client.post(
        f"/api/v1/shops/{world.shop_a}/customers/{world.customer_a}/entries",
        json={"kind": "payment", "amount": 1000},
        headers={**as_user(world.seller_a), **_key()},
    )
    assert payment.status_code == 201, "a limited shop still takes payments"


def test_raising_the_number_or_keeping_it_tells_nobody(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], free_plan: FreePlan
) -> None:
    _set(owner, world.shop_a, "limited")
    _set(owner, world.shop_b, "limited")
    _customers(owner, world.shop_b, 4)
    free_plan(ACTIVE_IN_A)
    for customers in (ACTIVE_IN_A, ACTIVE_IN_A + 3, ACTIVE_IN_A + 3):
        body = _lower(client, admin, customers)
        assert set(body) == SETTINGS_KEYS, "nothing was lowered, so nothing is said of it"
        assert body["settings"]["free_plan_customers"] == customers
    assert _told(owner, world.shop_a) == _told(owner, world.shop_b) == []


def test_a_shop_in_a_trial_or_paid_period_or_suspended_is_not_told(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    free_plan: FreePlan,
) -> None:
    today = _today(admin_env)
    free_plan(5)
    for stored, dates in (
        ("trial", {"trial_ends": today}),
        ("active", {"paid_through": today}),
        ("suspended", {"prior_state": "limited"}),
    ):
        _set(owner, world.shop_a, stored, **dates)
        expected = len(_leaving(owner, today, held=5, lowered_to=1))
        assert _lower(client, admin, 1)["free_plan_lowered"]["shops_limited"] == expected
        assert _told(owner, world.shop_a) == [], stored
        assert _lower(client, admin, 5)["settings"]["free_plan_customers"] == 5
    # The negative of the negative: the same shop without a period is told.
    _set(owner, world.shop_a, "limited")
    _lower(client, admin, 1)
    assert len(_told(owner, world.shop_a)) == 1


def test_the_same_lower_number_saved_again_tells_the_owner_once(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], free_plan: FreePlan
) -> None:
    _set(owner, world.shop_a, "limited")
    _customers(owner, world.shop_a, 2)  # four active customers
    free_plan(6)

    key = _key()
    first = client.patch(SETTINGS, json={"changes": {"free_plan_customers": 3}}, headers={**admin, **key})
    assert first.status_code == 200, first.text
    assert len(_told(owner, world.shop_a)) == 1
    # The very same request again: the stored answer, and nothing done twice.
    again = client.patch(SETTINGS, json={"changes": {"free_plan_customers": 3}}, headers={**admin, **key})
    assert (again.status_code, again.json()) == (200, first.json())
    assert len(_told(owner, world.shop_a)) == 1

    # Saved again as a new request: the plan already holds three, so nothing is lowered.
    assert "free_plan_lowered" not in _lower(client, admin, 3)
    assert len(_told(owner, world.shop_a)) == 1

    # Raised and lowered to the same number on the same day: the shop is limited again, and the outbox
    # still holds the one message.
    assert "free_plan_lowered" not in _lower(client, admin, 6)
    assert _lower(client, admin, 3)["free_plan_lowered"]["shops_limited"] >= 1
    assert len(_told(owner, world.shop_a)) == 1

    # Lowered further: the shop was limited already, so it is not among those the change limits.
    _lower(client, admin, 2)
    assert len(_told(owner, world.shop_a)) == 1


def test_a_shop_without_an_owner_to_tell_is_counted_and_the_change_is_saved(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    free_plan: FreePlan,
) -> None:
    _set(owner, world.shop_a, "limited")
    owner.execute("UPDATE membership SET status = 'suspended' WHERE id = %s", (world.owner_a_membership,))
    free_plan(5)
    expected = len(_leaving(owner, _today(admin_env), held=5, lowered_to=1))
    assert world.shop_a in _leaving(owner, _today(admin_env), held=5, lowered_to=1)
    assert _lower(client, admin, 1)["free_plan_lowered"] == {"customers": 1, "shops_limited": expected}
    assert _told(owner, world.shop_a) == []


# --- the switch off ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("switch", ["false", None, '"true"', "1"])
def test_with_the_switch_off_nothing_is_previewed_and_nobody_is_told(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], switch: str | None
) -> None:
    owner.execute("DELETE FROM platform_setting WHERE key IN ('free_plan_on', 'free_plan_customers')")
    owner.execute("INSERT INTO platform_setting (key, value, updated_by) VALUES ('free_plan_customers', '5', 'test')")
    if switch is not None:
        owner.execute(
            "INSERT INTO platform_setting (key, value, updated_by) VALUES ('free_plan_on', %s::jsonb, 'test')",
            (switch,),
        )
    try:
        _set(owner, world.shop_a, "limited")
        plain = client.get(SETTINGS, headers=admin)
        assert set(plain.json()) == SETTINGS_KEYS
        # The question is not looked at: a number, and what is no number, are answered alike.
        for asked in ("1", "abc", "0"):
            response = _preview(client, admin, asked)
            assert (response.status_code, response.content) == (200, plain.content)

        body = _lower(client, admin, 1)
        assert set(body) == SETTINGS_KEYS
        assert body["settings"]["free_plan_customers"] == 1
        assert _told(owner, world.shop_a) == []
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key IN ('free_plan_on', 'free_plan_customers')")


def test_switching_the_plan_on_or_off_together_with_a_lower_number_tells_nobody(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    """Lowering is a change of a plan that is on and stays on. Switched on with a lower number, the shops
    were limited before and some become free; switched off, the plan is gone for all, which is the
    switch's own meaning and not a lowering."""
    _set(owner, world.shop_a, "limited")
    owner.execute("DELETE FROM platform_setting WHERE key IN ('free_plan_on', 'free_plan_customers')")
    owner.execute("INSERT INTO platform_setting (key, value, updated_by) VALUES ('free_plan_customers', '5', 'test')")
    try:
        on = _save(client, admin, {"free_plan_on": True, "free_plan_customers": 1}, code=fresh_code(admin_env, secret))
        assert set(on) == SETTINGS_KEYS
        assert _save(client, admin, {"free_plan_customers": 5}) and _state(client, admin, world.shop_a) == "free"
        off = _save(
            client, admin, {"free_plan_on": False, "free_plan_customers": 1}, code=fresh_code(admin_env, secret)
        )
        assert set(off) == SETTINGS_KEYS
        assert _told(owner, world.shop_a) == []
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key IN ('free_plan_on', 'free_plan_customers')")
