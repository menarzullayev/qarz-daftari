"""The deployment setting that switches the administrators' second factor off (`QD_ADMIN_SECOND_FACTOR`,
the owner's decision of 2026-10-10; ADR-017).

Required is the default and is what every other test of the administrators' side runs with. Here: the
default still refuses, a wrong value refuses to start, and with `off` nothing asks for a code or an
enrolment, the API says so, the audit says so, and the stored second factor is left exactly as it was.
"""

import logging
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.admin_access import AdminAccess
from qarz.application.auth import AuthService
from qarz.infrastructure.db import Database
from qarz.infrastructure.settings import Settings
from qarz.interface import asgi
from qarz.interface.http import create_app
from qarz.interface.observability import Metrics

from .conftest import (
    ADMIN_API,
    TEST_BOT_TOKEN,
    TEST_SECRETS_KEY,
    AdminEnv,
    HeaderAuthenticator,
    World,
    allow_list,
    as_user,
    elevate,
    fresh_code,
    make_admin,
)

pytestmark = pytest.mark.db

AUTH = f"{ADMIN_API}/auth"
SETTINGS = f"{ADMIN_API}/settings"
METRICS_TOKEN = "metrics-token-for-second-factor-tests"
GAUGE = "qd_admin_second_factor_off"
REASON = "The owner lost the Telegram account; decision of the founder."


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"admin-{uuid.uuid4().hex}"}


def _app(app_url: str, admin_url: str, env: AdminEnv, *, required: bool) -> Iterator[TestClient]:
    database, admin_database = Database(app_url), Database(admin_url)
    admin = AdminAccess(
        admin_database,
        allowed_tg_ids=env.allowed,
        cipher=env.box,
        now=env.clock.now,
        second_factor_required=required,
    )
    app = create_app(
        database.reachable,
        database,
        auth=AuthService(database, TEST_BOT_TOKEN),
        admin=admin,
        admin_storage=admin_database,
        authenticator=HeaderAuthenticator(),
        now=env.clock.now,
        metrics_token=METRICS_TOKEN,
    )
    with TestClient(app) as client:
        yield client
        client.portal.call(database.dispose)  # type: ignore[union-attr]
        client.portal.call(admin_database.dispose)  # type: ignore[union-attr]


@pytest.fixture
def off(app_database_url: str, admin_database_url: str, admin_env: AdminEnv) -> Iterator[TestClient]:
    """The application of a deployment with QD_ADMIN_SECOND_FACTOR=off."""
    yield from _app(app_database_url, admin_database_url, admin_env, required=False)


@pytest.fixture
def required(app_database_url: str, admin_database_url: str, admin_env: AdminEnv) -> Iterator[TestClient]:
    """The same application with the default."""
    yield from _app(app_database_url, admin_database_url, admin_env, required=True)


@pytest.fixture
def secret(world: World, owner: psycopg.Connection, admin_env: AdminEnv) -> bytes:
    admin_env.clock.freeze()
    return make_admin(owner, admin_env, world.admin)


def _account(owner: psycopg.Connection, user: uuid.UUID) -> Any:
    return owner.execute(
        "SELECT totp_secret, confirmed_at, failed_codes, locked_until, last_step, status "
        "FROM admin_account WHERE user_id = %s",
        (user,),
    ).fetchone()


def _audit(owner: psycopg.Connection, admin: uuid.UUID, action: str) -> list[Any]:
    return owner.execute(
        "SELECT target_id, detail FROM admin_audit WHERE admin_id = %s AND action = %s ORDER BY at, target_id",
        (admin, action),
    ).fetchall()


def _stored(owner: psycopg.Connection, key: str) -> Any:
    row = owner.execute("SELECT value FROM platform_setting WHERE key = %s", (key,)).fetchone()
    return None if row is None else row[0]


def _gauge(client: TestClient) -> str | None:
    text = client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"}).text
    return next((line.split()[-1] for line in text.splitlines() if line.startswith(GAUGE + " ")), None)


# --- the setting ----------------------------------------------------------------------------------------


def test_the_setting_is_required_unless_it_says_off_and_any_other_value_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("QD_ADMIN_SECOND_FACTOR", raising=False)
    assert Settings().second_factor_required() is True
    monkeypatch.setenv("QD_ADMIN_SECOND_FACTOR", "required")
    assert Settings().second_factor_required() is True
    monkeypatch.setenv("QD_ADMIN_SECOND_FACTOR", "off")
    assert Settings().second_factor_required() is False
    # Nothing is read as either value: not another case, not a synonym, not an empty value.
    for bad in ("", "OFF", "Off", "on", "false", "0", "no", "disabled", " off", "optional"):
        monkeypatch.setenv("QD_ADMIN_SECOND_FACTOR", bad)
        with pytest.raises(ValueError, match="QD_ADMIN_SECOND_FACTOR"):
            Settings().second_factor_required()
        # And the API itself does not start with it, whether or not it serves administrators.
        with pytest.raises(ValueError, match="QD_ADMIN_SECOND_FACTOR"):
            asgi.build()


class _Kept(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.mark.parametrize(("value", "warned"), [("off", True), ("required", False), (None, False)])
def test_the_api_warns_at_start_when_the_second_factor_is_off_and_only_then(
    admin_database_url: str, monkeypatch: pytest.MonkeyPatch, value: str | None, warned: bool
) -> None:
    monkeypatch.setenv("QD_ADMIN_TG_IDS", "1")
    monkeypatch.setenv("QD_SECRETS_KEY", TEST_SECRETS_KEY)
    monkeypatch.setenv("QD_ADMIN_DATABASE_URL", admin_database_url)
    if value is None:
        monkeypatch.delenv("QD_ADMIN_SECOND_FACTOR", raising=False)
    else:
        monkeypatch.setenv("QD_ADMIN_SECOND_FACTOR", value)
    kept = _Kept()
    logger = logging.getLogger("qarz.admin")
    logger.addHandler(kept)
    try:
        asgi.build()
    finally:
        logger.removeHandler(kept)
    lines = [record for record in kept.records if record.getMessage().startswith("admin_second_factor_off")]
    assert len(lines) == (1 if warned else 0)
    assert all(record.levelno == logging.WARNING for record in lines)


def test_the_metrics_say_whether_the_second_factor_is_off(off: TestClient, required: TestClient) -> None:
    assert _gauge(off) == "1"
    assert _gauge(required) == "0"
    # An API that serves no administrators has no factor to speak of.
    assert GAUGE not in Metrics().render({})
    assert f"# TYPE {GAUGE} gauge\n{GAUGE} 1\n" in Metrics().render({}, {GAUGE: True})


# --- required, the default: nothing changed ---------------------------------------------------------------


def test_by_default_a_sensitive_change_without_a_code_is_refused_and_nothing_is_stored(
    required: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, secret: bytes
) -> None:
    signed_in = as_user(world.admin)
    # No admin session: the door says "enter a code", and everything behind it is "not found".
    status = required.get(AUTH, headers=signed_in).json()
    assert status == {
        "enrolled": True,
        "confirmed": True,
        "elevated": False,
        "expires_at": None,
        "locked_until": None,
    }, "the default status carries no new field"
    assert required.get(SETTINGS, headers=signed_in).status_code == 404

    admin = elevate(required, admin_env, world.admin, secret)
    read = required.get(SETTINGS, headers=admin).json()
    assert "second_factor" not in read
    assert "sms_on" in read["needs_code"]

    refused = required.patch(SETTINGS, json={"changes": {"sms_on": True}}, headers={**admin, **_key()})
    assert refused.status_code == 422, refused.text
    assert list(refused.json()["error"]["fields"]) == ["code"]
    assert _stored(owner, "sms_on") is None
    assert _audit(owner, world.admin, "setting.changed") == []

    new_tg = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (world.stranger,)).fetchone()[0]  # type: ignore[index]
    no_code = required.post(
        f"{ADMIN_API}/shops/{world.shop_a}/owner",
        json={"new_owner_tg_id": new_tg, "reason": REASON},
        headers={**admin, **_key()},
    )
    assert no_code.status_code == 422, no_code.text
    assert list(no_code.json()["error"]["fields"]) == ["code"]


# --- off ------------------------------------------------------------------------------------------------


def test_off_the_door_asks_for_nothing_and_says_why(off: TestClient, world: World, secret: bytes) -> None:
    status = off.get(AUTH, headers=as_user(world.admin))
    assert status.status_code == 200, status.text
    assert status.json() == {
        "enrolled": True,
        "confirmed": True,
        "elevated": True,
        "expires_at": None,
        "locked_until": None,
        "second_factor": "off",
    }
    # Behind the door with no admin session cookie at all.
    assert off.get(f"{ADMIN_API}/shops", headers=as_user(world.admin)).status_code == 200


def test_off_sensitive_settings_change_without_a_code_and_the_audit_says_so(
    off: TestClient, world: World, owner: psycopg.Connection, secret: bytes
) -> None:
    admin = as_user(world.admin)
    before = _account(owner, world.admin)

    read = off.get(SETTINGS, headers=admin).json()
    assert read["needs_code"] == [], "the panel is told that no change asks for a code"
    assert read["second_factor"] == "off"

    changed = off.patch(
        SETTINGS,
        json={"changes": {"sms_on": True, "price_uzs": 150_000, "trial_days": 45}},
        headers={**admin, **_key()},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["second_factor"] == "off"
    assert (_stored(owner, "sms_on"), _stored(owner, "price_uzs"), _stored(owner, "trial_days")) == (True, 150_000, 45)

    audit = dict(_audit(owner, world.admin, "setting.changed"))
    assert audit["sms_on"] == {"before": False, "after": True, "second_factor": "off"}
    assert audit["price_uzs"] == {"before": 100_000, "after": 150_000, "second_factor": "off"}
    # A setting that never asked for a code says nothing of one.
    assert audit["trial_days"] == {"before": 30, "after": 45}

    # A code that is sent anyway is not looked at: a wrong one is neither refused nor counted.
    wrong = off.patch(SETTINGS, json={"changes": {"sms_on": False}, "code": "000000"}, headers={**admin, **_key()})
    assert wrong.status_code == 200, wrong.text
    assert _account(owner, world.admin) == before, "the stored second factor is exactly as it was"


def test_off_an_owner_is_reassigned_without_a_code_and_a_row_beside_the_change_says_so(
    off: TestClient, world: World, owner: psycopg.Connection, secret: bytes
) -> None:
    new_tg = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (world.stranger,)).fetchone()[0]  # type: ignore[index]
    done = off.post(
        f"{ADMIN_API}/shops/{world.shop_a}/owner",
        json={"new_owner_tg_id": new_tg, "reason": REASON},
        headers={**as_user(world.admin), **_key()},
    )
    assert done.status_code == 200, done.text
    (change,) = owner.execute(
        "SELECT id, reason FROM admin_audit WHERE target_shop = %s AND action = 'shop.owner_reassigned'",
        (world.shop_a,),
    ).fetchall()
    assert change[1] == REASON, "the row of the change itself is written as ever"
    assert _audit(owner, world.admin, "shop.owner_reassigned_without_code") == [
        (str(world.shop_a), {"change": str(change[0]), "second_factor": "off"})
    ]


def test_off_someone_who_never_confirmed_a_second_factor_enrols_once_and_is_then_asked_for_nothing(
    off: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    """The database changes a setting only for an account that once confirmed a second factor (migration
    0027), and no setting of the application reaches that. So `off` does not pretend otherwise: the door
    says "enrol", and after the one code of the enrolment no code is asked for again."""
    admin_env.clock.freeze()
    allow_list(owner, admin_env, world.stranger)
    signed_in = as_user(world.stranger)

    status = off.get(AUTH, headers=signed_in).json()
    assert status == {
        "enrolled": False,
        "confirmed": False,
        "elevated": False,
        "expires_at": None,
        "locked_until": None,
        "second_factor": "off",
    }
    assert _account(owner, world.stranger) is None, "nothing is created behind the person's back"
    assert off.get(SETTINGS, headers=signed_in).status_code == 404

    enrolled = off.post(f"{AUTH}/enrolment", headers={**signed_in, **_key()})
    assert enrolled.status_code == 201, enrolled.text
    stored = _account(owner, world.stranger)[0]
    secret = admin_env.box.decrypt(bytes(stored), world.stranger.bytes)
    assert off.get(SETTINGS, headers=signed_in).status_code == 404, "enrolled, not yet confirmed"
    # The first code is judged as ever: a wrong one confirms nothing.
    wrong = off.post(f"{AUTH}/session", json={"code": "000000"}, headers=signed_in)
    assert wrong.status_code == 403, wrong.text
    opened = off.post(f"{AUTH}/session", json={"code": fresh_code(admin_env, secret)}, headers=signed_in)
    assert opened.status_code == 201, opened.text

    # From here on: no cookie, no code.
    assert off.get(AUTH, headers=signed_in).json()["elevated"] is True
    changed = off.patch(SETTINGS, json={"changes": {"sms_on": True}}, headers={**signed_in, **_key()})
    assert changed.status_code == 200, changed.text
    assert _stored(owner, "sms_on") is True


def test_off_the_database_is_shown_an_open_admin_session_and_the_audit_says_how_it_was_opened(
    off: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, secret: bytes
) -> None:
    signed_in = as_user(world.admin)

    def sessions() -> int:
        row = owner.execute(
            "SELECT count(*) FROM admin_session WHERE user_id = %s AND revoked_at IS NULL AND expires_at > %s",
            (world.admin, admin_env.clock.now()),
        ).fetchone()
        assert row is not None
        return int(row[0])

    assert sessions() == 0
    off.get(AUTH, headers=signed_in)
    off.get(AUTH, headers=signed_in)
    assert sessions() == 1, "one is kept open, not one for every question"
    (opened,) = _audit(owner, world.admin, "admin.session_opened")
    assert opened[1]["second_factor"] == "off"

    # It ran out, or the person signed out everywhere, while the panel stayed open: the next sensitive
    # change opens another in its own transaction, and the database accepts the change.
    owner.execute("UPDATE admin_session SET revoked_at = %s WHERE user_id = %s", (admin_env.clock.now(), world.admin))
    assert sessions() == 0
    changed = off.patch(SETTINGS, json={"changes": {"sms_on": True}}, headers={**signed_in, **_key()})
    assert changed.status_code == 200, changed.text
    assert sessions() == 1
    assert len(_audit(owner, world.admin, "admin.session_opened")) == 2


def test_off_opens_nothing_to_anybody_else(
    off: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, secret: bytes
) -> None:
    # Not on the allow-list: "not found" at the door and behind it, and no account is made.
    for path in (AUTH, SETTINGS, f"{ADMIN_API}/shops"):
        assert off.get(path, headers=as_user(world.stranger)).status_code == 404
    assert _account(owner, world.stranger) is None
    # Not signed in at all.
    assert off.get(SETTINGS).status_code == 401
    # A disabled administrator stays disabled.
    owner.execute("UPDATE admin_account SET status = 'disabled' WHERE user_id = %s", (world.admin,))
    for path in (AUTH, SETTINGS):
        assert off.get(path, headers=as_user(world.admin)).status_code == 404


def test_back_to_required_the_same_second_factor_is_asked_for_again_with_no_new_enrolment(
    off: TestClient,
    required: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    secret: bytes,
) -> None:
    signed_in = as_user(world.admin)
    assert off.patch(SETTINGS, json={"changes": {"sms_on": True}}, headers={**signed_in, **_key()}).status_code == 200

    # The same database, the setting back at its default: the door is shut and the old secret opens it.
    assert required.get(SETTINGS, headers=signed_in).status_code == 404
    status = required.get(AUTH, headers=signed_in).json()
    assert (status["enrolled"], status["confirmed"], status["elevated"]) == (True, True, False)
    admin = elevate(required, admin_env, world.admin, secret)
    refused = required.patch(SETTINGS, json={"changes": {"sms_on": False}}, headers={**admin, **_key()})
    assert refused.status_code == 422, refused.text
    assert _stored(owner, "sms_on") is True
