"""Shops as an administrator sees and changes them (REQ-052, REQ-058, REQ-059; BR-30; story S18.1)."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import ADMIN_API, AdminEnv, World, as_user, elevate, make_admin

pytestmark = pytest.mark.db

SHOPS = f"{ADMIN_API}/shops"
TASHKENT = ZoneInfo("Asia/Tashkent")


def _today(env: AdminEnv) -> date:
    return env.clock.now().astimezone(TASHKENT).date()


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"admin-{uuid.uuid4().hex}"}


@pytest.fixture
def admin(client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv) -> dict[str, str]:
    """Headers of an administrator who passed the second factor."""
    return elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))


def _tagged(owner: psycopg.Connection, world: World) -> str:
    """Give the world's two shops a name part no other test's shop has: the database is shared."""
    tag = uuid.uuid4().hex[:10]
    owner.execute("UPDATE shop SET name = name || ' ' || %s WHERE id IN (%s, %s)", (tag, world.shop_a, world.shop_b))
    return tag


def _set(owner: psycopg.Connection, shop: uuid.UUID, state: str, **dates: Any) -> None:
    owner.execute(
        "UPDATE subscription SET state = %s, trial_ends = %s, paid_through = %s, prior_state = %s WHERE shop_id = %s",
        (state, dates.get("trial_ends"), dates.get("paid_through"), dates.get("prior_state"), shop),
    )


def _stored(owner: psycopg.Connection, shop: uuid.UUID) -> Any:
    return owner.execute(
        "SELECT state, trial_ends, paid_through, prior_state FROM subscription WHERE shop_id = %s", (shop,)
    ).fetchone()


def _audit(owner: psycopg.Connection, shop: uuid.UUID, prefix: str = "subscription.") -> list[Any]:
    return owner.execute(
        "SELECT action, admin_id, target_type, target_id, reason, detail FROM admin_audit "
        "WHERE target_shop = %s AND starts_with(action, %s) ORDER BY at, id",
        (shop, prefix),
    ).fetchall()


def _post(client: TestClient, admin: dict[str, str], shop: uuid.UUID, what: str, body: dict[str, Any]) -> Any:
    return client.post(f"{SHOPS}/{shop}/{what}", json=body, headers={**admin, **_key()})


def _credit(client: TestClient, world: World) -> Any:
    """The shop's seller records a credit sale: what a limited or suspended shop may not do."""
    return client.post(
        f"/api/v1/shops/{world.shop_a}/customers/{world.customer_a}/entries",
        json={"kind": "credit", "amount": 1000},
        headers={**as_user(world.seller_a), **_key()},
    )


# --- list and search ------------------------------------------------------------------------------------


def test_the_list_shows_subscription_owner_and_counts_and_nothing_of_the_customers(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    tag = _tagged(owner, world)
    until = _today(admin_env) + timedelta(days=12)
    _set(owner, world.shop_a, "trial", trial_ends=until)
    owner_tg = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (world.owner_a,)).fetchone()
    assert owner_tg is not None

    response = client.get(SHOPS, params={"q": tag}, headers=admin)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["next_cursor"] is None
    assert {item["id"] for item in body["items"]} == {str(world.shop_a), str(world.shop_b)}
    shop = next(item for item in body["items"] if item["id"] == str(world.shop_a))
    assert set(shop) == {
        "id",
        "name",
        "status",
        "created_at",
        "subscription",
        "owner_tg_id",
        "staff_count",
        "customer_count",
    }
    assert shop["name"] == f"Shop A {tag}"
    assert shop["status"] == "active"
    assert shop["subscription"] == {
        "state": "trial",
        "stored_state": "trial",
        "trial_ends": until.isoformat(),
        "paid_through": None,
        "prior_state": None,
    }
    assert shop["owner_tg_id"] == owner_tg[0]
    assert shop["staff_count"] == 3, "owner, manager and seller; the suspended member is not counted"
    assert shop["customer_count"] == 3
    # REQ-059: no customer's name, no entry, no amount owed.
    for hidden in ("Ali", "Vali", "Sobir", "Kutuvchi", "50000", str(world.customer_a), str(world.entry_a)):
        assert hidden not in response.text


def test_a_finished_period_is_listed_as_limited_whatever_is_stored(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    tag = _tagged(owner, world)
    today = _today(admin_env)
    _set(owner, world.shop_a, "trial", trial_ends=today - timedelta(days=1))
    _set(owner, world.shop_b, "active", paid_through=today)

    def found(state: str) -> set[str]:
        response = client.get(SHOPS, params={"q": tag, "state": state}, headers=admin)
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    assert found("limited") == {str(world.shop_a)}
    assert found("active") == {str(world.shop_b)}, "the last paid day still counts"
    assert found("trial") == set()
    assert found("suspended") == set()
    listed = client.get(SHOPS, params={"q": tag, "state": "limited"}, headers=admin).json()["items"][0]
    assert (listed["subscription"]["state"], listed["subscription"]["stored_state"]) == ("limited", "trial")


def test_the_list_is_searched_by_a_part_of_the_name_and_paged(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str]
) -> None:
    tag = _tagged(owner, world)
    assert [item["id"] for item in client.get(SHOPS, params={"q": f"a {tag}"}, headers=admin).json()["items"]] == [
        str(world.shop_a)
    ]
    assert client.get(SHOPS, params={"q": f"{tag} yo'q"}, headers=admin).json()["items"] == []
    assert client.get(SHOPS, params={"q": f"  {tag}  "}, headers=admin).json()["items"] != [], "spaces are trimmed"

    first = client.get(SHOPS, params={"q": tag, "limit": 1}, headers=admin).json()
    assert len(first["items"]) == 1
    assert first["next_cursor"] is not None
    second = client.get(SHOPS, params={"q": tag, "limit": 1, "cursor": first["next_cursor"]}, headers=admin).json()
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
    assert {first["items"][0]["id"], second["items"][0]["id"]} == {str(world.shop_a), str(world.shop_b)}


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"limit": "many"}, "limit"),
        ({"state": "paused"}, "state"),
        ({"q": "x" * 81}, "q"),
        ({"cursor": "not-a-cursor"}, "cursor"),
    ],
)
def test_a_bad_list_request_is_a_validation_error(
    client: TestClient, admin: dict[str, str], params: dict[str, Any], field: str
) -> None:
    response = client.get(SHOPS, params=params, headers=admin)
    assert response.status_code == 422, response.text
    assert list(response.json()["error"]["fields"]) == [field]


def test_the_page_size_limits_are_accepted_at_their_edges(client: TestClient, admin: dict[str, str]) -> None:
    assert client.get(SHOPS, params={"limit": 1, "q": "x" * 80}, headers=admin).status_code == 200
    assert client.get(SHOPS, params={"limit": 100}, headers=admin).status_code == 200


# --- one shop -------------------------------------------------------------------------------------------


def test_one_shop_shows_its_payment_history_and_every_look_is_audited(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    receipt = uuid.uuid4()
    owner.execute(
        "INSERT INTO subscription_receipt (id, shop_id, stated_amount, status, months, decided_at) "
        "VALUES (%s, %s, 100000, 'approved', 1, now()), (gen_random_uuid(), %s, 999000, 'submitted', NULL, NULL)",
        (receipt, world.shop_a, world.shop_b),
    )
    changed = _post(client, admin, world.shop_a, "suspend", {"reason": "Tekshiruv uchun"})
    assert changed.status_code == 200, changed.text

    response = client.get(f"{SHOPS}/{world.shop_a}", headers=admin)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(world.shop_a)
    assert body["lang"] == "uz"
    assert body["deletion_due"] is None
    assert body["subscription"]["state"] == "suspended"
    assert [(item["id"], item["stated_amount"], item["status"], item["months"]) for item in body["receipts"]] == [
        (str(receipt), 100000, "approved", 1)
    ]
    assert [(item["action"], item["reason"]) for item in body["changes"]] == [
        ("subscription.suspended", "Tekshiruv uchun")
    ]
    assert "999000" not in response.text, "another shop's receipt"
    for hidden in ("Ali", "Vali", "50000", str(world.customer_a)):
        assert hidden not in response.text

    looks = _audit(owner, world.shop_a, "shop.viewed")
    assert [(row[0], row[1], row[2], row[3], row[4]) for row in looks] == [
        ("shop.viewed", world.admin, "shop", str(world.shop_a), None)
    ]
    client.get(f"{SHOPS}/{world.shop_a}", headers=admin)
    assert len(_audit(owner, world.shop_a, "shop.viewed")) == 2, "each look is written down"
    assert _audit(owner, world.shop_b, "shop.viewed") == []


def test_a_shop_that_does_not_exist_is_not_found_and_no_look_is_recorded(
    client: TestClient, owner: psycopg.Connection, admin: dict[str, str]
) -> None:
    before = owner.execute("SELECT count(*) FROM admin_audit").fetchone()
    assert client.get(f"{SHOPS}/{uuid.uuid4()}", headers=admin).status_code == 404
    assert client.get(f"{SHOPS}/not-a-uuid", headers=admin).status_code == 404
    assert owner.execute("SELECT count(*) FROM admin_audit").fetchone() == before


# --- trial ----------------------------------------------------------------------------------------------


def test_extending_a_trial_is_stored_audited_and_idempotent(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    today = _today(admin_env)
    old, new = today + timedelta(days=2), today + timedelta(days=20)
    _set(owner, world.shop_a, "trial", trial_ends=old)
    key = _key()
    body = {"trial_ends": new.isoformat(), "reason": "  Sinovni   uzaytirish  "}

    response = client.post(f"{SHOPS}/{world.shop_a}/trial", json=body, headers={**admin, **key})
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(world.shop_a)
    assert response.json()["subscription"]["trial_ends"] == new.isoformat()
    assert _stored(owner, world.shop_a) == ("trial", new, None, None)
    assert _stored(owner, world.shop_b)[1] != new, "the other shop is untouched"
    rows = _audit(owner, world.shop_a)
    assert len(rows) == 1
    action, who, target_type, target_id, reason, detail = rows[0]
    assert (action, who, target_type, target_id) == ("subscription.trial_set", world.admin, "shop", str(world.shop_a))
    assert reason == "Sinovni uzaytirish", "the reason given, tidied"
    assert detail == {
        "before": {"state": "trial", "trial_ends": old.isoformat(), "paid_through": None, "prior_state": None},
        "after": {"state": "trial", "trial_ends": new.isoformat(), "paid_through": None, "prior_state": None},
    }

    # The same request again: the stored answer, and no second effect or audit row.
    owner.execute("UPDATE subscription SET trial_ends = %s WHERE shop_id = %s", (old, world.shop_a))
    again = client.post(f"{SHOPS}/{world.shop_a}/trial", json=body, headers={**admin, **key})
    assert (again.status_code, again.json()) == (200, response.json())
    assert _stored(owner, world.shop_a)[1] == old
    assert len(_audit(owner, world.shop_a)) == 1

    # The same key for something else is refused.
    other = {**body, "trial_ends": (new + timedelta(days=1)).isoformat()}
    reused = client.post(f"{SHOPS}/{world.shop_a}/trial", json=other, headers={**admin, **key})
    assert (reused.status_code, reused.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    elsewhere = client.post(f"{SHOPS}/{world.shop_b}/trial", json=body, headers={**admin, **key})
    assert elsewhere.status_code == 409


def test_a_limited_shop_can_be_given_a_trial_and_sells_on_credit_again(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    today = _today(admin_env)
    _set(owner, world.shop_a, "limited", trial_ends=today - timedelta(days=3), prior_state="trial")
    assert _credit(client, world).status_code == 402
    response = _post(client, admin, world.shop_a, "trial", {"trial_ends": today.isoformat(), "reason": "Yana bir kun"})
    assert response.status_code == 200, response.text
    assert response.json()["subscription"]["state"] == "trial"
    assert _stored(owner, world.shop_a) == ("trial", today, None, None)
    assert _credit(client, world).status_code == 201


@pytest.mark.parametrize(
    ("state", "dates", "days", "why"),
    [
        ("trial", {"trial_ends": 5}, -1, "date_out_of_range"),
        ("trial", {"trial_ends": 5}, 366, "date_out_of_range"),
        ("active", {"paid_through": 0}, 10, "paid"),
        ("suspended", {"trial_ends": 5}, 10, "suspended"),
    ],
)
def test_a_trial_change_that_does_not_apply_is_refused_and_changes_nothing(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    state: str,
    dates: dict[str, int],
    days: int,
    why: str,
) -> None:
    today = _today(admin_env)
    _set(owner, world.shop_a, state, **{name: today + timedelta(days=shift) for name, shift in dates.items()})
    before = _stored(owner, world.shop_a)
    body = {"trial_ends": (today + timedelta(days=days)).isoformat(), "reason": "Sinab ko'rish"}
    response = _post(client, admin, world.shop_a, "trial", body)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "SUBSCRIPTION_CHANGE_REFUSED"
    assert response.json()["error"]["fields"] == {"reason": why}
    assert _stored(owner, world.shop_a) == before
    assert _audit(owner, world.shop_a) == []
    assert owner.execute("SELECT count(*) FROM admin_request_key WHERE admin_id = %s", (world.admin,)).fetchone() == (
        0,
    ), "a refused request stores no answer and can be retried"


def test_a_trial_may_end_a_year_from_today_at_the_latest(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    last = _today(admin_env) + timedelta(days=365)
    response = _post(client, admin, world.shop_a, "trial", {"trial_ends": last.isoformat(), "reason": "Bir yil"})
    assert response.status_code == 200, response.text
    assert _stored(owner, world.shop_a)[1] == last


def test_today_is_the_tashkent_date_not_the_utc_one(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    """At 20:30 UTC it is already tomorrow in Tashkent."""
    now = datetime.now(UTC)
    evening = now.replace(hour=20, minute=30, second=0, microsecond=0)
    admin_env.clock.offset = evening - now
    admin = elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))
    utc_today = admin_env.clock.now().date()
    assert _today(admin_env) == utc_today + timedelta(days=1)
    _set(owner, world.shop_a, "trial", trial_ends=utc_today + timedelta(days=9))

    stale = _post(client, admin, world.shop_a, "trial", {"trial_ends": utc_today.isoformat(), "reason": "Kecha"})
    assert stale.status_code == 409, "the UTC date is yesterday in Tashkent"
    fine = _post(client, admin, world.shop_a, "trial", {"trial_ends": _today(admin_env).isoformat(), "reason": "Bugun"})
    assert fine.status_code == 200, fine.text


def test_ending_a_trial_limits_the_shop_at_once(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    today = _today(admin_env)
    _set(owner, world.shop_a, "trial", trial_ends=today + timedelta(days=9))
    assert _credit(client, world).status_code == 201
    response = _post(client, admin, world.shop_a, "trial/end", {"reason": "Sinov bekor qilindi"})
    assert response.status_code == 200, response.text
    assert response.json()["subscription"]["state"] == "limited"
    assert _stored(owner, world.shop_a) == ("limited", today - timedelta(days=1), None, "trial")
    assert [(row[0], row[4]) for row in _audit(owner, world.shop_a)] == [
        ("subscription.trial_ended", "Sinov bekor qilindi")
    ]
    refused = _credit(client, world)
    assert (refused.status_code, refused.json()["error"]["code"]) == (402, "SUBSCRIPTION_LIMITED")

    again = _post(client, admin, world.shop_a, "trial/end", {"reason": "Yana bir marta"})
    assert (again.status_code, again.json()["error"]["fields"]) == (409, {"reason": "not_in_trial"})


# --- paid period ----------------------------------------------------------------------------------------


def test_setting_paid_through_makes_the_shop_active(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    today = _today(admin_env)
    _set(owner, world.shop_a, "limited", trial_ends=today - timedelta(days=5), prior_state="trial")
    until = today + timedelta(days=30)
    response = _post(
        client, admin, world.shop_a, "paid-through", {"paid_through": until.isoformat(), "reason": "Naqd to'lov"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["subscription"] == {
        "state": "active",
        "stored_state": "active",
        "trial_ends": (today - timedelta(days=5)).isoformat(),
        "paid_through": until.isoformat(),
        "prior_state": None,
    }
    assert _stored(owner, world.shop_a) == ("active", today - timedelta(days=5), until, None)
    assert [(row[0], row[4]) for row in _audit(owner, world.shop_a)] == [
        ("subscription.paid_through_set", "Naqd to'lov")
    ]
    assert _credit(client, world).status_code == 201


def test_a_paid_period_can_be_ended_as_of_yesterday_and_no_earlier(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    today = _today(admin_env)
    _set(owner, world.shop_a, "active", paid_through=today + timedelta(days=40))
    too_early = (today - timedelta(days=2)).isoformat()
    refused = _post(client, admin, world.shop_a, "paid-through", {"paid_through": too_early, "reason": "Xato"})
    assert (refused.status_code, refused.json()["error"]["fields"]) == (409, {"reason": "date_out_of_range"})
    yesterday = today - timedelta(days=1)
    ended = _post(
        client, admin, world.shop_a, "paid-through", {"paid_through": yesterday.isoformat(), "reason": "Xato tasdiq"}
    )
    assert ended.status_code == 200, ended.text
    assert _stored(owner, world.shop_a) == ("limited", None, yesterday, "active")
    assert _credit(client, world).status_code == 402


@pytest.mark.parametrize(("days", "status"), [(3 * 366, 200), (3 * 366 + 1, 409)])
def test_paid_through_is_at_most_three_years_ahead(
    client: TestClient, world: World, admin_env: AdminEnv, admin: dict[str, str], days: int, status: int
) -> None:
    until = (_today(admin_env) + timedelta(days=days)).isoformat()
    response = _post(client, admin, world.shop_a, "paid-through", {"paid_through": until, "reason": "Uzoq muddat"})
    assert response.status_code == status, response.text


# --- suspension -----------------------------------------------------------------------------------------


def test_suspending_stops_the_shop_tells_the_owner_and_unsuspending_brings_it_back(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    today = _today(admin_env)
    _set(owner, world.shop_a, "trial", trial_ends=today + timedelta(days=9))
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.owner_a,))
    owner_tg = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (world.owner_a,)).fetchone()
    assert owner_tg is not None

    def notices() -> list[Any]:
        return owner.execute(
            "SELECT channel, payload->>'text', shop_id FROM outbox_message WHERE recipient = %s ORDER BY created_at",
            (str(owner_tg[0]),),
        ).fetchall()

    key = _key()
    body = {"reason": "To'lov bo'yicha nizo"}
    response = client.post(f"{SHOPS}/{world.shop_a}/suspend", json=body, headers={**admin, **key})
    assert response.status_code == 200, response.text
    assert response.json()["subscription"] == {
        "state": "suspended",
        "stored_state": "suspended",
        "trial_ends": (today + timedelta(days=9)).isoformat(),
        "paid_through": None,
        "prior_state": "trial",
    }
    assert _stored(owner, world.shop_a) == ("suspended", today + timedelta(days=9), None, "trial")
    assert _stored(owner, world.shop_b)[0] == "trial", "the other shop is untouched"
    assert [(row[0], row[1], row[4]) for row in _audit(owner, world.shop_a)] == [
        ("subscription.suspended", world.admin, "To'lov bo'yicha nizo")
    ]
    # The owner is told, in their language, with the reason (specification, events: ShopSuspended).
    told = notices()
    assert len(told) == 1
    assert (told[0][0], told[0][2]) == ("telegram", world.shop_a)
    assert told[0][1].startswith("Магазин «Shop A» приостановлен")
    assert "To'lov bo'yicha nizo" in told[0][1]

    # BR-30: staff are stopped; the owner still sees the data.
    stopped = _credit(client, world)
    assert (stopped.status_code, stopped.json()["error"]["code"]) == (403, "SHOP_SUSPENDED")
    customers = f"/api/v1/shops/{world.shop_a}/customers"
    assert client.get(customers, headers=as_user(world.seller_a)).status_code == 403
    assert client.get(customers, headers=as_user(world.owner_a)).status_code == 200

    # A repeat of the same request changes nothing and tells nobody again.
    assert client.post(f"{SHOPS}/{world.shop_a}/suspend", json=body, headers={**admin, **key}).status_code == 200
    assert len(notices()) == 1
    assert len(_audit(owner, world.shop_a)) == 1
    twice = _post(client, admin, world.shop_a, "suspend", {"reason": "Yana"})
    assert (twice.status_code, twice.json()["error"]["fields"]) == (409, {"reason": "already_suspended"})
    assert len(notices()) == 1

    back = _post(client, admin, world.shop_a, "unsuspend", {"reason": "Nizo hal bo'ldi"})
    assert back.status_code == 200, back.text
    assert back.json()["subscription"]["state"] == "trial"
    assert _stored(owner, world.shop_a) == ("trial", today + timedelta(days=9), None, None)
    assert [row[0] for row in _audit(owner, world.shop_a)] == ["subscription.suspended", "subscription.unsuspended"]
    told = notices()
    assert len(told) == 2
    assert told[1][1].startswith("Магазин «Shop A» снова работает")
    assert "Nizo hal bo'ldi" in told[1][1]
    assert _credit(client, world).status_code == 201

    again = _post(client, admin, world.shop_a, "unsuspend", {"reason": "Yana"})
    assert (again.status_code, again.json()["error"]["fields"]) == (409, {"reason": "not_suspended"})


@pytest.mark.parametrize("state", ["active", "limited"])
def test_unsuspending_returns_to_whatever_state_was_interrupted(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    state: str,
) -> None:
    paid = _today(admin_env) + timedelta(days=10) if state == "active" else None
    _set(owner, world.shop_a, state, paid_through=paid)
    assert _post(client, admin, world.shop_a, "suspend", {"reason": "Tekshiruv"}).status_code == 200
    assert _stored(owner, world.shop_a) == ("suspended", None, paid, state)
    assert _post(client, admin, world.shop_a, "unsuspend", {"reason": "Tekshirildi"}).status_code == 200
    assert _stored(owner, world.shop_a) == (state, None, paid, None)


def test_a_shop_without_an_owner_to_tell_is_still_suspended(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str]
) -> None:
    owner.execute("UPDATE membership SET status = 'removed' WHERE id = %s", (world.owner_a_membership,))
    before = owner.execute("SELECT count(*) FROM outbox_message").fetchone()
    response = _post(client, admin, world.shop_a, "suspend", {"reason": "Egasiz do'kon"})
    assert response.status_code == 200, response.text
    assert response.json()["owner_tg_id"] is None
    assert _stored(owner, world.shop_a)[0] == "suspended"
    assert owner.execute("SELECT count(*) FROM outbox_message").fetchone() == before


# --- what every shop change shares ----------------------------------------------------------------------

CHANGES = [
    ("trial", {"trial_ends": "2099-01-01"}),
    ("trial/end", {}),
    ("paid-through", {"paid_through": "2099-01-01"}),
    ("suspend", {}),
    ("unsuspend", {}),
]


@pytest.mark.parametrize(("what", "extra"), CHANGES)
@pytest.mark.parametrize("reason", [None, "", "  ", "ab", "x" * 501])
def test_every_shop_change_needs_a_reason_of_a_sensible_length(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin: dict[str, str],
    what: str,
    extra: dict[str, Any],
    reason: str | None,
) -> None:
    before = _stored(owner, world.shop_a)
    body = extra if reason is None else {**extra, "reason": reason}
    response = _post(client, admin, world.shop_a, what, body)
    assert response.status_code == 422, response.text
    assert list(response.json()["error"]["fields"]) == ["reason"]
    assert _stored(owner, world.shop_a) == before
    assert _audit(owner, world.shop_a) == []


@pytest.mark.parametrize("length", [3, 500])
def test_a_reason_is_accepted_at_the_edges_of_its_length(
    client: TestClient, world: World, admin: dict[str, str], length: int
) -> None:
    assert _post(client, admin, world.shop_a, "suspend", {"reason": "x" * length}).status_code == 200


@pytest.mark.parametrize(("what", "extra"), CHANGES)
def test_a_change_to_a_shop_that_does_not_exist_or_was_erased_is_not_found(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin: dict[str, str],
    what: str,
    extra: dict[str, Any],
) -> None:
    body = {**extra, "reason": "Mavjud emas"}
    assert _post(client, admin, uuid.uuid4(), what, body).status_code == 404
    owner.execute("UPDATE shop SET status = 'erased' WHERE id = %s", (world.shop_a,))
    before = _stored(owner, world.shop_a)
    assert _post(client, admin, world.shop_a, what, body).status_code == 404
    assert _stored(owner, world.shop_a) == before
    assert _audit(owner, world.shop_a) == []


@pytest.mark.parametrize(
    ("what", "body", "field"),
    [
        ("trial", {"reason": "Sana yo'q"}, "trial_ends"),
        ("trial", {"trial_ends": "ertaga", "reason": "Sana emas"}, "trial_ends"),
        ("trial", {"trial_ends": "2099-01-01", "reason": "Ortiqcha", "state": "active"}, "state"),
        ("paid-through", {"paid_through": 20990101, "reason": "Son"}, "paid_through"),
        ("suspend", {"reason": "Ortiqcha", "forever": True}, "forever"),
    ],
)
def test_a_malformed_change_is_a_validation_error(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin: dict[str, str],
    what: str,
    body: dict[str, Any],
    field: str,
) -> None:
    before = _stored(owner, world.shop_a)
    response = _post(client, admin, world.shop_a, what, body)
    assert response.status_code == 422, response.text
    assert list(response.json()["error"]["fields"]) == [field]
    assert _stored(owner, world.shop_a) == before
