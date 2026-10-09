"""Connecting customers and telling them about their account (REQ-012 to REQ-015, REQ-019 to REQ-021)."""

import hashlib
import uuid
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import day, money, say

from .conftest import World, as_user
from .test_chat import Chat, chat_of, today
from .test_customers_ledger import key, record, reverse, shop

pytestmark = pytest.mark.db

_people = iter(range(99_100_000, 99_200_000))


def post(client: TestClient, user: uuid.UUID, path: str, body: Any = None) -> Any:
    return client.post(path, json=body, headers={**as_user(user), **key()})


def get(client: TestClient, user: uuid.UUID, path: str) -> Any:
    return client.get(path, headers=as_user(user))


def person(client: TestClient, owner: psycopg.Connection, first_name: str = "Karim", language: str = "uz") -> Chat:
    chat = Chat(client, owner, next(_people), language)
    chat.profile = {"first_name": first_name, "last_name": "Aliyev"}
    return chat


def personal_link(client: TestClient, world: World, customer: Any) -> str:
    response = post(client, world.seller_a, f"{shop(world)}/customers/{customer}/link")
    assert response.status_code == 201, response.text
    return str(response.json()["start"])


def counter_code(client: TestClient, world: World) -> str:
    response = post(client, world.manager_a, f"{shop(world)}/counter-code")
    assert response.status_code == 201, response.text
    return str(response.json()["start"])


def links(owner: psycopg.Connection, tg_id: int) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT l.shop_id, l.customer_id, l.status, l.consent_text_v, l.consent_at IS NOT NULL, l.waiting_name "
        "FROM customer_link l JOIN app_user u ON u.id = l.user_id WHERE u.tg_id = %s ORDER BY l.created_at",
        (tg_id,),
    ).fetchall()


def notices(owner: psycopg.Connection, world: World) -> list[tuple[str, str]]:
    """Messages queued for customers of shop A: (recipient, text)."""
    return [
        (str(row[0]), str(row[1]))
        for row in owner.execute(
            "SELECT recipient, payload->>'text' FROM outbox_message "
            "WHERE shop_id = %s AND dedupe_key LIKE 'entry:%%' ORDER BY created_at, id",
            (world.shop_a,),
        ).fetchall()
    ]


def agree(chat: Chat, start: str) -> Any:
    asked = chat.say(f"/start {start}")
    return chat.press(asked.button("✅"), chat.last_message_id)


# --- personal link ------------------------------------------------------------------------------------


def test_a_personal_link_is_shown_once_and_only_its_hash_is_kept(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    headers = {**as_user(world.seller_a), **key()}
    path = f"{shop(world)}/customers/{world.settled_customer_a}/link"
    first = client.post(path, headers=headers)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["start"] == "c_" + body["token"]
    assert len(body["start"]) <= 64, "Telegram limits the start parameter to 64 characters"
    stored = owner.execute(
        "SELECT token_hash, kind, status, "
        "expires_at > now() + interval '6 days', expires_at < now() + interval '8 days' "
        "FROM invitation WHERE customer_id = %s",
        (world.settled_customer_a,),
    ).fetchall()
    assert stored == [(hashlib.sha256(body["token"].encode()).digest(), "customer", "issued", True, True)]
    assert body["token"] not in str(owner.execute("SELECT response FROM request_key").fetchall())

    repeat = client.post(path, headers=headers)
    assert repeat.status_code == 201
    assert (repeat.json()["token"], repeat.json()["start"]) == (None, None), "a repeat does not hand the link out again"

    # A new link replaces the old one.
    personal_link(client, world, world.settled_customer_a)
    statuses = owner.execute(
        "SELECT status FROM invitation WHERE customer_id = %s ORDER BY created_at", (world.settled_customer_a,)
    ).fetchall()
    assert statuses == [("cancelled",), ("issued",)]


def test_a_link_is_not_issued_for_a_linked_archived_or_foreign_customer(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = owner.execute("SELECT count(*) FROM invitation").fetchone()
    linked = post(client, world.owner_a, f"{shop(world)}/customers/{world.customer_a}/link")
    assert (linked.status_code, linked.json()["error"]["code"]) == (409, "CUSTOMER_ALREADY_LINKED")
    archived = post(client, world.owner_a, f"{shop(world)}/customers/{world.archived_customer_a}/link")
    assert (archived.status_code, archived.json()["error"]["code"]) == (409, "CUSTOMER_ARCHIVED")
    assert post(client, world.owner_a, f"{shop(world)}/customers/{uuid.uuid4()}/link").status_code == 404
    assert owner.execute("SELECT count(*) FROM invitation").fetchone() == before


def test_staff_can_see_whether_a_customer_is_linked(client: TestClient, world: World) -> None:
    linked = get(client, world.seller_a, f"{shop(world)}/customers/{world.customer_a}/link").json()
    assert (linked["linked"], linked["status"]) == (True, "active")
    assert linked["since"] is not None
    free = get(client, world.seller_a, f"{shop(world)}/customers/{world.settled_customer_a}/link").json()
    assert free == {"linked": False, "status": None, "since": None}
    assert get(client, world.seller_a, f"{shop(world)}/customers/{uuid.uuid4()}/link").status_code == 404


def test_a_customer_connects_through_a_personal_link_after_agreeing(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    start = personal_link(client, world, world.settled_customer_a)
    customer = person(client, owner)

    asked = customer.say(f"/start {start}")
    assert asked.text == say("uz", "consent_v2", shop="Shop A")
    assert "Shop A" in asked.text and "/uzish" in asked.text and "/ochirish" in asked.text
    assert list(asked.buttons) == ["✅ Roziman", "Yo'q"]
    assert links(owner, customer.tg_id) == [], "nothing is stored about the person before they agree"

    done = customer.press(asked.button("✅"), customer.last_message_id)
    assert done.text == say("uz", "linked", shop="Shop A")
    assert links(owner, customer.tg_id) == [(world.shop_a, world.settled_customer_a, "active", 2, True, None)]
    assert owner.execute(
        "SELECT status FROM invitation WHERE customer_id = %s", (world.settled_customer_a,)
    ).fetchall() == [("used",)]
    logged = owner.execute(
        "SELECT actor_kind, actor_id FROM activity WHERE shop_id = %s AND action = 'customer.linked'", (world.shop_a,)
    ).fetchall()
    assert logged == [("customer", None)]

    # Pressing again, or someone else opening the same link, does nothing.
    assert customer.press(asked.button("✅")).text == say("uz", "expired")
    assert person(client, owner).say(f"/start {start}").text == say("uz", "link_invalid")
    assert len(links(owner, customer.tg_id)) == 1


def test_declining_stores_nothing_and_leaves_the_link_usable(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    start = personal_link(client, world, world.settled_customer_a)
    customer = person(client, owner)
    asked = customer.say(f"/start {start}")
    assert customer.press(asked.button("Yo'q")).text == say("uz", "consent_declined")
    assert links(owner, customer.tg_id) == []
    assert customer.press(asked.button("✅")).text == say("uz", "expired"), "a declined question cannot be agreed to"
    assert links(owner, customer.tg_id) == []
    # They may change their mind by opening the link again.
    assert agree(customer, start).text == say("uz", "linked", shop="Shop A")


def test_agreeing_to_someone_elses_question_does_nothing(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    start = personal_link(client, world, world.settled_customer_a)
    customer, other = person(client, owner), person(client, owner)
    asked = customer.say(f"/start {start}")
    assert other.press(asked.button("✅")).text == say("uz", "expired")
    assert links(owner, other.tg_id) == []
    assert customer.press(asked.button("✅")).text == say("uz", "linked", shop="Shop A")


@pytest.mark.parametrize("prefix", ["c_", "k_"])
def test_codes_that_lead_nowhere_are_refused_before_any_question(
    client: TestClient, world: World, owner: psycopg.Connection, prefix: str
) -> None:
    customer = person(client, owner)
    expired = personal_link(client, world, world.settled_customer_a)
    owner.execute("UPDATE invitation SET expires_at = now() - interval '1 minute' WHERE kind = 'customer'")
    old_counter = counter_code(client, world)
    counter_code(client, world)  # replaces the one before it
    questions = owner.execute("SELECT count(*) FROM chat_pending WHERE kind = 'consent'").fetchone()
    for code in (
        prefix + "x" * 43,
        prefix + "short",
        prefix + world.invitation_a_token,  # a staff invitation is not a customer code
        prefix + expired[2:],
        prefix + old_counter[2:],
        prefix,
    ):
        said = customer.say(f"/start {code}")
        assert said.text == say("uz", "link_invalid"), code
        assert said.buttons == {}
    assert links(owner, customer.tg_id) == []
    # No question was put to anyone. Compared with the count before: other tests leave theirs unanswered.
    assert owner.execute("SELECT count(*) FROM chat_pending WHERE kind = 'consent'").fetchone() == questions
    assert owner.execute(
        "SELECT count(*) FROM chat_pending p JOIN app_user u ON u.id = p.user_id WHERE u.tg_id = %s", (customer.tg_id,)
    ).fetchone() == (0,)


def test_a_record_already_linked_cannot_be_taken_over(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    start = personal_link(client, world, world.settled_customer_a)
    attached = post(
        client,
        world.seller_a,
        f"{shop(world)}/waiting/{world.waiting_a}/attach",
        {"customer_id": str(world.settled_customer_a)},
    )
    assert attached.status_code == 200, attached.text
    late = person(client, owner)
    assert agree(late, start).text == say("uz", "link_taken", shop="Shop A")
    assert links(owner, late.tg_id) == []


def test_a_customer_cannot_link_twice_in_one_shop(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    customer = person(client, owner)
    assert agree(customer, counter_code(client, world)).text == say("uz", "waiting_ok", shop="Shop A")
    again = agree(customer, personal_link(client, world, world.settled_customer_a))
    assert again.text == say("uz", "link_already", shop="Shop A")
    assert [row[2] for row in links(owner, customer.tg_id)] == ["waiting"]


# --- counter code and waiting list ---------------------------------------------------------------------


def test_the_counter_code_is_reusable_and_replaceable(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    assert get(client, world.seller_a, f"{shop(world)}/counter-code").json() == {"exists": False, "since": None}
    start = counter_code(client, world)
    assert start.startswith("k_") and len(start) <= 64
    state = get(client, world.seller_a, f"{shop(world)}/counter-code").json()
    assert state["exists"] is True and state["since"] is not None

    first, second = person(client, owner, "Bahrom"), person(client, owner, "Sardor")
    for customer in (first, second):
        asked = customer.say(f"/start {start}")
        assert asked.text == say("uz", "consent_v2", shop="Shop A")
        assert links(owner, customer.tg_id) == []
        assert customer.press(asked.button("✅")).text == say("uz", "waiting_ok", shop="Shop A")
    assert links(owner, first.tg_id) == [(world.shop_a, None, "waiting", 2, True, "Bahrom Aliyev")]

    waiting = get(client, world.seller_a, f"{shop(world)}/waiting").json()["items"]
    assert [item["name"] for item in waiting] == ["Kutuvchi Karim", "Bahrom Aliyev", "Sardor Aliyev"]
    # Another shop's waiting list shows none of them.
    assert get(client, world.owner_b, f"/api/v1/shops/{world.shop_b}/waiting").json() == {"items": []}

    counter_code(client, world)
    assert person(client, owner).say(f"/start {start}").text == say("uz", "link_invalid")


def test_staff_attach_a_waiting_person_to_their_record(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    waiter = chat_of(client, owner, world.waiter)
    path = f"{shop(world)}/waiting/{world.waiting_a}/attach"
    response = post(client, world.seller_a, path, {"customer_id": str(world.settled_customer_a)})
    assert response.status_code == 200, response.text
    assert links(owner, waiter.tg_id) == [(world.shop_a, world.settled_customer_a, "active", 2, True, None)]
    assert get(client, world.seller_a, f"{shop(world)}/waiting").json() == {"items": []}
    told = owner.execute(
        "SELECT recipient, payload->>'text' FROM outbox_message WHERE dedupe_key = %s",
        (f"link:{world.waiting_a}:attached",),
    ).fetchall()
    assert told == [(str(waiter.tg_id), say("uz", "linked", shop="Shop A"))]

    again = post(client, world.seller_a, path, {"customer_id": str(world.settled_customer_a)})
    assert again.status_code in (404, 409)
    # From now on they are told about their account.
    record(client, world, world.settled_customer_a, "credit", 12000)
    assert notices(owner, world)[-1][0] == str(waiter.tg_id)


def test_a_waiting_person_is_not_attached_to_a_taken_archived_or_unknown_record(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    path = f"{shop(world)}/waiting/{world.waiting_a}/attach"
    waiter = chat_of(client, owner, world.waiter)
    for customer, status, code in (
        (world.customer_a, 409, "CUSTOMER_ALREADY_LINKED"),
        (world.archived_customer_a, 409, "CUSTOMER_ARCHIVED"),
        (uuid.uuid4(), 404, "NOT_FOUND"),
    ):
        response = post(client, world.seller_a, path, {"customer_id": str(customer)})
        assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    assert post(client, world.seller_a, path, {}).status_code == 422
    unknown = post(
        client,
        world.seller_a,
        f"{shop(world)}/waiting/{uuid.uuid4()}/attach",
        {"customer_id": str(world.settled_customer_a)},
    )
    assert unknown.status_code == 404
    assert links(owner, waiter.tg_id) == [(world.shop_a, None, "waiting", 2, True, "Kutuvchi Karim")]


def test_a_waiting_entry_lapses_after_a_day(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    owner.execute(
        "UPDATE customer_link SET created_at = now() - interval '25 hours', consent_at = now() - interval '25 hours' "
        "WHERE id = %s",
        (world.waiting_a,),
    )
    assert get(client, world.seller_a, f"{shop(world)}/waiting").json() == {"items": []}
    late = post(
        client,
        world.seller_a,
        f"{shop(world)}/waiting/{world.waiting_a}/attach",
        {"customer_id": str(world.settled_customer_a)},
    )
    assert late.status_code == 404
    assert owner.execute("SELECT status FROM customer_link WHERE id = %s", (world.waiting_a,)).fetchone() == (
        "waiting",
    )


def test_staff_can_dismiss_a_waiting_person(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    path = f"{shop(world)}/waiting/{world.waiting_a}/dismiss"
    assert post(client, world.seller_a, path).status_code == 200
    row = owner.execute(
        "SELECT status, waiting_name, ended_at IS NOT NULL FROM customer_link WHERE id = %s", (world.waiting_a,)
    ).fetchone()
    assert row == ("ended", None, True)
    assert post(client, world.seller_a, path).status_code == 404
    assert get(client, world.seller_a, f"{shop(world)}/waiting").json() == {"items": []}


def test_a_person_is_told_when_the_shops_waiting_list_is_full_and_gets_on_once_staff_make_room(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """A hundred people wait at most (security review, finding 13). The one who finds the list full is
    told so in their language and nothing is kept about them; staff see the full list and can clear it."""
    start = counter_code(client, world)
    # Shop A's list has one person waiting already; ninety-nine more fill it.
    owner.execute(
        "WITH people AS (INSERT INTO app_user (id, tg_id) "
        "  SELECT gen_random_uuid(), %s + n FROM generate_series(1, 99) n RETURNING id) "
        "INSERT INTO customer_link (id, shop_id, user_id, status, consent_text_v, consent_at, waiting_name) "
        "SELECT gen_random_uuid(), %s, id, 'waiting', 2, now(), 'Someone' FROM people",
        (uuid.uuid4().int % 10**11 * 1000, world.shop_a),
    )
    uzbek, russian = person(client, owner, "Bahrom"), person(client, owner, "Sardor", "ru")
    assert agree(uzbek, start).text == say("uz", "waiting_full", shop="Shop A")
    assert agree(russian, start).text == say("ru", "waiting_full", shop="Shop A")
    assert "Shop A" in say("uz", "waiting_full", shop="Shop A") != say("ru", "waiting_full", shop="Shop A")
    assert links(owner, uzbek.tg_id) == [] and links(owner, russian.tg_id) == []
    listed = get(client, world.seller_a, f"{shop(world)}/waiting").json()["items"]
    assert len(listed) == 100

    # Another shop's counter still takes them, and a dismissal in this one makes room for one.
    code_b = post(client, world.owner_b, f"/api/v1/shops/{world.shop_b}/counter-code").json()["start"]
    assert agree(russian, code_b).text == say("ru", "waiting_ok", shop="Shop B")
    assert post(client, world.seller_a, f"{shop(world)}/waiting/{world.waiting_a}/dismiss").status_code == 200
    assert agree(uzbek, start).text == say("uz", "waiting_ok", shop="Shop A")
    assert agree(russian, start).text == say("ru", "waiting_full", shop="Shop A")
    assert [row[2] for row in links(owner, uzbek.tg_id)] == ["waiting"]


# --- notifications (REQ-012, REQ-015) -------------------------------------------------------------------


def test_a_linked_customer_is_told_of_every_entry_payment_and_reversal(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    promised = day(today() + timedelta(days=30))

    sale = record(client, world, world.customer_a, "credit", 30000).json()["entry"]["id"]
    payment = record(client, world, world.customer_a, "payment", 10000).json()["entry"]["id"]
    reverse(client, world, payment)
    reverse(client, world, sale)

    values = {"shop": "Shop A", "name": "Ali"}
    assert notices(owner, world) == [
        (
            str(customer.tg_id),
            say(
                "uz",
                "n_credit",
                amount=money("uz", 30000),
                balance=money("uz", 80000),
                goods="",
                date=promised,
                **values,
            ),
        ),
        (str(customer.tg_id), say("uz", "n_payment", amount=money("uz", 10000), balance=money("uz", 70000), **values)),
        (
            str(customer.tg_id),
            say("uz", "n_reversed_payment", amount=money("uz", 10000), balance=money("uz", 80000), **values),
        ),
        (
            str(customer.tg_id),
            say("uz", "n_reversed_credit", amount=money("uz", 30000), balance=money("uz", 50000), **values),
        ),
    ]


def test_an_entry_made_from_chat_and_a_chosen_date_are_told_too(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    seller = chat_of(client, owner, world.seller_a)
    saved = seller.say("Ali 7000")
    seller.press(saved.button("Ertaga"), seller.last_message_id)
    told = notices(owner, world)
    assert [recipient for recipient, _ in told] == [str(customer.tg_id)] * 2
    assert told[0][1].startswith("Shop A\nAli, sizga nasiya yozildi: 7 000")
    assert told[1][1] == say(
        "uz",
        "n_promise",
        shop="Shop A",
        name="Ali",
        amount=money("uz", 7000),
        date=day(today() + timedelta(days=1)),
        balance=money("uz", 57000),
    )


def test_the_message_is_in_the_customers_language(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.customer_of_a,))
    record(client, world, world.customer_a, "payment", 10000)
    assert notices(owner, world)[-1][1] == say(
        "ru", "n_payment", shop="Shop A", name="Ali", amount=money("ru", 10000), balance=money("ru", 40000)
    )


def test_nobody_is_told_about_someone_elses_account(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    record(client, world, world.settled_customer_a, "credit", 30000)  # not linked to anyone
    assert notices(owner, world) == []


@pytest.mark.parametrize("status", ["unreachable", "ended"])
def test_a_customer_who_left_or_blocked_the_bot_is_not_told(
    client: TestClient, world: World, owner: psycopg.Connection, status: str
) -> None:
    owner.execute("UPDATE customer_link SET status = %s WHERE customer_id = %s", (status, world.customer_a))
    record(client, world, world.customer_a, "credit", 30000)
    assert notices(owner, world) == []


def test_a_refused_or_repeated_entry_tells_the_customer_once_or_not_at_all(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    assert record(client, world, world.customer_a, "payment", 60000).status_code == 409
    assert notices(owner, world) == [], "no message about an entry that was not saved"

    headers = {**as_user(world.seller_a), **key()}
    path = f"{shop(world)}/customers/{world.customer_a}/entries"
    for _ in range(2):
        assert client.post(path, json={"kind": "credit", "amount": 5000}, headers=headers).status_code == 201
    assert len(notices(owner, world)) == 1


# --- the customer's own view and disconnecting (REQ-019, REQ-020, REQ-021) -----------------------------


def test_a_customer_sees_what_they_owe_in_each_shop_and_nothing_else(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    record(client, world, world.settled_customer_a, "credit", 999000)  # someone else's debt in the same shop
    assert customer.say("/qarzim").text == "\n".join(
        [say("uz", "accounts_header"), say("uz", "account_line", shop="Shop A", balance=money("uz", 50000))]
    )

    # The same person as a customer of shop B, owing something else there.
    in_b = uuid.uuid4()
    author = owner.execute("SELECT id FROM membership WHERE shop_id = %s", (world.shop_b,)).fetchone()
    assert author is not None
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Alijon', 'alijon')",
        (in_b, world.shop_b),
    )
    owner.execute(
        "INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at) "
        "VALUES (%s, %s, %s, %s, 'active', 2, now())",
        (uuid.uuid4(), world.shop_b, in_b, world.customer_of_a),
    )
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
        "VALUES (%s, %s, %s, 1, 'credit', 7000, %s)",
        (uuid.uuid4(), world.shop_b, in_b, author[0]),
    )
    lines = customer.say("/qarzim").text.split("\n")
    assert lines == [
        say("uz", "accounts_header"),
        say("uz", "account_line", shop="Shop A", balance=money("uz", 50000)),
        say("uz", "account_line", shop="Shop B", balance=money("uz", 7000)),
    ]
    assert chat_of(client, owner, world.stranger).say("/qarzim").text == say("uz", "no_accounts")
    assert chat_of(client, owner, world.waiter).say("/qarzim").text == say("uz", "no_accounts")


def test_a_customer_disconnects_and_the_ledger_keeps_the_entries(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    asked = customer.say("/uzish")
    assert asked.text == say("uz", "unlink_choose")
    assert list(asked.buttons) == [say("uz", "unlink_button", shop="Shop A")]

    assert customer.press(asked.button("Uzilish")).text == say("uz", "unlinked", shop="Shop A")
    row = owner.execute(
        "SELECT status, ended_at IS NOT NULL FROM customer_link WHERE customer_id = %s", (world.customer_a,)
    ).fetchone()
    assert row == ("ended", True)
    assert owner.execute(
        "SELECT count(*) FROM activity WHERE shop_id = %s AND action = 'customer.unlinked'", (world.shop_a,)
    ).fetchone() == (1,)

    # Notifications stop; the shop still has the customer and the debt (REQ-021).
    record(client, world, world.customer_a, "credit", 1000)
    assert notices(owner, world) == []
    detail = get(client, world.seller_a, f"{shop(world)}/customers/{world.customer_a}").json()
    assert (detail["display_name"], detail["balance"]) == ("Ali", 51000)
    assert customer.say("/qarzim").text == say("uz", "no_accounts")
    assert customer.say("/uzish").text == say("uz", "no_accounts")
    assert customer.press(asked.button("Uzilish")).text == say("uz", "expired")


def test_a_customer_cannot_disconnect_someone_else_or_another_shop(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    stranger = chat_of(client, owner, world.stranger)
    for shop_id in (world.shop_a, world.shop_b, uuid.uuid4()):
        assert stranger.press(f"v2:unl:{shop_id.hex}").text == say("uz", "expired")
    assert owner.execute("SELECT status FROM customer_link WHERE customer_id = %s", (world.customer_a,)).fetchone() == (
        "active",
    )


def test_someone_who_unblocks_the_bot_is_reachable_again(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE customer_link SET status = 'unreachable' WHERE customer_id = %s", (world.customer_a,))
    customer = chat_of(client, owner, world.customer_of_a)
    assert "Shop A" in customer.say("/qarzim").text
    assert owner.execute("SELECT status FROM customer_link WHERE customer_id = %s", (world.customer_a,)).fetchone() == (
        "active",
    )
    record(client, world, world.customer_a, "payment", 1000)
    assert len(notices(owner, world)) == 1


def test_a_link_issued_before_the_customer_was_archived_no_longer_connects(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    start = personal_link(client, world, world.settled_customer_a)
    archived = post(client, world.manager_a, f"{shop(world)}/customers/{world.settled_customer_a}/archive")
    assert archived.status_code == 200, archived.text
    late = person(client, owner)
    assert agree(late, start).text == say("uz", "link_invalid", shop="Shop A")
    assert links(owner, late.tg_id) == []
