"""Switching the free plan off (BR-33; module A, behind `free_plan_on`).

Switched off, the plan holds nobody: every shop it held is limited at once. The administrator is told how
many they are before saving, and their owners are told once, through the outbox, what happened and how
to leave the limited mode. No shop loses anything it wrote or the right to read it. A shop in a trial or
paid period, a suspended one, one being deleted and an erased one are neither counted nor told; switching
the plan on, or leaving it as it is, tells nobody.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
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
    # Every code asked for moves this test's clock half a minute on, and one test here goes on to the next
    # day. The audit is shared by the whole session and read newest first, so the test starts far enough
    # back that nothing it writes is dated after the tests that follow it.
    admin_env.clock.offset = -timedelta(days=1, minutes=30)
    admin_env.clock.freeze()
    return make_admin(owner, admin_env, world.admin)


@pytest.fixture
def admin(client: TestClient, world: World, admin_env: AdminEnv, secret: bytes) -> dict[str, str]:
    """Headers of an administrator who passed the second factor."""
    return elevate(client, admin_env, world.admin, secret)


@pytest.fixture
def save(
    client: TestClient, admin: dict[str, str], admin_env: AdminEnv, secret: bytes
) -> Callable[..., dict[str, Any]]:
    """Save a change of the settings with a fresh code, as the switch asks for one."""

    def send(changes: dict[str, Any]) -> dict[str, Any]:
        response = client.patch(
            SETTINGS,
            json={"changes": changes, "code": fresh_code(admin_env, secret)},
            headers={**admin, **_key()},
        )
        assert response.status_code == 200, response.text
        return dict(response.json())

    return send


Save = Callable[[dict[str, Any]], dict[str, Any]]


def _preview(client: TestClient, admin: dict[str, str], **asked: object) -> Any:
    return client.get(SETTINGS, params={"free_plan_on": "false", **asked}, headers=admin)


def _counted(client: TestClient, admin: dict[str, str]) -> int:
    return int(_preview(client, admin).json()["free_plan_off_preview"]["shops_limited"])


def _told(owner: psycopg.Connection, shop: uuid.UUID) -> list[tuple[str, str, str]]:
    """What the outbox holds for the shop about the free plan: who, the text, and the key it is kept under."""
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


_FREE = (
    "FROM shop s LEFT JOIN subscription sub ON sub.shop_id = s.id "
    "CROSS JOIN LATERAL (SELECT count(*) AS n FROM customer c WHERE c.shop_id = s.id AND c.status = 'active') c "
    "WHERE s.status = 'active' AND c.n <= %(held)s AND coalesce(sub.state, 'limited') <> 'suspended' AND NOT "
    "  coalesce((sub.state = 'trial' AND sub.trial_ends >= %(today)s) "
    "        OR (sub.state = 'active' AND sub.paid_through >= %(today)s), false)"
)


def _free(owner: psycopg.Connection, today: date, *, held: int) -> set[uuid.UUID]:
    """The shops of the whole database (it is shared by every test) the plan holds at `held` customers:
    not being deleted, without a running period, not suspended, and with no more active customers than
    that. Straight from the tables."""
    rows = owner.execute("SELECT s.id " + _FREE, {"held": held, "today": today}).fetchall()
    return {row[0] for row in rows}


def _free_with_an_owner(owner: psycopg.Connection, today: date, *, held: int) -> set[uuid.UUID]:
    """Those of them that have somebody to tell: an active owner."""
    rows = owner.execute(
        "SELECT s.id " + _FREE + " AND sub.shop_id IS NOT NULL AND EXISTS (SELECT 1 FROM membership m "
        "JOIN app_user u ON u.id = m.user_id "
        "WHERE m.shop_id = s.id AND m.role = 'owner' AND m.status = 'active' AND u.tg_id IS NOT NULL)",
        {"held": held, "today": today},
    ).fetchall()
    return {row[0] for row in rows}


def _told_off(owner: psycopg.Connection, today: date) -> set[uuid.UUID]:
    """Every shop the outbox holds a switched-off message of that day for."""
    rows = owner.execute(
        "SELECT shop_id FROM outbox_message WHERE dedupe_key = 'free_plan:off:' || shop_id || ':' || %s",
        (today.isoformat(),),
    ).fetchall()
    return {row[0] for row in rows}


def _state(client: TestClient, admin: dict[str, str], shop: uuid.UUID) -> str:
    return str(client.get(f"{SHOPS}/{shop}", headers=admin).json()["subscription"]["state"])


# --- before saving: how many shops switching the plan off would limit --------------------------------------


def test_the_settings_say_how_many_shops_switching_the_plan_off_would_limit_and_change_nothing(
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
    free_plan(3)

    plain = client.get(SETTINGS, headers=admin)
    asked = _preview(client, admin)
    assert asked.status_code == 200, asked.text
    free = _free(owner, today, held=3)
    assert world.shop_a in free and world.shop_b not in free, "four customers are not held by a plan of three"
    assert asked.json() == {**plain.json(), "free_plan_off_preview": {"shops_limited": len(free)}}

    # Every shop the plan holds is counted, not only those over some number: more than any lowering limits.
    lowering = client.get(SETTINGS, params={"free_plan_customers": 2}, headers=admin).json()["free_plan_preview"]
    assert lowering["shops_limited"] < len(free)

    # Off together with a number: the number is not looked at, whatever it holds, and nothing is said of it.
    for number in ("1", "9", "abc", "0"):
        both = _preview(client, admin, free_plan_customers=number)
        assert (both.status_code, both.json()) == (200, asked.json()), number

    # Asking about the plan staying on is asking nothing; with a number it is the lowering question alone.
    on = client.get(SETTINGS, params={"free_plan_on": "true"}, headers=admin)
    assert (on.status_code, on.json()) == (200, plain.json())
    on_lower = client.get(SETTINGS, params={"free_plan_on": "true", "free_plan_customers": 2}, headers=admin)
    assert on_lower.json() == {**plain.json(), "free_plan_preview": lowering}

    # Asking is not saving: nothing stored, nobody told, the shop still free.
    assert client.get(SETTINGS, headers=admin).json() == plain.json()
    assert plain.json()["settings"]["free_plan_on"] is True
    assert _told(owner, world.shop_a) == _told(owner, world.shop_b) == []
    assert _state(client, admin, world.shop_a) == "free"
    assert _credit(client, world).status_code == 201, "a free shop works in full"


@pytest.mark.parametrize("asked", ["0", "1", "no", "False", "FALSE", "", " false", "null"])
def test_a_switch_that_is_neither_true_nor_false_is_refused(
    client: TestClient, admin: dict[str, str], free_plan: FreePlan, asked: str
) -> None:
    free_plan(5)
    response = client.get(SETTINGS, params={"free_plan_on": asked}, headers=admin)
    assert response.status_code == 422, response.text
    assert list(response.json()["error"]["fields"]) == ["free_plan_on"]


def test_a_shop_in_a_period_or_suspended_or_being_deleted_or_erased_is_not_counted(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    free_plan: FreePlan,
) -> None:
    today = _today(admin_env)
    free_plan(5)
    _set(owner, world.shop_a, "trial", trial_ends=today)
    _set(owner, world.shop_b, "active", paid_through=today + timedelta(days=30))
    without = _counted(client, admin)
    _set(owner, world.shop_a, "suspended", prior_state="limited")
    assert _counted(client, admin) == without
    _set(owner, world.shop_a, "limited")
    assert _counted(client, admin) == without + 1, "the same shop, once no period runs"
    _set(owner, world.shop_b, "active", paid_through=today - timedelta(days=1))
    assert _counted(client, admin) == without + 2, "a period that ended yesterday is no period"
    # Over the number the shop is limited already: switching the plan off changes nothing for it.
    _customers(owner, world.shop_b, 6)
    assert _counted(client, admin) == without + 1
    for status in ("deletion_pending", "erased"):
        owner.execute(
            "UPDATE shop SET status = %s, deletion_due = now() + interval '7 days' WHERE id = %s",
            (status, world.shop_a),
        )
        assert _counted(client, admin) == without, status
    owner.execute("UPDATE shop SET status = 'active', deletion_due = NULL WHERE id = %s", (world.shop_a,))
    assert _counted(client, admin) == without + 1


# --- switching the plan off --------------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["uz", "uz-Cyrl", "ru", "tg", "kaa", "en"])
def test_switching_off_tells_the_owner_once_and_the_shop_keeps_its_data_and_reads_it(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    free_plan: FreePlan,
    save: Save,
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
    free = _free(owner, today, held=5)
    expected = _counted(client, admin)
    assert expected == len(free) and world.shop_a in free

    body = save({"free_plan_on": False})
    assert body["settings"]["free_plan_on"] is False
    assert body["free_plan_off"] == {"shops_limited": expected}, "as many as the question before it said"
    assert set(body) == SETTINGS_KEYS | {"free_plan_off"}

    text = say(lang, "free_plan_off", shop="Shop A")
    assert "/obuna" in text and "Shop A" in text and "{" not in text
    assert text != say("uz", "free_plan_off", shop="Shop A") or lang == "uz", "in the owner's own language"
    assert _told(owner, world.shop_a) == [
        (_chat(owner, world.owner_a), text, f"free_plan:off:{world.shop_a}:{today.isoformat()}")
    ]
    # Each shop the plan held that has an owner holds one message of today, and no other shop got one.
    told = _told_off(owner, today)
    assert told & free == _free_with_an_owner(owner, today, held=5)
    assert world.shop_b not in told, "a shop in a period is told nothing"
    assert client.get(f"{SHOPS}/{world.shop_a}", headers=admin).json()["subscription"]["state"] == "limited"

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


def test_a_shop_in_a_period_or_suspended_or_being_deleted_or_over_the_number_is_not_told(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    free_plan: FreePlan,
    save: Save,
) -> None:
    today = _today(admin_env)
    for stored, dates in (
        ("trial", {"trial_ends": today}),
        ("active", {"paid_through": today}),
        ("suspended", {"prior_state": "limited"}),
    ):
        free_plan(5)
        _set(owner, world.shop_a, stored, **dates)
        expected = len(_free(owner, today, held=5))
        assert save({"free_plan_on": False})["free_plan_off"] == {"shops_limited": expected}
        assert _told(owner, world.shop_a) == [], stored
    # Without a period, but not held by the plan: over its number, or on the way out.
    _set(owner, world.shop_a, "limited")
    free_plan(ACTIVE_IN_A - 1)
    assert _state(client, admin, world.shop_a) == "limited"
    save({"free_plan_on": False})
    assert _told(owner, world.shop_a) == [], "it was limited before the switch and is limited after it"
    free_plan(5)
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() + interval '7 days' WHERE id = %s",
        (world.shop_a,),
    )
    save({"free_plan_on": False})
    assert _told(owner, world.shop_a) == [], "a shop being deleted"
    # The negative of the negative: the same shop, held by the plan, is told.
    owner.execute("UPDATE shop SET status = 'active', deletion_due = NULL WHERE id = %s", (world.shop_a,))
    free_plan(5)
    save({"free_plan_on": False})
    assert len(_told(owner, world.shop_a)) == 1


def test_the_plan_switched_off_again_the_same_day_tells_the_owner_once(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
    free_plan: FreePlan,
    save: Save,
) -> None:
    _set(owner, world.shop_a, "limited")
    free_plan(5)

    key = _key()
    sent = {"changes": {"free_plan_on": False}, "code": fresh_code(admin_env, secret)}
    first = client.patch(SETTINGS, json=sent, headers={**admin, **key})
    assert first.status_code == 200, first.text
    assert len(_told(owner, world.shop_a)) == 1
    # The very same request again: the stored answer, and nothing done twice.
    again = client.patch(SETTINGS, json=sent, headers={**admin, **key})
    assert (again.status_code, again.json()) == (200, first.json())
    assert len(_told(owner, world.shop_a)) == 1

    # Saved again as a new request: the plan is off already, so nothing is switched off.
    assert set(save({"free_plan_on": False})) == SETTINGS_KEYS
    assert len(_told(owner, world.shop_a)) == 1

    # Switched on and off again on the same day: the shop is limited again, the answer says so, and the
    # outbox still holds the one message.
    assert set(save({"free_plan_on": True})) == SETTINGS_KEYS
    assert _state(client, admin, world.shop_a) == "free"
    assert save({"free_plan_on": False})["free_plan_off"]["shops_limited"] >= 1
    assert len(_told(owner, world.shop_a)) == 1

    # The next day it is news again.
    admin_env.clock.offset += timedelta(days=1)
    tomorrow = elevate(client, admin_env, world.admin, secret)
    for switch in (True, False):
        response = client.patch(
            SETTINGS,
            json={"changes": {"free_plan_on": switch}, "code": fresh_code(admin_env, secret)},
            headers={**tomorrow, **_key()},
        )
        assert response.status_code == 200, response.text
    assert len(_told(owner, world.shop_a)) == 2
    assert admin_env.clock.now() < datetime.now(UTC), "nothing here is dated after the tests that follow"


def test_switching_the_plan_on_or_leaving_it_as_it_is_tells_nobody(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin: dict[str, str],
    save: Save,
) -> None:
    _set(owner, world.shop_a, "limited")
    owner.execute("DELETE FROM platform_setting WHERE key IN ('free_plan_on', 'free_plan_customers')")
    try:
        # Off and left off, with and without a number: nothing was held, so nothing is switched off.
        assert set(save({"free_plan_on": False})) == SETTINGS_KEYS
        assert set(save({"free_plan_on": False, "free_plan_customers": 5})) == SETTINGS_KEYS
        # Switched on: shops become free, nobody is limited by it.
        on = save({"free_plan_on": True})
        assert set(on) == SETTINGS_KEYS and on["settings"]["free_plan_on"] is True
        assert _state(client, admin, world.shop_a) == "free"
        # On and left on, and another setting changed while it is on.
        assert set(save({"free_plan_on": True})) == SETTINGS_KEYS
        other = client.patch(SETTINGS, json={"changes": {"trial_days": 14}}, headers={**admin, **_key()})
        assert other.status_code == 200 and set(other.json()) == SETTINGS_KEYS
        assert _told(owner, world.shop_a) == []
        assert _state(client, admin, world.shop_a) == "free"
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key IN ('free_plan_on', 'free_plan_customers', 'trial_days')")


@pytest.mark.parametrize("number", [1, 5, 9])
def test_switched_off_with_a_changed_number_in_one_change_each_owner_gets_the_one_switched_off_message(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    free_plan: FreePlan,
    save: Save,
    number: int,
) -> None:
    """A plan that is off holds nobody whatever number is stored with it, so the number adds nothing: the
    owner of a shop the plan held is told that it was switched off, and nothing of a lowering."""
    today = _today(admin_env)
    _set(owner, world.shop_a, "limited")
    free_plan(5)
    expected = _counted(client, admin)
    assert expected == len(_free(owner, today, held=5))

    body = save({"free_plan_on": False, "free_plan_customers": number})
    assert body["settings"]["free_plan_customers"] == number
    assert body["free_plan_off"] == {"shops_limited": expected}
    assert set(body) == SETTINGS_KEYS | {"free_plan_off"}, "nothing was lowered: the plan is gone for all"
    assert _told(owner, world.shop_a) == [
        (
            _chat(owner, world.owner_a),
            say("uz", "free_plan_off", shop="Shop A"),
            f"free_plan:off:{world.shop_a}:{today.isoformat()}",
        )
    ]


def test_a_shop_without_an_owner_to_tell_is_counted_and_the_change_is_saved(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    free_plan: FreePlan,
    save: Save,
) -> None:
    _set(owner, world.shop_a, "limited")
    owner.execute("UPDATE membership SET status = 'suspended' WHERE id = %s", (world.owner_a_membership,))
    free_plan(5)
    free = _free(owner, _today(admin_env), held=5)
    assert world.shop_a in free
    assert save({"free_plan_on": False})["free_plan_off"] == {"shops_limited": len(free)}
    assert _told(owner, world.shop_a) == []


# --- the switch off ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("switch", ["false", None, '"true"', "1"])
def test_with_the_switch_off_the_question_is_not_looked_at(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str], switch: str | None
) -> None:
    owner.execute("DELETE FROM platform_setting WHERE key IN ('free_plan_on', 'free_plan_customers')")
    if switch is not None:
        owner.execute(
            "INSERT INTO platform_setting (key, value, updated_by) VALUES ('free_plan_on', %s::jsonb, 'test')",
            (switch,),
        )
    try:
        _set(owner, world.shop_a, "limited")
        plain = client.get(SETTINGS, headers=admin)
        assert set(plain.json()) == SETTINGS_KEYS
        # A switch, and what is no switch, are answered alike.
        for asked in ("false", "true", "abc", ""):
            response = client.get(SETTINGS, params={"free_plan_on": asked}, headers=admin)
            assert (response.status_code, response.content) == (200, plain.content)
        assert _told(owner, world.shop_a) == []
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key IN ('free_plan_on', 'free_plan_customers')")
