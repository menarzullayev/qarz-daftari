"""A Telegram administrator of the review group decides a subscription receipt from the group's buttons
(REQ-055; the founder's decision of 2026-10-08, DEC-064, which changes DEC-051).

Until that decision only a platform administrator's press counted in the group. Now a press also counts
from anyone Telegram names, at that moment, as the creator or an administrator of the configured review
group, whether or not they are on the platform allow-list. Telegram is never called here: the fake in
`conftest` answers in its place.

What must stay true: an ordinary member's press changes nothing; no answer from Telegram is a refusal;
the decision is recorded with the presser's Telegram identifier and with no administrator; and such a
person gets nothing else, neither an administrator account nor the panel.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat import CALLBACK_NOTICE_MAX
from qarz.application.chat_texts import CATALOGS, day, money, say
from qarz.domain.subscription import add_months

from .conftest import ADMIN_API, AdminEnv, FakeChatMembers, World, as_user, elevate, make_admin
from .test_chat import SECRET, Chat, _update_ids
from .test_customers_ledger import key
from .test_disputes import tg
from .test_subscription_receipts import (
    RECEIPTS,
    audit,
    decided_notice,
    rows,
    sent_ok,
    subscription,
    today,
    unique_image,
)
from .test_subscription_receipts_admin_chat import REASON, press_of, review_group, state

pytestmark = pytest.mark.db

ANNOUNCEMENT = 7  # the message in the group that carries the buttons


def new_tg_id() -> int:
    """Somebody the service has never seen."""
    return 7_100_000_000 + uuid.uuid4().int % 10**9


def press(client: TestClient, owner: psycopg.Connection, tg_id: int, group: int, data: str) -> tuple[Any, list[Any]]:
    """A press under the group's announcement: Telegram's answer to the press, and what the bot then sends."""
    update_id = next(_update_ids)
    response = client.post(
        "/tg/webhook",
        json={
            "update_id": update_id,
            "callback_query": {
                "id": f"cb-{uuid.uuid4().hex}",
                "from": {"id": tg_id, "language_code": "uz"},
                "message": {"message_id": ANNOUNCEMENT, "chat": {"id": group, "type": "supergroup"}},
                "data": data,
            },
        },
        headers=SECRET,
    )
    assert response.status_code == 200, response.text
    sent = owner.execute(
        "SELECT recipient, payload FROM outbox_message WHERE dedupe_key LIKE %s ORDER BY dedupe_key",
        (f"update:{update_id}:%",),
    ).fetchall()
    return response.json(), [(str(recipient), payload) for recipient, payload in sent]


def deciders(owner: psycopg.Connection, receipt: str) -> Any:
    """Who the receipt says decided it: the administrator and the Telegram identifier."""
    return owner.execute(
        "SELECT status, decided_by, decided_by_tg FROM subscription_receipt WHERE id = %s", (receipt,)
    ).fetchone()


def actors(owner: psycopg.Connection, receipt: str) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT action, admin_id, actor_tg FROM admin_audit WHERE target_id = %s ORDER BY at, id", (receipt,)
    ).fetchall()


def users(owner: psycopg.Connection) -> Any:
    return owner.execute("SELECT count(*) FROM app_user").fetchone()


def closed(group: int, text: str) -> tuple[str, dict[str, Any]]:
    """The group's announcement replaced by an outcome, without buttons."""
    return (
        str(group),
        {
            "method": "editMessageText",
            "message_id": ANNOUNCEMENT,
            "text": text,
            "reply_markup": {"inline_keyboard": []},
        },
    )


@pytest.fixture
def group(owner: psycopg.Connection, world: World, admin_env: AdminEnv) -> int:
    return review_group(owner, world)


@pytest.fixture
def receipt(client: TestClient, world: World, group: int) -> str:
    return sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])


# --- who may decide ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["creator", "administrator"])
def test_a_telegram_administrator_of_the_review_group_approves_there(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    telegram_members: FakeChatMembers,
    group: int,
    receipt: str,
    status: str,
) -> None:
    """Somebody who is on no allow-list and has never used the service."""
    presser = new_tg_id()
    telegram_members.statuses[(group, presser)] = status
    trial_ends = subscription(owner, world.shop_a)[1]

    answer, sent = press(client, owner, presser, group, press_of(receipt, "sra"))

    until = add_months(subscription(owner, world.shop_a)[1], 3)
    # The group sees the outcome on the announcement, which loses its buttons. The presser is not
    # written to: the bot cannot write to someone who never started it.
    assert sent == [closed(group, say("uz", "a_receipt_approved", shop="Shop A", months=3, date=day(until)))]
    assert set(answer) == {"method", "callback_query_id"}
    assert telegram_members.asked == [(group, presser)], "asked once, about the presser, in the review group"

    assert subscription(owner, world.shop_a) == ("active", trial_ends, until, None)
    assert rows(owner, world.shop_a) == [(300_000, 3, "approved", 3, None, None, True)]
    assert deciders(owner, receipt) == ("approved", None, presser)
    ((action, who, target_type, shop, reason, detail),) = audit(owner, receipt)
    assert (action, who, target_type, shop, reason) == (
        "subscription.receipt_approved",
        None,
        "receipt",
        world.shop_a,
        None,
    )
    assert actors(owner, receipt) == [("subscription.receipt_approved", None, presser)]
    assert detail == {
        "via": "group",
        "group": group,
        "months": 3,
        "stated_amount": 300_000,
        "stated_months": 3,
        "before": {"state": "trial", "paid_through": None},
        "after": {"state": "active", "paid_through": until.isoformat()},
    }
    assert owner.execute(
        "SELECT actor_kind, actor_id FROM activity WHERE shop_id = %s AND action = 'subscription.receipt_approved'",
        (world.shop_a,),
    ).fetchall() == [("admin", None)]
    assert decided_notice(owner, receipt) == [
        (tg(owner, world.owner_a), say("uz", "sub_receipt_approved", shop="Shop A", months=3, date=day(until)))
    ]


def test_a_telegram_administrator_rejects_there_and_writes_the_reason_to_the_bot(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    telegram_members: FakeChatMembers,
    group: int,
    receipt: str,
) -> None:
    presser = new_tg_id()
    telegram_members.statuses[(group, presser)] = "administrator"
    was = subscription(owner, world.shop_a)

    answer, sent = press(client, owner, presser, group, press_of(receipt, "srj"))
    # The reason is asked for in the person's own chat with the bot, and the press itself says so, for
    # someone who has not started the bot yet. The group sees nothing until there is a decision.
    assert [(recipient, payload["text"]) for recipient, payload in sent] == [
        (str(presser), say("uz", "ask_receipt_reject_reason"))
    ]
    assert (answer["text"], answer["show_alert"]) == (say("uz", "g_reason_in_private"), True)
    assert deciders(owner, receipt) == ("submitted", None, None), "nothing is decided until the reason is written"

    chat = Chat(client, owner, presser)
    assert chat.say("xx").text == say("uz", "a_reason_invalid")
    assert deciders(owner, receipt) == ("submitted", None, None)

    done = chat.say(f"  {REASON}  ")
    assert done.text == say("uz", "a_receipt_rejected", shop="Shop A", reason=REASON)
    assert deciders(owner, receipt) == ("rejected", None, presser)
    assert rows(owner, world.shop_a) == [(300_000, 3, "rejected", None, REASON, None, True)]
    assert subscription(owner, world.shop_a) == was
    assert actors(owner, receipt) == [("subscription.receipt_rejected", None, presser)]
    assert [(row[0], row[4], row[5]) for row in audit(owner, receipt)] == [
        (
            "subscription.receipt_rejected",
            REASON,
            {"stated_amount": 300_000, "stated_months": 3, "via": "group", "group": group},
        )
    ]
    in_group = owner.execute(
        "SELECT recipient, payload FROM outbox_message WHERE recipient = %s AND payload->>'method' = 'editMessageText'",
        (str(group),),
    ).fetchall()
    assert [(str(recipient), payload) for recipient, payload in in_group] == [
        closed(group, say("uz", "a_receipt_rejected", shop="Shop A", reason=REASON))
    ]
    assert decided_notice(owner, receipt) == [
        (
            tg(owner, world.owner_a),
            say("uz", "sub_receipt_rejected", shop="Shop A", amount=money("uz", 300_000), reason=REASON),
        )
    ]
    # Telegram was asked at the press and again for each message that could have been the reason.
    assert telegram_members.asked == [(group, presser)] * 3
    # The question is answered: the next message is not another reason.
    assert chat.say(REASON).text != say("uz", "a_receipt_rejected", shop="Shop A", reason=REASON)


@pytest.mark.parametrize("status", ["member", "restricted", "left", "kicked", "owner", "Administrator", "", None])
def test_a_press_by_anyone_who_does_not_administer_the_group_changes_nothing(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    telegram_members: FakeChatMembers,
    group: int,
    receipt: str,
    status: str | None,
) -> None:
    """An ordinary member, someone restricted or gone, a status this service does not know, and no status
    at all. Nothing is decided, nothing is said, and nobody becomes a user of the service by pressing."""
    before, known = state(owner, world, receipt), users(owner)
    for presser in (new_tg_id(), int(tg(owner, world.owner_b))):
        telegram_members.statuses[(group, presser)] = status
        for action in ("sra", "srj"):
            answer, sent = press(client, owner, presser, group, press_of(receipt, action))
            assert sent == [], (status, action)
            assert set(answer) == {"method", "callback_query_id"}, "the spinner stops; not a word is said"
    assert state(owner, world, receipt) == before
    assert deciders(owner, receipt) == ("submitted", None, None)
    assert users(owner) == known


def test_when_telegram_cannot_be_asked_nobody_is_an_administrator_of_the_group(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    telegram_members: FakeChatMembers,
    group: int,
    receipt: str,
) -> None:
    """Fail closed: the person does administer the group, but Telegram gives no answer."""
    presser = new_tg_id()
    telegram_members.statuses[(group, presser)] = "creator"
    telegram_members.unreachable = True
    before, known = state(owner, world, receipt), users(owner)
    for action in ("sra", "srj"):
        assert press(client, owner, presser, group, press_of(receipt, action))[1] == []
    assert telegram_members.asked == [(group, presser)] * 2
    assert state(owner, world, receipt) == before and users(owner) == known

    # The same person, once Telegram answers again.
    telegram_members.unreachable = False
    assert press(client, owner, presser, group, press_of(receipt, "sra"))[1] != []
    assert deciders(owner, receipt) == ("approved", None, presser)


def test_the_right_is_asked_again_for_the_reason_and_is_gone_when_telegram_no_longer_gives_it(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    telegram_members: FakeChatMembers,
    group: int,
    receipt: str,
) -> None:
    """Between pressing "reject" and writing the reason the person stops administering the group, or
    Telegram stops answering: the reason decides nothing."""
    for lost in ("demoted", "unreachable"):
        presser = new_tg_id()
        telegram_members.statuses[(group, presser)] = "administrator"
        telegram_members.unreachable = False
        assert press(client, owner, presser, group, press_of(receipt, "srj"))[1] != []
        before = state(owner, world, receipt)
        if lost == "demoted":
            telegram_members.statuses[(group, presser)] = "member"
        else:
            telegram_members.unreachable = True
        chat = Chat(client, owner, presser)
        assert chat.say(REASON).text == say("uz", "expired"), lost
        assert deciders(owner, receipt) == ("submitted", None, None)
        # The question was closed with it; only what was waiting is different.
        assert state(owner, world, receipt)[:5] == before[:5]
        telegram_members.unreachable = False
        telegram_members.statuses[(group, presser)] = "administrator"
        assert chat.say(REASON).text != say("uz", "a_receipt_rejected", shop="Shop A", reason=REASON)
        assert deciders(owner, receipt) == ("submitted", None, None)


def test_only_the_configured_review_group_counts_and_telegram_is_asked_about_no_other_chat(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    telegram_members: FakeChatMembers,
    group: int,
    receipt: str,
) -> None:
    presser = new_tg_id()
    elsewhere = group - 1
    telegram_members.statuses[(elsewhere, presser)] = "creator"  # administers another group, not this one
    before, known = state(owner, world, receipt), users(owner)

    assert press(client, owner, presser, elsewhere, press_of(receipt, "sra"))[1] == []
    assert telegram_members.asked == [], "a press in a group that is not the review group asks Telegram nothing"

    assert press(client, owner, presser, group, press_of(receipt, "sra"))[1] == []
    assert telegram_members.asked == [(group, presser)]

    # Without a review group configured there is no group to administer.
    telegram_members.statuses[(group, presser)] = "creator"
    owner.execute("DELETE FROM platform_setting WHERE key = 'review_group'")
    assert press(client, owner, presser, group, press_of(receipt, "sra"))[1] == []
    assert telegram_members.asked == [(group, presser)]
    assert state(owner, world, receipt) == before and users(owner) == known


def test_the_buttons_data_gives_no_right_and_other_buttons_are_not_served_in_the_group(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    telegram_members: FakeChatMembers,
    group: int,
    receipt: str,
) -> None:
    """Nothing in the callback data is believed: it names a receipt and nothing else."""
    member, administrator = new_tg_id(), new_tg_id()
    telegram_members.statuses[(group, administrator)] = "administrator"
    before = state(owner, world, receipt)
    forged = (
        f"{press_of(receipt, 'sra')}:administrator",
        f"{press_of(receipt, 'sra')}:{administrator}",
        f"v2:sra:{uuid.UUID(receipt).hex}:{group}:creator",
    )
    for data in forged:
        assert press(client, owner, member, group, data)[1] == []
        # From a real administrator of the group the same data is no button either: it has too many parts.
        assert press(client, owner, administrator, group, data)[1] == []
    for data in ("v2:lang:ru", "v2:srn", "v2:newshop", "v1:sra:" + uuid.UUID(receipt).hex):
        assert press(client, owner, administrator, group, data)[1] == []
    assert state(owner, world, receipt) == before


# --- nothing else comes with it ------------------------------------------------------------------------------


def test_a_group_administrator_gets_no_administrator_account_no_session_and_no_panel(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    telegram_members: FakeChatMembers,
    group: int,
    receipt: str,
) -> None:
    presser = new_tg_id()
    telegram_members.statuses[(group, presser)] = "creator"
    accounts = owner.execute("SELECT count(*) FROM admin_account").fetchone()
    sessions = owner.execute("SELECT count(*) FROM admin_session").fetchone()
    assert press(client, owner, presser, group, press_of(receipt, "sra"))[1] != []
    assert deciders(owner, receipt) == ("approved", None, presser)

    person = owner.execute("SELECT id FROM app_user WHERE tg_id = %s", (presser,)).fetchone()
    assert person is not None, "known to the service from now on, as anyone who writes to the bot is"
    assert owner.execute("SELECT count(*) FROM admin_account").fetchone() == accounts
    assert owner.execute("SELECT count(*) FROM admin_session").fetchone() == sessions
    assert owner.execute("SELECT count(*) FROM membership WHERE user_id = %s", (person[0],)).fetchone() == (0,)

    # The administrator's API answers them exactly as it answers a stranger.
    for path in (RECEIPTS, f"{RECEIPTS}/{receipt}", f"{ADMIN_API}/shops", f"{ADMIN_API}/audit"):
        theirs, strangers = (
            client.get(path, headers=as_user(who)) for who in (uuid.UUID(str(person[0])), world.stranger)
        )
        assert theirs.status_code == strangers.status_code and theirs.status_code in (401, 403, 404), path
        assert theirs.json() == strangers.json()
    for action in ("approve", "reject"):
        other = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1, unique_image()[0])
        refused = client.post(
            f"{RECEIPTS}/{other}/{action}",
            json={"reason": REASON},
            headers={**as_user(uuid.UUID(str(person[0]))), **key()},
        )
        assert refused.status_code in (401, 403, 404)
        assert deciders(owner, other) == ("submitted", None, None)


def test_the_right_is_for_the_group_only_and_not_for_a_private_chat(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    telegram_members: FakeChatMembers,
    group: int,
    receipt: str,
) -> None:
    """The same button pressed in the person's private chat with the bot is no button at all."""
    presser = new_tg_id()
    telegram_members.statuses[(group, presser)] = "administrator"
    chat = Chat(client, owner, presser)
    chat.say("/start")
    before = state(owner, world, receipt)
    for action in ("sra", "srj"):
        said = chat.press(press_of(receipt, action))
        assert [set(payload) for payload in said.payloads] == [{"method", "message_id", "reply_markup"}]
    # Nor is a reason taken from a question that names no group, should one have been left open.
    owner.execute(
        "INSERT INTO chat_pending (id, user_id, kind, payload, expires_at) "
        "SELECT gen_random_uuid(), id, 'receipt_reject', %s::jsonb, now() + interval '10 minutes' "
        "FROM app_user WHERE tg_id = %s",
        (f'{{"receipt": "{uuid.UUID(receipt).hex}"}}', presser),
    )
    assert chat.say(REASON).text == say("uz", "expired")
    # Nor from one that names a group other than the configured review group.
    owner.execute(
        "INSERT INTO chat_pending (id, user_id, kind, payload, expires_at) "
        "SELECT gen_random_uuid(), id, 'receipt_reject', %s::jsonb, now() + interval '10 minutes' "
        "FROM app_user WHERE tg_id = %s",
        (f'{{"receipt": "{uuid.UUID(receipt).hex}", "group": [{group - 1}, 7]}}', presser),
    )
    telegram_members.statuses[(group - 1, presser)] = "creator"
    assert chat.say(REASON).text == say("uz", "expired")
    assert state(owner, world, receipt) == before
    assert deciders(owner, receipt) == ("submitted", None, None)


# --- beside the platform administrators ----------------------------------------------------------------------


def test_a_platform_administrator_is_still_recorded_as_one_and_one_without_a_session_decides_as_a_group_administrator(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    telegram_members: FakeChatMembers,
    group: int,
) -> None:
    signed_in, signed_out = world.admin, world.stranger
    elevate(client, admin_env, signed_in, make_admin(owner, admin_env, signed_in))
    make_admin(owner, admin_env, signed_out)  # on the allow-list, confirmed, but holding no session
    for user in (signed_in, signed_out):
        telegram_members.statuses[(group, int(tg(owner, user)))] = "administrator"

    first = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    assert press(client, owner, int(tg(owner, signed_in)), group, press_of(first, "sra"))[1] != []
    assert deciders(owner, first) == ("approved", signed_in, None)
    assert actors(owner, first) == [("subscription.receipt_approved", signed_in, None)]

    second = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1, unique_image()[0])
    assert press(client, owner, int(tg(owner, signed_out)), group, press_of(second, "sra"))[1] != []
    assert deciders(owner, second) == ("approved", None, int(tg(owner, signed_out)))
    assert actors(owner, second) == [("subscription.receipt_approved", None, int(tg(owner, signed_out)))]

    # Without the group's administration such an administrator is still sent to the panel, as before.
    telegram_members.statuses[(group, int(tg(owner, signed_out)))] = "member"
    third = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1, unique_image()[0])
    _, sent = press(client, owner, int(tg(owner, signed_out)), group, press_of(third, "sra"))
    assert [(recipient, payload["text"]) for recipient, payload in sent] == [
        (tg(owner, signed_out), say("uz", "a_sign_in_first"))
    ]
    assert deciders(owner, third) == ("submitted", None, None)


def test_the_panel_shows_who_decided_from_the_group(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    telegram_members: FakeChatMembers,
    group: int,
    receipt: str,
) -> None:
    presser = new_tg_id()
    telegram_members.statuses[(group, presser)] = "administrator"
    assert press(client, owner, presser, group, press_of(receipt, "sra"))[1] != []

    panel = elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))
    shown = client.get(f"{RECEIPTS}/{receipt}", headers=panel).json()
    assert (shown["status"], shown["decided_by"], shown["decided_by_tg_id"]) == ("approved", None, presser)
    listed = client.get(RECEIPTS, params={"status": "approved"}, headers=panel).json()["items"]
    assert [(item["decided_by"], item["decided_by_tg_id"]) for item in listed if item["id"] == receipt] == [
        (None, presser)
    ]
    trail = client.get(f"{ADMIN_API}/audit", params={"shop_id": str(world.shop_a)}, headers=panel).json()["items"]
    decision = [row for row in trail if row["action"] == "subscription.receipt_approved"]
    assert [(row["admin_id"], row["actor_tg_id"], row["detail"]["via"]) for row in decision] == [
        (None, presser, "group")
    ]
    # An administrator's own rows name the administrator and no Telegram identifier.
    looked = [row for row in trail if row["action"] == "receipt.viewed"]
    assert looked and all((row["admin_id"], row["actor_tg_id"]) == (str(world.admin), None) for row in looked)
    page = client.get(f"{ADMIN_API}/shops/{world.shop_a}", headers=panel).json()
    assert [(change["admin_id"], change["actor_tg_id"]) for change in page["changes"]] == [(None, presser)]


# --- decided once ------------------------------------------------------------------------------------------


def test_a_second_press_and_a_redelivered_press_decide_nothing_more(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    telegram_members: FakeChatMembers,
    group: int,
    receipt: str,
) -> None:
    first, second = new_tg_id(), new_tg_id()
    for presser in (first, second):
        telegram_members.statuses[(group, presser)] = "administrator"
    update = {
        "update_id": next(_update_ids),
        "callback_query": {
            "id": "cb-again",
            "from": {"id": first, "language_code": "uz"},
            "message": {"message_id": ANNOUNCEMENT, "chat": {"id": group, "type": "supergroup"}},
            "data": press_of(receipt, "sra"),
        },
    }
    for _ in range(2):
        assert client.post("/tg/webhook", json=update, headers=SECRET).status_code == 200
    decided = state(owner, world, receipt)
    assert deciders(owner, receipt) == ("approved", None, first)
    assert len(audit(owner, receipt)) == 1 and len(decided_notice(owner, receipt)) == 1

    # Another administrator of the group presses afterwards: told over the button, and nothing changes.
    for action in ("sra", "srj"):
        answer, sent = press(client, owner, second, group, press_of(receipt, action))
        assert (answer["text"], answer["show_alert"]) == (say("uz", "a_receipt_decided"), True)
        assert sent == [closed(group, say("uz", "a_receipt_decided"))]
    assert state(owner, world, receipt) == decided
    assert deciders(owner, receipt) == ("approved", None, first)


def test_two_group_administrators_pressing_at_once_cannot_both_decide(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    telegram_members: FakeChatMembers,
    group: int,
) -> None:
    pressers = [new_tg_id(), new_tg_id()]
    for presser in pressers:
        telegram_members.statuses[(group, presser)] = "administrator"
    for _ in range(3):
        owner.execute(
            "UPDATE subscription SET state = 'limited', paid_through = NULL, prior_state = NULL WHERE shop_id = %s",
            (world.shop_a,),
        )
        receipt = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1, unique_image()[0])
        barrier = threading.Barrier(2)

        def decide(presser: int, receipt: str = receipt, wait: Any = barrier) -> Any:
            wait.wait(timeout=10)
            return press(client, owner, presser, group, press_of(receipt, "sra"))[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            answers = list(pool.map(decide, pressers))
        assert sorted("text" in answer for answer in answers) == [False, True], "one decided, one was told so"
        status, by_admin, by_tg = deciders(owner, receipt)
        assert (status, by_admin) == ("approved", None) and by_tg in pressers
        assert len(audit(owner, receipt)) == 1 and len(decided_notice(owner, receipt)) == 1
        assert subscription(owner, world.shop_a)[2] == add_months(today(admin_env), 1) - timedelta(days=1)


def test_a_receipt_without_stated_months_is_left_for_the_panel(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    telegram_members: FakeChatMembers,
    group: int,
    receipt: str,
) -> None:
    """The months that count cannot be entered from the group."""
    presser = new_tg_id()
    telegram_members.statuses[(group, presser)] = "administrator"
    owner.execute("UPDATE subscription_receipt SET stated_months = NULL WHERE id = %s", (receipt,))
    before = state(owner, world, receipt)
    answer, sent = press(client, owner, presser, group, press_of(receipt, "sra"))
    assert sent == []
    assert (answer["text"], answer["show_alert"]) == (say("uz", "g_receipt_needs_panel"), True)
    assert state(owner, world, receipt) == before


@pytest.mark.parametrize("lang", sorted(CATALOGS))
def test_what_is_said_over_the_button_fits_telegrams_limit(lang: str) -> None:
    for words in ("g_reason_in_private", "g_receipt_needs_panel", "a_receipt_decided"):
        assert 0 < len(say(lang, words)) <= CALLBACK_NOTICE_MAX == 200, (lang, words)
