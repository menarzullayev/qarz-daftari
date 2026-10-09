"""Platform switches and prices as the administrator reads and changes them (REQ-052, REQ-053, REQ-N14;
ADR-018; story S18.1), and the audit of everything an administrator does (REQ-058)."""

import uuid
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.domain import totp

from .conftest import ADMIN_API, AdminEnv, World, as_user, elevate, fresh_code, make_admin
from .test_authorization_suite import ADMIN_CALLS, ADMIN_ENTRY_CALLS

pytestmark = pytest.mark.db

SETTINGS = f"{ADMIN_API}/settings"
AUDIT = f"{ADMIN_API}/audit"
DEFAULTS = {
    "trial_on": True,
    "trial_days": 30,
    "price_uzs": 100_000,
    "payment_cards": [],
    "review_group": None,
    "sms_on": False,
    "sms_monthly_quota": 0,
    "online_pay_on": False,
    "usd_on": False,
}
# Specification, clients table: "changes to price, card number, and switches ask for the code again".
NEEDS_CODE = {
    "price_uzs": 150_000,
    "payment_cards": [{"number": "8600 1234 5678 9012", "label": "Humo · Anorbank"}],
    "trial_on": False,
    "sms_on": True,
    "online_pay_on": True,
    "review_group": -1001234567890,
    "usd_on": True,
}


HUMO = {"number": "8600123456789012", "label": "Humo · Anorbank"}
UZCARD = {"number": "5614681234567890", "label": "Uzcard · Kapitalbank"}

REASSIGN = "admin.shops.owner.reassign"


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"admin-{uuid.uuid4().hex}"}


@pytest.fixture
def secret(world: World, owner: psycopg.Connection, admin_env: AdminEnv) -> bytes:
    # The clock stands still, so "the code of this very step" means the same thing all through a test.
    admin_env.clock.freeze()
    return make_admin(owner, admin_env, world.admin)


@pytest.fixture
def admin(client: TestClient, world: World, admin_env: AdminEnv, secret: bytes) -> dict[str, str]:
    """Headers of an administrator who passed the second factor."""
    return elevate(client, admin_env, world.admin, secret)


def _stored(owner: psycopg.Connection) -> dict[str, Any]:
    return dict(owner.execute("SELECT key, value FROM platform_setting").fetchall())


def _audit(owner: psycopg.Connection, admin: uuid.UUID, action: str = "setting.changed") -> list[Any]:
    return owner.execute(
        "SELECT target_type, target_id, reason, detail FROM admin_audit WHERE admin_id = %s AND action = %s "
        "ORDER BY at, target_id",
        (admin, action),
    ).fetchall()


def _failures(owner: psycopg.Connection, admin: uuid.UUID) -> Any:
    row = owner.execute("SELECT failed_codes FROM admin_account WHERE user_id = %s", (admin,)).fetchone()
    assert row is not None
    return row[0]


def _patch(client: TestClient, admin: dict[str, str], body: dict[str, Any]) -> Any:
    return client.patch(SETTINGS, json=body, headers={**admin, **_key()})


# --- reading --------------------------------------------------------------------------------------------


def test_with_nothing_stored_the_defaults_apply_and_both_integration_switches_are_off(
    client: TestClient, admin: dict[str, str], owner: psycopg.Connection
) -> None:
    assert _stored(owner) == {}
    response = client.get(SETTINGS, headers=admin)
    assert response.status_code == 200, response.text
    assert response.json() == {"settings": DEFAULTS, "needs_code": sorted(NEEDS_CODE), "changed": {}}
    assert response.json()["settings"]["sms_on"] is False
    assert response.json()["settings"]["online_pay_on"] is False
    assert _stored(owner) == {}, "reading stores nothing"


def test_a_damaged_stored_value_reads_as_the_default(
    client: TestClient, admin: dict[str, str], owner: psycopg.Connection
) -> None:
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('price_uzs', '\"free\"', 'test'), "
        "('sms_on', '1', 'test'), ('trial_days', '21', 'test')"
    )
    try:
        body = client.get(SETTINGS, headers=admin).json()
        assert body["settings"] == {**DEFAULTS, "trial_days": 21}
        assert body["changed"]["trial_days"]["by"] == "test"
    finally:
        owner.execute("DELETE FROM platform_setting WHERE updated_by = 'test'")


# --- changes that need no code --------------------------------------------------------------------------


def test_the_trial_length_changes_at_once_for_the_next_shop(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    """REQ-N14: a setting takes effect without a release."""
    response = _patch(client, admin, {"changes": {"trial_days": 14}, "reason": "Qisqa sinov"})
    assert response.status_code == 200, response.text
    assert response.json()["settings"] == {**DEFAULTS, "trial_days": 14}
    assert response.json()["changed"]["trial_days"]["by"] == str(world.admin)
    assert _stored(owner) == {"trial_days": 14}
    assert _audit(owner, world.admin) == [("setting", "trial_days", "Qisqa sinov", {"before": 30, "after": 14})]

    created = client.post(
        "/api/v1/shops", json={"name": "Yangi do'kon", "lang": "uz"}, headers={**as_user(world.stranger), **_key()}
    )
    assert created.status_code == 201, created.text
    today = admin_env.clock.now().astimezone(ZoneInfo("Asia/Tashkent")).date()
    row = owner.execute(
        "SELECT state, trial_ends FROM subscription WHERE shop_id = %s", (created.json()["id"],)
    ).fetchone()
    assert row == ("trial", today + timedelta(days=14))


def test_a_setting_change_is_idempotent(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str]
) -> None:
    key = _key()
    first = client.patch(SETTINGS, json={"changes": {"sms_monthly_quota": 200}}, headers={**admin, **key})
    assert first.status_code == 200, first.text
    owner.execute("UPDATE platform_setting SET value = '5' WHERE key = 'sms_monthly_quota'")
    again = client.patch(SETTINGS, json={"changes": {"sms_monthly_quota": 200}}, headers={**admin, **key})
    assert (again.status_code, again.json()) == (200, first.json())
    assert _stored(owner) == {"sms_monthly_quota": 5}, "the repeat wrote nothing"
    assert len(_audit(owner, world.admin)) == 1
    other = client.patch(SETTINGS, json={"changes": {"sms_monthly_quota": 300}}, headers={**admin, **key})
    assert (other.status_code, other.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert client.patch(SETTINGS, json={"changes": {"trial_days": 7}}, headers=admin).status_code == 422


def test_a_setting_remembers_who_changed_it_last(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    assert _patch(client, admin, {"changes": {"trial_days": 14}}).status_code == 200
    second = elevate(client, admin_env, world.owner_b, make_admin(owner, admin_env, world.owner_b))
    admin_env.clock.offset += timedelta(minutes=1)
    changed = _patch(client, second, {"changes": {"trial_days": 21}})
    assert changed.status_code == 200, changed.text
    assert changed.json()["changed"]["trial_days"] == {
        "by": str(world.owner_b),
        "at": admin_env.clock.now().isoformat(),
    }


# --- changes that need the code again -------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(NEEDS_CODE))
def test_a_sensitive_change_needs_a_current_code(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
    name: str,
) -> None:
    change = {"changes": {name: NEEDS_CODE[name]}}

    without = _patch(client, admin, change)
    assert without.status_code == 422, without.text
    assert list(without.json()["error"]["fields"]) == ["code"]
    assert _failures(owner, world.admin) == 0, "a missing code is not a wrong code"

    # The code that opened the session a moment ago was used up by that.
    used = totp.code_at(secret, admin_env.clock.now())
    replayed = _patch(client, admin, {**change, "code": used})
    assert (replayed.status_code, replayed.json()["error"]["code"]) == (403, "SECOND_FACTOR_INVALID")
    assert _failures(owner, world.admin) == 1
    assert _stored(owner) == {}
    assert _audit(owner, world.admin) == []
    assert owner.execute("SELECT count(*) FROM admin_request_key WHERE admin_id = %s", (world.admin,)).fetchone() == (
        0,
    ), "a refused change stores no answer"

    done = _patch(client, admin, {**change, "code": fresh_code(admin_env, secret)})
    assert done.status_code == 200, done.text
    expected = [HUMO] if name == "payment_cards" else NEEDS_CODE[name]
    assert done.json()["settings"][name] == expected
    assert _stored(owner) == {name: expected}
    assert _failures(owner, world.admin) == 0
    assert len(_audit(owner, world.admin)) == 1
    assert client.get(SETTINGS, headers=admin).status_code == 200, "the session goes on"


def test_the_new_price_and_card_reach_the_owner_at_once_and_the_audit_hides_the_card(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    body = {
        "changes": {
            "price_uzs": 120_000,
            "payment_cards": [{"number": "8600 1234 5678 9012", "label": " Humo · Anorbank "}, UZCARD],
        },
        "code": fresh_code(admin_env, secret),
        "reason": "Yangi narx",
    }
    assert _patch(client, admin, body).status_code == 200
    seen = client.get(f"/api/v1/shops/{world.shop_a}/subscription", headers=as_user(world.owner_a)).json()
    assert seen["price_uzs"] == 120_000
    # In the administrator's order; the first is the primary and is also what `card_number` says.
    assert (seen["cards"], seen["card_number"]) == ([HUMO, UZCARD], HUMO["number"])

    hidden = [
        {"number": "************9012", "label": "Humo · Anorbank"},
        {"number": "************7890", "label": "Uzcard · Kapitalbank"},
    ]
    rows = _audit(owner, world.admin)
    assert rows == [
        ("setting", "payment_cards", "Yangi narx", {"before": [], "after": hidden}),
        ("setting", "price_uzs", "Yangi narx", {"before": 100_000, "after": 120_000}),
    ]
    listed = client.get(AUDIT, params={"action": "setting."}, headers=admin)
    assert listed.status_code == 200
    assert HUMO["number"] not in listed.text and UZCARD["number"] not in listed.text
    assert "************9012" in listed.text and "Humo · Anorbank" in listed.text, "the label and four digits stay"
    whole = owner.execute("SELECT count(*) FROM admin_audit WHERE detail::text ~ '[0-9]{16}'").fetchone()
    assert whole == (0,), "no audit row holds a whole card number"

    # The order is the setting: the other card first makes it the primary.
    swapped = _patch(
        client, admin, {"changes": {"payment_cards": [UZCARD, HUMO]}, "code": fresh_code(admin_env, secret)}
    )
    assert swapped.status_code == 200, swapped.text
    seen = client.get(f"/api/v1/shops/{world.shop_a}/subscription", headers=as_user(world.owner_a)).json()
    assert (seen["cards"], seen["card_number"]) == ([UZCARD, HUMO], UZCARD["number"])

    for nothing in (None, []):
        cleared = _patch(client, admin, {"changes": {"payment_cards": nothing}, "code": fresh_code(admin_env, secret)})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["settings"]["payment_cards"] == []
        seen = client.get(f"/api/v1/shops/{world.shop_a}/subscription", headers=as_user(world.owner_a)).json()
        assert (seen["cards"], seen["card_number"]) == ([], None)
    assert {"before": hidden[::-1], "after": []} in [row[3] for row in _audit(owner, world.admin)]


def test_with_the_trial_switched_off_a_new_shop_starts_limited(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    off = _patch(client, admin, {"changes": {"trial_on": False}, "code": fresh_code(admin_env, secret)})
    assert off.status_code == 200, off.text
    created = client.post(
        "/api/v1/shops", json={"name": "Sinovsiz", "lang": "uz"}, headers={**as_user(world.stranger), **_key()}
    )
    row = owner.execute(
        "SELECT state, trial_ends FROM subscription WHERE shop_id = %s", (created.json()["id"],)
    ).fetchone()
    assert row == ("limited", None)


def test_one_code_covers_one_request_and_a_mixed_change_is_all_or_nothing(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    mixed = {"changes": {"trial_days": 10, "sms_on": True}}
    assert _patch(client, admin, mixed).status_code == 422
    assert _stored(owner) == {}, "the harmless half is not applied without the code either"

    code = fresh_code(admin_env, secret)
    assert _patch(client, admin, {**mixed, "code": code}).status_code == 200
    assert _stored(owner) == {"trial_days": 10, "sms_on": True}
    assert len(_audit(owner, world.admin)) == 2

    second = _patch(client, admin, {"changes": {"online_pay_on": True}, "code": code})
    assert second.status_code == 403, "the same code does not confirm a second change"
    assert "online_pay_on" not in _stored(owner)


def test_a_repeat_of_a_confirmed_change_returns_the_stored_answer_without_a_new_code(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    key = _key()
    body = {"changes": {"price_uzs": 130_000}, "code": fresh_code(admin_env, secret)}
    first = client.patch(SETTINGS, json=body, headers={**admin, **key})
    assert first.status_code == 200, first.text
    again = client.patch(SETTINGS, json=body, headers={**admin, **key})
    assert (again.status_code, again.json()) == (200, first.json())
    assert _failures(owner, world.admin) == 0, "the repeat did not present the used code as an attempt"
    # A retry typed with the next code is still the same request: the code is not part of what is asked.
    retyped = client.patch(SETTINGS, json={**body, "code": fresh_code(admin_env, secret)}, headers={**admin, **key})
    assert (retyped.status_code, retyped.json()) == (200, first.json())
    assert len(_audit(owner, world.admin)) == 1
    stored = owner.execute(
        "SELECT response::text FROM admin_request_key WHERE admin_id = %s", (world.admin,)
    ).fetchall()
    assert len(stored) == 1
    assert body["code"] not in stored[0][0]


def test_five_wrong_codes_on_a_change_lock_the_factor_and_end_the_session(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
) -> None:
    now = admin_env.clock.now()
    taken = {totp.code_at(secret, now + timedelta(seconds=30 * shift)) for shift in (-1, 0, 1)}
    wrong = next(code for code in ("000000", "111111", "222222", "333333") if code not in taken)
    change = {"changes": {"price_uzs": 1_000}, "code": wrong}
    for _ in range(4):
        assert _patch(client, admin, change).status_code == 403
    fifth = _patch(client, admin, change)
    assert (fifth.status_code, fifth.json()["error"]["code"]) == (429, "SECOND_FACTOR_LOCKED")
    assert _stored(owner) == {}
    assert _audit(owner, world.admin, "admin.second_factor_locked") != []
    assert client.get(SETTINGS, headers=admin).status_code == 404


# --- validation -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"trial_on": "yes"}, "changes.trial_on"),
        ({"trial_on": 1}, "changes.trial_on"),
        ({"sms_on": None}, "changes.sms_on"),
        ({"online_pay_on": "true"}, "changes.online_pay_on"),
        ({"trial_days": 0}, "changes.trial_days"),
        ({"trial_days": 366}, "changes.trial_days"),
        ({"trial_days": True}, "changes.trial_days"),
        ({"trial_days": 30.5}, "changes.trial_days"),
        ({"price_uzs": 999}, "changes.price_uzs"),
        ({"price_uzs": 10_000_001}, "changes.price_uzs"),
        ({"price_uzs": "100000"}, "changes.price_uzs"),
        ({"price_uzs": None}, "changes.price_uzs"),
        ({"sms_monthly_quota": -1}, "changes.sms_monthly_quota"),
        ({"sms_monthly_quota": 100_001}, "changes.sms_monthly_quota"),
        ({"payment_cards": [{"number": "8600 1234 5678 901", "label": "Humo"}]}, "changes.payment_cards"),
        ({"payment_cards": [{"number": "٨٦٠٠١٢٣٤٥٦٧٨٩٠١٢", "label": "Humo"}]}, "changes.payment_cards"),
        ({"payment_cards": [{"number": "8600123456789012", "label": ""}]}, "changes.payment_cards"),
        ({"payment_cards": [{"number": "8600123456789012", "label": "x" * 41}]}, "changes.payment_cards"),
        ({"payment_cards": [HUMO, {**HUMO, "label": "Yana"}]}, "changes.payment_cards"),
        ({"payment_cards": [{**HUMO, "number": f"86001234567890{n:02d}"} for n in range(11)]}, "changes.payment_cards"),
        ({"payment_cards": "8600123456789012"}, "changes.payment_cards"),
        ({"payment_cards": [{"number": "8600123456789012"}]}, "changes.payment_cards"),
        # The setting the list replaced is not a setting any more.
        ({"card_number": "8600123456789012"}, "changes.card_number"),
        ({"review_group": 12345}, "changes.review_group"),
        ({"review_group": "-100123"}, "changes.review_group"),
        ({"price": 100_000}, "changes.price"),
        ({"trial_days": [14]}, "changes.trial_days"),
    ],
)
def test_a_value_of_the_wrong_type_or_out_of_range_is_refused_before_the_code_is_looked_at(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
    changes: dict[str, Any],
    field: str,
) -> None:
    code = fresh_code(admin_env, secret)
    # Together with a valid change: nothing of a request with one bad value is applied.
    response = _patch(client, admin, {"changes": {"sms_monthly_quota": 50, **changes}, "code": code})
    assert response.status_code == 422, response.text
    assert list(response.json()["error"]["fields"]) == [field]
    assert _stored(owner) == {}
    assert _audit(owner, world.admin) == []
    # The code was not used up by the refused request.
    assert _patch(client, admin, {"changes": {"price_uzs": 110_000}, "code": code}).status_code == 200


@pytest.mark.parametrize(
    ("changes", "stored"),
    [
        ({"trial_days": 1}, 1),
        ({"trial_days": 365}, 365),
        ({"price_uzs": 1_000}, 1_000),
        ({"price_uzs": 10_000_000}, 10_000_000),
        ({"sms_monthly_quota": 0}, 0),
        ({"sms_monthly_quota": 100_000}, 100_000),
        ({"review_group": -1}, -1),
        ({"review_group": None}, None),
    ],
)
def test_values_at_the_edges_of_their_ranges_are_accepted(
    client: TestClient,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
    changes: dict[str, Any],
    stored: Any,
) -> None:
    response = _patch(client, admin, {"changes": changes, "code": fresh_code(admin_env, secret)})
    assert response.status_code == 200, response.text
    assert _stored(owner) == {next(iter(changes)): stored}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"changes": {}}, "changes"),
        ({}, "changes"),
        ({"changes": {"trial_days": 14}, "confirm": True}, "confirm"),
        ({"changes": {"trial_days": 14}, "code": "12345"}, "code"),
        ({"changes": {"trial_days": 14}, "reason": "ab"}, "reason"),
        ({"changes": [["trial_days", 14]]}, "changes"),
    ],
)
def test_a_malformed_settings_request_is_a_validation_error(
    client: TestClient, owner: psycopg.Connection, admin: dict[str, str], body: dict[str, Any], field: str
) -> None:
    response = _patch(client, admin, body)
    assert response.status_code == 422, response.text
    assert list(response.json()["error"]["fields"]) == [field]
    assert _stored(owner) == {}


# --- the audit ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("op_name", sorted(name for name, call in ADMIN_CALLS.items() if call.changes_data))
def test_every_administrator_write_leaves_exactly_one_audit_row_saying_who_what_and_why(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    admin: dict[str, str],
    secret: bytes,
    op_name: str,
) -> None:
    call = ADMIN_CALLS[op_name]
    if call.prepare is not None:
        call.prepare(owner, world)
    before = owner.execute("SELECT count(*) FROM admin_audit").fetchone()
    assert before is not None
    body = None if call.json is None else call.json(world)
    expected = call.ok_status
    if op_name == REASSIGN:
        # The suite's request carries a wrong code and names nobody; here the write has to happen.
        new_owner = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (world.stranger,)).fetchone()
        assert new_owner is not None and body is not None
        body = {**body, "new_owner_tg_id": new_owner[0], "code": fresh_code(admin_env, secret)}
        expected = 200
    response = client.request(call.method, call.path(world), json=body, headers={**admin, **_key()})
    assert response.status_code == expected, response.text

    rows = owner.execute(
        "SELECT admin_id, action, target_type, target_id, target_shop, reason, at FROM admin_audit "
        "WHERE admin_id = %s AND action <> 'admin.session_opened'",
        (world.admin,),
    ).fetchall()
    assert len(rows) == 1
    assert owner.execute("SELECT count(*) FROM admin_audit").fetchone() == (before[0] + 1,)
    who, action, target_type, target_id, target_shop, reason, at = rows[0]
    assert who == world.admin
    assert at is not None
    if "support" in op_name:
        assert action == op_name.replace("admin.support.", "support.") + ("ed" if op_name.endswith("open") else "d")
        assert (target_type, target_id, target_shop) == ("shop", str(world.shop_a), world.shop_a)
        assert reason == (None if body is None else body["reason"])
    elif op_name == REASSIGN:
        assert (action, target_type, target_id, target_shop) == (
            "shop.owner_reassigned",
            "shop",
            str(world.shop_a),
            world.shop_a,
        )
        assert body is not None and reason == body["reason"]
    elif "shops" in op_name:
        assert action.startswith("subscription.")
        assert (target_type, target_id, target_shop) == ("shop", str(world.shop_a), world.shop_a)
        assert body is not None
        assert reason == body["reason"]
    elif "receipts" in op_name:
        receipt = call.path(world).split("/")[-2]
        decided = {"admin.receipts.approve": "approved", "admin.receipts.reject": "rejected"}[op_name]
        assert action == f"subscription.receipt_{decided}"
        assert (target_type, target_id, target_shop) == ("receipt", receipt, world.shop_a)
        assert body is not None
        assert reason == body.get("reason")
    else:
        assert (action, target_type, target_id, target_shop) == ("setting.changed", "setting", "trial_days", None)


def test_lists_are_not_audited_but_a_look_at_one_shop_is(
    client: TestClient, world: World, owner: psycopg.Connection, admin: dict[str, str]
) -> None:
    def count() -> Any:
        return owner.execute("SELECT count(*) FROM admin_audit").fetchone()

    before = count()
    for path in (f"{ADMIN_API}/shops", SETTINGS, AUDIT, ADMIN_ENTRY_CALLS["admin.auth.read"].path(world)):
        assert client.get(path, headers=admin).status_code == 200
    assert count() == before
    assert client.get(f"{ADMIN_API}/shops/{world.shop_a}", headers=admin).status_code == 200
    assert count() == (before[0] + 1,)


def test_the_audit_is_listed_newest_first_filtered_and_paged(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, admin: dict[str, str]
) -> None:
    def step() -> None:
        admin_env.clock.offset += timedelta(seconds=1)

    def post(shop: uuid.UUID, what: str, reason: str) -> None:
        step()
        path = f"{ADMIN_API}/shops/{shop}/{what}"
        assert client.post(path, json={"reason": reason}, headers={**admin, **_key()}).status_code == 200

    post(world.shop_a, "suspend", "Birinchi")
    post(world.shop_b, "suspend", "Ikkinchi")
    post(world.shop_a, "unsuspend", "Uchinchi")
    step()
    assert client.get(f"{ADMIN_API}/shops/{world.shop_a}", headers=admin).status_code == 200

    of_a = client.get(AUDIT, params={"shop_id": str(world.shop_a)}, headers=admin)
    assert of_a.status_code == 200, of_a.text
    items = of_a.json()["items"]
    assert [(item["action"], item["reason"]) for item in items] == [
        ("shop.viewed", None),
        ("subscription.unsuspended", "Uchinchi"),
        ("subscription.suspended", "Birinchi"),
    ]
    assert of_a.json()["next_cursor"] is None
    first = items[1]
    assert set(first) == {
        "id",
        "at",
        "admin_id",
        "actor_tg_id",
        "action",
        "target_type",
        "target_id",
        "shop_id",
        "reason",
        "detail",
    }
    assert (first["admin_id"], first["target_type"], first["target_id"], first["shop_id"]) == (
        str(world.admin),
        "shop",
        str(world.shop_a),
        str(world.shop_a),
    )
    assert first["detail"]["before"]["state"] == "suspended"
    assert first["detail"]["after"]["state"] == "trial"

    only_changes = client.get(
        AUDIT, params={"shop_id": str(world.shop_a), "action": "subscription."}, headers=admin
    ).json()["items"]
    assert [item["action"] for item in only_changes] == ["subscription.unsuspended", "subscription.suspended"]
    of_b = client.get(AUDIT, params={"shop_id": str(world.shop_b)}, headers=admin).json()["items"]
    assert [item["reason"] for item in of_b] == ["Ikkinchi"]

    # Paged without gaps or repeats.
    walked: list[str] = []
    cursor: str | None = None
    while True:
        assert len(walked) <= len(items), "the pages must come to an end"
        params: dict[str, Any] = {"shop_id": str(world.shop_a), "limit": 1} | ({"cursor": cursor} if cursor else {})
        page = client.get(AUDIT, params=params, headers=admin).json()
        assert len(page["items"]) == 1
        walked += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert walked == [item["id"] for item in items]

    # Without a filter the administrator's own sign-in is there too.
    everything = client.get(AUDIT, params={"limit": 100}, headers=admin).json()["items"]
    assert "admin.session_opened" in {item["action"] for item in everything if item["admin_id"] == str(world.admin)}


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"action": "drop table;"}, "action"),
        ({"action": "x" * 61}, "action"),
        ({"cursor": "not-a-cursor"}, "cursor"),
        ({"shop_id": "not-a-uuid"}, "shop_id"),
    ],
)
def test_a_bad_audit_request_is_a_validation_error(
    client: TestClient, admin: dict[str, str], params: dict[str, Any], field: str
) -> None:
    response = client.get(AUDIT, params=params, headers=admin)
    assert response.status_code == 422, response.text
    assert list(response.json()["error"]["fields"]) == [field]


def test_the_audit_page_size_is_accepted_at_its_edges(client: TestClient, admin: dict[str, str]) -> None:
    assert client.get(AUDIT, params={"limit": 1}, headers=admin).status_code == 200
    assert client.get(AUDIT, params={"limit": 100, "action": "x" * 60}, headers=admin).status_code == 200
