"""Sign-in and sessions through the application as deployed (ADR-017, story S2.1)."""

import itertools
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from tests.test_telegram_auth import login_data, webapp_init_data

from .conftest import TEST_BOT_TOKEN, WEBHOOK_SECRET, SessionClient

pytestmark = pytest.mark.db

WEBAPP = "/api/v1/auth/telegram-webapp"
LOGIN = "/api/v1/auth/telegram-login"
ME = "/api/v1/me"


_launches = itertools.count(1)


def _fresh(**kwargs: Any) -> str:
    # Each launch of the Mini App is signed anew, with a query identifier of its own. The same signed data
    # is accepted only once (tests/api/test_security_review.py, finding 9), so two sign-ins of one person
    # in one second must not be the same data here either.
    kwargs.setdefault("extra", {"query_id": f"AAE{next(_launches)}"})
    return webapp_init_data(token=TEST_BOT_TOKEN, auth_date=datetime.now(UTC) - timedelta(seconds=30), **kwargs)


def _fresh_login(tg_id: int) -> dict[str, object]:
    # Likewise: the widget signs the moment of each login, and two logins never share one.
    signed_at = datetime.now(UTC) - timedelta(seconds=30 + next(_launches))
    return login_data(tg_id, token=TEST_BOT_TOKEN, auth_date=signed_at)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"qd_session={token}"}


def _web_session(app: SessionClient, tg_id: int) -> tuple[str, str]:
    response = app.http.post(LOGIN, json=_fresh_login(tg_id))
    assert response.status_code == 200, response.text
    token = response.cookies.get("qd_session") or response.headers["set-cookie"].split("qd_session=")[1].split(";")[0]
    return token, response.json()["csrf_token"]


# --- Mini App ---------------------------------------------------------------------------------------


def test_mini_app_sign_in_creates_the_user_once_and_the_token_works(
    session_client: SessionClient, owner: psycopg.Connection
) -> None:
    first = session_client.http.post(WEBAPP, json={"init_data": _fresh(tg_id=5_000_001, language="ru")})
    assert first.status_code == 200, first.text
    token = first.json()["token"]

    me = session_client.http.get(ME, headers=_bearer(token))
    assert me.status_code == 200
    assert me.json()["lang"] == "ru"

    second = session_client.http.post(WEBAPP, json={"init_data": _fresh(tg_id=5_000_001, language="uz")})
    assert second.json()["token"] != token
    rows = owner.execute("SELECT id, lang FROM app_user WHERE tg_id = 5000001").fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "ru", "a later sign-in must not overwrite the language the user already has"
    assert str(rows[0][0]) == me.json()["id"]


def test_only_hashes_of_tokens_are_stored(session_client: SessionClient, owner: psycopg.Connection) -> None:
    token = session_client.http.post(WEBAPP, json={"init_data": _fresh(tg_id=5_000_002)}).json()["token"]
    stored = owner.execute(
        "SELECT s.token_hash FROM user_session s JOIN app_user u ON u.id = s.user_id WHERE u.tg_id = 5000002"
    ).fetchall()
    assert len(stored) == 1
    assert token.encode() not in bytes(stored[0][0])
    assert len(bytes(stored[0][0])) == 32
    dump = owner.execute("SELECT string_agg(encode(token_hash, 'escape'), '') FROM user_session").fetchone()
    assert dump is not None
    assert token not in (dump[0] or "")


@pytest.mark.parametrize(
    "init_data",
    [
        webapp_init_data(token="1234567890:some-other-bot"),
        webapp_init_data(token=TEST_BOT_TOKEN, auth_date=datetime.now(UTC) - timedelta(hours=2)),
        webapp_init_data(token=TEST_BOT_TOKEN, auth_date=datetime.now(UTC) - timedelta(seconds=30)) + "&x=1",
    ],
    ids=["another bot", "too old", "tampered"],
)
def test_bad_mini_app_data_signs_nobody_in(
    session_client: SessionClient, owner: psycopg.Connection, init_data: str
) -> None:
    before = owner.execute("SELECT count(*) FROM user_session").fetchone()
    response = session_client.http.post(WEBAPP, json={"init_data": init_data})
    assert response.status_code == 401
    assert "token" not in response.text
    assert owner.execute("SELECT count(*) FROM user_session").fetchone() == before


def test_a_mini_app_session_expires_after_twelve_hours(session_client: SessionClient) -> None:
    token = session_client.http.post(WEBAPP, json={"init_data": _fresh(tg_id=5_000_003)}).json()["token"]
    session_client.clock.offset = timedelta(hours=11, minutes=59)
    assert session_client.http.get(ME, headers=_bearer(token)).status_code == 200
    session_client.clock.offset = timedelta(hours=12, minutes=1)
    assert session_client.http.get(ME, headers=_bearer(token)).status_code == 401


def test_sign_out_revokes_the_session(session_client: SessionClient) -> None:
    token = session_client.http.post(WEBAPP, json={"init_data": _fresh(tg_id=5_000_004)}).json()["token"]
    other = session_client.http.post(WEBAPP, json={"init_data": _fresh(tg_id=5_000_004)}).json()["token"]
    assert session_client.http.post("/api/v1/auth/sign-out", headers=_bearer(token)).status_code == 204
    assert session_client.http.get(ME, headers=_bearer(token)).status_code == 401
    assert session_client.http.get(ME, headers=_bearer(other)).status_code == 200, "only this session ends"


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer " + "A" * 43},
        {"Authorization": "Bearer short"},
        {"Authorization": "Bearer " + "x" * 500},
    ],
)
def test_invalid_bearer_tokens_are_refused(session_client: SessionClient, headers: dict[str, str]) -> None:
    assert session_client.http.get(ME, headers=headers).status_code == 401


# --- web panel ---------------------------------------------------------------------------------------


def test_web_sign_in_sets_a_protected_cookie(session_client: SessionClient) -> None:
    response = session_client.http.post(LOGIN, json=_fresh_login(5_100_001))
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"]
    lowered = cookie.lower()
    assert "qd_session=" in cookie
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=lax" in lowered
    assert "path=/api" in lowered
    body = response.json()
    assert set(body) == {"csrf_token", "expires_at"}, "the session token itself must never be in the body"
    assert body["csrf_token"] not in cookie


def test_cookie_sessions_need_the_csrf_token_to_change_anything(session_client: SessionClient) -> None:
    token, csrf = _web_session(session_client, 5_100_002)
    http = session_client.http

    assert http.get(ME, headers=_cookie(token)).status_code == 200, "reading needs no CSRF token"
    assert http.patch(ME, json={"lang": "ru"}, headers=_cookie(token)).status_code == 401
    wrong = {**_cookie(token), "X-CSRF-Token": "not-the-token"}
    assert http.patch(ME, json={"lang": "ru"}, headers=wrong).status_code == 401
    assert http.get(ME, headers=_cookie(token)).json()["lang"] == "uz", "the refused changes changed nothing"

    right = {**_cookie(token), "X-CSRF-Token": csrf}
    assert http.patch(ME, json={"lang": "ru"}, headers=right).status_code == 200
    assert http.get(ME, headers=_cookie(token)).json()["lang"] == "ru"


def test_session_kinds_are_not_interchangeable(session_client: SessionClient) -> None:
    """A cookie session's token used as a bearer token would bypass the CSRF check; it must be refused."""
    web_token, _ = _web_session(session_client, 5_100_003)
    app_token = session_client.http.post(WEBAPP, json={"init_data": _fresh(tg_id=5_100_003)}).json()["token"]
    http = session_client.http
    assert http.get(ME, headers=_bearer(web_token)).status_code == 401
    assert http.patch(ME, json={"lang": "ru"}, headers=_bearer(web_token)).status_code == 401
    assert http.get(ME, headers=_cookie(app_token)).status_code == 401
    # each works in its own place
    assert http.get(ME, headers=_cookie(web_token)).status_code == 200
    assert http.get(ME, headers=_bearer(app_token)).status_code == 200


def test_a_web_session_lasts_fourteen_days(session_client: SessionClient) -> None:
    token, _ = _web_session(session_client, 5_100_004)
    session_client.clock.offset = timedelta(days=13, hours=23)
    assert session_client.http.get(ME, headers=_cookie(token)).status_code == 200
    session_client.clock.offset = timedelta(days=14, minutes=1)
    assert session_client.http.get(ME, headers=_cookie(token)).status_code == 401


def test_web_sign_out_needs_the_csrf_token_and_clears_the_cookie(session_client: SessionClient) -> None:
    token, csrf = _web_session(session_client, 5_100_005)
    http = session_client.http
    assert http.post("/api/v1/auth/sign-out", headers=_cookie(token)).status_code == 401
    done = http.post("/api/v1/auth/sign-out", headers={**_cookie(token), "X-CSRF-Token": csrf})
    assert done.status_code == 204
    assert "qd_session=" in done.headers["set-cookie"]
    assert http.get(ME, headers=_cookie(token)).status_code == 401


def test_bad_login_data_sets_no_cookie(session_client: SessionClient) -> None:
    forged = {**_fresh_login(5_100_006), "id": 1}
    response = session_client.http.post(LOGIN, json=forged)
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


# --- one identity across clients ----------------------------------------------------------------------


def test_chat_mini_app_and_web_are_the_same_user(session_client: SessionClient, owner: psycopg.Connection) -> None:
    """M1 exit condition: a user signs in through chat, Mini App, and web."""
    tg_id = 5_200_001
    http = session_client.http

    # chat: writing to the bot creates the user, in the language Telegram reports
    update = {
        "update_id": 9_500_000_001,
        "message": {
            "message_id": 1,
            "chat": {"id": tg_id, "type": "private"},
            "from": {"id": tg_id, "language_code": "ru"},
            "text": "/start",
        },
    }
    assert (
        http.post("/tg/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET}).status_code
        == 200
    )
    created = owner.execute("SELECT id, lang FROM app_user WHERE tg_id = %s", (tg_id,)).fetchall()
    assert len(created) == 1
    assert created[0][1] == "ru"

    app_token = http.post(WEBAPP, json={"init_data": _fresh(tg_id=tg_id, language="uz")}).json()["token"]
    web_token, _ = _web_session(session_client, tg_id)
    from_app = http.get(ME, headers=_bearer(app_token)).json()
    from_web = http.get(ME, headers=_cookie(web_token)).json()
    assert from_app == from_web == {"id": str(created[0][0]), "lang": "ru"}
    assert owner.execute("SELECT count(*) FROM app_user WHERE tg_id = %s", (tg_id,)).fetchone() == (1,)


def test_a_signed_in_user_with_no_shop_reaches_no_shop(
    session_client: SessionClient, owner: psycopg.Connection
) -> None:
    token = session_client.http.post(WEBAPP, json={"init_data": _fresh(tg_id=5_200_002)}).json()["token"]
    shop = owner.execute(
        "INSERT INTO shop (id, name) VALUES (gen_random_uuid(), 'Somebody else') RETURNING id"
    ).fetchone()
    assert shop is not None
    assert session_client.http.get(f"/api/v1/shops/{shop[0]}", headers=_bearer(token)).status_code == 404


def test_no_name_or_username_from_telegram_is_stored(session_client: SessionClient, owner: psycopg.Connection) -> None:
    """Data minimization (REQ-N05): the sign-in data carries a first name; the user table has no place for it."""
    session_client.http.post(WEBAPP, json={"init_data": _fresh(tg_id=5_200_003)})
    columns = {
        row[0]
        for row in owner.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name IN ('app_user', 'user_session')"
        ).fetchall()
    }
    assert not {c for c in columns if "name" in c or "photo" in c or "phone" in c}
