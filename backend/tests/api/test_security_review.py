"""Tests written by the security review of story S19.2 (docs/10-operations/security-review.md).

Two kinds of test live here:

- a test marked `xfail(strict=True, reason="security review finding N")` demonstrates a confirmed defect.
  It states what must hold; today it fails. The fix makes it pass, and strict mode then fails the suite
  until the mark is removed, so a fix cannot go unnoticed;
- an unmarked test pins down something the review checked and found sound, where the existing suite did
  not already cover it.
"""

import itertools
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.auth import AuthService
from qarz.infrastructure.db import Database
from qarz.interface.http import create_app

from .conftest import TEST_BOT_TOKEN, WEBHOOK_SECRET, HeaderAuthenticator, World, as_user
from .test_authorization_suite import CALLS, _invoke, _key, _prepare, _snapshot

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
