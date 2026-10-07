"""Disputes: a customer objects to an entry; the shop reverses it or declines; the customer may withdraw.

REQ-016, REQ-017; domain rules BR-10 to BR-13.
"""

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import money, say

from .conftest import World, as_user
from .test_chat import chat_of
from .test_customer_account import ME, link_of
from .test_customers_ledger import key, record, reverse, seed_entry, shop, today

pytestmark = pytest.mark.db


def open_dispute(
    client: TestClient, user: uuid.UUID, link: Any, entry: Any, reason: Any = "Men buni olmaganman"
) -> Any:
    return client.post(f"{ME}/{link}/disputes", json={"entry_id": str(entry), "reason": reason}, headers=as_user(user))


def decline(client: TestClient, world: World, user: uuid.UUID, dispute: Any, reason: Any = "Mahsulot berilgan") -> Any:
    return client.post(
        f"{shop(world)}/disputes/{dispute}/decline", json={"reason": reason}, headers={**as_user(user), **key()}
    )


def disputes(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT entry_id, status, reason, decline_reason, decided_by, closed_at IS NOT NULL FROM dispute "
        "WHERE shop_id = %s ORDER BY created_at",
        (world.shop_a,),
    ).fetchall()


def staff_notices(owner: psycopg.Connection, prefix: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (str(row[0]), row[1])
        for row in owner.execute(
            "SELECT recipient, payload FROM outbox_message WHERE dedupe_key LIKE %s ORDER BY recipient", (prefix + "%",)
        ).fetchall()
    ]


def tg(owner: psycopg.Connection, user: uuid.UUID) -> str:
    row = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (user,)).fetchone()
    assert row is not None
    return str(row[0])


def staff_view(client: TestClient, world: World) -> dict[str, Any]:
    return dict(client.get(f"{shop(world)}/customers/{world.customer_a}", headers=as_user(world.seller_a)).json())


# --- opening ------------------------------------------------------------------------------------------


def test_a_customer_disputes_an_entry_and_the_owner_and_managers_are_told(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    response = open_dispute(client, world.customer_of_a, link, world.entry_a, "  Men   buni olmaganman ")
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["entry_id"], body["status"], body["reason"]) == (str(world.entry_a), "open", "Men buni olmaganman")
    assert disputes(owner, world) == [(world.entry_a, "open", "Men buni olmaganman", None, None, False)]

    # The entry stays in the balance and is marked disputed, for staff and for the customer (REQ-017).
    seen = staff_view(client, world)
    assert seen["balance"] == 50000
    assert seen["entries"][0]["disputed"] is True
    mine = client.get(f"{ME}/{link}", headers=as_user(world.customer_of_a)).json()
    assert mine["balance"] == 50000
    assert mine["entries"][0]["dispute"] == {
        "id": body["id"],
        "status": "open",
        "reason": "Men buni olmaganman",
        "decline_reason": None,
    }

    told = staff_notices(owner, f"dispute:{body['id']}:opened")
    assert sorted(recipient for recipient, _ in told) == sorted([tg(owner, world.owner_a), tg(owner, world.manager_a)])
    text = say("uz", "s_dispute", shop="Shop A", name="Ali", amount=money("uz", 50000), reason="Men buni olmaganman")
    for _, payload in told:
        assert payload["text"] == text
        buttons = {b["text"]: b["callback_data"] for b in payload["reply_markup"]["inline_keyboard"][0]}
        assert buttons == {
            "↩️ Bekor qilish": f"v2:rv:{world.entry_a.hex}",
            "Rad etish": f"v2:dcl:{uuid.UUID(body['id']).hex}",
        }
    logged = owner.execute(
        "SELECT actor_kind FROM activity WHERE shop_id = %s AND action = 'dispute.opened'", (world.shop_a,)
    ).fetchall()
    assert logged == [("customer",)]


def test_an_entry_can_be_disputed_only_once_whatever_became_of_the_first_dispute(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    first = open_dispute(client, world.customer_of_a, link, world.entry_a).json()["id"]
    again = open_dispute(client, world.customer_of_a, link, world.entry_a, "Yana bir marta")
    assert (again.status_code, again.json()["error"]["code"]) == (409, "DISPUTE_NOT_ALLOWED")
    assert again.json()["error"]["fields"] == {"reason": "already_disputed"}

    withdrawn = client.post(f"{ME}/{link}/disputes/{first}/withdraw", headers=as_user(world.customer_of_a))
    assert withdrawn.status_code == 200
    assert open_dispute(client, world.customer_of_a, link, world.entry_a).json()["error"]["fields"] == {
        "reason": "already_disputed"
    }
    assert len(disputes(owner, world)) == 1


def test_only_a_live_debt_entry_can_be_disputed(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    link = link_of(owner, world.customer_a)
    payment = record(client, world, world.customer_a, "payment", 1000).json()["entry"]["id"]
    sale = record(client, world, world.customer_a, "credit", 2000).json()["entry"]["id"]
    reversal = reverse(client, world, sale).json()["entry"]["id"]
    for entry, why in ((payment, "not_a_debt"), (reversal, "not_a_debt"), (sale, "reversed")):
        response = open_dispute(client, world.customer_of_a, link, entry)
        assert (response.status_code, response.json()["error"]["fields"]) == (409, {"reason": why})
    assert disputes(owner, world) == []


def test_a_dispute_must_come_within_thirty_days_of_being_notified(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    owner.execute(
        "UPDATE customer_link SET created_at = now() - interval '90 days', consent_at = now() - interval '90 days' "
        "WHERE id = %s",
        (link,),
    )
    in_time = seed_entry(owner, world, world.customer_a, 2, "credit", 1000, promised=today(), days_ago=29.9)
    late = seed_entry(owner, world, world.customer_a, 3, "credit", 1000, promised=today(), days_ago=30.1)
    assert open_dispute(client, world.customer_of_a, link, in_time).status_code == 201
    refused = open_dispute(client, world.customer_of_a, link, late)
    assert (refused.status_code, refused.json()["error"]["fields"]) == (409, {"reason": "too_late"})


def test_an_old_entry_is_new_to_someone_who_has_just_been_linked(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The thirty days run from when the customer could first have known of the entry."""
    old = seed_entry(owner, world, world.customer_a, 2, "credit", 1000, promised=today(), days_ago=200)
    assert open_dispute(client, world.customer_of_a, link_of(owner, world.customer_a), old).status_code == 201


@pytest.mark.parametrize("reason", ["", "  ", "ab", "x" * 301, None, 5])
def test_a_dispute_needs_a_short_reason(
    client: TestClient, world: World, owner: psycopg.Connection, reason: Any
) -> None:
    response = open_dispute(client, world.customer_of_a, link_of(owner, world.customer_a), world.entry_a, reason)
    assert response.status_code == 422, response.text
    assert disputes(owner, world) == []


def test_nobody_can_dispute_an_entry_that_is_not_on_their_own_account(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    other_entry = record(client, world, world.settled_customer_a, "credit", 9000).json()["entry"]["id"]
    # Another customer's entry in the same shop, an entry that does not exist, and a link that is not theirs.
    for user, used_link, entry in (
        (world.customer_of_a, link, other_entry),
        (world.customer_of_a, link, uuid.uuid4()),
        (world.stranger, link, world.entry_a),
        (world.owner_a, link, world.entry_a),
        (world.seller_a, link, world.entry_a),
        (world.customer_of_a, uuid.uuid4(), world.entry_a),
    ):
        response = open_dispute(client, user, used_link, entry)
        assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert open_dispute(client, world.customer_of_a, link, "not-a-uuid").status_code == 422
    assert (
        client.post(f"{ME}/{link}/disputes", json={"entry_id": str(world.entry_a), "reason": "abc"}).status_code == 401
    )
    assert disputes(owner, world) == []
    told = owner.execute(
        "SELECT count(*) FROM outbox_message WHERE shop_id = %s AND dedupe_key LIKE 'dispute:%%'", (world.shop_a,)
    ).fetchone()
    assert told == (0,)


# --- how a dispute ends (BR-12) -------------------------------------------------------------------------


def test_a_manager_declines_with_a_reason_and_the_customer_is_told(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    dispute = open_dispute(client, world.customer_of_a, link, world.entry_a).json()["id"]

    response = decline(client, world, world.manager_a, dispute, "  Mahsulot   berilgan, imzo bor ")
    assert response.status_code == 200, response.text
    assert (response.json()["status"], response.json()["decline_reason"]) == ("declined", "Mahsulot berilgan, imzo bor")
    assert disputes(owner, world) == [
        (
            world.entry_a,
            "declined",
            "Men buni olmaganman",
            "Mahsulot berilgan, imzo bor",
            world.manager_a_membership,
            True,
        )
    ]
    told = staff_notices(owner, f"dispute:{dispute}:declined")
    assert told == [
        (
            tg(owner, world.customer_of_a),
            {
                "text": say(
                    "uz",
                    "n_dispute_declined",
                    shop="Shop A",
                    amount=money("uz", 50000),
                    reason="Mahsulot berilgan, imzo bor",
                )
            },
        )
    ]
    # Once declined it counts again as an ordinary entry (BR-13), and the outcome stays visible to the customer.
    assert staff_view(client, world)["entries"][0]["disputed"] is False
    mine = client.get(f"{ME}/{link}", headers=as_user(world.customer_of_a)).json()
    assert mine["entries"][0]["dispute"]["status"] == "declined"
    assert mine["entries"][0]["dispute"]["decline_reason"] == "Mahsulot berilgan, imzo bor"

    again = decline(client, world, world.owner_a, dispute)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "DISPUTE_NOT_ALLOWED")
    late = client.post(f"{ME}/{link}/disputes/{dispute}/withdraw", headers=as_user(world.customer_of_a))
    assert late.status_code == 409


@pytest.mark.parametrize("reason", ["", "ab", "x" * 301, None])
def test_declining_needs_a_reason(client: TestClient, world: World, owner: psycopg.Connection, reason: Any) -> None:
    dispute = open_dispute(client, world.customer_of_a, link_of(owner, world.customer_a), world.entry_a).json()["id"]
    assert decline(client, world, world.owner_a, dispute, reason).status_code == 422
    assert disputes(owner, world)[0][1] == "open"


def test_reversing_the_entry_ends_the_dispute(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    link = link_of(owner, world.customer_a)
    dispute = open_dispute(client, world.customer_of_a, link, world.entry_a).json()["id"]
    assert reverse(client, world, world.entry_a, world.owner_a).status_code == 201
    assert disputes(owner, world) == [
        (world.entry_a, "reversed", "Men buni olmaganman", None, world.owner_a_membership, True)
    ]
    assert decline(client, world, world.owner_a, dispute).status_code == 409
    assert client.get(f"{shop(world)}/disputes", headers=as_user(world.owner_a)).json() == {"items": []}


def test_the_customer_withdraws_and_the_managers_are_told(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    dispute = open_dispute(client, world.customer_of_a, link, world.entry_a).json()["id"]

    for user in (world.stranger, world.owner_a):
        assert client.post(f"{ME}/{link}/disputes/{dispute}/withdraw", headers=as_user(user)).status_code == 404
    assert (
        client.post(f"{ME}/{link}/disputes/{uuid.uuid4()}/withdraw", headers=as_user(world.customer_of_a)).status_code
        == 404
    )
    assert disputes(owner, world)[0][1] == "open"

    done = client.post(f"{ME}/{link}/disputes/{dispute}/withdraw", headers=as_user(world.customer_of_a))
    assert (done.status_code, done.json()["status"]) == (200, "withdrawn")
    assert disputes(owner, world) == [(world.entry_a, "withdrawn", "Men buni olmaganman", None, None, True)]
    assert staff_view(client, world)["entries"][0]["disputed"] is False
    told = staff_notices(owner, f"dispute:{dispute}:withdrawn")
    assert [payload["text"] for _, payload in told] == [
        say("uz", "s_dispute_withdrawn", shop="Shop A", name="Ali", amount=money("uz", 50000))
    ] * 2
    again = client.post(f"{ME}/{link}/disputes/{dispute}/withdraw", headers=as_user(world.customer_of_a))
    assert again.status_code == 409


def test_a_customer_cannot_withdraw_another_customers_dispute(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    dispute = open_dispute(client, world.customer_of_a, link_of(owner, world.customer_a), world.entry_a).json()["id"]
    # The waiting person becomes a customer of the same shop, with a link of their own.
    attached = client.post(
        f"{shop(world)}/waiting/{world.waiting_a}/attach",
        json={"customer_id": str(world.settled_customer_a)},
        headers={**as_user(world.seller_a), **key()},
    )
    assert attached.status_code == 200
    other_link = link_of(owner, world.settled_customer_a)
    response = client.post(f"{ME}/{other_link}/disputes/{dispute}/withdraw", headers=as_user(world.waiter))
    assert response.status_code == 404
    assert disputes(owner, world)[0][1] == "open"


def test_managers_list_the_open_disputes_of_their_own_shop(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    first = open_dispute(client, world.customer_of_a, link, world.entry_a).json()["id"]
    second_entry = record(client, world, world.customer_a, "credit", 7000).json()["entry"]["id"]
    second = open_dispute(client, world.customer_of_a, link, second_entry, "Summa noto'g'ri").json()["id"]

    listed = client.get(f"{shop(world)}/disputes", headers=as_user(world.manager_a)).json()["items"]
    assert [(d["id"], d["customer_name"], d["amount"], d["reason"]) for d in listed] == [
        (first, "Ali", 50000, "Men buni olmaganman"),
        (second, "Ali", 7000, "Summa noto'g'ri"),
    ]
    assert client.get(f"/api/v1/shops/{world.shop_b}/disputes", headers=as_user(world.owner_b)).json() == {"items": []}
    decline(client, world, world.manager_a, first)
    remaining = client.get(f"{shop(world)}/disputes", headers=as_user(world.manager_a)).json()["items"]
    assert [d["id"] for d in remaining] == [second]


# --- in the chat ----------------------------------------------------------------------------------------


def _notice(owner: psycopg.Connection, entry: Any) -> dict[str, Any]:
    row = owner.execute(
        "SELECT payload FROM outbox_message WHERE dedupe_key = %s", (f"entry:{entry}:notify",)
    ).fetchone()
    assert row is not None
    return dict(row[0])


def test_only_a_credit_sales_message_offers_to_dispute_and_nothing_asks_to_confirm(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    sale = record(client, world, world.customer_a, "credit", 3000).json()["entry"]["id"]
    payment = record(client, world, world.customer_a, "payment", 1000).json()["entry"]["id"]
    reversal = reverse(client, world, payment).json()["entry"]["id"]
    buttons = _notice(owner, sale)["reply_markup"]["inline_keyboard"]
    assert buttons == [
        [{"text": "⚠️ E'tiroz bildirish", "callback_data": f"v2:dsp:{uuid.UUID(sale).hex}"}],
        [{"text": "📅 Muddatni ko'chirish", "callback_data": f"v2:dmv:{uuid.UUID(sale).hex}"}],
    ]
    assert "reply_markup" not in _notice(owner, payment)
    assert "reply_markup" not in _notice(owner, reversal)


def test_a_dispute_and_its_decline_through_the_chat(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer, manager = chat_of(client, owner, world.customer_of_a), chat_of(client, owner, world.manager_a)
    sale = record(client, world, world.customer_a, "credit", 3000).json()["entry"]["id"]
    press = _notice(owner, sale)["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

    assert customer.press(press).text == say("uz", "ask_dispute_reason")
    assert disputes(owner, world) == [], "asking for the reason stores no dispute"
    assert customer.say("Bu savdo meniki emas").text == say("uz", "dispute_sent", shop="Shop A")
    stored = disputes(owner, world)
    assert [(row[0], row[1], row[2]) for row in stored] == [(uuid.UUID(sale), "open", "Bu savdo meniki emas")]
    # The reason was asked once: the next message is not another dispute.
    assert customer.say("Yana bir gap").text.startswith(say("uz", "accounts_header"))
    assert len(disputes(owner, world)) == 1

    dispute_id = owner.execute("SELECT id FROM dispute WHERE entry_id = %s", (sale,)).fetchone()
    assert dispute_id is not None
    decline_button = f"v2:dcl:{dispute_id[0].hex}"
    assert manager.press(decline_button).text == say("uz", "ask_decline_reason")
    assert manager.say("Daftarda imzo bor").text == say("uz", "dispute_declined_staff")
    assert disputes(owner, world)[0][1:5] == (
        "declined",
        "Bu savdo meniki emas",
        "Daftarda imzo bor",
        world.manager_a_membership,
    )
    told = staff_notices(owner, f"dispute:{dispute_id[0]}:declined")
    assert told[0][1]["text"].endswith("Sabab: Daftarda imzo bor")
    # The manager's next message is an ordinary entry again.
    assert manager.say("Ali 1000").text.startswith("✅ Shop A")


def test_a_reason_that_is_too_short_stores_nothing(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    customer.press(f"v2:dsp:{world.entry_a.hex}")
    assert customer.say("yo").text == say("uz", "reason_invalid")
    assert disputes(owner, world) == []


def test_a_dispute_button_for_someone_elses_entry_leads_nowhere(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    other_entry = record(client, world, world.settled_customer_a, "credit", 9000).json()["entry"]["id"]
    customer = chat_of(client, owner, world.customer_of_a)
    customer.press(f"v2:dsp:{uuid.UUID(other_entry).hex}")
    assert customer.say("Bu meniki emas").text == say("uz", "not_found")
    stranger = chat_of(client, owner, world.stranger)
    stranger.press(f"v2:dsp:{world.entry_a.hex}")
    assert stranger.say("Bu meniki emas").text == say("uz", "not_found")
    assert disputes(owner, world) == []


def test_a_seller_or_another_shops_owner_cannot_decline_from_chat(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    dispute = open_dispute(client, world.customer_of_a, link_of(owner, world.customer_a), world.entry_a).json()["id"]
    button = f"v2:dcl:{uuid.UUID(dispute).hex}"
    seller = chat_of(client, owner, world.seller_a)
    seller.press(button)
    assert seller.say("Rad etaman").text == say("uz", "forbidden")
    outsider = chat_of(client, owner, world.owner_b)
    outsider.press(button)
    assert outsider.say("Rad etaman").text == say("uz", "not_found")
    assert disputes(owner, world)[0][1] == "open"


def test_a_decline_reason_that_is_too_short_declines_nothing(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    dispute = open_dispute(client, world.customer_of_a, link_of(owner, world.customer_a), world.entry_a).json()["id"]
    manager = chat_of(client, owner, world.manager_a)
    manager.press(f"v2:dcl:{uuid.UUID(dispute).hex}")
    assert manager.say("yo").text == say("uz", "reason_invalid")
    assert disputes(owner, world)[0][1] == "open"
