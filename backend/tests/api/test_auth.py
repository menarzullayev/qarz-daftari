"""Sign-in and sessions through the application as deployed (ADR-017, story S2.1)."""

import asyncio
import itertools
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.auth import SIGNED_DATA_MAX_AGE, WEB_LOGIN_MAX_AGE, WEBAPP_SESSION, AuthService
from qarz.infrastructure.db import Database
from qarz.infrastructure.settings import Settings
from qarz.interface.http import create_app
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
    kwargs.setdefault("auth_date", datetime.now(UTC) - timedelta(seconds=30))
    return webapp_init_data(token=TEST_BOT_TOKEN, **kwargs)


def _fresh_login(tg_id: int) -> dict[str, object]:
    # Likewise: the widget signs the moment of each login, and two logins never share one. The signed
    # moment is a whole second and the only thing that tells two logins of one person apart, so each
    # login is dated five seconds before the one before it. (One second was not enough: the clock had
    # moved on by the time of the second login, and once in some dozens of runs both fell into the same
    # second, where the second login is rightly refused as a repeat.) The cycle keeps the data young
    # enough to be accepted however many logins the module has made: web login data is accepted for
    # five minutes, and the oldest made here is under four.
    signed_at = datetime.now(UTC) - timedelta(seconds=30 + 5 * (next(_launches) % 40))
    return login_data(tg_id, token=TEST_BOT_TOKEN, auth_date=signed_at)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"qd_session={token}"}


def _web_session(app: SessionClient, tg_id: int) -> tuple[str, str]:
    return _web_session_with(app, _fresh_login(tg_id))


def _web_session_with(app: SessionClient, data: dict[str, object]) -> tuple[str, str]:
    response = app.http.post(LOGIN, json=data)
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


# --- sign out everywhere (security review, finding 9) -------------------------------------------------

EVERYWHERE = "/api/v1/auth/sign-out-everywhere"


def _app_token(app: SessionClient, tg_id: int) -> str:
    return str(app.http.post(WEBAPP, json={"init_data": _fresh(tg_id=tg_id)}).json()["token"])


def _sessions(owner: psycopg.Connection, tg_id: int) -> list[tuple[str, bool]]:
    """The person's sessions, oldest first: the kind, and whether it was ended."""
    rows = owner.execute(
        "SELECT s.kind, s.revoked_at IS NOT NULL FROM user_session s JOIN app_user u ON u.id = s.user_id "
        "WHERE u.tg_id = %s ORDER BY s.created_at, s.id",
        (tg_id,),
    ).fetchall()
    return [(str(kind), bool(ended)) for kind, ended in rows]


def test_signing_out_everywhere_ends_every_session_of_the_person_on_every_device(
    session_client: SessionClient, owner: psycopg.Connection
) -> None:
    """Mini App sessions and web sessions alike, the one that asked included; the next request of each is
    refused exactly as a signed-out one is."""
    http, me = session_client.http, 5_300_001
    phone, tablet = _app_token(session_client, me), _app_token(session_client, me)
    laptop, laptop_csrf = _web_session(session_client, me)
    office, office_csrf = _web_session(session_client, me)
    # Signing in at the office ended the web session on the laptop (a person has one web session); the
    # Mini App sessions and the newer web session are the ones still open.
    assert http.get(ME, headers=_cookie(laptop)).status_code == 401
    for headers in (_bearer(phone), _bearer(tablet), _cookie(office)):
        assert http.get(ME, headers=headers).status_code == 200

    done = http.post(EVERYWHERE, headers=_bearer(phone))
    assert (done.status_code, done.content) == (204, b"")

    for headers in (_bearer(phone), _bearer(tablet), _cookie(laptop), _cookie(office)):
        refused = http.get(ME, headers=headers)
        assert refused.status_code == 401
        assert refused.json()["error"]["code"] == "UNAUTHENTICATED"
    # Not only reads: a web session's own CSRF token no longer changes anything either.
    for token, csrf in ((laptop, laptop_csrf), (office, office_csrf)):
        changed = http.patch(ME, json={"lang": "ru"}, headers={**_cookie(token), "X-CSRF-Token": csrf})
        assert changed.status_code == 401
    assert _sessions(owner, me) == [("webapp", True), ("webapp", True), ("web", True), ("web", True)]
    assert owner.execute("SELECT lang FROM app_user WHERE tg_id = %s", (me,)).fetchone() == ("uz",)


def test_signing_out_everywhere_from_the_web_needs_the_csrf_token_and_clears_the_cookie(
    session_client: SessionClient, owner: psycopg.Connection
) -> None:
    http, me = session_client.http, 5_300_002
    phone = _app_token(session_client, me)
    laptop, csrf = _web_session(session_client, me)

    # A cookie alone is refused, as for every request that changes something, and nothing is ended.
    assert http.post(EVERYWHERE, headers=_cookie(laptop)).status_code == 401
    assert http.post(EVERYWHERE, headers={**_cookie(laptop), "X-CSRF-Token": "not-the-token"}).status_code == 401
    assert _sessions(owner, me) == [("webapp", False), ("web", False)]

    done = http.post(EVERYWHERE, headers={**_cookie(laptop), "X-CSRF-Token": csrf})
    assert done.status_code == 204
    cleared = done.headers["set-cookie"].lower()
    assert cleared.startswith(('qd_session="";', "qd_session=;"))
    assert "path=/api" in cleared and "max-age=0" in cleared
    assert http.get(ME, headers=_cookie(laptop)).status_code == 401
    assert http.get(ME, headers=_bearer(phone)).status_code == 401, "the Mini App session on the phone ended too"
    assert _sessions(owner, me) == [("webapp", True), ("web", True)]


def test_one_person_cannot_end_another_persons_sessions(
    session_client: SessionClient, owner: psycopg.Connection
) -> None:
    """Whose sessions end is decided by the session that asks and by nothing in the request."""
    http, me, other = session_client.http, 5_300_003, 5_300_004
    mine = _app_token(session_client, me)
    theirs = _app_token(session_client, other)
    their_web, their_csrf = _web_session(session_client, other)
    row = owner.execute("SELECT id FROM app_user WHERE tg_id = %s", (other,)).fetchone()
    assert row is not None
    their_id = str(row[0])

    # Naming the other person every way a request can: in the body, in the query, in headers.
    done = http.post(
        f"{EVERYWHERE}?user_id={their_id}&user={their_id}",
        json={"user_id": their_id, "user": their_id, "tg_id": other},
        headers={**_bearer(mine), "X-User-Id": their_id, "X-Test-User": their_id},
    )
    assert done.status_code == 204
    assert _sessions(owner, me) == [("webapp", True)]
    assert _sessions(owner, other) == [("webapp", False), ("web", False)], "the other person's sessions are untouched"
    assert http.get(ME, headers=_bearer(theirs)).status_code == 200
    kept = http.patch(ME, json={"lang": "ru"}, headers={**_cookie(their_web), "X-CSRF-Token": their_csrf})
    assert kept.status_code == 200
    assert http.get(ME, headers=_bearer(mine)).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer " + "A" * 43},
        {"Authorization": "Basic abc"},
        {"Cookie": "qd_session=" + "B" * 43},
        {"Cookie": "qd_session=" + "B" * 43, "X-CSRF-Token": "C" * 43},
    ],
    ids=["nothing", "empty bearer", "unknown bearer", "another scheme", "unknown cookie", "unknown cookie and token"],
)
def test_without_a_valid_session_nobody_is_signed_out(
    session_client: SessionClient, owner: psycopg.Connection, headers: dict[str, str]
) -> None:
    token = _app_token(session_client, 5_300_005)
    before = owner.execute("SELECT count(*) FROM user_session WHERE revoked_at IS NOT NULL").fetchone()
    refused = session_client.http.post(EVERYWHERE, headers=headers)
    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "UNAUTHENTICATED"
    assert "set-cookie" not in refused.headers
    assert owner.execute("SELECT count(*) FROM user_session WHERE revoked_at IS NOT NULL").fetchone() == before
    assert session_client.http.get(ME, headers=_bearer(token)).status_code == 200


def test_a_session_that_has_ended_cannot_end_the_persons_other_sessions(
    session_client: SessionClient, owner: psycopg.Connection
) -> None:
    """Neither a session that was signed out, nor one that expired, nor a web session's token sent as a
    bearer token: whoever holds only that holds nothing."""
    http, me = session_client.http, 5_300_006
    signed_out, expiring = _app_token(session_client, me), _app_token(session_client, me)
    web, _ = _web_session(session_client, me)
    assert http.post("/api/v1/auth/sign-out", headers=_bearer(signed_out)).status_code == 204
    assert http.post(EVERYWHERE, headers=_bearer(signed_out)).status_code == 401
    assert http.post(EVERYWHERE, headers=_bearer(web)).status_code == 401, "a cookie session's token is no bearer"

    session_client.clock.offset = timedelta(hours=12, minutes=1)  # Mini App sessions are over; the web one is not
    assert http.post(EVERYWHERE, headers=_bearer(expiring)).status_code == 401
    assert _sessions(owner, me) == [("webapp", True), ("webapp", False), ("web", False)]
    assert http.get(ME, headers=_cookie(web)).status_code == 200


def test_after_signing_out_everywhere_the_person_signs_in_again_and_the_old_sessions_stay_ended(
    session_client: SessionClient, owner: psycopg.Connection
) -> None:
    http, me = session_client.http, 5_300_007
    old_phone, (old_web, old_csrf) = _app_token(session_client, me), _web_session(session_client, me)
    assert http.post(EVERYWHERE, headers=_bearer(old_phone)).status_code == 204
    # Asking again with the session that has just ended is refused: there is nothing left to end with it.
    assert http.post(EVERYWHERE, headers=_bearer(old_phone)).status_code == 401
    assert http.post(EVERYWHERE, headers={**_cookie(old_web), "X-CSRF-Token": old_csrf}).status_code == 401

    new_phone = _app_token(session_client, me)
    assert http.get(ME, headers=_bearer(new_phone)).status_code == 200
    assert http.get(ME, headers=_bearer(old_phone)).status_code == 401
    assert http.get(ME, headers=_cookie(old_web)).status_code == 401
    assert _sessions(owner, me) == [("webapp", True), ("web", True), ("webapp", False)]
    assert owner.execute("SELECT count(*) FROM app_user WHERE tg_id = %s", (me,)).fetchone() == (1,)


def test_signing_out_everywhere_leaves_the_time_of_an_earlier_sign_out_as_it_was(
    session_client: SessionClient, owner: psycopg.Connection
) -> None:
    """Only sessions that are still open are ended: the record of an earlier sign-out keeps its time."""
    http, me = session_client.http, 5_300_008
    earlier, current = _app_token(session_client, me), _app_token(session_client, me)
    assert http.post("/api/v1/auth/sign-out", headers=_bearer(earlier)).status_code == 204
    when = "SELECT s.revoked_at FROM user_session s JOIN app_user u ON u.id = s.user_id WHERE u.tg_id = %s ORDER BY 1"
    first = owner.execute(when, (me,)).fetchall()[0]
    session_client.clock.offset = timedelta(minutes=5)
    assert http.post(EVERYWHERE, headers=_bearer(current)).status_code == 204
    ended = owner.execute(when, (me,)).fetchall()
    assert ended[0] == first and ended[1][0] - first[0] >= timedelta(minutes=4)


def test_the_service_says_how_many_sessions_it_ended(session_client: SessionClient, app_database_url: str) -> None:
    me = 5_300_009
    _app_token(session_client, me)
    _web_session(session_client, me)
    last = _app_token(session_client, me)
    user_id = uuid.UUID(session_client.http.get(ME, headers=_bearer(last)).json()["id"])

    async def three_times() -> tuple[int, int, int]:
        database = Database(app_database_url)
        try:
            service = AuthService(database, TEST_BOT_TOKEN)
            return (
                await service.sign_out_everywhere(uuid.uuid4()),  # somebody with no session: nothing to end
                await service.sign_out_everywhere(user_id),
                await service.sign_out_everywhere(user_id),  # a repeat finds nothing left
            )
        finally:
            await database.dispose()

    assert asyncio.run(three_times()) == (0, 3, 0)


# --- one web session a person, and web login data accepted for minutes (security review, finding 9) ----


def _open(owner: psycopg.Connection, tg_id: int) -> list[str]:
    """The kinds of the person's sessions that are still open, oldest first."""
    return [kind for kind, ended in _sessions(owner, tg_id) if not ended]


def test_a_new_web_sign_in_ends_the_persons_earlier_web_session(
    session_client: SessionClient, owner: psycopg.Connection
) -> None:
    """In this browser after a reload, or in another: the older web session answers as a signed-out one
    does, to reads and to writes with its own CSRF token. The person's Mini App session is left alone."""
    http, me = session_client.http, 5_400_001
    phone = _app_token(session_client, me)
    laptop, laptop_csrf = _web_session(session_client, me)
    assert http.get(ME, headers=_cookie(laptop)).status_code == 200

    office, office_csrf = _web_session(session_client, me)
    http.cookies.clear()

    refused = http.get(ME, headers=_cookie(laptop))
    assert refused.status_code == 401 and refused.json()["error"]["code"] == "UNAUTHENTICATED"
    changed = http.patch(ME, json={"lang": "ru"}, headers={**_cookie(laptop), "X-CSRF-Token": laptop_csrf})
    assert changed.status_code == 401
    assert http.get(ME, headers=_cookie(office)).status_code == 200
    kept = http.patch(ME, json={"lang": "ru"}, headers={**_cookie(office), "X-CSRF-Token": office_csrf})
    assert kept.status_code == 200
    assert http.get(ME, headers=_bearer(phone)).status_code == 200, "a Mini App session and a web session coexist"
    assert _sessions(owner, me) == [("webapp", False), ("web", True), ("web", False)]


def test_a_web_sign_in_ends_nobody_elses_web_session(session_client: SessionClient, owner: psycopg.Connection) -> None:
    http, me, other = session_client.http, 5_400_002, 5_400_003
    theirs, their_csrf = _web_session(session_client, other)
    _web_session(session_client, me)
    _web_session(session_client, me)
    http.cookies.clear()
    assert _open(owner, me) == ["web"]
    assert _sessions(owner, other) == [("web", False)]
    kept = http.patch(ME, json={"lang": "ru"}, headers={**_cookie(theirs), "X-CSRF-Token": their_csrf})
    assert kept.status_code == 200


def test_a_mini_app_sign_in_ends_no_session_of_either_kind(
    session_client: SessionClient, owner: psycopg.Connection
) -> None:
    """Only the web has one session a person: the Mini App is opened on a phone and a tablet alike."""
    http, me = session_client.http, 5_400_004
    web, _ = _web_session(session_client, me)
    http.cookies.clear()
    phone, tablet = _app_token(session_client, me), _app_token(session_client, me)
    for headers in (_cookie(web), _bearer(phone), _bearer(tablet)):
        assert http.get(ME, headers=headers).status_code == 200
    assert _sessions(owner, me) == [("web", False), ("webapp", False), ("webapp", False)]


def test_a_refused_web_sign_in_ends_nothing(session_client: SessionClient, owner: psycopg.Connection) -> None:
    """Data that is forged, too old, or used before signs nobody in and therefore ends nobody's session."""
    http, me = session_client.http, 5_400_005
    used = _fresh_login(me)
    web, _ = _web_session_with(session_client, used)
    http.cookies.clear()

    forged = {**_fresh_login(me), "hash": "0" * 64}
    old = login_data(me, token=TEST_BOT_TOKEN, auth_date=datetime.now(UTC) - WEB_LOGIN_MAX_AGE - timedelta(seconds=20))
    for data in (forged, old, used):
        assert http.post(LOGIN, json=data).status_code == 401
    assert _sessions(owner, me) == [("web", False)]
    assert http.get(ME, headers=_cookie(web)).status_code == 200


def test_two_web_sign_ins_of_one_person_at_once_leave_one_web_session(
    session_client: SessionClient, app_database_url: str, owner: psycopg.Connection
) -> None:
    """Neither may miss the session the other is making: they go one after the other in the database."""
    me, at = 5_400_006, datetime.now(UTC) - timedelta(seconds=30)
    _app_token(session_client, me)  # the person exists, as they do at any sign-in but the first
    logins = [login_data(me, token=TEST_BOT_TOKEN, auth_date=at - timedelta(seconds=n)) for n in range(8)]

    async def together() -> None:
        database = Database(app_database_url)
        try:
            service = AuthService(database, TEST_BOT_TOKEN)
            await asyncio.gather(*(service.sign_in_web(dict(data)) for data in logins))
        finally:
            await database.dispose()

    asyncio.run(together())
    assert len(_sessions(owner, me)) == 9, "every sign-in was accepted"
    assert _open(owner, me) == ["webapp", "web"]


def test_web_login_data_is_accepted_for_five_minutes_and_mini_app_data_for_an_hour(
    session_client: SessionClient,
) -> None:
    """The age is measured from the moment Telegram signed, by the server's clock."""
    http, me = session_client.http, 5_400_007
    now = datetime.now(UTC)
    assert timedelta(0) < WEB_LOGIN_MAX_AGE < SIGNED_DATA_MAX_AGE, "the web login is given less than the Mini App"

    young = login_data(me, token=TEST_BOT_TOKEN, auth_date=now - WEB_LOGIN_MAX_AGE + timedelta(seconds=30))
    stale = login_data(me, token=TEST_BOT_TOKEN, auth_date=now - WEB_LOGIN_MAX_AGE - timedelta(seconds=30))
    refused = http.post(LOGIN, json=stale)
    assert refused.status_code == 401 and "set-cookie" not in refused.headers
    assert refused.json()["error"]["code"] == "UNAUTHENTICATED"
    assert http.post(LOGIN, json=young).status_code == 200
    http.cookies.clear()

    # The same age, and far more, is still accepted from the Mini App.
    as_old = _fresh(tg_id=me, auth_date=now - WEB_LOGIN_MAX_AGE - timedelta(seconds=30))
    older = _fresh(tg_id=me, auth_date=now - SIGNED_DATA_MAX_AGE + timedelta(minutes=2))
    too_old = _fresh(tg_id=me, auth_date=now - SIGNED_DATA_MAX_AGE - timedelta(minutes=2))
    assert http.post(WEBAPP, json={"init_data": as_old}).status_code == 200
    assert http.post(WEBAPP, json={"init_data": older}).status_code == 200
    assert http.post(WEBAPP, json={"init_data": too_old}).status_code == 401


def test_used_web_login_data_is_remembered_for_as_long_as_it_would_be_accepted(
    session_client: SessionClient, owner: psycopg.Connection
) -> None:
    """Not for the Mini App's hour: the record of a web login may go once the data is too old anyway."""
    me, signed_at = 5_400_008, datetime.now(UTC) - timedelta(seconds=40)
    known = {bytes(row[0]) for row in owner.execute("SELECT payload_hash FROM signin_replay").fetchall()}
    data = login_data(me, token=TEST_BOT_TOKEN, auth_date=signed_at)
    assert session_client.http.post(LOGIN, json=data).status_code == 200
    session_client.http.cookies.clear()
    rows = owner.execute("SELECT payload_hash, expires_at FROM signin_replay").fetchall()
    kept = [expires for digest, expires in rows if bytes(digest) not in known]
    assert len(kept) == 1
    until = signed_at.replace(microsecond=0) + WEB_LOGIN_MAX_AGE
    assert until < kept[0] <= until + timedelta(minutes=10)
    # Within that time a repeat is refused.
    session_client.clock.offset = WEB_LOGIN_MAX_AGE - timedelta(seconds=60)
    assert session_client.http.post(LOGIN, json=data).status_code == 401


def test_the_age_accepted_for_web_login_data_is_a_setting_that_cannot_exceed_the_hour(
    app_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert Settings().web_login_max_age_seconds == WEB_LOGIN_MAX_AGE.total_seconds() == 300
    monkeypatch.setenv("QD_WEB_LOGIN_MAX_AGE_SECONDS", "90")
    assert Settings().web_login_max_age_seconds == 90
    for bad in ("0", "-5", "3601", "soon"):
        monkeypatch.setenv("QD_WEB_LOGIN_MAX_AGE_SECONDS", bad)
        with pytest.raises(ValueError):
            Settings()

    database = Database(app_database_url)
    for age in (timedelta(0), timedelta(seconds=-1), SIGNED_DATA_MAX_AGE + timedelta(seconds=1)):
        with pytest.raises(ValueError):
            AuthService(database, TEST_BOT_TOKEN, web_login_max_age=age)
    AuthService(database, TEST_BOT_TOKEN, web_login_max_age=SIGNED_DATA_MAX_AGE)

    # The configured age is the one applied: with 90 seconds, two minutes is too old and one is not.
    auth = AuthService(database, TEST_BOT_TOKEN, web_login_max_age=timedelta(seconds=90))
    now, me = datetime.now(UTC), 5_400_009
    with TestClient(create_app(database.reachable, database, auth=auth)) as http:
        two_minutes = login_data(me, token=TEST_BOT_TOKEN, auth_date=now - timedelta(seconds=120))
        one_minute = login_data(me, token=TEST_BOT_TOKEN, auth_date=now - timedelta(seconds=60))
        assert http.post(LOGIN, json=two_minutes).status_code == 401
        assert http.post(LOGIN, json=one_minute).status_code == 200
        http.portal.call(database.dispose)  # type: ignore[union-attr]


def test_a_mini_app_session_still_lasts_twelve_hours_beside_a_web_session(session_client: SessionClient) -> None:
    """Session lifetimes are as they were: what was shortened is the age of the login data, not these."""
    http, me = session_client.http, 5_400_010
    phone = _app_token(session_client, me)
    web, _ = _web_session(session_client, me)
    http.cookies.clear()
    session_client.clock.offset = WEBAPP_SESSION + timedelta(minutes=1)
    assert http.get(ME, headers=_bearer(phone)).status_code == 401
    assert http.get(ME, headers=_cookie(web)).status_code == 200
