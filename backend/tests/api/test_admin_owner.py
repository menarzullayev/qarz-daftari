"""An administrator gives a shop to another person (operations runbook 7; REQ-031, REQ-058, REQ-059, INV-11)."""

import logging
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.admin_access import AdminAccess
from qarz.application.auth import AuthService
from qarz.application.chat_texts import say
from qarz.domain import totp
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app

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

REASON = "Egasi Telegram hisobini yo'qotdi; pasport va karta to'lovi bilan tasdiqlandi"
METRICS_TOKEN = "metrics-token-for-owner-tests-0123456789"


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


def _tg(owner: psycopg.Connection, user: uuid.UUID) -> int:
    row = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (user,)).fetchone()
    assert row is not None
    return int(row[0])


def _path(shop: uuid.UUID) -> str:
    return f"{ADMIN_API}/shops/{shop}/owner"


def _members(owner: psycopg.Connection, shop: uuid.UUID) -> dict[uuid.UUID, tuple[str, str]]:
    rows = owner.execute("SELECT user_id, role, status FROM membership WHERE shop_id = %s", (shop,)).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def _active_owners(owner: psycopg.Connection, shop: uuid.UUID) -> list[uuid.UUID]:
    rows = owner.execute(
        "SELECT user_id FROM membership WHERE shop_id = %s AND role = 'owner' AND status = 'active'", (shop,)
    ).fetchall()
    return [row[0] for row in rows]


def _audit(owner: psycopg.Connection, shop: uuid.UUID) -> list[Any]:
    return owner.execute(
        "SELECT admin_id, target_type, target_id, reason, detail FROM admin_audit "
        "WHERE target_shop = %s AND action = 'shop.owner_reassigned' ORDER BY at, id",
        (shop,),
    ).fetchall()


def _told(owner: psycopg.Connection, tg: int) -> list[str]:
    rows = owner.execute(
        "SELECT payload->>'text' FROM outbox_message WHERE recipient = %s AND channel = 'telegram' ORDER BY created_at",
        (str(tg),),
    ).fetchall()
    return [row[0] for row in rows]


def _failures(owner: psycopg.Connection, admin: uuid.UUID) -> Any:
    row = owner.execute("SELECT failed_codes FROM admin_account WHERE user_id = %s", (admin,)).fetchone()
    assert row is not None
    return row[0]


def _reassign(
    client: TestClient,
    admin: dict[str, str],
    env: AdminEnv,
    secret: bytes,
    shop: uuid.UUID,
    new_owner_tg: int,
    **changes: Any,
) -> Any:
    # A fresh code is taken only when the test brings none: taking one moves the clock.
    code = changes.pop("code") if "code" in changes else fresh_code(env, secret)
    body = {"new_owner_tg_id": new_owner_tg, "reason": REASON, "code": code, **changes}
    body = {name: value for name, value in body.items() if value is not ...}
    return client.post(_path(shop), json=body, headers={**admin, **_key()})


def test_the_shop_gets_its_new_owner_and_the_old_one_is_suspended_as_a_manager(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.owner_a,))
    old_tg, new_tg = _tg(owner, world.owner_a), _tg(owner, world.stranger)
    before_b = _members(owner, world.shop_b)

    response = _reassign(client, admin, admin_env, secret, world.shop_a, new_tg)
    assert response.status_code == 200, response.text
    body = response.json()
    # The shop as an administrator sees it and what was done; no customer, entry or amount (REQ-059).
    assert set(body) == {
        "id",
        "name",
        "status",
        "created_at",
        "subscription",
        "owner_tg_id",
        "staff_count",
        "customer_count",
        "deletion_due",
        "previous_owner_tg_id",
        "previous_owner_membership",
        "transfer_cancelled",
    }
    assert (body["id"], body["owner_tg_id"], body["previous_owner_tg_id"]) == (str(world.shop_a), new_tg, old_tg)
    assert (body["previous_owner_membership"], body["transfer_cancelled"], body["deletion_due"]) == (
        "suspended",
        False,
        None,
    )
    assert "Ali" not in response.text and "50000" not in response.text

    members = _members(owner, world.shop_a)
    assert members[world.stranger] == ("owner", "active")
    # BR-22 makes a former owner a manager; a lost account is not left able to act as one.
    assert members[world.owner_a] == ("manager", "suspended")
    assert _active_owners(owner, world.shop_a) == [world.stranger], "exactly one owner (INV-11)"
    assert members[world.manager_a] == ("manager", "active") and members[world.seller_a] == ("seller", "active")
    assert _members(owner, world.shop_b) == before_b, "the other shop is untouched"

    audit = _audit(owner, world.shop_a)
    assert len(audit) == 1
    assert audit[0][:4] == (world.admin, "shop", str(world.shop_a), REASON)
    assert audit[0][4] == {
        "previous_owner": str(world.owner_a),
        "new_owner": str(world.stranger),
        "previous_owner_membership": "manager, suspended",
        "transfer_cancelled": False,
        "shop_status": "active",
    }
    activity = owner.execute(
        "SELECT actor_kind, actor_id, subject_type, m.user_id FROM activity a JOIN membership m ON m.id = a.subject_id "
        "WHERE a.shop_id = %s AND a.action = 'ownership.reassigned_by_admin'",
        (world.shop_a,),
    ).fetchall()
    assert activity == [("admin", None, "membership", world.stranger)]

    # The new owner is told what happened; the old account is told the bare fact, in its language.
    assert _told(owner, new_tg) == [say("uz", "owner_reassigned_new", shop="Shop A")]
    assert _told(owner, old_tg) == ["Владелец магазина «Shop A» изменён администрацией сервиса."]

    # The old account is out of the shop; the new owner is in it, as its owner.
    assert client.get(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.owner_a)).status_code == 404
    seen = client.get(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.stranger))
    assert seen.status_code == 200, seen.text


@pytest.mark.parametrize("lang", ["uz", "ru"])
def test_the_old_account_is_told_nothing_but_that_the_administration_changed_the_ownership(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
    lang: str,
) -> None:
    owner.execute("UPDATE app_user SET lang = %s WHERE id = %s", (lang, world.owner_a))
    old_tg, new_tg = _tg(owner, world.owner_a), _tg(owner, world.stranger)
    assert _reassign(client, admin, admin_env, secret, world.shop_a, new_tg).status_code == 200
    (text,) = _told(owner, old_tg)
    # The whole message is fixed text around the shop's name: whoever holds that account learns neither
    # why, nor who has the shop now, nor who decided.
    assert text == say(lang, "owner_reassigned_old", shop="Shop A")
    for leak in (REASON, "pasport", str(new_tg), str(world.stranger), str(world.admin), "\n"):
        assert leak not in text
    assert len(text) < 100


def test_the_change_always_needs_a_fresh_code(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    new_tg = _tg(owner, world.stranger)
    before = _members(owner, world.shop_a)

    without = _reassign(client, admin, admin_env, secret, world.shop_a, new_tg, code=...)
    assert without.status_code == 422, without.text
    assert list(without.json()["error"]["fields"]) == ["code"]
    assert _failures(owner, world.admin) == 0, "a missing code is not a wrong code"

    # The code that opened the session was used up by that: holding a session is not enough.
    used = totp.code_at(secret, admin_env.clock.now())
    replayed = _reassign(client, admin, admin_env, secret, world.shop_a, new_tg, code=used)
    assert (replayed.status_code, replayed.json()["error"]["code"]) == (403, "SECOND_FACTOR_INVALID")
    assert _failures(owner, world.admin) == 1, "the wrong code is counted"

    assert _members(owner, world.shop_a) == before
    assert _audit(owner, world.shop_a) == []
    assert _told(owner, new_tg) == []
    assert owner.execute("SELECT count(*) FROM admin_request_key WHERE admin_id = %s", (world.admin,)).fetchone() == (
        0,
    ), "a refused change stores no answer"

    assert _reassign(client, admin, admin_env, secret, world.shop_a, new_tg).status_code == 200


@pytest.mark.parametrize("reason", [..., "", "  ", "ab", None])
def test_a_reason_is_required(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
    reason: Any,
) -> None:
    refused = _reassign(client, admin, admin_env, secret, world.shop_a, _tg(owner, world.stranger), reason=reason)
    assert refused.status_code == 422, refused.text
    assert list(refused.json()["error"]["fields"]) == ["reason"]
    assert _active_owners(owner, world.shop_a) == [world.owner_a]
    assert _audit(owner, world.shop_a) == []


@pytest.mark.parametrize("bad", [..., "12345", 0, -5, 1.5, None, True])
def test_the_new_owner_is_named_by_a_telegram_identifier(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
    bad: Any,
) -> None:
    refused = _reassign(client, admin, admin_env, secret, world.shop_a, 1, new_owner_tg_id=bad)
    assert refused.status_code == 422, refused.text
    assert _active_owners(owner, world.shop_a) == [world.owner_a]


def test_someone_the_service_does_not_know_or_the_owner_already_is_refused(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    before = _members(owner, world.shop_a)
    unknown = _reassign(client, admin, admin_env, secret, world.shop_a, 987654321987)
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (409, "OWNER_REASSIGNMENT_REFUSED")
    assert unknown.json()["error"]["fields"] == {"reason": "unknown_user"}
    assert owner.execute("SELECT count(*) FROM app_user WHERE tg_id = 987654321987").fetchone() == (0,), (
        "nobody is created by asking"
    )

    same = _reassign(client, admin, admin_env, secret, world.shop_a, _tg(owner, world.owner_a))
    assert (same.status_code, same.json()["error"]["fields"]) == (409, {"reason": "already_owner"})

    assert _members(owner, world.shop_a) == before
    assert _audit(owner, world.shop_a) == []
    assert _told(owner, _tg(owner, world.owner_a)) == []


def test_a_person_who_owns_five_shops_is_not_given_a_sixth(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    for number in range(5):
        shop = uuid.uuid4()
        owner.execute("INSERT INTO shop (id, name) VALUES (%s, %s)", (shop, f"Own {number}"))
        owner.execute(
            "INSERT INTO membership (id, shop_id, user_id, role) VALUES (%s, %s, %s, 'owner')",
            (uuid.uuid4(), shop, world.stranger),
        )
    refused = _reassign(client, admin, admin_env, secret, world.shop_a, _tg(owner, world.stranger))
    assert (refused.status_code, refused.json()["error"]["fields"]) == (409, {"reason": "shop_limit"})
    assert _active_owners(owner, world.shop_a) == [world.owner_a]

    # An erased shop does not count, exactly as when a shop is created.
    owner.execute(
        "UPDATE shop SET status = 'erased' WHERE id = (SELECT shop_id FROM membership WHERE user_id = %s LIMIT 1)",
        (world.stranger,),
    )
    assert _reassign(client, admin, admin_env, secret, world.shop_a, _tg(owner, world.stranger)).status_code == 200


@pytest.mark.parametrize("who", ["manager_a", "seller_a", "suspended_a"])
def test_someone_already_in_the_shop_keeps_their_membership_and_becomes_its_owner(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
    who: str,
) -> None:
    user = getattr(world, who)
    count = owner.execute("SELECT count(*) FROM membership WHERE shop_id = %s", (world.shop_a,)).fetchone()
    assert _reassign(client, admin, admin_env, secret, world.shop_a, _tg(owner, user)).status_code == 200
    assert _members(owner, world.shop_a)[user] == ("owner", "active")
    assert _active_owners(owner, world.shop_a) == [user]
    assert owner.execute("SELECT count(*) FROM membership WHERE shop_id = %s", (world.shop_a,)).fetchone() == count


def test_a_waiting_transfer_is_cancelled(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    transfer, other = uuid.uuid4(), uuid.uuid4()
    other_manager = uuid.uuid4()
    owner.execute(
        "INSERT INTO membership (id, shop_id, user_id, role) VALUES (%s, %s, %s, 'manager')",
        (other_manager, world.shop_b, world.manager_a),
    )
    b_owner = owner.execute(
        "SELECT id FROM membership WHERE shop_id = %s AND role = 'owner'", (world.shop_b,)
    ).fetchone()
    assert b_owner is not None
    owner.execute(
        "INSERT INTO ownership_transfer (id, shop_id, from_membership, to_membership, expires_at) VALUES "
        "(%s, %s, %s, %s, now() + interval '1 day'), (%s, %s, %s, %s, now() + interval '1 day')",
        (
            transfer,
            world.shop_a,
            world.owner_a_membership,
            world.manager_a_membership,
            other,
            world.shop_b,
            b_owner[0],
            other_manager,
        ),
    )
    response = _reassign(client, admin, admin_env, secret, world.shop_a, _tg(owner, world.stranger))
    assert response.status_code == 200, response.text
    assert response.json()["transfer_cancelled"] is True
    rows = dict(owner.execute("SELECT id, status FROM ownership_transfer WHERE id IN (%s, %s)", (transfer, other)))
    assert rows == {transfer: "cancelled", other: "pending"}, "only this shop's transfer"
    assert _audit(owner, world.shop_a)[0][4]["transfer_cancelled"] is True
    # The manager it was offered to can no longer accept it.
    accept = client.post(
        f"/api/v1/shops/{world.shop_a}/ownership-transfer/accept", headers={**as_user(world.manager_a), **_key()}
    )
    assert accept.status_code in (404, 409), accept.text
    assert _active_owners(owner, world.shop_a) == [world.stranger]


def test_an_erased_or_unknown_shop_is_not_found(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    new_tg = _tg(owner, world.stranger)
    missing = _reassign(client, admin, admin_env, secret, uuid.uuid4(), new_tg)
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "NOT_FOUND")

    owner.execute("UPDATE shop SET status = 'erased', name = 'erased' WHERE id = %s", (world.shop_a,))
    before = _members(owner, world.shop_a)
    erased = _reassign(client, admin, admin_env, secret, world.shop_a, new_tg)
    assert (erased.status_code, erased.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert _members(owner, world.shop_a) == before
    assert _audit(owner, world.shop_a) == []
    assert _told(owner, new_tg) == []


def test_a_suspended_shop_can_be_reassigned_and_stays_suspended(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    owner.execute(
        "UPDATE subscription SET state = 'suspended', prior_state = 'trial' WHERE shop_id = %s", (world.shop_a,)
    )
    response = _reassign(client, admin, admin_env, secret, world.shop_a, _tg(owner, world.stranger))
    assert response.status_code == 200, response.text
    assert response.json()["subscription"]["state"] == "suspended"
    assert _active_owners(owner, world.shop_a) == [world.stranger]


def test_a_shop_waiting_for_deletion_keeps_waiting_and_the_new_owner_is_told_when(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = '2031-03-10T09:00:00+00' WHERE id = %s",
        (world.shop_a,),
    )
    new_tg = _tg(owner, world.stranger)
    response = _reassign(client, admin, admin_env, secret, world.shop_a, new_tg)
    assert response.status_code == 200, response.text
    assert (response.json()["status"], response.json()["deletion_due"]) == (
        "deletion_pending",
        "2031-03-10T09:00:00+00:00",
    )
    stored = owner.execute("SELECT status, deletion_due FROM shop WHERE id = %s", (world.shop_a,)).fetchone()
    assert stored is not None and stored[0] == "deletion_pending"
    assert stored[1].isoformat() == "2031-03-10T09:00:00+00:00", "the due time is neither cancelled nor moved"
    (text,) = _told(owner, new_tg)
    assert "10.03.2031" in text
    assert _audit(owner, world.shop_a)[0][4]["shop_status"] == "deletion_pending"


def test_a_shop_left_without_an_owner_can_be_given_one(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    owner.execute("UPDATE membership SET status = 'removed' WHERE id = %s", (world.owner_a_membership,))
    response = _reassign(client, admin, admin_env, secret, world.shop_a, _tg(owner, world.stranger))
    assert response.status_code == 200, response.text
    assert (response.json()["previous_owner_tg_id"], response.json()["previous_owner_membership"]) == (None, None)
    assert _active_owners(owner, world.shop_a) == [world.stranger]
    assert _members(owner, world.shop_a)[world.owner_a] == ("owner", "removed"), "a removed membership is left alone"
    assert _audit(owner, world.shop_a)[0][4]["previous_owner"] is None


def test_a_repeat_returns_the_stored_answer_and_does_nothing_twice(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    old_tg, new_tg = _tg(owner, world.owner_a), _tg(owner, world.stranger)
    key = _key()
    body = {"new_owner_tg_id": new_tg, "reason": REASON}
    first = client.post(
        _path(world.shop_a), json={**body, "code": fresh_code(admin_env, secret)}, headers={**admin, **key}
    )
    assert first.status_code == 200, first.text
    # A retry carries whatever code the device shows by then; even a used one changes nothing.
    again = client.post(
        _path(world.shop_a),
        json={**body, "code": totp.code_at(secret, admin_env.clock.now())},
        headers={**admin, **key},
    )
    assert (again.status_code, again.json()) == (200, first.json())
    assert len(_audit(owner, world.shop_a)) == 1
    assert (len(_told(owner, new_tg)), len(_told(owner, old_tg))) == (1, 1)
    assert _failures(owner, world.admin) == 0

    other = client.post(
        _path(world.shop_a),
        json={"new_owner_tg_id": old_tg, "reason": REASON, "code": fresh_code(admin_env, secret)},
        headers={**admin, **key},
    )
    assert (other.status_code, other.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert _active_owners(owner, world.shop_a) == [world.stranger]

    # The stored answer names the shop's owner: it is kept under the shop, to be erased with it.
    kept = owner.execute("SELECT about_shop FROM admin_request_key WHERE admin_id = %s", (world.admin,)).fetchall()
    assert kept == [(world.shop_a,)]


def test_nothing_of_the_shops_ledger_customers_or_other_staff_is_touched(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    """The authorization suite can only try this operation with a wrong code; this is the same check
    with a right one. Of the shop's own tables only two membership rows and the activity log change."""

    def digest(table: str, where: str = "true") -> Any:
        return owner.execute(
            f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) FROM {table} t "
            f"WHERE shop_id = %s AND {where}",
            (world.shop_a,),
        ).fetchone()

    tables = ("ledger_entry", "goods_line", "promise", "customer", "customer_link", "catalog_item", "dispute")
    others = f"user_id NOT IN ('{world.owner_a}', '{world.stranger}')"
    before = [digest(table) for table in tables] + [digest("membership", others), digest("subscription")]
    shop_row = owner.execute("SELECT s::text FROM shop s WHERE id = %s", (world.shop_a,)).fetchone()

    assert _reassign(client, admin, admin_env, secret, world.shop_a, _tg(owner, world.stranger)).status_code == 200

    assert [digest(table) for table in tables] + [digest("membership", others), digest("subscription")] == before
    assert owner.execute("SELECT s::text FROM shop s WHERE id = %s", (world.shop_a,)).fetchone() == shop_row


def test_the_way_back_is_the_same_operation(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    """A mistaken reassignment is undone by reassigning again; each direction leaves its own audit row."""
    assert _reassign(client, admin, admin_env, secret, world.shop_a, _tg(owner, world.stranger)).status_code == 200
    assert _reassign(client, admin, admin_env, secret, world.shop_a, _tg(owner, world.owner_a)).status_code == 200
    members = _members(owner, world.shop_a)
    assert members[world.owner_a] == ("owner", "active")
    assert members[world.stranger] == ("manager", "suspended")
    assert _active_owners(owner, world.shop_a) == [world.owner_a]
    assert len(_audit(owner, world.shop_a)) == 2


def test_the_database_refuses_an_administrator_whose_session_has_ended(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    """The route's own check is the first control; the function's is the second (security review, finding 1)."""
    new_tg = _tg(owner, world.stranger)
    call = "SELECT outcome FROM admin_reassign_owner(%s, %s, %s, %s, %s)"
    now = admin_env.clock.now()
    assert owner.execute(call, (world.stranger, world.shop_a, new_tg, REASON, now)).fetchone() == ("refused",)
    late = now + timedelta(hours=9)
    assert owner.execute(call, (world.admin, world.shop_a, new_tg, REASON, late)).fetchone() == ("refused",)
    assert owner.execute(call, (world.admin, world.shop_a, new_tg, "  ", now)).fetchone() == ("refused",)
    owner.execute("UPDATE admin_account SET status = 'disabled' WHERE user_id = %s", (world.admin,))
    assert owner.execute(call, (world.admin, world.shop_a, new_tg, REASON, now)).fetchone() == ("refused",)
    assert _active_owners(owner, world.shop_a) == [world.owner_a]
    assert _audit(owner, world.shop_a) == []


@pytest.fixture
def observed(app_database_url: str, admin_env: AdminEnv) -> Iterator[TestClient]:
    database = Database(app_database_url)
    access = AdminAccess(database, allowed_tg_ids=admin_env.allowed, cipher=admin_env.box, now=admin_env.clock.now)
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        admin=access,
        authenticator=HeaderAuthenticator(),
        now=admin_env.clock.now,
        metrics_token=METRICS_TOKEN,
    )
    with TestClient(app) as test_client:
        yield test_client
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]


def _events(client: TestClient, kind: str) -> int:
    text = client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"}).text
    for line in text.splitlines():
        if line.startswith(f'qd_security_events_total{{kind="{kind}"}}'):
            return int(line.split()[-1])
    return 0


def test_a_reassignment_is_a_security_event_and_a_refused_one_is_not(
    observed: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    secret: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    admin = elevate(observed, admin_env, world.admin, secret)
    new_tg = _tg(owner, world.stranger)

    assert _reassign(observed, admin, admin_env, secret, world.shop_a, 987654321987).status_code == 409
    assert _reassign(observed, admin, admin_env, secret, world.shop_a, new_tg, reason="ab").status_code == 422
    assert _events(observed, "owner_reassigned") == 0
    wrong = _reassign(observed, admin, admin_env, secret, world.shop_a, new_tg, code="000000")
    assert wrong.status_code == 403
    assert (_events(observed, "owner_reassigned"), _events(observed, "bad_second_factor")) == (0, 1)

    assert _reassign(observed, admin, admin_env, secret, world.shop_a, new_tg).status_code == 200
    assert _events(observed, "owner_reassigned") == 1

    events = [record.__dict__ for record in caplog.records if record.getMessage() == "security"]
    assert [(event["kind"], str(event["user_id"]), str(event["shop_id"])) for event in events] == [
        ("bad_second_factor", str(world.admin), str(world.shop_a)),
        ("owner_reassigned", str(world.admin), str(world.shop_a)),
    ]
    # Identifiers only: neither the reason, nor a code, nor a Telegram identifier.
    logged = " ".join(str(record.__dict__) for record in caplog.records)
    for secret_thing in (REASON, "pasport", str(new_tg)):
        assert secret_thing not in logged
    named = [record.getMessage() for record in caplog.records if record.name == "qarz.admin"]
    assert any(
        line == f"admin_owner_reassigned admin={world.admin} shop={world.shop_a} "
        f"previous={world.owner_a} new={world.stranger}"
        for line in named
    )
