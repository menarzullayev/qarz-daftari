"""An administrator decides a subscription receipt with the buttons in their private chat with the bot
(specification, chat section: "forwarded to the administrator and the review group with approve and reject
buttons"; REQ-055; ADR-017).

A press counts only for someone on the allow-list, with an active and confirmed administrator account,
who holds an admin session that is still valid: the proof that they passed the second factor. The review
group's copy has them too. There such an administrator's press decides as well, and so, since the
founder's decision of 2026-10-08 (DEC-064), does the press of any Telegram administrator of the group:
that is proved in `test_subscription_receipts_group_admins`. In this file nobody administers the group,
so what is proved here is what holds for everyone else.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import day, money, say
from qarz.domain.subscription import add_months

from .conftest import AdminEnv, World, as_user, elevate, make_admin
from .test_chat import SECRET, Chat, _update_ids, chat_of
from .test_disputes import tg
from .test_subscription_receipts import (
    approve,
    audit,
    decided_notice,
    reject,
    rows,
    second_admin_app,
    sent_ok,
    setting,
    subscription,
    today,
    unique_image,
)

pytestmark = pytest.mark.db

CANCEL = "v2:srn"
REASON = "Pul kelib tushmagan"


def buttons_of(owner: psycopg.Connection, receipt: str, recipient: str) -> dict[str, str]:
    """The buttons on the announcement one recipient got: label -> callback data."""
    row = owner.execute(
        "SELECT payload FROM outbox_message WHERE dedupe_key = %s", (f"subreceipt:{receipt}:new:{recipient}",)
    ).fetchone()
    assert row is not None, "this recipient was not told"
    markup = row[0].get("reply_markup")
    return {} if markup is None else {b["text"]: b["callback_data"] for line in markup["inline_keyboard"] for b in line}


def press_of(receipt: str, action: str) -> str:
    return f"v2:{action}:{uuid.UUID(receipt).hex}"


def state(owner: psycopg.Connection, world: World, receipt: str) -> tuple[Any, ...]:
    """Everything a press could change."""
    return (
        rows(owner, world.shop_a),
        subscription(owner, world.shop_a),
        audit(owner, receipt),
        decided_notice(owner, receipt),
        owner.execute("SELECT count(*) FROM activity WHERE shop_id = %s", (world.shop_a,)).fetchone(),
        owner.execute("SELECT count(*) FROM chat_pending WHERE kind = 'receipt_reject'").fetchone(),
    )


def no_text(said: Any) -> bool:
    """What any unknown button gets: its stale buttons taken away, and not a word."""
    return [set(payload) for payload in said.payloads] == [{"method", "message_id", "reply_markup"}]


@pytest.fixture
def reviewer(client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv) -> Chat:
    """An administrator who passed the second factor, in their private chat with the bot."""
    elevate(client, admin_env, world.admin, make_admin(owner, admin_env, world.admin))
    return chat_of(client, owner, world.admin)


@pytest.fixture
def receipt(client: TestClient, world: World, reviewer: Chat) -> str:
    return sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])


def test_every_copy_of_the_announcement_has_the_two_buttons(
    client: TestClient, world: World, owner: psycopg.Connection, reviewer: Chat
) -> None:
    """In each administrator's private chat and in the review group (the founder's decision of 2026-10-07)."""
    group = review_group(owner, world)
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    expected = {"✅ Tasdiqlash": press_of(receipt, "sra"), "Rad etish": press_of(receipt, "srj")}
    assert buttons_of(owner, receipt, tg(owner, world.admin)) == expected
    assert buttons_of(owner, receipt, str(group)) == expected


def test_approve_from_the_chat_does_what_the_panel_does_and_says_where_it_came_from(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, reviewer: Chat, receipt: str
) -> None:
    trial_ends = subscription(owner, world.shop_a)[1]
    said = reviewer.press(press_of(receipt, "sra"))
    until = add_months(today(admin_env), 3) - timedelta(days=1)
    assert said.text == say("uz", "a_receipt_approved", shop="Shop A", months=3, date=day(until))
    # The message that carried the buttons is replaced by the outcome: no buttons are left on it.
    assert said.payloads[0]["method"] == "editMessageText"
    assert said.payloads[0]["reply_markup"] == {"inline_keyboard": []}

    assert subscription(owner, world.shop_a) == ("active", trial_ends, until, None)
    assert rows(owner, world.shop_a) == [(300_000, 3, "approved", 3, None, world.admin, True)]
    ((action, who, target_type, shop, reason, detail),) = audit(owner, receipt)
    assert (action, who, target_type, shop, reason) == (
        "subscription.receipt_approved",
        world.admin,
        "receipt",
        world.shop_a,
        None,
    )
    assert detail["via"] == "chat" and detail["months"] == 3
    assert owner.execute(
        "SELECT actor_kind, actor_id FROM activity WHERE shop_id = %s AND action = 'subscription.receipt_approved'",
        (world.shop_a,),
    ).fetchall() == [("admin", None)]
    assert decided_notice(owner, receipt) == [
        (tg(owner, world.owner_a), say("uz", "sub_receipt_approved", shop="Shop A", months=3, date=day(until)))
    ]


def test_reject_asks_for_the_reason_and_then_rejects(
    client: TestClient, world: World, owner: psycopg.Connection, reviewer: Chat, receipt: str
) -> None:
    was = subscription(owner, world.shop_a)
    asked = reviewer.press(press_of(receipt, "srj"), message_id=4242)
    assert asked.text == say("uz", "ask_receipt_reject_reason")
    assert asked.buttons == {"Bekor": CANCEL}
    assert rows(owner, world.shop_a)[0][2] == "submitted", "nothing is decided until the reason is written"

    for bad in ("xx", "x" * 501):
        again = reviewer.say(bad)
        assert again.text == say("uz", "a_reason_invalid") and again.buttons == {"Bekor": CANCEL}
    assert rows(owner, world.shop_a)[0][2] == "submitted"

    done = reviewer.say(f"  {REASON}  ")
    assert done.text == say("uz", "a_receipt_rejected", shop="Shop A", reason=REASON)
    # The buttons are taken off the announcement the administrator pressed "reject" on.
    assert done.payloads[1] == {
        "method": "editMessageReplyMarkup",
        "message_id": 4242,
        "reply_markup": {"inline_keyboard": []},
    }
    assert rows(owner, world.shop_a) == [(300_000, 3, "rejected", None, REASON, world.admin, True)]
    assert subscription(owner, world.shop_a) == was
    assert [(row[0], row[4], row[5]) for row in audit(owner, receipt)] == [
        ("subscription.receipt_rejected", REASON, {"stated_amount": 300_000, "stated_months": 3, "via": "chat"})
    ]
    assert decided_notice(owner, receipt) == [
        (
            tg(owner, world.owner_a),
            say("uz", "sub_receipt_rejected", shop="Shop A", amount=money("uz", 300_000), reason=REASON),
        )
    ]
    # The question is answered: the next message is not another reason.
    assert reviewer.say(REASON).text != say("uz", "a_receipt_rejected", shop="Shop A", reason=REASON)


def test_a_rejection_that_is_cancelled_or_left_for_a_command_decides_nothing(
    world: World, owner: psycopg.Connection, reviewer: Chat, receipt: str
) -> None:
    before = state(owner, world, receipt)
    reviewer.press(press_of(receipt, "srj"))
    assert reviewer.press(CANCEL).text == say("uz", "cancelled")
    reviewer.say(REASON)
    assert state(owner, world, receipt) == before

    reviewer.press(press_of(receipt, "srj"))
    reviewer.say("/yordam")
    reviewer.say(REASON)
    assert state(owner, world, receipt) == before
    # The buttons still work afterwards.
    assert reviewer.press(press_of(receipt, "sra")).text.startswith("✅")


def test_an_administrator_without_a_live_session_is_sent_to_the_panel_and_nothing_changes(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    make_admin(owner, admin_env, world.admin)  # on the list, active, confirmed; never passed the code today
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    chat = chat_of(client, owner, world.admin)
    before = state(owner, world, receipt)
    for action in ("sra", "srj"):
        said = chat.press(press_of(receipt, action))
        assert said.text == say("uz", "a_sign_in_first")
        assert said.payloads[0].get("method") is None, "a new message; the buttons stay for after signing in"
    assert state(owner, world, receipt) == before

    # A confirmed second factor is part of it: an account that never confirmed has no session to show.
    owner.execute("UPDATE admin_account SET confirmed_at = NULL WHERE user_id = %s", (world.admin,))
    owner.execute(
        "INSERT INTO admin_session (id, token_hash, user_id, expires_at) "
        "VALUES (gen_random_uuid(), %s, %s, now() + interval '1 hour')",
        (uuid.uuid4().bytes * 2, world.admin),
    )
    assert chat.press(press_of(receipt, "sra")).text == say("uz", "a_sign_in_first")
    assert state(owner, world, receipt) == before


def test_a_session_counts_until_the_instant_it_ends_and_not_after_it_is_closed(
    world: World, owner: psycopg.Connection, admin_env: AdminEnv, reviewer: Chat, receipt: str
) -> None:
    admin_env.clock.freeze()
    now = admin_env.clock.now()
    before = state(owner, world, receipt)

    def session(sql: str, *values: Any) -> None:
        owner.execute(f"UPDATE admin_session SET {sql} WHERE user_id = %s", (*values, world.admin))

    session("expires_at = %s", now)
    assert reviewer.press(press_of(receipt, "sra")).text == say("uz", "a_sign_in_first"), "ended this instant"
    session("expires_at = %s, revoked_at = %s", now + timedelta(hours=1), now)
    assert reviewer.press(press_of(receipt, "sra")).text == say("uz", "a_sign_in_first"), "signed out"
    assert state(owner, world, receipt) == before

    # While the reason is being written the session may run out too.
    session("expires_at = %s, revoked_at = NULL", now + timedelta(microseconds=1))
    assert reviewer.press(press_of(receipt, "srj")).text == say("uz", "ask_receipt_reject_reason")
    session("expires_at = %s", now)
    assert reviewer.say(REASON).text == say("uz", "a_sign_in_first")
    assert rows(owner, world.shop_a)[0][2] == "submitted"

    session("expires_at = %s", now + timedelta(microseconds=1))
    assert reviewer.say(REASON).text == say("uz", "a_receipt_rejected", shop="Shop A", reason=REASON)


@pytest.mark.parametrize("who", ["disabled", "off the list", "shop owner", "customer", "stranger"])
def test_for_anyone_else_the_buttons_are_no_buttons_and_tell_nothing(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    reviewer: Chat,
    receipt: str,
    who: str,
) -> None:
    if who == "disabled":
        # Still on the list, still holding the session opened a moment ago.
        owner.execute("UPDATE admin_account SET status = 'disabled' WHERE user_id = %s", (world.admin,))
        chat = reviewer
    elif who == "off the list":
        admin_env.allowed.discard(int(tg(owner, world.admin)))
        chat = reviewer
    else:
        user = {"shop owner": world.owner_a, "customer": world.customer_of_a, "stranger": world.stranger}[who]
        chat = chat_of(client, owner, user)
    before = state(owner, world, receipt)
    unknown = chat.press(f"v2:zzz:{uuid.uuid4().hex}")
    assert no_text(unknown)
    for forged in (press_of(receipt, "sra"), press_of(receipt, "srj"), "v2:sra:not-a-receipt", "v2:srj"):
        said = chat.press(forged)
        assert no_text(said), (who, forged)
        assert said.payloads[0]["reply_markup"] == unknown.payloads[0]["reply_markup"]
    # Nor is a reason taken from them, should a question have been left open before they lost the right.
    owner.execute(
        "INSERT INTO chat_pending (id, user_id, kind, payload, expires_at) "
        "SELECT gen_random_uuid(), id, 'receipt_reject', %s::jsonb, now() + interval '10 minutes' "
        "FROM app_user WHERE tg_id = %s",
        (f'{{"receipt": "{uuid.UUID(receipt).hex}"}}', chat.tg_id),
    )
    assert chat.say(REASON).text == say("uz", "expired")
    assert state(owner, world, receipt) == before


def review_group(owner: psycopg.Connection, world: World) -> int:
    group = -1_000_000_000_000 - uuid.uuid4().int % 10**9
    setting(owner, world.admin, "review_group", group)
    return group


def press_in_group(
    client: TestClient, owner: psycopg.Connection, tg_id: int, group: int, data: str, kind: str = "supergroup"
) -> list[tuple[str, dict[str, Any]]]:
    """Someone presses a button under message 7 of a group. Returns what the bot then sends: recipient, payload."""
    update_id = next(_update_ids)
    response = client.post(
        "/tg/webhook",
        json={
            "update_id": update_id,
            "callback_query": {
                "id": f"cb-{uuid.uuid4().hex}",
                "from": {"id": tg_id, "language_code": "uz"},
                "message": {"message_id": 7, "chat": {"id": group, "type": kind}},
                "data": data,
            },
        },
        headers=SECRET,
    )
    assert response.status_code == 200, response.text
    rows = owner.execute(
        "SELECT recipient, payload FROM outbox_message WHERE dedupe_key LIKE %s ORDER BY dedupe_key",
        (f"update:{update_id}:%",),
    ).fetchall()
    return [(str(recipient), payload) for recipient, payload in rows]


def test_an_administrator_approves_from_the_review_group(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, reviewer: Chat
) -> None:
    group = review_group(owner, world)
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    sent = press_in_group(client, owner, reviewer.tg_id, group, press_of(receipt, "sra"))

    until = add_months(today(admin_env), 3) - timedelta(days=1)
    outcome = say("uz", "a_receipt_approved", shop="Shop A", months=3, date=day(until))
    # The group's announcement is replaced by the outcome, without buttons; the administrator is told in
    # their own chat with a new message, because the pressed message is not there.
    assert sorted(sent, key=lambda pair: pair[0] != str(group)) == [
        (
            str(group),
            {"method": "editMessageText", "message_id": 7, "text": outcome, "reply_markup": {"inline_keyboard": []}},
        ),
        (str(reviewer.tg_id), {"text": outcome}),
    ]
    assert rows(owner, world.shop_a) == [(300_000, 3, "approved", 3, None, world.admin, True)]
    ((action, who, _, shop, _, detail),) = audit(owner, receipt)
    assert (action, who, shop, detail["via"]) == ("subscription.receipt_approved", world.admin, world.shop_a, "chat")

    # A second press there changes nothing and says so in both places.
    before = state(owner, world, receipt)
    again = press_in_group(client, owner, reviewer.tg_id, group, press_of(receipt, "sra"))
    decided = say("uz", "a_receipt_decided")
    assert {recipient: payload["text"] for recipient, payload in again} == {
        str(group): decided,
        str(reviewer.tg_id): decided,
    }
    assert state(owner, world, receipt) == before


def test_an_administrator_rejects_from_the_review_group_and_gives_the_reason_in_their_own_chat(
    client: TestClient, world: World, owner: psycopg.Connection, reviewer: Chat
) -> None:
    group = review_group(owner, world)
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    asked = press_in_group(client, owner, reviewer.tg_id, group, press_of(receipt, "srj"))
    # The question goes to the administrator alone; the group sees nothing until there is a decision.
    assert [(recipient, payload["text"]) for recipient, payload in asked] == [
        (str(reviewer.tg_id), say("uz", "ask_receipt_reject_reason"))
    ]
    assert rows(owner, world.shop_a) == [(300_000, 3, "submitted", None, None, None, True)], "nothing is decided yet"

    said = reviewer.say(REASON)
    assert said.text == say("uz", "a_receipt_rejected", shop="Shop A", reason=REASON)
    assert rows(owner, world.shop_a) == [(300_000, 3, "rejected", None, REASON, world.admin, True)]
    closed = owner.execute(
        "SELECT payload FROM outbox_message WHERE recipient = %s AND payload->>'method' = 'editMessageText'",
        (str(group),),
    ).fetchall()
    assert [row[0] for row in closed] == [
        {
            "method": "editMessageText",
            "message_id": 7,
            "text": say("uz", "a_receipt_rejected", shop="Shop A", reason=REASON),
            "reply_markup": {"inline_keyboard": []},
        }
    ]


def test_in_the_group_a_press_by_someone_who_is_neither_kind_of_administrator_changes_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, reviewer: Chat
) -> None:
    """Nobody here administers the group in Telegram, so only a platform administrator who passed the
    second factor decides (ADR-017); a press by anyone else changes nothing at all. Renamed when DEC-064
    let the group's Telegram administrators decide too: its assertions are unchanged."""
    group = review_group(owner, world)
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    before = state(owner, world, receipt)
    users = owner.execute("SELECT count(*) FROM app_user").fetchone()
    questions = owner.execute("SELECT count(*) FROM chat_pending WHERE kind = 'receipt_reject'").fetchone()

    # On the allow-list and confirmed, but with no administrator session: told, in private, to sign in.
    no_session = world.stranger
    make_admin(owner, admin_env, no_session)
    sent = press_in_group(client, owner, int(tg(owner, no_session)), group, press_of(receipt, "sra"))
    assert [(recipient, payload["text"]) for recipient, payload in sent] == [
        (tg(owner, no_session), say("uz", "a_sign_in_first"))
    ]
    # A disabled administrator, a shop owner in the group, and a member the service has never seen:
    # nothing is said to anyone, and nobody becomes a user by pressing.
    disabled = world.manager_a
    make_admin(owner, admin_env, disabled)
    owner.execute("UPDATE admin_account SET status = 'disabled' WHERE user_id = %s", (disabled,))
    for presser in (int(tg(owner, disabled)), int(tg(owner, world.owner_b)), 7_000_000_000 + uuid.uuid4().int % 10**9):
        for action in ("sra", "srj"):
            assert press_in_group(client, owner, presser, group, press_of(receipt, action)) == []
    assert state(owner, world, receipt) == before
    assert owner.execute("SELECT count(*) FROM app_user").fetchone() == users
    assert owner.execute("SELECT count(*) FROM chat_pending WHERE kind = 'receipt_reject'").fetchone() == questions


def test_the_buttons_work_in_the_review_group_and_in_no_other_group(
    client: TestClient, world: World, owner: psycopg.Connection, reviewer: Chat
) -> None:
    group = review_group(owner, world)
    receipt = sent_ok(client, world.owner_a, world.shop_a, 300_000, 3, unique_image()[0])
    before = state(owner, world, receipt)
    for elsewhere, kind in ((group - 1, "supergroup"), (group, "channel"), (-group, "supergroup")):
        assert press_in_group(client, owner, reviewer.tg_id, elsewhere, press_of(receipt, "sra"), kind) == []
    # Other buttons of the bot are not served in the review group either, even from an administrator.
    for data in ("v2:lang:ru", "v2:srn", "v2:newshop", "v1:sra:" + uuid.UUID(receipt).hex):
        assert press_in_group(client, owner, reviewer.tg_id, group, data) == []
    assert state(owner, world, receipt) == before
    # Without a review group configured there is no group to press in.
    owner.execute("DELETE FROM platform_setting WHERE key = 'review_group'")
    assert press_in_group(client, owner, reviewer.tg_id, group, press_of(receipt, "sra")) == []
    assert state(owner, world, receipt) == before


def test_a_second_press_and_a_press_after_the_panel_decided_change_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, reviewer: Chat, receipt: str
) -> None:
    assert reviewer.press(press_of(receipt, "sra")).text.startswith("✅")
    decided = state(owner, world, receipt)
    for action in ("sra", "srj"):
        again = reviewer.press(press_of(receipt, action))
        assert again.text == say("uz", "a_receipt_decided")
    assert state(owner, world, receipt) == decided

    # Decided in the panel first: the buttons another administrator still has say so.
    other = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1, unique_image()[0])
    panel = {**as_user(world.admin), "Cookie": _cookie(client, owner, admin_env, world)}
    assert reject(client, panel, other).status_code == 200
    settled = state(owner, world, other)
    assert reviewer.press(press_of(other, "sra")).text == say("uz", "a_receipt_decided")
    assert reviewer.press(press_of(other, "srj")).text == say("uz", "a_receipt_decided")
    assert state(owner, world, other) == settled
    # And a reason that was being written when the panel decided is not used.
    third = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1, unique_image()[0])
    reviewer.press(press_of(third, "srj"))
    assert approve(client, panel, third).status_code == 200
    assert reviewer.say(REASON).text == say("uz", "a_receipt_decided")
    assert rows(owner, world.shop_a)[-1][2] == "approved"


def _cookie(client: TestClient, owner: psycopg.Connection, env: AdminEnv, world: World) -> str:
    """A fresh admin session of the world's administrator, for the panel."""
    return elevate(client, env, world.admin, make_admin(owner, env, world.admin))["Cookie"]


def test_a_redelivered_press_decides_once(
    client: TestClient, world: World, owner: psycopg.Connection, reviewer: Chat, receipt: str
) -> None:
    update = {
        "update_id": next(_update_ids),
        "callback_query": {
            "id": "cb-again",
            "from": reviewer._from(),
            "message": {"message_id": 900, "chat": {"id": reviewer.tg_id, "type": "private"}},
            "data": press_of(receipt, "sra"),
        },
    }
    reviewer._post(update)
    paid = subscription(owner, world.shop_a)
    reviewer._post(update)
    assert subscription(owner, world.shop_a) == paid
    assert len(audit(owner, receipt)) == 1 and len(decided_notice(owner, receipt)) == 1


def test_the_panel_and_the_chat_deciding_at_once_cannot_both_win(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    reviewer: Chat,
    app_database_url: str,
    admin_database_url: str,
    file_root: Path,
) -> None:
    colleague = elevate(client, admin_env, world.stranger, make_admin(owner, admin_env, world.stranger))
    with second_admin_app(app_database_url, admin_database_url, admin_env, file_root) as panel:
        for attempt in range(4):
            owner.execute(
                "UPDATE subscription SET state = 'limited', paid_through = NULL, prior_state = NULL WHERE shop_id = %s",
                (world.shop_a,),
            )
            receipt = sent_ok(client, world.owner_a, world.shop_a, 100_000, 1, unique_image()[0])
            barrier = threading.Barrier(2)

            def decide(which: int, receipt: str = receipt, wait: Any = barrier, attempt: int = attempt) -> Any:
                wait.wait(timeout=10)
                if which == 0:
                    return reviewer.press(press_of(receipt, "sra")).text
                if attempt % 2 == 0:
                    return approve(panel, colleague, receipt, {"months": 12}).status_code
                return reject(panel, colleague, receipt).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                in_chat, in_panel = pool.map(decide, (0, 1))
            status, months, _, decided_by = rows(owner, world.shop_a)[-1][2:6]
            assert (in_panel, in_chat.startswith("✅")) in ((200, False), (409, True)), (attempt, in_panel, in_chat)
            if in_panel == 409:
                assert (status, months, decided_by) == ("approved", 1, world.admin)
            else:
                assert in_chat == say("uz", "a_receipt_decided") and decided_by == world.stranger
            state_now, _, paid_through, _ = subscription(owner, world.shop_a)
            expected = (
                ("active", add_months(today(admin_env), months) - timedelta(days=1))
                if status == "approved"
                else ("limited", None)
            )
            assert (state_now, paid_through) == expected
            assert len(audit(owner, receipt)) == 1 and len(decided_notice(owner, receipt)) == 1
