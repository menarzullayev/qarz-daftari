"""Tests written by the security review of story S19.2 (docs/10-operations/security-review.md).

Two kinds of test live here:

- a test marked `xfail(strict=True, reason="security review finding N")` demonstrates a confirmed defect.
  It states what must hold; today it fails. The fix makes it pass, and strict mode then fails the suite
  until the mark is removed, so a fix cannot go unnoticed;
- an unmarked test pins down something the review checked and found sound, where the existing suite did
  not already cover it.
"""

import asyncio
import itertools
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.auth import AuthService
from qarz.application.chat_texts import say
from qarz.application.reminders import ReminderService
from qarz.application.scheduler import Scheduler
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app
from tests.test_telegram_auth import login_data, webapp_init_data

from .conftest import TEST_BOT_TOKEN, WEBHOOK_SECRET, HeaderAuthenticator, SessionClient, World, as_user
from .test_authorization_suite import CALLS, _invoke, _key, _prepare, _snapshot
from .test_chat import Chat

pytestmark = pytest.mark.db

_update_ids = itertools.count(8_100_000_000)


# --- finding 1: the role check is not independent of row-level security -----------------------------------


def test_a_stranger_is_refused_even_when_the_database_role_bypasses_row_level_security(
    database_url: str, world: World
) -> None:
    """REQ-N12 promises isolation in the application "as well as" below it.

    The application is connected here as a role that bypasses row-level security (the migration owner),
    which is what a wrong QD_DATABASE_URL would do. The membership lookup has no shop condition of its
    own, so the owner of shop B passes as a member of shop A, and of a shop that does not exist.
    """
    database = Database(database_url)
    app = create_app(
        database.reachable, database, auth=AuthService(database, TEST_BOT_TOKEN), authenticator=HeaderAuthenticator()
    )
    with TestClient(app) as http:
        try:
            into_a = http.get(f"/api/v1/shops/{world.shop_a}/customers", headers=as_user(world.owner_b))
            into_nowhere = http.get(f"/api/v1/shops/{uuid.uuid4()}/customers", headers=as_user(world.owner_b))
        finally:
            http.portal.call(database.dispose)  # type: ignore[union-attr]
    assert (into_a.status_code, into_nowhere.status_code) == (404, 404), into_a.text[:200]


# --- finding 5: a suspended shop still accepts changes from its owner and managers ------------------------

# Operation and caller. The technical specification: "in suspended mode only owner viewing and export
# remain" (BR-30). Requesting and cancelling deletion are left out on purpose: whether a suspended shop's
# owner may still ask for its data to be erased is a decision for the founder, not a defect.
SUSPENDED_WRITES = [
    ("shop.update", "owner_a"),
    ("staff.invite", "owner_a"),
    ("staff.invitations.cancel", "owner_a"),
    ("staff.update", "owner_a"),
    ("staff.remove", "owner_a"),
    ("ownership.transfer.start", "owner_a"),
    ("ownership.transfer.cancel", "owner_a"),
    ("ownership.transfer.accept", "manager_a"),
    ("ownership.transfer.decline", "manager_a"),
]


@pytest.mark.parametrize(("op_name", "caller"), SUSPENDED_WRITES)
def test_a_suspended_shop_accepts_no_change(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, caller: str
) -> None:
    call = CALLS[op_name]
    _prepare(owner, world, call)
    owner.execute("UPDATE subscription SET state = 'suspended' WHERE shop_id = %s", (world.shop_a,))
    before = _snapshot(owner, world.shop_a)
    response = _invoke(client, world, call, world.shop_a, as_user(getattr(world, caller)))
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "SHOP_SUSPENDED"
    assert _snapshot(owner, world.shop_a) == before


def test_the_suspension_check_does_refuse_the_writes_it_covers(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The counterpart of the test above: the same assertion passes for a write that is checked."""
    owner.execute("UPDATE subscription SET state = 'suspended' WHERE shop_id = %s", (world.shop_a,))
    before = _snapshot(owner, world.shop_a)
    response = _invoke(client, world, CALLS["customers.create"], world.shop_a, as_user(world.owner_a))
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "SHOP_SUSPENDED"
    assert _snapshot(owner, world.shop_a) == before


# --- finding 6: a signature that is not ASCII crashes sign-in ----------------------------------------------

NON_ASCII_SIGNATURES = [
    ("/api/v1/auth/telegram-webapp", {"init_data": "user=%7B%7D&auth_date=1&hash=%C3%A9"}),
    ("/api/v1/auth/telegram-login", {"id": 1, "auth_date": 1, "hash": "é"}),
]


@pytest.mark.parametrize(("path", "body"), NON_ASCII_SIGNATURES, ids=["webapp", "login"])
def test_a_signature_with_a_non_ascii_character_is_refused_not_crashed_on(
    client: TestClient, path: str, body: dict[str, Any]
) -> None:
    """`hmac.compare_digest` raises TypeError for a non-ASCII string; nobody is signed in, but the answer
    is an unhandled 500 with a traceback in the log instead of the 401 every other bad signature gets."""
    quiet = TestClient(client.app, raise_server_exceptions=False)
    response = quiet.post(path, json=body)
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_an_ascii_signature_that_is_wrong_is_refused_with_401(client: TestClient) -> None:
    """The counterpart: the same request with an ASCII signature is refused the intended way."""
    for path, body in NON_ASCII_SIGNATURES:
        fixed = {
            key: value.replace("%C3%A9", "00").replace("é", "00") if isinstance(value, str) else value
            for key, value in body.items()
        }
        response = TestClient(client.app, raise_server_exceptions=False).post(path, json=fixed)
        assert response.status_code == 401, response.text


# --- finding 4: nothing limits the size of a request body ------------------------------------------------


def test_an_oversized_body_from_nobody_is_refused_before_it_is_read(client: TestClient, world: World) -> None:
    """Three megabytes of JSON, no credentials. The body is read and parsed in full before the caller is
    even asked who they are; the answer is 401, not 413. No proxy configuration exists in the repository
    to refuse it earlier."""
    response = client.post(f"/api/v1/shops/{world.shop_a}/customers", json={"display_name": "x" * (3 * 1024 * 1024)})
    assert response.status_code == 413, response.text[:200]


# --- finding 10: API request keys and chat request keys share one namespace -------------------------------


def _chat_entry(client: TestClient, owner: psycopg.Connection, world: World, update_id: int) -> int:
    """manager_a types "Vali 5000" to the bot. Returns how many such entries Vali's account then has."""
    row = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (world.manager_a,)).fetchone()
    assert row is not None
    tg_id = row[0]
    update = {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": {"id": tg_id, "is_bot": False, "first_name": "M"},
            "chat": {"id": tg_id, "type": "private"},
            "text": "Vali 5000",
        },
    }
    answered = client.post("/tg/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET})
    assert answered.status_code == 200, answered.text
    recorded = owner.execute(
        "SELECT count(*) FROM ledger_entry WHERE customer_id = %s AND amount = 5000", (world.settled_customer_a,)
    ).fetchone()
    assert recorded is not None
    return int(recorded[0])


def test_an_api_request_key_cannot_block_a_colleagues_chat_entry(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The chat stores its result under the key `tg-update-<update_id>`, which is also a valid API key.
    A staff member who uses that key on any API write first makes the colleague's chat entry fail."""
    update_id = next(_update_ids)
    taken = client.post(
        f"/api/v1/shops/{world.shop_a}/customers",
        json={"display_name": "Band qilingan kalit"},
        headers={**as_user(world.seller_a), "Idempotency-Key": f"tg-update-{update_id}"},
    )
    assert taken.status_code == 201, taken.text
    assert _chat_entry(client, owner, world, update_id) == 1, "the manager's chat entry must be recorded"


def test_the_same_chat_entry_is_recorded_when_nobody_took_its_key(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The counterpart: without the API request before it, the same message records the entry."""
    assert _chat_entry(client, owner, world, next(_update_ids)) == 1


# --- checked and found sound: identifiers of another shop carried in a request body ---------------------


def _shop_b_resources(owner: psycopg.Connection, world: World) -> dict[str, uuid.UUID]:
    """What owner_b needs of their own, so that only the identifier in the body is foreign."""
    customer, entry, learned, waiting, waiter = (uuid.uuid4() for _ in range(5))
    row = owner.execute(
        "SELECT id FROM membership WHERE shop_id = %s AND user_id = %s", (world.shop_b, world.owner_b)
    ).fetchone()
    assert row is not None
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Bek', 'bek')",
        (customer, world.shop_b),
    )
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
        "VALUES (%s, %s, %s, 1, 'credit', 50000, %s)",
        (entry, world.shop_b, customer, row[0]),
    )
    owner.execute(
        "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor) "
        "VALUES (%s, %s, %s, current_date + 7, 'default')",
        (uuid.uuid4(), world.shop_b, entry),
    )
    owner.execute("UPDATE shop SET reminders_on = true WHERE id = %s", (world.shop_b,))
    owner.execute(
        "INSERT INTO catalog_item (id, shop_id, name, name_norm, unit, price, learned) "
        "VALUES (%s, %s, 'Tuz', 'tuz', 'dona', 1000, true)",
        (learned, world.shop_b),
    )
    owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (waiter, uuid.uuid4().int % 10**15))
    owner.execute(
        "INSERT INTO customer_link (id, shop_id, user_id, status, consent_text_v, consent_at, waiting_name) "
        "VALUES (%s, %s, %s, 'waiting', 2, now(), 'Kutuvchi')",
        (waiting, world.shop_b, waiter),
    )
    return {"customer": customer, "entry": entry, "learned": learned, "waiting": waiting}


def _foreign_body_calls(world: World, mine: dict[str, uuid.UUID], foreign: dict[str, str]) -> dict[str, Any]:
    """Writes through shop B whose body names a member, customer or catalog item."""
    base = f"/api/v1/shops/{world.shop_b}"
    return {
        "ownership.transfer.start": (f"{base}/ownership-transfer", {"membership_id": foreign["member"]}),
        "reminders.send": (f"{base}/reminders/manual", {"customer_id": foreign["customer"]}),
        "waiting.attach": (f"{base}/waiting/{mine['waiting']}/attach", {"customer_id": foreign["customer"]}),
        "catalog.learned.merge": (f"{base}/catalog/{mine['learned']}/merge", {"into": foreign["item"]}),
        "ledger.entry.lines.add": (
            f"{base}/entries/{mine['entry']}/lines",
            {"lines": [{"catalog_item_id": foreign["item"], "qty": "1", "unit_price": 50000}]},
        ),
        "ledger.entry.create": (
            f"{base}/customers/{mine['customer']}/entries",
            {"kind": "credit", "lines": [{"catalog_item_id": foreign["item"], "qty": "1", "unit_price": 50000}]},
        ),
    }


def test_another_shops_identifier_in_a_body_is_answered_like_one_that_does_not_exist(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The authorization suite sends foreign identifiers in the path. These are the ones sent in a body.

    For each, the answer to shop A's real identifier must equal the answer to a random one (so nothing
    says the record exists), must not be a success, and must leave both shops as they were.
    """
    mine = _shop_b_resources(owner, world)
    real = {
        "member": str(world.manager_a_membership),
        "customer": str(world.settled_customer_a),
        "item": str(world.catalog_item_a),
    }
    random = {name: str(uuid.uuid4()) for name in real}
    before_a, before_b = _snapshot(owner, world.shop_a), _snapshot(owner, world.shop_b)

    with_real = _foreign_body_calls(world, mine, real)
    with_random = _foreign_body_calls(world, mine, random)
    for name, (path, body) in with_real.items():
        assert name in CALLS, name
        answered = client.post(path, json=body, headers={**as_user(world.owner_b), **_key()})
        missing = client.post(path, json=with_random[name][1], headers={**as_user(world.owner_b), **_key()})
        assert answered.status_code >= 400, (name, answered.text)
        assert (answered.status_code, answered.json()) == (missing.status_code, missing.json()), name

    assert _snapshot(owner, world.shop_a) == before_a, "shop A must be untouched"
    assert _snapshot(owner, world.shop_b) == before_b, "a refused call must change nothing in shop B either"


def test_the_same_bodies_succeed_with_the_shops_own_identifiers(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The counterpart of the test above: its refusals come from the foreign identifier, not from a
    request that could never succeed. Shop B's own customer and catalog item are accepted."""
    mine = _shop_b_resources(owner, world)
    item = uuid.uuid4()
    owner.execute(
        "INSERT INTO catalog_item (id, shop_id, name, name_norm, unit, price) VALUES (%s, %s, 'Un', 'un', 'kg', 50000)",
        (item, world.shop_b),
    )
    own = {"member": str(uuid.uuid4()), "customer": str(mine["customer"]), "item": str(item)}
    calls = _foreign_body_calls(world, mine, own)
    for name in ("waiting.attach", "catalog.learned.merge", "ledger.entry.lines.add"):
        path, body = calls[name]
        response = client.post(path, json=body, headers={**as_user(world.owner_b), **_key()})
        assert response.status_code in (200, 201), (name, response.text)


# --- checked and found sound: a removed member ------------------------------------------------------------


def test_a_removed_member_reaches_nothing_of_the_shop(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The suite covers a suspended member; a removed one keeps their row (REQ-034) and must fare the same."""
    assert client.get(f"/api/v1/shops/{world.shop_a}/customers", headers=as_user(world.seller_a)).status_code == 200
    owner.execute("UPDATE membership SET status = 'removed' WHERE id = %s", (world.seller_a_membership,))
    before = _snapshot(owner, world.shop_a)

    listed = client.get(f"/api/v1/shops/{world.shop_a}/customers", headers=as_user(world.seller_a))
    assert listed.status_code == 404, listed.text
    written = _invoke(client, world, CALLS["customers.create"], world.shop_a, as_user(world.seller_a))
    assert written.status_code == 404, written.text
    shops = client.get("/api/v1/me/shops", headers=as_user(world.seller_a))
    assert shops.status_code == 200
    assert shops.json()["items"] == []
    assert _snapshot(owner, world.shop_a) == before


# --- finding 9: signed sign-in data is accepted once ------------------------------------------------------

_WEBAPP = "/api/v1/auth/telegram-webapp"
_LOGIN = "/api/v1/auth/telegram-login"
_tg_ids = itertools.count(9_300_000_001)


def _signed_at() -> datetime:
    return datetime.now(UTC) - timedelta(seconds=30)


def test_mini_app_launch_data_signs_in_once_and_the_first_session_stays(session_client: SessionClient) -> None:
    """The widget may send the same data twice. The second is refused as a wrong signature is, and the
    person is not locked out: the session the first one made keeps working."""
    data = webapp_init_data(next(_tg_ids), token=TEST_BOT_TOKEN, auth_date=_signed_at())
    first = session_client.http.post(_WEBAPP, json={"init_data": data})
    assert first.status_code == 200, first.text
    bearer = {"Authorization": f"Bearer {first.json()['token']}"}

    again = session_client.http.post(_WEBAPP, json={"init_data": data})
    forged = session_client.http.post(_WEBAPP, json={"init_data": data[:-1] + ("0" if data[-1] != "0" else "1")})
    assert again.status_code == 401 and "token" not in again.text
    assert again.json()["error"] | {"request_id": ""} == forged.json()["error"] | {"request_id": ""}
    assert session_client.http.get("/api/v1/me", headers=bearer).status_code == 200


def test_the_same_launch_data_written_in_another_order_is_still_the_same_data(session_client: SessionClient) -> None:
    data = webapp_init_data(next(_tg_ids), token=TEST_BOT_TOKEN, auth_date=_signed_at())
    reordered = "&".join(reversed(data.split("&")))
    assert reordered != data
    assert session_client.http.post(_WEBAPP, json={"init_data": data}).status_code == 200
    assert session_client.http.post(_WEBAPP, json={"init_data": reordered}).status_code == 401


def test_web_login_data_signs_in_once_and_the_first_session_stays(session_client: SessionClient) -> None:
    data = login_data(next(_tg_ids), token=TEST_BOT_TOKEN, auth_date=_signed_at())
    first = session_client.http.post(_LOGIN, json=data)
    assert first.status_code == 200, first.text
    token = first.headers["set-cookie"].split("qd_session=")[1].split(";")[0]
    session_client.http.cookies.clear()

    again = session_client.http.post(_LOGIN, json=data)
    assert again.status_code == 401 and "set-cookie" not in again.headers
    assert session_client.http.get("/api/v1/me", headers={"Cookie": f"qd_session={token}"}).status_code == 200


def test_a_person_signs_in_again_with_data_signed_anew(session_client: SessionClient) -> None:
    """Only the very same signed data is refused; opening the Mini App again gives new data and a new session."""
    tg_id, at = next(_tg_ids), _signed_at()
    first = session_client.http.post(
        _WEBAPP, json={"init_data": webapp_init_data(tg_id, token=TEST_BOT_TOKEN, auth_date=at)}
    )
    later = webapp_init_data(tg_id, token=TEST_BOT_TOKEN, auth_date=at + timedelta(seconds=5))
    second = session_client.http.post(_WEBAPP, json={"init_data": later})
    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json()["token"] != second.json()["token"]


def test_a_refused_repeat_leaves_one_session_and_one_record(
    session_client: SessionClient, owner: psycopg.Connection
) -> None:
    tg_id = next(_tg_ids)
    data = webapp_init_data(tg_id, token=TEST_BOT_TOKEN, auth_date=_signed_at())
    before = owner.execute("SELECT count(*) FROM signin_replay").fetchone()
    for _ in range(3):
        session_client.http.post(_WEBAPP, json={"init_data": data})
    after = owner.execute("SELECT count(*) FROM signin_replay").fetchone()
    sessions = owner.execute(
        "SELECT count(*) FROM user_session s JOIN app_user u ON u.id = s.user_id WHERE u.tg_id = %s", (tg_id,)
    ).fetchone()
    assert before is not None and after is not None
    assert (after[0] - before[0], sessions) == (1, (1,))
    kept = owner.execute("SELECT min(expires_at) > now() + interval '30 minutes' FROM signin_replay").fetchone()
    assert kept == (True,), "a record is kept for as long as its data would still be accepted"


def test_the_worker_purges_dead_sessions_and_used_sign_in_data_once_an_hour(
    session_client: SessionClient, owner: psycopg.Connection, app_database_url: str, world: World
) -> None:
    tg_id = next(_tg_ids)
    token = session_client.http.post(
        _WEBAPP, json={"init_data": webapp_init_data(tg_id, token=TEST_BOT_TOKEN, auth_date=_signed_at())}
    ).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    gone, dead = uuid.uuid4().bytes * 2, uuid.uuid4().bytes * 2
    owner.execute(
        "INSERT INTO user_session (id, token_hash, user_id, kind, created_at, expires_at) "
        "VALUES (gen_random_uuid(), %s, %s, 'webapp', now() - interval '1 day', now() - interval '1 hour')",
        (gone, world.stranger),
    )
    owner.execute(
        "INSERT INTO signin_replay (payload_hash, expires_at) VALUES (%s, now() - interval '1 hour')", (dead,)
    )
    owner.execute("DELETE FROM job_run WHERE job = 'sign_in_cleanup'")
    moment = datetime(2084, 4, 4, 21, 30, tzinfo=UTC)  # night in Tashkent: no other job has work

    def tick() -> None:
        async def run() -> None:
            database = Database(app_database_url)
            try:
                clock = lambda: moment  # noqa: E731
                await Scheduler(database, ReminderService(database, clock), clock, sign_in_cleanup=True).tick()
            finally:
                await database.dispose()

        asyncio.run(run())

    def left() -> tuple[int, int]:
        sessions = owner.execute("SELECT count(*) FROM user_session WHERE token_hash = %s", (gone,)).fetchone()
        replays = owner.execute("SELECT count(*) FROM signin_replay WHERE payload_hash = %s", (dead,)).fetchone()
        assert sessions is not None and replays is not None
        return int(sessions[0]), int(replays[0])

    tick()
    assert left() == (0, 0)
    assert session_client.http.get("/api/v1/me", headers=bearer).status_code == 200, "a live session is kept"
    assert owner.execute("SELECT period FROM job_run WHERE job = 'sign_in_cleanup'").fetchall() == [("2084-04-05T02",)]

    # Signed out, then the same hour again: the job has run for this period, so the row waits for the next.
    assert session_client.http.post("/api/v1/auth/sign-out", headers=bearer).status_code in (200, 204)
    tick()
    revoked = "SELECT count(*) FROM user_session s JOIN app_user u ON u.id = s.user_id WHERE u.tg_id = %s"
    assert owner.execute(revoked, (tg_id,)).fetchone() == (1,)
    moment += timedelta(hours=1)
    tick()
    assert owner.execute(revoked, (tg_id,)).fetchone() == (0,)
    owner.execute("DELETE FROM job_run WHERE job = 'sign_in_cleanup'")


# --- finding 12: API answers are not to be cached, with or without the proxy -------------------------------


def _caching(response: Any) -> tuple[list[str], list[str]]:
    return response.headers.get_list("cache-control"), response.headers.get_list("x-content-type-options")


def test_every_api_answer_forbids_caching_and_type_guessing(client: TestClient, world: World) -> None:
    answers = [
        client.get("/api/v1/me", headers=as_user(world.owner_a)),
        client.get(f"/api/v1/shops/{world.shop_a}/customers", headers=as_user(world.owner_a)),
        client.get("/api/v1/me"),  # 401
        client.get(f"/api/v1/shops/{world.shop_a}", headers=as_user(world.stranger)),  # 404
        client.get("/api/v1/nothing-here"),
        client.post("/api/v1/shops", content=b"{", headers=as_user(world.owner_a)),  # 422
    ]
    assert [answer.status_code for answer in answers] == [200, 200, 401, 404, 404, 422]
    for answer in answers:
        assert _caching(answer) == (["no-store"], ["nosniff"]), answer.request.url


def test_an_unhandled_failure_is_not_to_be_cached_either(app_database_url: str, world: World) -> None:
    database = Database(app_database_url)

    class Broken(HeaderAuthenticator):
        async def user_id(self, request: Any) -> uuid.UUID | None:
            raise RuntimeError("broken")

    app = create_app(database.reachable, database, auth=AuthService(database, TEST_BOT_TOKEN), authenticator=Broken())
    with TestClient(app, raise_server_exceptions=False) as broken:
        response = broken.get("/api/v1/me", headers=as_user(world.owner_a))
        broken.portal.call(database.dispose)  # type: ignore[union-attr]
    assert response.status_code == 500
    assert _caching(response) == (["no-store"], ["nosniff"])


def test_a_route_that_names_its_own_caching_rule_keeps_it_once(client: TestClient, world: World) -> None:
    """The import template says `no-store` itself: the header is not sent twice. And what is not the API
    (the health check a load balancer reads) is left alone."""
    template = client.get(f"/api/v1/shops/{world.shop_a}/imports/template", headers=as_user(world.owner_a))
    assert template.status_code == 200, template.text
    assert _caching(template) == (["no-store"], ["nosniff"])
    assert _caching(client.get("/healthz")) == ([], [])


# --- finding 13: one trial per person; no limit on shops (DEC-065) -------------------------------------------


def _open_shop(client: TestClient, user: uuid.UUID, name: str = "Do'kon") -> Any:
    return client.post("/api/v1/shops", json={"name": name, "lang": "uz"}, headers={**as_user(user), **_key()})


def _person(owner: psycopg.Connection, lang: str = "uz") -> uuid.UUID:
    person = uuid.uuid4()
    owner.execute(
        "INSERT INTO app_user (id, tg_id, lang) VALUES (%s, %s, %s)", (person, uuid.uuid4().int % 10**15, lang)
    )
    return person


def _subscriptions(owner: psycopg.Connection, person: uuid.UUID) -> list[str]:
    rows = owner.execute(
        "SELECT sub.state FROM membership m JOIN shop s ON s.id = m.shop_id "
        "JOIN subscription sub ON sub.shop_id = s.id WHERE m.user_id = %s AND m.role = 'owner' "
        "ORDER BY s.created_at, s.id",
        (person,),
    ).fetchall()
    return [state for (state,) in rows]


def test_the_trial_is_for_a_persons_first_shop_and_later_shops_start_limited(
    client: TestClient, owner: psycopg.Connection
) -> None:
    person = _person(owner)
    first, second = _open_shop(client, person, "Birinchi"), _open_shop(client, person, "Ikkinchi")
    assert (first.status_code, second.status_code) == (201, 201), second.text
    assert (first.json()["subscription_state"], second.json()["subscription_state"]) == ("trial", "limited")
    assert sorted(_subscriptions(owner, person)) == ["limited", "trial"]
    trial_ends = owner.execute(
        "SELECT trial_ends FROM subscription WHERE shop_id = %s", (second.json()["id"],)
    ).fetchone()
    assert trial_ends == (None,)
    # A shop that starts limited takes no credit sale until it is paid for (BR-29).
    shop = second.json()["id"]
    customer = client.post(
        f"/api/v1/shops/{shop}/customers", json={"display_name": "Ali"}, headers={**as_user(person), **_key()}
    )
    assert customer.status_code == 201, customer.text
    sale = client.post(
        f"/api/v1/shops/{shop}/customers/{customer.json()['id']}/entries",
        json={"kind": "credit", "amount": 1000},
        headers={**as_user(person), **_key()},
    )
    assert sale.status_code == 402 and sale.json()["error"]["code"] == "SUBSCRIPTION_LIMITED", sale.text


@pytest.mark.parametrize("lang", ["uz", "ru"])
def test_a_person_may_open_more_than_five_shops_and_only_the_first_gets_a_trial(
    client: TestClient, owner: psycopg.Connection, lang: str
) -> None:
    """The founder's decision of 2026-10-08 (DEC-065): there is no limit on the number of shops. Until
    then the sixth was refused with SHOP_LIMIT_REACHED, a code that no longer exists."""
    person = _person(owner, lang)
    opened = [_open_shop(client, person, f"Do'kon {number}") for number in range(8)]
    assert [answer.status_code for answer in opened] == [201] * 8, [answer.text for answer in opened]
    assert [answer.json()["subscription_state"] for answer in opened] == ["trial"] + ["limited"] * 7
    assert sorted(_subscriptions(owner, person)) == ["limited"] * 7 + ["trial"]


def test_a_repeated_request_opens_one_shop_and_an_erased_first_shop_does_not_bring_the_trial_back(
    client: TestClient, owner: psycopg.Connection
) -> None:
    person = _person(owner)
    key = _key()
    for _ in range(3):
        again = client.post("/api/v1/shops", json={"name": "Bir", "lang": "uz"}, headers={**as_user(person), **key})
        assert again.status_code == 201
    assert _subscriptions(owner, person) == ["trial"]

    one = owner.execute(
        "SELECT shop_id FROM membership WHERE user_id = %s AND role = 'owner' LIMIT 1", (person,)
    ).fetchone()
    assert one is not None
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() - interval '1 second' WHERE id = %s", one
    )
    assert owner.execute("SELECT erase_shop(%s)", one).fetchone() == (True,)
    # One trial for a person, not one for each first shop: the next one starts limited.
    later = _open_shop(client, person)
    assert (later.status_code, later.json()["subscription_state"]) == (201, "limited")


def test_many_shops_opened_at_once_are_all_opened(client: TestClient, owner: psycopg.Connection) -> None:
    """With the limit, six requests at once for the last place gave one shop and five refusals."""
    person = _person(owner)
    for number in range(4):
        assert _open_shop(client, person, f"Do'kon {number}").status_code == 201
    results: list[int] = []

    def call() -> None:
        results.append(_open_shop(client, person, "Poyga").status_code)

    threads = [threading.Thread(target=call) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [201] * 6
    assert sorted(_subscriptions(owner, person)) == ["limited"] * 9 + ["trial"]


def test_two_first_shops_at_once_get_one_trial_between_them(client: TestClient, owner: psycopg.Connection) -> None:
    person = _person(owner)
    results: list[int] = []

    def call() -> None:
        results.append(_open_shop(client, person, "Poyga").status_code)

    threads = [threading.Thread(target=call) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [201] * 4
    assert sorted(_subscriptions(owner, person)) == ["limited", "limited", "limited", "trial"]


def test_the_chat_says_in_words_that_a_later_shop_has_no_trial_and_opens_a_sixth_shop_too(
    client: TestClient, owner: psycopg.Connection
) -> None:
    person = Chat(client, owner, next(_tg_ids), language="ru")
    new_shop = person.say("/start").button("🏪")

    def open_shop(name: str) -> str:
        assert person.press(new_shop).text == say("ru", "ask_shop_name")
        return person.say(name).text

    assert open_shop("Первый") == say("ru", "shop_created", shop="Первый")
    # No limit on the number of shops (DEC-065): the sixth and the seventh open like the second.
    for name in ("Второй", "Третий", "Четвёртый", "Пятый", "Шестой", "Седьмой"):
        assert open_shop(name) == say("ru", "shop_created_limited", shop=name)
    names = owner.execute(
        "SELECT count(*) FROM shop s JOIN membership m ON m.shop_id = s.id JOIN app_user u ON u.id = m.user_id "
        "WHERE u.tg_id = %s",
        (person.tg_id,),
    ).fetchone()
    assert names == (7,)
    # There is no refusal left to word.
    for catalog in ("uz", "ru"):
        with pytest.raises(KeyError):
            say(catalog, "shop_limit_reached")
