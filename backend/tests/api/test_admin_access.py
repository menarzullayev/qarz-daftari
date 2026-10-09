"""The administrator's second factor and admin session, through the API (ADR-017; story S18.1)."""

import base64
import hashlib
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.admin_access import ADMIN_SESSION, AdminAccess
from qarz.application.auth import AuthService
from qarz.domain import totp
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app
from tests.test_telegram_auth import login_data, webapp_init_data

from .conftest import (
    ADMIN_API,
    ADMIN_COOKIE,
    TEST_BOT_TOKEN,
    AdminEnv,
    World,
    admin_cookie,
    allow_list,
    as_user,
    elevate,
    fresh_code,
    make_admin,
)

pytestmark = pytest.mark.db

AUTH = f"{ADMIN_API}/auth"
ENROL = f"{ADMIN_API}/auth/enrolment"
SESSION = f"{ADMIN_API}/auth/session"
SETTINGS = f"{ADMIN_API}/settings"
STEP = timedelta(seconds=totp.STEP_SECONDS)


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"admin-{uuid.uuid4().hex}"}


def _account(owner: psycopg.Connection, user_id: uuid.UUID) -> dict[str, Any]:
    row = owner.execute(
        "SELECT status, totp_secret, confirmed_at, failed_codes, locked_until, last_step "
        "FROM admin_account WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    assert row is not None
    return dict(zip(("status", "secret", "confirmed_at", "failures", "locked_until", "last_step"), row, strict=True))


def _audit(owner: psycopg.Connection, admin: uuid.UUID) -> list[str]:
    rows = owner.execute("SELECT action FROM admin_audit WHERE admin_id = %s ORDER BY at, id", (admin,)).fetchall()
    return [action for (action,) in rows]


def _secret_of(uri: str) -> bytes:
    encoded = parse_qs(urlsplit(uri).query)["secret"][0]
    return base64.b32decode(encoded + "=" * (-len(encoded) % 8))


def _wrong(env: AdminEnv, secret: bytes) -> str:
    """A well-formed code that is right for none of the steps the server would accept."""
    now = env.clock.now()
    taken = {totp.code_at(secret, now + shift * STEP) for shift in (-1, 0, 1)}
    return next(code for code in ("000000", "111111", "222222", "333333") if code not in taken)


def _open(client: TestClient, user_id: uuid.UUID, code: str) -> Any:
    return client.post(SESSION, json={"code": code}, headers=as_user(user_id))


# --- enrolment ------------------------------------------------------------------------------------------


def test_the_first_sign_in_of_an_allow_listed_person_enrols_a_secret_shown_once(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    tg_id = allow_list(owner, admin_env, world.stranger)
    before = client.get(AUTH, headers=as_user(world.stranger))
    assert before.json() == {
        "enrolled": False,
        "confirmed": False,
        "elevated": False,
        "expires_at": None,
        "locked_until": None,
    }

    key = _key()
    response = client.post(ENROL, headers={**as_user(world.stranger), **key})
    assert response.status_code == 201, response.text
    uri = response.json()["otpauth_uri"]
    assert uri.startswith(f"otpauth://totp/Qarz%20Daftari%3Aadmin-{tg_id}?secret=")
    secret = _secret_of(uri)
    assert len(secret) == 20

    account = _account(owner, world.stranger)
    assert (account["status"], account["confirmed_at"]) == ("active", None)
    # Encrypted at rest: the stored bytes are not the secret, and open only with the key.
    assert secret not in bytes(account["secret"])
    assert admin_env.box.decrypt(bytes(account["secret"]), world.stranger.bytes) == secret
    assert _audit(owner, world.stranger) == ["admin.enrolled"]
    assert client.get(AUTH, headers=as_user(world.stranger)).json()["enrolled"] is True

    # A repeat of the same request says that it happened and does not show the secret again.
    again = client.post(ENROL, headers={**as_user(world.stranger), **key})
    assert again.status_code == 201
    assert again.json() == {"enrolled": True, "otpauth_uri": None}
    assert bytes(_account(owner, world.stranger)["secret"]) == bytes(account["secret"])
    assert _audit(owner, world.stranger) == ["admin.enrolled"]
    stored = owner.execute(
        "SELECT response::text FROM admin_request_key WHERE admin_id = %s", (world.stranger,)
    ).fetchall()
    assert len(stored) == 1
    assert uri.split("secret=")[1].split("&")[0] not in stored[0][0]
    assert "otpauth://" not in stored[0][0]

    # The secret works: its code opens an admin session and confirms the enrolment.
    opened = _open(client, world.stranger, fresh_code(admin_env, secret))
    assert opened.status_code == 201, opened.text
    assert _account(owner, world.stranger)["confirmed_at"] is not None
    assert client.get(AUTH, headers=as_user(world.stranger)).json()["confirmed"] is True


def test_enrolment_needs_an_idempotency_key(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    allow_list(owner, admin_env, world.stranger)
    response = client.post(ENROL, headers=as_user(world.stranger))
    assert response.status_code == 422
    assert "Idempotency-Key" in response.json()["error"]["fields"]
    assert owner.execute("SELECT count(*) FROM admin_account WHERE user_id = %s", (world.stranger,)).fetchone() == (0,)


def test_until_confirmed_an_enrolment_can_be_repeated_and_replaces_the_secret(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    allow_list(owner, admin_env, world.stranger)
    first = _secret_of(client.post(ENROL, headers={**as_user(world.stranger), **_key()}).json()["otpauth_uri"])
    # A wrong code before the second enrolment: the new secret starts with a clean count.
    assert _open(client, world.stranger, _wrong(admin_env, first)).status_code == 403
    assert _account(owner, world.stranger)["failures"] == 1

    second_response = client.post(ENROL, headers={**as_user(world.stranger), **_key()})
    assert second_response.status_code == 201
    second = _secret_of(second_response.json()["otpauth_uri"])
    assert second != first
    assert _account(owner, world.stranger)["failures"] == 0
    assert _open(client, world.stranger, fresh_code(admin_env, first)).status_code == 403, "the old secret is gone"
    assert _open(client, world.stranger, fresh_code(admin_env, second)).status_code == 201


def test_a_confirmed_second_factor_cannot_be_replaced_through_the_api(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    secret = make_admin(owner, admin_env, world.admin)
    stored = bytes(_account(owner, world.admin)["secret"])
    for headers in (as_user(world.admin), elevate(client, admin_env, world.admin, secret)):
        response = client.post(ENROL, headers={**headers, **_key()})
        assert (response.status_code, response.json()["error"]["code"]) == (409, "ADMIN_ALREADY_ENROLLED")
    assert bytes(_account(owner, world.admin)["secret"]) == stored
    assert "admin.enrolled" not in _audit(owner, world.admin)


def test_an_account_whose_secret_cannot_be_read_accepts_no_code(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    """The world's administrator was seeded with bytes this application never encrypted."""
    allow_list(owner, admin_env, world.admin)
    assert bytes(_account(owner, world.admin)["secret"]) == b"test-only"
    assert _open(client, world.admin, "123456").status_code == 403
    assert _account(owner, world.admin)["failures"] == 1


# --- passing the second factor --------------------------------------------------------------------------


def test_the_right_code_opens_an_admin_session_in_a_protected_cookie(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    secret = make_admin(owner, admin_env, world.admin)
    admin_env.clock.freeze()
    ordinary_sessions = owner.execute("SELECT count(*) FROM user_session").fetchone()
    response = _open(client, world.admin, fresh_code(admin_env, secret))
    assert response.status_code == 201, response.text
    expires = admin_env.clock.now() + ADMIN_SESSION
    assert timedelta(hours=8) == ADMIN_SESSION, "specification: the administrator's session is valid 8 hours"
    assert response.json() == {"expires_at": expires.isoformat()}

    cookie = response.headers["set-cookie"]
    token = admin_cookie(response)
    lowered = cookie.lower()
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=strict" in lowered
    assert "path=/api/admin" in lowered
    assert token not in response.text, "the token travels only in the cookie"

    # Stored as a hash, and nowhere else.
    rows = owner.execute(
        "SELECT token_hash, expires_at, revoked_at FROM admin_session WHERE user_id = %s", (world.admin,)
    ).fetchall()
    assert [(bytes(row[0]), row[1], row[2]) for row in rows] == [
        (hashlib.sha256(token.encode()).digest(), expires, None)
    ]
    # Counted against what was there before: other tests leave ordinary sessions of their own people.
    assert owner.execute("SELECT count(*) FROM user_session").fetchone() == ordinary_sessions, "not an ordinary session"
    assert owner.execute("SELECT count(*) FROM user_session WHERE user_id = %s", (world.admin,)).fetchone() == (0,)

    headers = {**as_user(world.admin), "Cookie": f"{ADMIN_COOKIE}={token}"}
    assert client.get(SETTINGS, headers=headers).status_code == 200
    assert client.get(AUTH, headers=headers).json() == {
        "enrolled": True,
        "confirmed": True,
        "elevated": True,
        "expires_at": expires.isoformat(),
        "locked_until": None,
    }
    assert _audit(owner, world.admin) == ["admin.session_opened"]


def test_a_wrong_code_opens_nothing_and_is_counted(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    secret = make_admin(owner, admin_env, world.admin)
    response = _open(client, world.admin, _wrong(admin_env, secret))
    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "SECOND_FACTOR_INVALID",
        "message": "Kod noto'g'ri, eskirgan yoki allaqachon ishlatilgan. Yangi kodni kiriting.",
        "fields": {},
    }
    assert "set-cookie" not in response.headers
    assert owner.execute("SELECT count(*) FROM admin_session WHERE user_id = %s", (world.admin,)).fetchone() == (0,)
    assert _account(owner, world.admin)["failures"] == 1

    # The right code afterwards gets in and clears the count.
    assert _open(client, world.admin, fresh_code(admin_env, secret)).status_code == 201
    assert _account(owner, world.admin)["failures"] == 0


@pytest.mark.parametrize("body", [{}, {"code": "12345"}, {"code": "1234567"}, {"code": "12345a"}, {"code": 123456}])
def test_a_malformed_code_is_a_validation_error_and_is_not_counted(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, body: dict[str, Any]
) -> None:
    make_admin(owner, admin_env, world.admin)
    response = client.post(SESSION, json=body, headers=as_user(world.admin))
    assert response.status_code == 422, response.text
    assert "code" in response.json()["error"]["fields"]
    assert _account(owner, world.admin)["failures"] == 0


def test_an_unknown_field_or_a_body_that_is_not_json_is_a_validation_error(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    secret = make_admin(owner, admin_env, world.admin)
    code = fresh_code(admin_env, secret)
    extra = client.post(SESSION, json={"code": code, "remember": True}, headers=as_user(world.admin))
    assert extra.status_code == 422
    assert "remember" in extra.json()["error"]["fields"]
    broken = client.post(
        SESSION, content=b"{not json", headers={**as_user(world.admin), "Content-Type": "application/json"}
    )
    assert (broken.status_code, broken.json()["error"]["code"]) == (422, "VALIDATION")
    assert owner.execute("SELECT count(*) FROM admin_session WHERE user_id = %s", (world.admin,)).fetchone() == (0,)
    assert _open(client, world.admin, code).status_code == 201, "the refused requests did not use the code up"


def test_a_code_cannot_be_replayed(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    secret = make_admin(owner, admin_env, world.admin)
    admin_env.clock.freeze()
    code = fresh_code(admin_env, secret)
    assert _open(client, world.admin, code).status_code == 201
    replay = _open(client, world.admin, code)
    assert (replay.status_code, replay.json()["error"]["code"]) == (403, "SECOND_FACTOR_INVALID")
    assert _account(owner, world.admin)["failures"] == 1
    # Nor does the code of the step before it get in behind it, though it is inside the clock allowance.
    earlier = totp.code_at(secret, admin_env.clock.now() - STEP)
    assert _open(client, world.admin, earlier).status_code == 403
    # The first session is still the live one: a refused replay revoked nothing.
    live = owner.execute(
        "SELECT count(*) FROM admin_session WHERE user_id = %s AND revoked_at IS NULL", (world.admin,)
    ).fetchone()
    assert live == (1,)
    assert _open(client, world.admin, fresh_code(admin_env, secret)).status_code == 201


@pytest.mark.parametrize(("steps_away", "status"), [(-2, 403), (-1, 201), (0, 201), (1, 201), (2, 403)])
def test_the_clocks_may_differ_by_one_step_and_not_by_two(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, steps_away: int, status: int
) -> None:
    secret = make_admin(owner, admin_env, world.admin)
    admin_env.clock.freeze()
    code = totp.code_at(secret, admin_env.clock.now() + steps_away * STEP)
    assert _open(client, world.admin, code).status_code == status


# --- the lock -------------------------------------------------------------------------------------------


def test_four_wrong_codes_do_not_lock_and_a_right_one_clears_the_count(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    secret = make_admin(owner, admin_env, world.admin)
    for _ in range(4):
        assert _open(client, world.admin, _wrong(admin_env, secret)).status_code == 403
    assert _account(owner, world.admin)["failures"] == 4
    assert _open(client, world.admin, fresh_code(admin_env, secret)).status_code == 201
    for _ in range(4):
        assert _open(client, world.admin, _wrong(admin_env, secret)).status_code == 403
    assert _account(owner, world.admin)["locked_until"] is None
    assert "admin.second_factor_locked" not in _audit(owner, world.admin)


def test_the_fifth_wrong_code_locks_the_factor_for_fifteen_minutes(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    secret = make_admin(owner, admin_env, world.admin)
    admin_env.clock.freeze()
    headers = elevate(client, admin_env, world.admin, secret)
    for _ in range(4):
        assert _open(client, world.admin, _wrong(admin_env, secret)).status_code == 403
    assert client.get(SETTINGS, headers=headers).status_code == 200, "four wrong codes cost nothing yet"

    fifth = _open(client, world.admin, _wrong(admin_env, secret))
    assert fifth.status_code == 429
    assert fifth.json()["error"]["code"] == "SECOND_FACTOR_LOCKED"
    assert fifth.json()["error"]["fields"] == {"retry_after_seconds": "900"}
    locked_until = admin_env.clock.now() + timedelta(minutes=15)
    account = _account(owner, world.admin)
    assert (account["locked_until"], account["failures"]) == (locked_until, 0)
    assert client.get(AUTH, headers=as_user(world.admin)).json()["locked_until"] == locked_until.isoformat()
    assert _audit(owner, world.admin).count("admin.second_factor_locked") == 1
    # Whoever was typing those codes may have been holding a session of this administrator.
    assert client.get(SETTINGS, headers=headers).status_code == 404

    # While it is locked the right code is refused too, and refusals no longer change anything.
    admin_env.clock.offset += timedelta(minutes=15) - timedelta(microseconds=1)
    right = totp.code_at(secret, admin_env.clock.now())
    still = _open(client, world.admin, right)
    assert (still.status_code, still.json()["error"]["fields"]) == (429, {"retry_after_seconds": "1"})
    assert _account(owner, world.admin) == account
    assert _audit(owner, world.admin).count("admin.second_factor_locked") == 1

    # At the instant the lock says, it is over.
    admin_env.clock.offset += timedelta(microseconds=1)
    assert admin_env.clock.now() == locked_until
    assert client.get(AUTH, headers=as_user(world.admin)).json()["locked_until"] is None
    assert _open(client, world.admin, right).status_code == 201
    assert client.get(AUTH, headers=as_user(world.admin)).json()["locked_until"] is None


# --- the admin session ----------------------------------------------------------------------------------


def test_an_admin_session_ends_after_eight_hours_exactly(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    secret = make_admin(owner, admin_env, world.admin)
    admin_env.clock.freeze()
    headers = elevate(client, admin_env, world.admin, secret)
    admin_env.clock.offset += timedelta(hours=8) - timedelta(microseconds=1)
    assert client.get(SETTINGS, headers=headers).status_code == 200
    assert client.get(AUTH, headers=headers).json()["elevated"] is True
    admin_env.clock.offset += timedelta(microseconds=1)
    assert client.get(SETTINGS, headers=headers).status_code == 404
    assert client.get(AUTH, headers=headers).json()["elevated"] is False


def test_passing_the_factor_again_ends_the_earlier_admin_session(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    secret = make_admin(owner, admin_env, world.admin)
    first = elevate(client, admin_env, world.admin, secret)
    second = elevate(client, admin_env, world.admin, secret)
    assert first["Cookie"] != second["Cookie"]
    assert client.get(SETTINGS, headers=first).status_code == 404
    assert client.get(SETTINGS, headers=second).status_code == 200


def test_closing_the_admin_session_revokes_it_and_clears_the_cookie(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    secret = make_admin(owner, admin_env, world.admin)
    headers = elevate(client, admin_env, world.admin, secret)
    closed = client.delete(SESSION, headers=headers)
    assert closed.status_code == 204
    assert f"{ADMIN_COOKIE}=" in closed.headers["set-cookie"]
    assert "max-age=0" in closed.headers["set-cookie"].lower()
    assert client.get(SETTINGS, headers=headers).status_code == 404
    revoked = owner.execute("SELECT revoked_at IS NOT NULL FROM admin_session WHERE user_id = %s", (world.admin,))
    assert revoked.fetchall() == [(True,)]
    assert _audit(owner, world.admin) == ["admin.session_opened", "admin.session_closed"]
    # The ordinary sign-in is untouched: only the elevation ended.
    assert client.get("/api/v1/me", headers=headers).status_code == 200


def test_a_garbage_admin_cookie_is_just_no_admin_session(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    make_admin(owner, admin_env, world.admin)
    for value in ("", "short", "x" * 200, "né-ascii-" + "x" * 30, "a" * 43):
        headers = {**as_user(world.admin), "Cookie": f"{ADMIN_COOKIE}={value}".encode()}
        assert client.get(SETTINGS, headers=headers).status_code == 404
        assert client.get(AUTH, headers=headers).json()["elevated"] is False


# --- with real sessions: the two kinds of session are not interchangeable -------------------------------


@pytest.fixture
def deployed(app_database_url: str, admin_database_url: str, admin_env: AdminEnv) -> Iterator[TestClient]:
    """The application as deployed: Telegram-backed sessions, no test authenticator, the admin side on."""
    database, admin_database = Database(app_database_url), Database(admin_database_url)
    auth = AuthService(database, TEST_BOT_TOKEN)
    admin = AdminAccess(admin_database, allowed_tg_ids=admin_env.allowed, cipher=admin_env.box, now=admin_env.clock.now)
    app = create_app(
        database.reachable, database, auth=auth, admin=admin, admin_storage=admin_database, now=admin_env.clock.now
    )
    with TestClient(app) as test_client:
        yield test_client
        test_client.portal.call(database.dispose)  # type: ignore[union-attr]
        test_client.portal.call(admin_database.dispose)  # type: ignore[union-attr]


def _signed_in(deployed: TestClient, owner: psycopg.Connection, tg_id: int) -> tuple[uuid.UUID, str]:
    init_data = webapp_init_data(token=TEST_BOT_TOKEN, auth_date=datetime.now(UTC) - timedelta(seconds=30), tg_id=tg_id)
    response = deployed.post("/api/v1/auth/telegram-webapp", json={"init_data": init_data})
    assert response.status_code == 200, response.text
    row = owner.execute("SELECT id FROM app_user WHERE tg_id = %s", (tg_id,)).fetchone()
    assert row is not None
    return row[0], response.json()["token"]


def _tg() -> int:
    return 7_000_000_000 + uuid.uuid4().int % 10**9


def test_an_ordinary_session_and_an_admin_session_cannot_stand_in_for_each_other(
    deployed: TestClient, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    user_id, bearer = _signed_in(deployed, owner, _tg())
    secret = make_admin(owner, admin_env, user_id)
    ordinary = {"Authorization": f"Bearer {bearer}"}

    opened = deployed.post(SESSION, json={"code": fresh_code(admin_env, secret)}, headers=ordinary)
    assert opened.status_code == 201, opened.text
    admin_token = admin_cookie(opened)
    both = {**ordinary, "Cookie": f"{ADMIN_COOKIE}={admin_token}"}
    assert deployed.get(SETTINGS, headers=both).status_code == 200

    # The ordinary token in the admin cookie's place: not an admin session.
    assert deployed.get(SETTINGS, headers={**ordinary, "Cookie": f"{ADMIN_COOKIE}={bearer}"}).status_code == 404
    # The admin token in an ordinary session's place, as a bearer token or as the web cookie: signs nobody in.
    assert deployed.get("/api/v1/me", headers={"Authorization": f"Bearer {admin_token}"}).status_code == 401
    assert deployed.get("/api/v1/me", headers={"Cookie": f"qd_session={admin_token}"}).status_code == 401
    assert deployed.get(SETTINGS, headers={"Authorization": f"Bearer {admin_token}"}).status_code == 401
    # The admin token alone, without the sign-in it belongs to: nothing.
    assert deployed.get(SETTINGS, headers={"Cookie": f"{ADMIN_COOKIE}={admin_token}"}).status_code == 401


def test_an_admin_session_is_bound_to_the_user_who_passed_the_factor(
    deployed: TestClient, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    first, first_bearer = _signed_in(deployed, owner, _tg())
    second, second_bearer = _signed_in(deployed, owner, _tg())
    secret = make_admin(owner, admin_env, first)
    make_admin(owner, admin_env, second)
    opened = deployed.post(
        SESSION, json={"code": fresh_code(admin_env, secret)}, headers={"Authorization": f"Bearer {first_bearer}"}
    )
    cookie = {"Cookie": f"{ADMIN_COOKIE}={admin_cookie(opened)}"}
    assert deployed.get(SETTINGS, headers={"Authorization": f"Bearer {first_bearer}", **cookie}).status_code == 200
    assert deployed.get(SETTINGS, headers={"Authorization": f"Bearer {second_bearer}", **cookie}).status_code == 404


def test_signing_out_of_the_ordinary_session_leaves_the_admin_session_useless(
    deployed: TestClient, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    user_id, bearer = _signed_in(deployed, owner, _tg())
    secret = make_admin(owner, admin_env, user_id)
    ordinary = {"Authorization": f"Bearer {bearer}"}
    opened = deployed.post(SESSION, json={"code": fresh_code(admin_env, secret)}, headers=ordinary)
    both = {**ordinary, "Cookie": f"{ADMIN_COOKIE}={admin_cookie(opened)}"}
    assert deployed.post("/api/v1/auth/sign-out", headers=ordinary).status_code == 204
    assert deployed.get(SETTINGS, headers=both).status_code == 401


def _again(deployed: TestClient, tg_id: int) -> dict[str, str]:
    """The same person signs in once more, with launch data signed a little earlier than the first."""
    signed = datetime.now(UTC) - timedelta(seconds=40 + uuid.uuid4().int % 600)
    init_data = webapp_init_data(token=TEST_BOT_TOKEN, auth_date=signed, tg_id=tg_id)
    response = deployed.post("/api/v1/auth/telegram-webapp", json={"init_data": init_data})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _admin_sessions(owner: psycopg.Connection, user_id: uuid.UUID) -> list[bool]:
    """Whether each administrator session of the person was ended, oldest first."""
    rows = owner.execute(
        "SELECT revoked_at IS NOT NULL FROM admin_session WHERE user_id = %s ORDER BY created_at, id", (user_id,)
    ).fetchall()
    return [bool(ended) for (ended,) in rows]


def test_signing_out_everywhere_ends_the_persons_admin_session_too(
    deployed: TestClient, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    """An admin session has hours of its own. Left open, it would work again as soon as the person signed
    in again in the same browser (security review, finding 9)."""
    tg_id = _tg()
    user_id, bearer = _signed_in(deployed, owner, tg_id)
    secret = make_admin(owner, admin_env, user_id)
    ordinary = {"Authorization": f"Bearer {bearer}"}
    opened = deployed.post(SESSION, json={"code": fresh_code(admin_env, secret)}, headers=ordinary)
    cookie = {"Cookie": f"{ADMIN_COOKIE}={admin_cookie(opened)}"}
    assert deployed.get(SETTINGS, headers={**ordinary, **cookie}).status_code == 200

    assert deployed.post("/api/v1/auth/sign-out-everywhere", headers=ordinary).status_code == 204
    assert _admin_sessions(owner, user_id) == [True]
    assert deployed.get(SETTINGS, headers={**ordinary, **cookie}).status_code == 401

    # Signed in again, with the admin cookie the browser still holds: an ordinary user and no administrator.
    back = _again(deployed, tg_id)
    assert deployed.get("/api/v1/me", headers=back).status_code == 200
    assert deployed.get(SETTINGS, headers={**back, **cookie}).status_code == 404
    assert deployed.get(AUTH, headers={**back, **cookie}).json()["elevated"] is False
    # It is in the audit log, told apart from a session the administrator closed in the panel.
    closed = owner.execute(
        "SELECT detail FROM admin_audit WHERE admin_id = %s AND action = 'admin.session_closed'", (user_id,)
    ).fetchall()
    assert closed == [({"by": "sign_out_everywhere"},)]
    # The second factor opens a new one as before.
    reopened = deployed.post(SESSION, json={"code": fresh_code(admin_env, secret)}, headers=back)
    assert reopened.status_code == 201
    assert _admin_sessions(owner, user_id) == [True, False]


def test_an_ordinary_sign_out_leaves_the_admin_session_and_signing_out_everywhere_ends_only_ones_own(
    deployed: TestClient, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    """What ends an admin session is signing out everywhere, by its own administrator: not a plain
    sign-out (the session is then unusable, as before, and the browser's next sign-in finds it), and not
    another person's request."""
    mine_tg, theirs_tg = _tg(), _tg()
    me, my_bearer = _signed_in(deployed, owner, mine_tg)
    them, their_bearer = _signed_in(deployed, owner, theirs_tg)
    my_secret, their_secret = make_admin(owner, admin_env, me), make_admin(owner, admin_env, them)
    mine, theirs = ({"Authorization": f"Bearer {token}"} for token in (my_bearer, their_bearer))
    my_cookie = {
        "Cookie": f"{ADMIN_COOKIE}="
        + admin_cookie(deployed.post(SESSION, json={"code": fresh_code(admin_env, my_secret)}, headers=mine))
    }
    their_cookie = {
        "Cookie": f"{ADMIN_COOKIE}="
        + admin_cookie(deployed.post(SESSION, json={"code": fresh_code(admin_env, their_secret)}, headers=theirs))
    }
    audit_before = owner.execute("SELECT count(*) FROM admin_audit WHERE admin_id = %s", (them,)).fetchone()

    assert deployed.post("/api/v1/auth/sign-out", headers=mine).status_code == 204
    assert _admin_sessions(owner, me) == [False]
    back = _again(deployed, mine_tg)
    assert deployed.get(SETTINGS, headers={**back, **my_cookie}).status_code == 200

    assert deployed.post("/api/v1/auth/sign-out-everywhere", headers=back).status_code == 204
    assert (_admin_sessions(owner, me), _admin_sessions(owner, them)) == ([True], [False])
    assert deployed.get(SETTINGS, headers={**theirs, **their_cookie}).status_code == 200
    assert owner.execute("SELECT count(*) FROM admin_audit WHERE admin_id = %s", (them,)).fetchone() == audit_before


def test_signing_out_everywhere_writes_nothing_about_someone_who_is_no_administrator(
    deployed: TestClient, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    """Anyone signed in may ask; for most there is no admin session to end and no audit row to write. A
    repeat by an administrator finds nothing left and writes no second row."""
    user_id, bearer = _signed_in(deployed, owner, _tg())
    assert (
        deployed.post("/api/v1/auth/sign-out-everywhere", headers={"Authorization": f"Bearer {bearer}"}).status_code
        == 204
    )
    assert owner.execute("SELECT count(*) FROM admin_audit WHERE admin_id = %s", (user_id,)).fetchone() == (0,)
    assert deployed.get("/api/v1/me", headers={"Authorization": f"Bearer {bearer}"}).status_code == 401

    tg_id = _tg()
    admin_id, admin_bearer = _signed_in(deployed, owner, tg_id)
    secret = make_admin(owner, admin_env, admin_id)
    ordinary = {"Authorization": f"Bearer {admin_bearer}"}
    assert deployed.post(SESSION, json={"code": fresh_code(admin_env, secret)}, headers=ordinary).status_code == 201
    assert deployed.post("/api/v1/auth/sign-out-everywhere", headers=ordinary).status_code == 204
    assert deployed.post("/api/v1/auth/sign-out-everywhere", headers=_again(deployed, tg_id)).status_code == 204
    closed = "SELECT count(*) FROM admin_audit WHERE admin_id = %s AND action = 'admin.session_closed'"
    assert owner.execute(closed, (admin_id,)).fetchone() == (1,)


def test_from_the_web_panel_an_admin_write_needs_the_csrf_token(
    deployed: TestClient, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    tg_id = _tg()
    signed = deployed.post(
        "/api/v1/auth/telegram-login",
        json=login_data(tg_id, token=TEST_BOT_TOKEN, auth_date=datetime.now(UTC) - timedelta(seconds=30)),
    )
    assert signed.status_code == 200, signed.text
    session = signed.headers["set-cookie"].split("qd_session=")[1].split(";")[0]
    csrf = {"X-CSRF-Token": signed.json()["csrf_token"]}
    row = owner.execute("SELECT id FROM app_user WHERE tg_id = %s", (tg_id,)).fetchone()
    assert row is not None
    secret = make_admin(owner, admin_env, row[0])

    def cookies(admin_token: str | None = None) -> dict[str, str]:
        extra = "" if admin_token is None else f"; {ADMIN_COOKIE}={admin_token}"
        return {"Cookie": f"qd_session={session}{extra}"}

    # Passing the factor is itself a write.
    code = fresh_code(admin_env, secret)
    assert deployed.post(SESSION, json={"code": code}, headers=cookies()).status_code == 401
    opened = deployed.post(SESSION, json={"code": code}, headers={**cookies(), **csrf})
    assert opened.status_code == 201, opened.text
    headers = cookies(admin_cookie(opened))

    assert deployed.get(SETTINGS, headers=headers).status_code == 200, "reading needs no CSRF token"
    change = {"changes": {"trial_days": 21}}
    refused = deployed.patch(SETTINGS, json=change, headers={**headers, **_key()})
    assert refused.status_code == 401
    assert deployed.get(SETTINGS, headers=headers).json()["settings"]["trial_days"] == 30
    done = deployed.patch(SETTINGS, json=change, headers={**headers, **csrf, **_key()})
    assert done.status_code == 200, done.text
    assert done.json()["settings"]["trial_days"] == 21


# --- nothing secret is written down ---------------------------------------------------------------------


def test_codes_secrets_and_tokens_reach_neither_the_logs_nor_the_audit(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    allow_list(owner, admin_env, world.stranger)
    uri = client.post(ENROL, headers={**as_user(world.stranger), **_key()}).json()["otpauth_uri"]
    secret = _secret_of(uri)
    wrong = _wrong(admin_env, secret)
    assert _open(client, world.stranger, wrong).status_code == 403
    code = fresh_code(admin_env, secret)
    opened = _open(client, world.stranger, code)
    token = admin_cookie(opened)
    headers = {**as_user(world.stranger), "Cookie": f"{ADMIN_COOKIE}={token}"}
    price_code = fresh_code(admin_env, secret)
    changed = client.patch(
        SETTINGS, json={"changes": {"price_uzs": 120_000}, "code": price_code}, headers={**headers, **_key()}
    )
    assert changed.status_code == 200, changed.text
    for _ in range(5):
        _open(client, world.stranger, wrong)
    assert client.get(f"{ADMIN_API}/shops", headers=as_user(world.owner_a)).status_code == 404

    written = caplog.text + " ".join(
        str(row)
        for row in owner.execute(
            "SELECT action, target_id, reason, detail::text FROM admin_audit WHERE admin_id = %s", (world.stranger,)
        ).fetchall()
    )
    written += " ".join(
        str(row)
        for row in owner.execute(
            "SELECT response::text FROM admin_request_key WHERE admin_id = %s", (world.stranger,)
        ).fetchall()
    )
    encoded = uri.split("secret=")[1].split("&")[0]
    for hidden in (encoded, secret.hex(), token, code, price_code, wrong):
        assert hidden not in written
    # What is logged: who was refused, by identifier only (specification, "Logs").
    assert f"admin_code_refused user={world.stranger}" in caplog.text
    assert f"admin_second_factor_locked user={world.stranger}" in caplog.text
    assert f"admin_route_refused user={world.owner_a} method=GET path=/api/admin/v1/shops" in caplog.text
