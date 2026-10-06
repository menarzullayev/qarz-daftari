"""Date change requests and the history of promised dates (story S10.3).

REQ-066, REQ-067; domain rule BR-15; INV-9 and INV-15. A customer asks to move the promised date of one
of their own credit entries; a manager or owner accepts or declines, or moves a date directly; a request
ends by itself when its entry is reversed or fully paid.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import day, money, say

from .conftest import World, as_user
from .test_chat import chat_of
from .test_customer_account import ME, link_of
from .test_customers_ledger import detail, key, read, record, reverse, seed_entry, shop, today, write
from .test_disputes import staff_notices, tg
from .test_reminders import manual

pytestmark = pytest.mark.db

SUM = money("uz", 50000)
RU_SUM = money("ru", 50000)


def current(owner: psycopg.Connection, entry: Any) -> date:
    """The entry's promised date now: its newest promise row."""
    row = owner.execute(
        "SELECT promised_date FROM promise WHERE entry_id = %s ORDER BY created_at DESC, id DESC LIMIT 1", (entry,)
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def ask(client: TestClient, user: uuid.UUID, link: Any, entry: Any, requested: Any, **extra: Any) -> Any:
    body = {"entry_id": str(entry), "requested_date": requested.isoformat(), **extra}
    return client.post(f"{ME}/{link}/date-requests", json=body, headers=as_user(user))


def ask_a(client: TestClient, world: World, owner: psycopg.Connection, days: int = 10, **extra: Any) -> Any:
    """The linked customer of shop A asks to move entry_a `days` past its current date."""
    requested = current(owner, world.entry_a) + timedelta(days=days)
    return ask(client, world.customer_of_a, link_of(owner, world.customer_a), world.entry_a, requested, **extra)


def accept(client: TestClient, world: World, user: uuid.UUID, request: Any, headers: Any = None) -> Any:
    path = f"{shop(world)}/date-requests/{request}/accept"
    return client.post(path, headers={**as_user(user), **(headers or key())})


def decline(client: TestClient, world: World, user: uuid.UUID, request: Any, body: Any = None) -> Any:
    path = f"{shop(world)}/date-requests/{request}/decline"
    return client.post(path, json=body, headers={**as_user(user), **key()})


def change(client: TestClient, world: World, user: uuid.UUID, entry: Any, promised: date, **extra: Any) -> Any:
    body = {"promised_date": promised.isoformat(), **extra}
    return write(client, user, "POST", f"{shop(world)}/entries/{entry}/promise", body)


def requests(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT entry_id, requested_date, reason, status, decline_reason, decided_by, closed_at IS NOT NULL "
        "FROM date_change_request WHERE shop_id = %s ORDER BY created_at, id",
        (world.shop_a,),
    ).fetchall()


def statuses(owner: psycopg.Connection, world: World) -> list[str]:
    return [str(row[3]) for row in requests(owner, world)]


def promise_rows(owner: psycopg.Connection, entry: Any) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT actor, promised_date, reason FROM promise WHERE entry_id = %s ORDER BY created_at, id", (entry,)
    ).fetchall()


def told(owner: psycopg.Connection, world: World, prefix: str) -> list[tuple[str, dict[str, Any]]]:
    """Messages queued in shop A whose dedupe key starts with the prefix, as (recipient, payload)."""
    return staff_notices(owner, prefix)


def date_messages(owner: psycopg.Connection, world: World) -> int:
    row = owner.execute(
        "SELECT count(*) FROM outbox_message WHERE shop_id = %s AND "
        "(dedupe_key LIKE 'date-request:%%' OR dedupe_key LIKE '%%:promise-changed:%%')",
        (world.shop_a,),
    ).fetchone()
    assert row is not None
    return int(row[0])


def subscription(owner: psycopg.Connection, world: World, state: str) -> None:
    owner.execute("UPDATE subscription SET state = %s WHERE shop_id = %s", (state, world.shop_a))


def actions(owner: psycopg.Connection, world: World, prefix: str) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT action, actor_kind, actor_id FROM activity WHERE shop_id = %s AND action LIKE %s ORDER BY at, id",
        (world.shop_a, prefix + "%"),
    ).fetchall()


def my_entry(client: TestClient, world: World, owner: psycopg.Connection, entry: Any) -> dict[str, Any]:
    """The entry as the customer sees it on their own page."""
    page = client.get(f"{ME}/{link_of(owner, world.customer_a)}", headers=as_user(world.customer_of_a)).json()
    return next(dict(item) for item in page["entries"] if item["id"] == str(entry))


def staff_entry(client: TestClient, world: World, entry: Any) -> dict[str, Any]:
    return next(dict(item) for item in detail(client, world, world.customer_a)["entries"] if item["id"] == str(entry))


# --- a customer asks (REQ-066) --------------------------------------------------------------------------


def test_a_customer_asks_to_move_a_date_and_the_owner_and_managers_are_told(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    old = current(owner, world.entry_a)
    wanted = old + timedelta(days=10)
    response = ask_a(client, world, owner, reason="  Oylik   kechikdi ")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body == {
        "id": body["id"],
        "entry_id": str(world.entry_a),
        "status": "open",
        "requested_date": wanted.isoformat(),
        "reason": "Oylik kechikdi",
        "decline_reason": None,
        "created_at": body["created_at"],
        "closed_at": None,
    }
    assert requests(owner, world) == [(world.entry_a, wanted, "Oylik kechikdi", "open", None, None, False)]

    # Asking changes nothing by itself (INV-15): the promised date is still the old one, for everyone.
    assert promise_rows(owner, world.entry_a) == [("default", old, None)]
    assert staff_entry(client, world, world.entry_a)["promised_date"] == old.isoformat()
    assert my_entry(client, world, owner, world.entry_a)["promised_date"] == old.isoformat()
    assert staff_entry(client, world, world.entry_a)["date_request"] == body
    assert my_entry(client, world, owner, world.entry_a)["date_request"] == body

    notices = told(owner, world, f"date-request:{body['id']}:opened")
    assert sorted(who for who, _ in notices) == sorted([tg(owner, world.owner_a), tg(owner, world.manager_a)])
    text = (
        f"📅 Shop A\nAli {SUM} nasiyaning to'lash muddatini {day(old)} dan {day(wanted)} ga ko'chirishni "
        "so'rayapti.\nSabab: Oylik kechikdi"
    )
    request_hex = uuid.UUID(body["id"]).hex
    for _, payload in notices:
        assert payload["text"] == text
        assert payload["reply_markup"]["inline_keyboard"] == [
            [
                {"text": "✅ Qabul qilish", "callback_data": f"v2:dok:{request_hex}"},
                {"text": "Rad etish", "callback_data": f"v2:dno:{request_hex}"},
            ]
        ]
    assert date_messages(owner, world) == 2, "the seller and the customer are sent nothing"
    assert actions(owner, world, "date_request.") == [("date_request.opened", "customer", None)]


def test_each_manager_is_told_in_their_own_language(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.manager_a,))
    old = current(owner, world.entry_a)
    body = ask_a(client, world, owner).json()
    texts = dict(told(owner, world, f"date-request:{body['id']}:opened"))
    russian = texts[tg(owner, world.manager_a)]
    assert russian["text"] == (
        f"📅 Shop A\nAli просит перенести срок оплаты долга {RU_SUM} с {day(old)} на {day(old + timedelta(days=10))}."
    )
    assert [button["text"] for button in russian["reply_markup"]["inline_keyboard"][0]] == ["✅ Принять", "Отклонить"]
    assert texts[tg(owner, world.owner_a)]["text"].endswith("ko'chirishni so'rayapti."), "no reason, no reason line"


@pytest.mark.parametrize("extra", [{}, {"reason": None}, {"reason": ""}, {"reason": "  \n "}])
def test_the_reason_is_optional(client: TestClient, world: World, owner: psycopg.Connection, extra: Any) -> None:
    response = ask_a(client, world, owner, **extra)
    assert (response.status_code, response.json()["reason"]) == (201, None)
    assert requests(owner, world)[0][2] is None


def test_a_reason_is_at_most_three_hundred_characters(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    for reason in ("x" * 301, 5, ["a"]):
        response = ask_a(client, world, owner, reason=reason)
        assert response.status_code == 422, response.text
    assert requests(owner, world) == []
    assert ask_a(client, world, owner, reason="x" * 300).status_code == 201


def test_one_request_is_open_per_entry(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    assert ask_a(client, world, owner).status_code == 201
    again = ask_a(client, world, owner, days=20)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "REQUEST_ALREADY_OPEN")
    assert again.json()["error"]["fields"] == {}
    assert len(requests(owner, world)) == 1
    assert date_messages(owner, world) == 2, "the refused request told nobody"

    # Another entry of the same account has a request of its own.
    other = record(client, world, world.customer_a, "credit", 7000).json()["entry"]["id"]
    second = ask(
        client, world.customer_of_a, link_of(owner, world.customer_a), other, current(owner, other) + timedelta(days=1)
    )
    assert second.status_code == 201, second.text
    assert statuses(owner, world) == ["open", "open"]


def test_the_requested_date_must_be_after_the_current_one(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    for days in (0, -1, -30):
        refused = ask_a(client, world, owner, days=days)
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "DATE_REQUEST_NOT_ALLOWED")
        assert refused.json()["error"]["fields"] == {"reason": "not_later"}
    assert requests(owner, world) == []
    assert date_messages(owner, world) == 0
    assert ask_a(client, world, owner, days=1).status_code == 201


def test_the_requested_date_is_within_a_year_of_the_sale(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    too_far = ask(client, world.customer_of_a, link, world.entry_a, today() + timedelta(days=366))
    assert (too_far.status_code, too_far.json()["error"]["code"]) == (422, "VALIDATION")
    assert too_far.json()["error"]["fields"] == {"requested_date": "PROMISE_TOO_FAR"}
    assert requests(owner, world) == []
    assert ask(client, world.customer_of_a, link, world.entry_a, today() + timedelta(days=365)).status_code == 201


def test_a_requested_date_before_the_sale_is_refused(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """Only an imported promise can lie before its entry; "later" is then still not enough."""
    link = link_of(owner, world.customer_a)
    odd = seed_entry(owner, world, world.customer_a, 2, "credit", 4000, promised=today() - timedelta(days=20))
    refused = ask(client, world.customer_of_a, link, odd, today() - timedelta(days=1))
    assert refused.status_code == 422
    assert refused.json()["error"]["fields"] == {"requested_date": "PROMISE_BEFORE_SALE"}
    assert requests(owner, world) == []
    assert ask(client, world.customer_of_a, link, odd, today()).status_code == 201


def test_only_a_debt_that_still_stands_has_a_date_to_move(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    payment = record(client, world, world.customer_a, "payment", 1000).json()["entry"]["id"]
    sale = record(client, world, world.customer_a, "credit", 2000).json()["entry"]["id"]
    reversal = reverse(client, world, sale).json()["entry"]["id"]
    for entry, why in ((payment, "not_a_debt"), (reversal, "not_a_debt"), (sale, "reversed")):
        refused = ask(client, world.customer_of_a, link, entry, today() + timedelta(days=60))
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "DATE_REQUEST_NOT_ALLOWED")
        assert refused.json()["error"]["fields"] == {"reason": why}
    assert requests(owner, world) == []
    assert date_messages(owner, world) == 0


def test_a_fully_paid_entry_has_nothing_to_move(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    """Payments cover the oldest debt first (BR-3): entry_a is settled, the later sale is not."""
    link = link_of(owner, world.customer_a)
    later = record(client, world, world.customer_a, "credit", 7000).json()["entry"]["id"]
    assert record(client, world, world.customer_a, "payment", 50000).status_code == 201
    refused = ask_a(client, world, owner)
    assert (refused.status_code, refused.json()["error"]["fields"]) == (409, {"reason": "fully_paid"})
    assert requests(owner, world) == []

    assert record(client, world, world.customer_a, "payment", 6999).status_code == 201
    still_owed = ask(client, world.customer_of_a, link, later, current(owner, later) + timedelta(days=5))
    assert still_owed.status_code == 201, "one sum of it is still owed"


def test_a_declined_request_cannot_be_repeated_for_seven_days(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    first = ask_a(client, world, owner).json()["id"]
    assert decline(client, world, world.manager_a, first).status_code == 200
    for days in (10, 25):
        again = ask_a(client, world, owner, days=days)
        assert (again.status_code, again.json()["error"]["code"]) == (409, "DATE_REQUEST_NOT_ALLOWED")
        assert again.json()["error"]["fields"] == {"reason": "declined_recently"}
    assert statuses(owner, world) == ["declined"]

    # The decline does not concern another entry of the same account.
    other = record(client, world, world.customer_a, "credit", 7000).json()["entry"]["id"]
    link = link_of(owner, world.customer_a)
    wanted = current(owner, other) + timedelta(days=1)
    assert ask(client, world.customer_of_a, link, other, wanted).status_code == 201

    owner.execute(
        "UPDATE date_change_request SET closed_at = now() - interval '6 days 23 hours 59 minutes' WHERE id = %s",
        (first,),
    )
    assert ask_a(client, world, owner).json()["error"]["fields"] == {"reason": "declined_recently"}
    owner.execute(
        "UPDATE date_change_request SET closed_at = now() - interval '7 days 1 second' WHERE id = %s", (first,)
    )
    assert ask_a(client, world, owner).status_code == 201
    assert statuses(owner, world) == ["declined", "open", "open"]


def test_only_a_decline_makes_the_customer_wait(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    first = ask_a(client, world, owner).json()["id"]
    assert accept(client, world, world.manager_a, first).status_code == 200
    assert ask_a(client, world, owner, days=5).status_code == 201, "after an accepted request another may follow"


def test_nobody_can_ask_about_an_entry_that_is_not_on_their_own_account(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    other_entry = record(client, world, world.settled_customer_a, "credit", 9000).json()["entry"]["id"]
    wanted = today() + timedelta(days=60)
    # Another customer's entry in the same shop, an entry that does not exist, and a link that is not theirs.
    for user, used_link, entry in (
        (world.customer_of_a, link, other_entry),
        (world.customer_of_a, link, uuid.uuid4()),
        (world.stranger, link, world.entry_a),
        (world.owner_a, link, world.entry_a),
        (world.manager_a, link, world.entry_a),
        (world.owner_b, link, world.entry_a),
        (world.customer_of_a, uuid.uuid4(), world.entry_a),
    ):
        response = ask(client, user, used_link, entry, wanted)
        assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND"), response.text
        assert response.json()["error"]["fields"] == {}
    assert (
        client.post(f"{ME}/not-a-uuid/date-requests", json={}, headers=as_user(world.customer_of_a)).status_code == 404
    )
    unsigned = client.post(
        f"{ME}/{link}/date-requests", json={"entry_id": str(world.entry_a), "requested_date": wanted.isoformat()}
    )
    assert unsigned.status_code == 401
    assert requests(owner, world) == []
    assert date_messages(owner, world) == 0


def test_an_ended_link_can_ask_for_nothing(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    link = link_of(owner, world.customer_a)
    owner.execute("UPDATE customer_link SET status = 'ended', ended_at = now() WHERE id = %s", (link,))
    response = ask(client, world.customer_of_a, link, world.entry_a, today() + timedelta(days=60))
    assert response.status_code == 404
    assert requests(owner, world) == []


@pytest.mark.parametrize(
    "body",
    [
        {"requested_date": "2027-01-15"},
        {"entry_id": "not-a-uuid", "requested_date": "2027-01-15"},
        {"entry_id": "ENTRY"},
        {"entry_id": "ENTRY", "requested_date": "ertaga"},
        {"entry_id": "ENTRY", "requested_date": "2027-02-30"},
        {"entry_id": "ENTRY", "requested_date": "2027-01-15", "status": "accepted"},
    ],
)
def test_a_malformed_request_is_refused(
    client: TestClient, world: World, owner: psycopg.Connection, body: dict[str, Any]
) -> None:
    body = {name: str(world.entry_a) if value == "ENTRY" else value for name, value in body.items()}
    link = link_of(owner, world.customer_a)
    response = client.post(f"{ME}/{link}/date-requests", json=body, headers=as_user(world.customer_of_a))
    assert response.status_code == 422, response.text
    assert requests(owner, world) == []


def test_in_limited_mode_a_customer_can_still_ask_but_not_in_a_suspended_shop(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    subscription(owner, world, "suspended")
    refused = ask_a(client, world, owner)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "SHOP_SUSPENDED")
    assert requests(owner, world) == []
    assert date_messages(owner, world) == 0
    subscription(owner, world, "limited")
    assert ask_a(client, world, owner).status_code == 201


# --- a manager or owner decides (REQ-067) ---------------------------------------------------------------


def test_an_accepted_request_sets_the_new_date_and_keeps_the_old_one_in_the_history(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    old = current(owner, world.entry_a)
    wanted = old + timedelta(days=10)
    request = ask_a(client, world, owner, reason="Oylik kechikdi").json()["id"]
    before = date_messages(owner, world)

    response = accept(client, world, world.manager_a, request)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["status"], body["requested_date"]) == (request, "accepted", wanted.isoformat())
    assert (body["customer_id"], body["customer_name"], body["amount"]) == (str(world.customer_a), "Ali", 50000)
    assert body["promised_date"] == wanted.isoformat(), "the entry now carries the requested date"
    assert body["closed_at"] is not None
    assert requests(owner, world) == [
        (world.entry_a, wanted, "Oylik kechikdi", "accepted", None, world.manager_a_membership, True)
    ]
    assert promise_rows(owner, world.entry_a) == [
        ("default", old, None),
        ("customer_request", wanted, "Oylik kechikdi"),
    ]
    assert staff_entry(client, world, world.entry_a)["promised_date"] == wanted.isoformat()
    assert my_entry(client, world, owner, world.entry_a)["promised_date"] == wanted.isoformat()
    assert my_entry(client, world, owner, world.entry_a)["date_request"]["status"] == "accepted"

    assert told(owner, world, f"date-request:{request}:accepted") == [
        (
            tg(owner, world.customer_of_a),
            {
                "text": f"Shop A\n{SUM} nasiya muddatini ko'chirish so'rovingiz qabul qilindi.\n"
                f"Yangi to'lash muddati: {day(wanted)}"
            },
        )
    ]
    assert date_messages(owner, world) == before + 1
    assert actions(owner, world, "date_request.accepted") == [
        ("date_request.accepted", "staff", world.manager_a_membership)
    ]

    # Decided once: neither a second acceptance nor a decline changes anything.
    for again in (accept(client, world, world.owner_a, request), decline(client, world, world.owner_a, request)):
        assert (again.status_code, again.json()["error"]["code"]) == (409, "DATE_REQUEST_NOT_ALLOWED")
        assert again.json()["error"]["fields"] == {"reason": "not_open"}
    assert len(promise_rows(owner, world.entry_a)) == 2
    assert statuses(owner, world) == ["accepted"]
    assert date_messages(owner, world) == before + 1


def test_the_customer_is_told_the_outcome_in_their_own_language(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.customer_of_a,))
    wanted = current(owner, world.entry_a) + timedelta(days=10)
    request = ask_a(client, world, owner).json()["id"]
    accept(client, world, world.owner_a, request)
    assert told(owner, world, f"date-request:{request}:accepted")[0][1] == {
        "text": f"Shop A\nВаша просьба перенести срок оплаты долга {RU_SUM} принята.\nНовый срок оплаты: {day(wanted)}"
    }


def test_a_customer_who_disconnected_is_told_nothing_but_the_decision_stands(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    request = ask_a(client, world, owner).json()["id"]
    owner.execute(
        "UPDATE customer_link SET status = 'ended', ended_at = now() WHERE customer_id = %s", (world.customer_a,)
    )
    assert accept(client, world, world.manager_a, request).status_code == 200
    assert told(owner, world, f"date-request:{request}:accepted") == []
    assert statuses(owner, world) == ["accepted"]


def test_accepting_twice_with_the_same_key_moves_the_date_once(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    request = ask_a(client, world, owner).json()["id"]
    other_entry = record(client, world, world.customer_a, "credit", 7000).json()["entry"]["id"]
    other = ask(
        client,
        world.customer_of_a,
        link_of(owner, world.customer_a),
        other_entry,
        current(owner, other_entry) + timedelta(days=3),
    ).json()["id"]
    same = key()
    first = accept(client, world, world.manager_a, request, same)
    repeat = accept(client, world, world.manager_a, request, same)
    assert (first.status_code, repeat.status_code) == (200, 200)
    assert repeat.json() == first.json()
    assert len(promise_rows(owner, world.entry_a)) == 2
    assert len(told(owner, world, f"date-request:{request}:accepted")) == 1

    reused = accept(client, world, world.manager_a, other, same)
    assert (reused.status_code, reused.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert statuses(owner, world) == ["accepted", "open"]
    assert len(promise_rows(owner, other_entry)) == 1


def test_a_declined_request_changes_no_date_and_the_customer_is_told_why(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    old = current(owner, world.entry_a)
    wanted = old + timedelta(days=10)
    request = ask_a(client, world, owner, reason="Oylik kechikdi").json()["id"]

    response = decline(client, world, world.owner_a, request, {"reason": "  Muddat   ikki marta uzaytirilgan "})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["decline_reason"]) == ("declined", "Muddat ikki marta uzaytirilgan")
    assert body["promised_date"] == old.isoformat()
    assert requests(owner, world) == [
        (
            world.entry_a,
            wanted,
            "Oylik kechikdi",
            "declined",
            "Muddat ikki marta uzaytirilgan",
            world.owner_a_membership,
            True,
        )
    ]
    assert promise_rows(owner, world.entry_a) == [("default", old, None)]
    assert staff_entry(client, world, world.entry_a)["promised_date"] == old.isoformat()
    assert told(owner, world, f"date-request:{request}:declined") == [
        (
            tg(owner, world.customer_of_a),
            {
                "text": f"Shop A\n{SUM} nasiya muddatini {day(wanted)} ga ko'chirish so'rovingiz rad etildi.\n"
                "Sabab: Muddat ikki marta uzaytirilgan"
            },
        )
    ]
    assert actions(owner, world, "date_request.declined") == [
        ("date_request.declined", "staff", world.owner_a_membership)
    ]
    mine = my_entry(client, world, owner, world.entry_a)["date_request"]
    assert (mine["status"], mine["decline_reason"]) == ("declined", "Muddat ikki marta uzaytirilgan")

    for again in (decline(client, world, world.owner_a, request), accept(client, world, world.owner_a, request)):
        assert (again.status_code, again.json()["error"]["fields"]) == (409, {"reason": "not_open"})
    assert len(promise_rows(owner, world.entry_a)) == 1


@pytest.mark.parametrize("body", [None, {}, {"reason": None}, {"reason": "   "}])
def test_declining_needs_no_reason(client: TestClient, world: World, owner: psycopg.Connection, body: Any) -> None:
    wanted = current(owner, world.entry_a) + timedelta(days=10)
    request = ask_a(client, world, owner).json()["id"]
    response = decline(client, world, world.manager_a, request, body)
    assert (response.status_code, response.json()["decline_reason"]) == (200, None)
    assert requests(owner, world)[0][3:5] == ("declined", None)
    assert told(owner, world, f"date-request:{request}:declined")[0][1] == {
        "text": f"Shop A\n{SUM} nasiya muddatini {day(wanted)} ga ko'chirish so'rovingiz rad etildi."
    }


@pytest.mark.parametrize("body", [{"reason": "x" * 301}, {"reason": 5}, {"why": "because"}])
def test_a_decline_reason_that_is_not_one_declines_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, body: Any
) -> None:
    request = ask_a(client, world, owner).json()["id"]
    assert decline(client, world, world.manager_a, request, body).status_code == 422
    assert statuses(owner, world) == ["open"]
    assert told(owner, world, f"date-request:{request}:declined") == []


def test_declining_twice_with_the_same_key_tells_the_customer_once(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    request = ask_a(client, world, owner).json()["id"]
    path = f"{shop(world)}/date-requests/{request}/decline"
    headers = {**as_user(world.manager_a), **key()}
    first = client.post(path, json={"reason": "Yo'q"}, headers=headers)
    repeat = client.post(path, json={"reason": "Yo'q"}, headers=headers)
    assert (first.status_code, repeat.status_code) == (200, 200)
    assert repeat.json() == first.json()
    assert len(told(owner, world, f"date-request:{request}:declined")) == 1
    changed = client.post(path, json={"reason": "Boshqa sabab"}, headers=headers)
    assert (changed.status_code, changed.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert requests(owner, world)[0][4] == "Yo'q"


def test_a_request_of_another_shop_does_not_exist(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    request = ask_a(client, world, owner).json()["id"]
    for target in (request, uuid.uuid4()):
        for action in ("accept", "decline"):
            # The owner of shop B through their own shop; the owner of shop A with an unknown request.
            who, where = (world.owner_b, world.shop_b) if target == request else (world.owner_a, world.shop_a)
            response = client.post(
                f"/api/v1/shops/{where}/date-requests/{target}/{action}", headers={**as_user(who), **key()}
            )
            assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert statuses(owner, world) == ["open"]
    assert len(promise_rows(owner, world.entry_a)) == 1


def test_managers_list_the_open_requests_of_their_own_shop_oldest_first(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    old = current(owner, world.entry_a)
    first = ask_a(client, world, owner, reason="Oylik kechikdi").json()
    second_entry = record(client, world, world.customer_a, "credit", 7000).json()["entry"]["id"]
    second = ask(client, world.customer_of_a, link, second_entry, current(owner, second_entry) + timedelta(days=3))

    listed = client.get(f"{shop(world)}/date-requests", headers=as_user(world.manager_a)).json()["items"]
    assert listed[0] == {
        **first,
        "customer_id": str(world.customer_a),
        "customer_name": "Ali",
        "amount": 50000,
        "promised_date": old.isoformat(),
    }
    assert [(item["id"], item["amount"], item["reason"]) for item in listed] == [
        (first["id"], 50000, "Oylik kechikdi"),
        (second.json()["id"], 7000, None),
    ]
    theirs = client.get(f"/api/v1/shops/{world.shop_b}/date-requests", headers=as_user(world.owner_b))
    assert theirs.json() == {"items": []}

    decline(client, world, world.manager_a, first["id"])
    remaining = client.get(f"{shop(world)}/date-requests", headers=as_user(world.owner_a)).json()["items"]
    assert [item["id"] for item in remaining] == [second.json()["id"]]


def test_deciding_and_changing_work_in_limited_mode_and_stop_in_a_suspended_shop(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    old = current(owner, world.entry_a)
    request = ask_a(client, world, owner).json()["id"]
    subscription(owner, world, "suspended")
    refused = [
        accept(client, world, world.owner_a, request),
        decline(client, world, world.owner_a, request),
        change(client, world, world.owner_a, world.entry_a, old + timedelta(days=2)),
        client.get(f"{shop(world)}/date-requests", headers=as_user(world.manager_a)),
    ]
    for response in refused:
        assert (response.status_code, response.json()["error"]["code"]) == (403, "SHOP_SUSPENDED")
    assert statuses(owner, world) == ["open"]
    assert promise_rows(owner, world.entry_a) == [("default", old, None)]
    # BR-30: the owner may still look.
    seen = client.get(f"{shop(world)}/date-requests", headers=as_user(world.owner_a))
    assert [item["id"] for item in seen.json()["items"]] == [request]

    subscription(owner, world, "limited")
    assert change(client, world, world.manager_a, world.entry_a, old + timedelta(days=2)).status_code == 200
    assert accept(client, world, world.manager_a, request).status_code == 200
    other = record(client, world, world.customer_a, "payment", 1000)  # payments go on as well
    assert other.status_code == 201
    assert [row[0] for row in promise_rows(owner, world.entry_a)] == ["default", "staff", "customer_request"]


# --- the accepted date drives overdue status (BR-4, BR-15) ----------------------------------------------


def test_overdue_status_follows_the_accepted_date(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    link = link_of(owner, world.customer_a)
    late = seed_entry(
        owner, world, world.customer_a, 2, "credit", 8000, promised=today() - timedelta(days=3), days_ago=40
    )

    def overdue() -> tuple[Any, ...]:
        mine = client.get(f"{ME}/{link}", headers=as_user(world.customer_of_a)).json()["overdue"]["amount"]
        staff = detail(client, world, world.customer_a)["overdue"]
        whole_shop = read(client, world.seller_a, f"{shop(world)}/overview").json()["overdue"]
        return (mine, staff["amount"], staff["since"], whole_shop["amount"], whole_shop["customers"])

    was = (8000, 8000, (today() - timedelta(days=3)).isoformat(), 8000, 1)
    assert overdue() == was
    request = ask(client, world.customer_of_a, link, late, today() + timedelta(days=5)).json()["id"]
    assert overdue() == was, "a request moves nothing until it is accepted (INV-15)"

    assert accept(client, world, world.manager_a, request).status_code == 200
    assert overdue() == (0, 0, None, 0, 0)
    assert detail(client, world, world.customer_a)["balance"] == 58000, "the debt itself is unchanged"


def test_a_declined_request_leaves_the_entry_overdue(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    late = seed_entry(
        owner, world, world.customer_a, 2, "credit", 8000, promised=today() - timedelta(days=3), days_ago=40
    )
    request = ask(client, world.customer_of_a, link, late, today() + timedelta(days=5)).json()["id"]
    decline(client, world, world.manager_a, request)
    assert detail(client, world, world.customer_a)["overdue"]["amount"] == 8000


def test_a_date_moved_back_by_the_shop_makes_the_debt_due_or_overdue(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    old = seed_entry(
        owner, world, world.customer_a, 2, "credit", 8000, promised=today() + timedelta(days=9), days_ago=40
    )
    assert change(client, world, world.manager_a, old, today()).status_code == 200
    status = detail(client, world, world.customer_a)["overdue"]
    assert (status["amount"], status["due_today"]) == (0, 8000)
    assert change(client, world, world.manager_a, old, today() - timedelta(days=1)).status_code == 200
    status = detail(client, world, world.customer_a)["overdue"]
    assert (status["amount"], status["due_today"], status["days"]) == (8000, 0, 1)


# --- requests close by themselves ----------------------------------------------------------------------


def test_reversing_the_entry_expires_its_open_request(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    wanted = current(owner, world.entry_a) + timedelta(days=10)
    request = ask_a(client, world, owner).json()["id"]
    before = date_messages(owner, world)
    assert reverse(client, world, world.entry_a, world.owner_a).status_code == 201
    assert requests(owner, world) == [(world.entry_a, wanted, None, "expired", None, None, True)]
    assert client.get(f"{shop(world)}/date-requests", headers=as_user(world.owner_a)).json() == {"items": []}
    for late in (accept(client, world, world.owner_a, request), decline(client, world, world.owner_a, request)):
        assert (late.status_code, late.json()["error"]["fields"]) == (409, {"reason": "not_open"})
    assert len(promise_rows(owner, world.entry_a)) == 1
    assert date_messages(owner, world) == before, "expiry is silent: the reversal has its own message"
    assert my_entry(client, world, owner, world.entry_a)["date_request"]["status"] == "expired"


def test_the_payment_that_settles_the_entry_expires_its_open_request(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    later = record(client, world, world.customer_a, "credit", 7000).json()["entry"]["id"]
    ask_a(client, world, owner)
    ask(client, world.customer_of_a, link, later, current(owner, later) + timedelta(days=3))

    assert record(client, world, world.customer_a, "payment", 49900).status_code == 201
    assert statuses(owner, world) == ["open", "open"], "a hundred sums of entry_a are still owed"
    assert record(client, world, world.customer_a, "payment", 100).status_code == 201
    assert statuses(owner, world) == ["expired", "open"], "entry_a is settled; the later sale is not"
    assert requests(owner, world)[0][5:] == (None, True)
    assert record(client, world, world.customer_a, "payment", 7000).status_code == 201
    assert statuses(owner, world) == ["expired", "expired"]
    listed = client.get(f"{shop(world)}/date-requests", headers=as_user(world.owner_a)).json()
    assert listed == {"items": []}


def test_a_reversed_credit_frees_its_payment_and_so_can_settle_a_later_entry(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The entry that becomes fully paid is not the one reversed: the allocation decides, not the entry id."""
    link = link_of(owner, world.customer_a)
    later = record(client, world, world.customer_a, "credit", 60000).json()["entry"]["id"]
    assert ask(client, world.customer_of_a, link, later, current(owner, later) + timedelta(days=3)).status_code == 201
    assert record(client, world, world.customer_a, "payment", 60000).status_code == 201
    assert statuses(owner, world) == ["open"], "the payment covers entry_a and only part of the later sale"

    assert reverse(client, world, world.entry_a, world.owner_a).status_code == 201
    assert detail(client, world, world.customer_a)["balance"] == 0
    assert statuses(owner, world) == ["expired"]


def test_a_new_sale_or_a_reversed_payment_expires_nothing_and_an_expired_request_can_be_made_again(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    ask_a(client, world, owner)
    assert record(client, world, world.customer_a, "credit", 3000).status_code == 201
    assert statuses(owner, world) == ["open"]

    payment = record(client, world, world.customer_a, "payment", 50000).json()["entry"]["id"]
    assert statuses(owner, world) == ["expired"]
    assert reverse(client, world, payment, world.owner_a).status_code == 201
    assert statuses(owner, world) == ["expired"], "what has expired stays expired"
    # The debt stands again, and nothing was declined: the customer may ask at once.
    assert ask_a(client, world, owner).status_code == 201
    assert statuses(owner, world) == ["expired", "open"]


def test_a_payment_recorded_through_the_chat_expires_the_request_too(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    ask_a(client, world, owner)
    seller = chat_of(client, owner, world.seller_a)
    assert seller.say("Ali -50000").text.startswith("✅ Shop A")
    assert statuses(owner, world) == ["expired"]


# --- a manager or owner changes a date directly (REQ-067) -----------------------------------------------


def test_a_manager_moves_a_date_and_the_customer_is_told(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    old = current(owner, world.entry_a)
    new = old + timedelta(days=14)
    response = change(client, world, world.manager_a, world.entry_a, new, reason="  Telefonda   kelishildi ")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entry"] == {
        "id": str(world.entry_a),
        "amount": 50000,
        "promised_date": new.isoformat(),
        "previous_date": old.isoformat(),
    }
    assert (body["customer"]["id"], body["customer"]["balance"]) == (str(world.customer_a), 50000)
    assert body["date_request"] is None
    assert promise_rows(owner, world.entry_a) == [("default", old, None), ("staff", new, "Telefonda kelishildi")]
    assert staff_entry(client, world, world.entry_a)["promised_date"] == new.isoformat()
    assert my_entry(client, world, owner, world.entry_a)["promised_date"] == new.isoformat()
    assert told(owner, world, f"entry:{world.entry_a}:promise-changed") == [
        (
            tg(owner, world.customer_of_a),
            {
                "text": f"Shop A\nAli, {SUM} nasiyaning to'lash muddati o'zgartirildi: {day(old)} → {day(new)}\n"
                "Sabab: Telefonda kelishildi"
            },
        )
    ]
    assert actions(owner, world, "ledger.promise_changed") == [
        ("ledger.promise_changed", "staff", world.manager_a_membership)
    ]


def test_a_date_can_be_moved_back_and_forth_and_every_change_is_kept_and_told(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    old = current(owner, world.entry_a)
    earlier, later = old - timedelta(days=3), old + timedelta(days=3)
    for chosen in (earlier, later, earlier):
        assert change(client, world, world.owner_a, world.entry_a, chosen).status_code == 200
    assert promise_rows(owner, world.entry_a) == [
        ("default", old, None),
        ("staff", earlier, None),
        ("staff", later, None),
        ("staff", earlier, None),
    ]
    texts = [payload["text"] for _, payload in told(owner, world, f"entry:{world.entry_a}:promise-changed")]
    assert sorted(texts) == sorted(
        [
            f"Shop A\nAli, {SUM} nasiyaning to'lash muddati o'zgartirildi: {day(old)} → {day(earlier)}",
            f"Shop A\nAli, {SUM} nasiyaning to'lash muddati o'zgartirildi: {day(earlier)} → {day(later)}",
            f"Shop A\nAli, {SUM} nasiyaning to'lash muddati o'zgartirildi: {day(later)} → {day(earlier)}",
        ]
    )


def test_a_date_can_be_changed_long_after_the_sale_and_on_a_paid_entry(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """Unlike the one-tap choice, which is open for a day and only while the default stands."""
    old = seed_entry(
        owner, world, world.customer_a, 2, "opening", 8000, promised=today() - timedelta(days=3), days_ago=90
    )
    body = {"promised_date": (today() + timedelta(days=5)).isoformat()}
    choice = write(client, world.manager_a, "POST", f"{shop(world)}/entries/{old}/promise-choice", body)
    assert (choice.status_code, choice.json()["error"]["code"]) == (409, "PROMISE_ALREADY_SET")
    assert change(client, world, world.manager_a, old, today() + timedelta(days=5)).status_code == 200
    assert record(client, world, world.customer_a, "payment", 58000).status_code == 201
    assert change(client, world, world.manager_a, old, today() + timedelta(days=6)).status_code == 200
    assert [row[0] for row in promise_rows(owner, old)] == ["staff", "staff", "staff"]


def test_a_changed_date_is_checked(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    old = current(owner, world.entry_a)
    for chosen, problem in (
        (old, "PROMISE_UNCHANGED"),
        (today() - timedelta(days=1), "PROMISE_BEFORE_SALE"),
        (today() + timedelta(days=366), "PROMISE_TOO_FAR"),
    ):
        refused = change(client, world, world.manager_a, world.entry_a, chosen)
        assert (refused.status_code, refused.json()["error"]["code"]) == (422, "VALIDATION")
        assert refused.json()["error"]["fields"] == {"promised_date": problem}
    for body in ({}, {"promised_date": "ertaga"}, {"promised_date": old.isoformat(), "actor": "default"}):
        malformed = write(client, world.manager_a, "POST", f"{shop(world)}/entries/{world.entry_a}/promise", body)
        assert malformed.status_code == 422
    for reason in ("x" * 301, 5):
        assert (
            change(client, world, world.manager_a, world.entry_a, old + timedelta(days=1), reason=reason).status_code
            == 422
        )
    assert promise_rows(owner, world.entry_a) == [("default", old, None)]
    assert date_messages(owner, world) == 0

    assert change(client, world, world.manager_a, world.entry_a, today()).status_code == 200
    assert (
        change(
            client, world, world.manager_a, world.entry_a, today() + timedelta(days=365), reason="x" * 300
        ).status_code
        == 200
    )


def test_only_a_debt_that_still_stands_has_a_date_to_change(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    payment = record(client, world, world.customer_a, "payment", 1000).json()["entry"]["id"]
    sale = record(client, world, world.customer_a, "credit", 2000).json()["entry"]["id"]
    reversal = reverse(client, world, sale).json()["entry"]["id"]
    promises = owner.execute("SELECT count(*) FROM promise WHERE shop_id = %s", (world.shop_a,)).fetchone()
    for entry, why in ((payment, "not_a_debt"), (reversal, "not_a_debt"), (sale, "reversed")):
        refused = change(client, world, world.owner_a, entry, today() + timedelta(days=60))
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "PROMISE_NOT_CHANGEABLE")
        assert refused.json()["error"]["fields"] == {"reason": why}
    missing = change(client, world, world.owner_a, uuid.uuid4(), today() + timedelta(days=60))
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "NOT_FOUND")
    assert owner.execute("SELECT count(*) FROM promise WHERE shop_id = %s", (world.shop_a,)).fetchone() == promises
    assert date_messages(owner, world) == 0


def test_changing_twice_with_the_same_key_changes_once(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    new = current(owner, world.entry_a) + timedelta(days=4)
    path = f"{shop(world)}/entries/{world.entry_a}/promise"
    headers = {**as_user(world.manager_a), **key()}
    first = client.post(path, json={"promised_date": new.isoformat()}, headers=headers)
    repeat = client.post(path, json={"promised_date": new.isoformat()}, headers=headers)
    assert (first.status_code, repeat.status_code) == (200, 200)
    assert repeat.json() == first.json()
    assert len(promise_rows(owner, world.entry_a)) == 2
    assert len(told(owner, world, f"entry:{world.entry_a}:promise-changed")) == 1
    other = client.post(path, json={"promised_date": (new + timedelta(days=1)).isoformat()}, headers=headers)
    assert (other.status_code, other.json()["error"]["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")


@pytest.mark.parametrize("days_past_requested", [0, 5])
def test_a_direct_change_to_the_requested_date_or_later_closes_the_request_as_accepted(
    client: TestClient, world: World, owner: psycopg.Connection, days_past_requested: int
) -> None:
    old = current(owner, world.entry_a)
    wanted = old + timedelta(days=10)
    request = ask_a(client, world, owner).json()["id"]
    before = date_messages(owner, world)

    response = change(client, world, world.manager_a, world.entry_a, wanted + timedelta(days=days_past_requested))
    assert response.status_code == 200, response.text
    closed = response.json()["date_request"]
    assert (closed["id"], closed["status"], closed["requested_date"]) == (request, "accepted", wanted.isoformat())
    assert requests(owner, world) == [(world.entry_a, wanted, None, "accepted", None, world.manager_a_membership, True)]
    assert [row[0] for row in promise_rows(owner, world.entry_a)] == ["default", "staff"]
    # One message about the new date; no second one about the request.
    assert date_messages(owner, world) == before + 1
    assert told(owner, world, f"date-request:{request}:accepted") == []
    late = accept(client, world, world.owner_a, request)
    assert (late.status_code, late.json()["error"]["fields"]) == (409, {"reason": "not_open"})


def test_a_direct_change_to_an_earlier_date_leaves_the_request_open(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    old = current(owner, world.entry_a)
    wanted = old + timedelta(days=10)
    request = ask_a(client, world, owner).json()["id"]
    for chosen in (wanted - timedelta(days=1), old - timedelta(days=2)):
        response = change(client, world, world.manager_a, world.entry_a, chosen)
        assert (response.status_code, response.json()["date_request"]) == (200, None)
        assert statuses(owner, world) == ["open"]

    assert accept(client, world, world.owner_a, request).status_code == 200
    assert promise_rows(owner, world.entry_a)[-1] == ("customer_request", wanted, None)
    assert current(owner, world.entry_a) == wanted


def test_a_change_on_one_entry_does_not_close_the_request_of_another(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    ask_a(client, world, owner)
    other = record(client, world, world.customer_a, "credit", 7000).json()["entry"]["id"]
    response = change(client, world, world.manager_a, other, today() + timedelta(days=200))
    assert (response.status_code, response.json()["date_request"]) == (200, None)
    assert statuses(owner, world) == ["open"]


def test_a_one_tap_choice_that_reaches_the_requested_date_leaves_nothing_to_ask(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    sales = [record(client, world, world.customer_a, "credit", 7000).json()["entry"]["id"] for _ in range(2)]
    wanted = today() + timedelta(days=35)
    for sale in sales:
        assert ask(client, world.customer_of_a, link, sale, wanted).status_code == 201

    def choose(sale: Any, days: int) -> Any:
        body = {"promised_date": (today() + timedelta(days=days)).isoformat()}
        return write(client, world.seller_a, "POST", f"{shop(world)}/entries/{sale}/promise-choice", body)

    assert choose(sales[0], 34).status_code == 200
    assert choose(sales[1], 35).status_code == 200
    # Not a decision by anyone: the second request simply has nothing more to ask for.
    assert [row[3:] for row in requests(owner, world)] == [("open", None, None, False), ("expired", None, None, True)]


# --- the history of promised dates (INV-9) --------------------------------------------------------------


def test_every_promised_date_an_entry_has_carried_is_shown_oldest_first(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    sale = record(client, world, world.customer_a, "credit", 7000).json()["entry"]["id"]
    default = current(owner, sale)
    chosen, wanted, final = default + timedelta(days=1), default + timedelta(days=9), default - timedelta(days=4)
    write(
        client,
        world.seller_a,
        "POST",
        f"{shop(world)}/entries/{sale}/promise-choice",
        {"promised_date": chosen.isoformat()},
    )
    request = ask(client, world.customer_of_a, link, sale, wanted, reason="Oylik kechikdi").json()["id"]
    accept(client, world, world.manager_a, request)
    change(client, world, world.owner_a, sale, final, reason="Kelishuv o'zgardi")
    payment = record(client, world, world.customer_a, "payment", 1000).json()["entry"]["id"]

    expected = [
        ("default", default.isoformat(), None),
        ("staff", chosen.isoformat(), None),
        ("customer_request", wanted.isoformat(), "Oylik kechikdi"),
        ("staff", final.isoformat(), "Kelishuv o'zgardi"),
    ]
    for seen in (staff_entry(client, world, sale), my_entry(client, world, owner, sale)):
        history = seen["promises"]
        assert [(row["actor"], row["promised_date"], row["reason"]) for row in history] == expected
        assert all(set(row) == {"promised_date", "actor", "reason", "created_at"} for row in history)
        assert [row["created_at"] for row in history] == sorted(row["created_at"] for row in history)
        assert seen["promised_date"] == history[-1]["promised_date"] == final.isoformat()
    # An entry without a promised date has no history, and entry_a's is its own.
    assert staff_entry(client, world, payment)["promises"] == []
    assert my_entry(client, world, owner, payment)["promises"] == []
    assert [row["actor"] for row in staff_entry(client, world, world.entry_a)["promises"]] == ["default"]
    assert staff_entry(client, world, payment)["date_request"] is None
    assert staff_entry(client, world, world.entry_a)["date_request"] is None


def test_the_pages_show_the_newest_request_of_an_entry(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    first = ask_a(client, world, owner).json()["id"]
    decline(client, world, world.manager_a, first, {"reason": "Hozircha yo'q"})
    owner.execute("UPDATE date_change_request SET closed_at = now() - interval '8 days' WHERE id = %s", (first,))
    second = ask_a(client, world, owner, days=4).json()["id"]
    for seen in (staff_entry(client, world, world.entry_a), my_entry(client, world, owner, world.entry_a)):
        assert (seen["date_request"]["id"], seen["date_request"]["status"]) == (second, "open")
        assert seen["date_request"]["decline_reason"] is None


# --- in the chat ----------------------------------------------------------------------------------------


def _notice(owner: psycopg.Connection, entry: Any) -> dict[str, Any]:
    row = owner.execute(
        "SELECT payload FROM outbox_message WHERE dedupe_key = %s", (f"entry:{entry}:notify",)
    ).fetchone()
    assert row is not None
    return dict(row[0])


def _typed(value: date) -> str:
    return f"{value.day:02d}.{value.month:02d}"


def _sale(client: TestClient, world: World, owner: psycopg.Connection) -> tuple[str, str, date]:
    """A credit sale to the linked customer: its id, its "move the date" button, and its promised date."""
    sale = record(client, world, world.customer_a, "credit", 3000).json()["entry"]["id"]
    return sale, _notice(owner, sale)["reply_markup"]["inline_keyboard"][1][0]["callback_data"], current(owner, sale)


def test_only_a_credit_sales_message_offers_to_move_the_date(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    sale = record(client, world, world.customer_a, "credit", 3000).json()["entry"]["id"]
    payment = record(client, world, world.customer_a, "payment", 1000).json()["entry"]["id"]
    rows = _notice(owner, sale)["reply_markup"]["inline_keyboard"]
    assert rows[1] == [{"text": "📅 Muddatni ko'chirish", "callback_data": f"v2:dmv:{uuid.UUID(sale).hex}"}]
    assert len(rows) == 2
    assert "reply_markup" not in _notice(owner, payment)


def test_a_request_and_its_acceptance_through_the_chat(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer, manager = chat_of(client, owner, world.customer_of_a), chat_of(client, owner, world.manager_a)
    sale, button, old = _sale(client, world, owner)
    wanted = old + timedelta(days=10)

    assert customer.press(button).text == say("uz", "ask_move_date")
    assert requests(owner, world) == [], "asking for the date stores no request"
    assert customer.say("32.13").text == say("uz", "move_date_invalid")
    assert customer.say("ertaga").text == say("uz", "move_date_invalid")
    assert requests(owner, world) == []
    # The question is still open after a mistyped date.
    sent = customer.say(_typed(wanted))
    assert sent.text == (
        f"So'rovingiz «Shop A» do'koniga yuborildi: to'lash muddatini {day(wanted)} ga ko'chirish. "
        "Javobi shu yerga keladi."
    )
    assert requests(owner, world) == [(uuid.UUID(sale), wanted, None, "open", None, None, False)]
    # The date was asked for once: the next message is not another request.
    assert customer.say(_typed(wanted + timedelta(days=1))).text.startswith(say("uz", "accounts_header"))
    assert len(requests(owner, world)) == 1

    request_id = owner.execute("SELECT id FROM date_change_request WHERE entry_id = %s", (sale,)).fetchone()
    assert request_id is not None
    notice = dict(told(owner, world, f"date-request:{request_id[0]}:opened"))[tg(owner, world.manager_a)]
    accept_button = notice["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    pressed = manager.press(accept_button)
    assert pressed.text == f"✅ Ali: {money('uz', 3000)} nasiyaning to'lash muddati {day(wanted)} ga ko'chirildi."
    assert pressed.payloads[0]["method"] == "editMessageText", "the notice and its buttons are replaced"
    assert pressed.buttons == {}
    assert requests(owner, world) == [
        (uuid.UUID(sale), wanted, None, "accepted", None, world.manager_a_membership, True)
    ]
    assert promise_rows(owner, sale) == [("default", old, None), ("customer_request", wanted, None)]
    assert len(told(owner, world, f"date-request:{request_id[0]}:accepted")) == 1

    # Pressed again, by the manager or by the owner: nothing more happens.
    for again in (manager, chat_of(client, owner, world.owner_a)):
        late = again.press(accept_button)
        assert late.text == say("uz", "date_request_closed")
        assert late.payloads[-1]["method"] == "editMessageReplyMarkup"
    assert len(promise_rows(owner, sale)) == 2
    # The manager's next message is an ordinary entry.
    assert manager.say("Ali 1000").text.startswith("✅ Shop A")


def test_a_decline_through_the_chat_needs_one_press_and_the_customer_must_then_wait(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer, owner_chat = chat_of(client, owner, world.customer_of_a), chat_of(client, owner, world.owner_a)
    sale, button, old = _sale(client, world, owner)
    wanted = old + timedelta(days=10)
    customer.press(button)
    customer.say(_typed(wanted))
    request_id = owner.execute("SELECT id FROM date_change_request WHERE entry_id = %s", (sale,)).fetchone()
    assert request_id is not None

    pressed = owner_chat.press(f"v2:dno:{request_id[0].hex}")
    assert pressed.text == f"So'rov rad etildi: Ali, {money('uz', 3000)}. To'lash muddati o'zgarmadi."
    assert requests(owner, world) == [(uuid.UUID(sale), wanted, None, "declined", None, world.owner_a_membership, True)]
    assert promise_rows(owner, sale) == [("default", old, None)]
    assert told(owner, world, f"date-request:{request_id[0]}:declined")[0][1] == {
        "text": f"Shop A\n{money('uz', 3000)} nasiya muddatini {day(wanted)} ga ko'chirish so'rovingiz rad etildi."
    }

    customer.press(button)
    assert customer.say(_typed(wanted)).text == say("uz", "date_declined_recently")
    assert statuses(owner, world) == ["declined"]


def test_the_chat_tells_the_customer_why_a_request_was_not_taken(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    _, button, old = _sale(client, world, owner)

    def answer(text: str) -> str:
        customer.press(button)
        return customer.say(text).text

    assert answer(_typed(old)) == say("uz", "date_not_later")
    assert answer(f"{_typed(old)}.{old.year + 2}") == say("uz", "PROMISE_TOO_FAR")
    assert requests(owner, world) == []
    assert answer(_typed(old + timedelta(days=3))).startswith("So'rovingiz «Shop A»")
    assert answer(_typed(old + timedelta(days=4))) == say("uz", "REQUEST_ALREADY_OPEN")
    assert len(requests(owner, world)) == 1

    assert record(client, world, world.customer_a, "payment", 53000).status_code == 201
    assert answer(_typed(old + timedelta(days=4))) == say("uz", "date_fully_paid")
    assert statuses(owner, world) == ["expired"]


def test_a_move_button_for_someone_elses_entry_leads_nowhere(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    other_entry = record(client, world, world.settled_customer_a, "credit", 9000).json()["entry"]["id"]
    wanted = _typed(today() + timedelta(days=60))
    customer = chat_of(client, owner, world.customer_of_a)
    customer.press(f"v2:dmv:{uuid.UUID(other_entry).hex}")
    assert customer.say(wanted).text == say("uz", "not_found")
    stranger = chat_of(client, owner, world.stranger)
    stranger.press(f"v2:dmv:{world.entry_a.hex}")
    assert stranger.say(wanted).text == say("uz", "not_found")
    # A button that carries no entry asks nothing.
    assert customer.press("v2:dmv:zzz").payloads[0]["method"] == "editMessageReplyMarkup"
    assert customer.say(wanted).text.startswith(say("uz", "accounts_header"))
    assert requests(owner, world) == []


def test_a_seller_or_another_shops_owner_cannot_decide_from_the_chat(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    request = uuid.UUID(ask_a(client, world, owner).json()["id"])
    for action in ("dok", "dno"):
        assert chat_of(client, owner, world.seller_a).press(f"v2:{action}:{request.hex}").text == say("uz", "forbidden")
        assert chat_of(client, owner, world.owner_b).press(f"v2:{action}:{request.hex}").text == say("uz", "not_found")
        assert chat_of(client, owner, world.customer_of_a).press(f"v2:{action}:{request.hex}").text == say(
            "uz", "not_found"
        )
        assert chat_of(client, owner, world.owner_a).press(f"v2:{action}:{uuid.uuid4().hex}").text == say(
            "uz", "not_found"
        )
    assert statuses(owner, world) == ["open"]
    assert len(promise_rows(owner, world.entry_a)) == 1


def test_a_suspended_shop_decides_nothing_from_the_chat(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    request = uuid.UUID(ask_a(client, world, owner).json()["id"])
    subscription(owner, world, "suspended")
    manager = chat_of(client, owner, world.manager_a)
    for action in ("dok", "dno"):
        pressed = manager.press(f"v2:{action}:{request.hex}")
        assert pressed.text == say("uz", "SHOP_SUSPENDED")
        assert len(pressed.payloads) == 1, "the buttons stay: the request is still open"
    assert statuses(owner, world) == ["open"]
    customer = chat_of(client, owner, world.customer_of_a)
    customer.press(f"v2:dmv:{world.entry_a.hex}")
    assert customer.say(_typed(today() + timedelta(days=60))).text == say("uz", "SHOP_SUSPENDED")
    assert len(requests(owner, world)) == 1


# --- the schema (migration 0011) ------------------------------------------------------------------------


def test_reasons_are_bounded_by_the_database_as_well(owner: psycopg.Connection, world: World) -> None:
    request = (
        "INSERT INTO date_change_request (id, shop_id, entry_id, requested_date, status, reason, decline_reason) "
        "VALUES (%s, %s, %s, current_date + 30, 'declined', %s, %s)"
    )
    promise = (
        "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor, reason) "
        "VALUES (%s, %s, %s, current_date + 30, 'staff', %s)"
    )
    for reason, decline_reason in (("x" * 301, None), ("", None), (None, "x" * 301), (None, "")):
        with pytest.raises(psycopg.errors.CheckViolation):
            owner.execute(request, (uuid.uuid4(), world.shop_a, world.entry_a, reason, decline_reason))
    for reason in ("x" * 301, ""):
        with pytest.raises(psycopg.errors.CheckViolation):
            owner.execute(promise, (uuid.uuid4(), world.shop_a, world.entry_a, reason))
    owner.execute(request, (uuid.uuid4(), world.shop_a, world.entry_a, "x" * 300, "y" * 300))
    owner.execute(request, (uuid.uuid4(), world.shop_a, world.entry_a, None, None))
    owner.execute(promise, (uuid.uuid4(), world.shop_a, world.entry_a, "x" * 300))


def test_the_chat_may_remember_a_date_question_and_no_unknown_kind(owner: psycopg.Connection, world: World) -> None:
    pending = (
        "INSERT INTO chat_pending (id, user_id, kind, payload, expires_at) "
        "VALUES (%s, %s, %s, '{}', now() + interval '15 minutes')"
    )
    owner.execute(pending, (uuid.uuid4(), world.customer_of_a, "date_request"))
    for kind in ("dispute", "decline", "promise_date"):
        owner.execute(pending, (uuid.uuid4(), world.customer_of_a, kind))
    with pytest.raises(psycopg.errors.CheckViolation):
        owner.execute(pending, (uuid.uuid4(), world.customer_of_a, "date_decline"))


# --- found by breaking the rules one at a time ----------------------------------------------------------


def test_the_newest_decline_is_the_one_that_counts(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    first = ask_a(client, world, owner).json()["id"]
    decline(client, world, world.manager_a, first)
    owner.execute("UPDATE date_change_request SET closed_at = now() - interval '20 days' WHERE id = %s", (first,))
    second = ask_a(client, world, owner).json()["id"]
    decline(client, world, world.manager_a, second)
    refused = ask_a(client, world, owner)
    assert (refused.status_code, refused.json()["error"]["fields"]) == (409, {"reason": "declined_recently"})
    assert statuses(owner, world) == ["declined", "declined"]


def test_a_direct_change_does_not_touch_a_request_that_is_already_decided(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    wanted = current(owner, world.entry_a) + timedelta(days=10)
    request = ask_a(client, world, owner).json()["id"]
    decline(client, world, world.manager_a, request, {"reason": "Hozircha yo'q"})
    response = change(client, world, world.owner_a, world.entry_a, wanted)
    assert (response.status_code, response.json()["date_request"]) == (200, None)
    assert requests(owner, world) == [
        (world.entry_a, wanted, None, "declined", "Hozircha yo'q", world.manager_a_membership, True)
    ]


def test_the_year_is_measured_from_the_sales_own_tashkent_day_not_from_today(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """Sold at 20:00 UTC, which is already the next day in Tashkent: that day is the sale's date."""
    utc_day = today() - timedelta(days=10)
    sold_at = datetime.combine(utc_day, time(20, 0), tzinfo=UTC)
    age = (datetime.now(UTC) - sold_at).total_seconds() / 86400
    sale_day = utc_day + timedelta(days=1)
    link = link_of(owner, world.customer_a)
    sales = [
        seed_entry(owner, world, world.customer_a, seq, "credit", 4000, promised=today(), days_ago=age)
        for seq in (2, 3)
    ]

    too_far = ask(client, world.customer_of_a, link, sales[0], sale_day + timedelta(days=366))
    assert too_far.json()["error"]["fields"] == {"requested_date": "PROMISE_TOO_FAR"}
    assert ask(client, world.customer_of_a, link, sales[0], sale_day + timedelta(days=365)).status_code == 201

    for chosen, problem in ((utc_day, "PROMISE_BEFORE_SALE"), (sale_day + timedelta(days=366), "PROMISE_TOO_FAR")):
        refused = change(client, world, world.manager_a, sales[1], chosen)
        assert (refused.status_code, refused.json()["error"]["fields"]) == (422, {"promised_date": problem})
    assert change(client, world, world.manager_a, sales[1], sale_day).status_code == 200
    assert change(client, world, world.manager_a, sales[1], sale_day + timedelta(days=365)).status_code == 200


def test_someone_who_manages_two_shops_decides_in_the_shop_the_request_belongs_to(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """Whichever of their shops is looked at first, the request is found in its own."""
    for user, other_shop in ((world.owner_b, world.shop_a), (world.manager_a, world.shop_b)):
        owner.execute(
            "INSERT INTO membership (id, shop_id, user_id, role) VALUES (%s, %s, %s, 'manager')",
            (uuid.uuid4(), other_shop, user),
        )
    in_a = uuid.UUID(ask_a(client, world, owner).json()["id"])
    # A request in shop B, written straight into the tables: shop B has no linked customer.
    author = owner.execute(
        "SELECT id FROM membership WHERE shop_id = %s AND role = 'owner'", (world.shop_b,)
    ).fetchone()
    assert author is not None
    customer_b = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Bek', 'bek')",
        (customer_b, world.shop_b),
    )
    entry_b = seed_entry(
        owner, world, customer_b, 1, "credit", 9000, promised=today() + timedelta(days=5), shop_id=world.shop_b,
        author=author[0],
    )  # fmt: skip
    in_b = uuid.uuid4()
    owner.execute(
        "INSERT INTO date_change_request (id, shop_id, entry_id, requested_date) VALUES (%s, %s, %s, %s)",
        (in_b, world.shop_b, entry_b, today() + timedelta(days=15)),
    )

    assert chat_of(client, owner, world.owner_b).press(f"v2:dok:{in_a.hex}").text.startswith("✅ Ali")
    assert chat_of(client, owner, world.manager_a).press(f"v2:dno:{in_b.hex}").text.startswith("So'rov rad etildi: Bek")
    assert statuses(owner, world) == ["accepted"]
    assert owner.execute("SELECT status FROM date_change_request WHERE id = %s", (in_b,)).fetchone() == ("declined",)
    assert current(owner, entry_b) == today() + timedelta(days=5)


def test_a_request_that_was_decided_is_not_expired_afterwards(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    later = record(client, world, world.customer_a, "credit", 7000).json()["entry"]["id"]
    declined = ask_a(client, world, owner).json()["id"]
    decline(client, world, world.manager_a, declined, {"reason": "Hozircha yo'q"})
    accepted = ask(client, world.customer_of_a, link, later, current(owner, later) + timedelta(days=3)).json()["id"]
    accept(client, world, world.owner_a, accepted)
    before = requests(owner, world)

    assert record(client, world, world.customer_a, "payment", 50000).status_code == 201  # settles entry_a
    assert reverse(client, world, later, world.owner_a).status_code == 201
    assert requests(owner, world) == before
    assert statuses(owner, world) == ["declined", "accepted"]


def test_someone_linked_to_two_shops_asks_in_the_shop_the_entry_belongs_to(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """Whichever of their accounts is looked at first, the entry is found on its own."""
    author = owner.execute(
        "SELECT id FROM membership WHERE shop_id = %s AND role = 'owner'", (world.shop_b,)
    ).fetchone()
    assert author is not None
    customer_b = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Ali B', 'ali b')",
        (customer_b, world.shop_b),
    )
    owner.execute(
        "INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at) "
        "VALUES (%s, %s, %s, %s, 'active', 2, now())",
        (uuid.uuid4(), world.shop_b, customer_b, world.customer_of_a),
    )
    entry_b = seed_entry(
        owner, world, customer_b, 1, "credit", 9000, promised=today() + timedelta(days=5), shop_id=world.shop_b,
        author=author[0],
    )  # fmt: skip
    wanted = today() + timedelta(days=40)
    customer = chat_of(client, owner, world.customer_of_a)

    customer.press(f"v2:dmv:{entry_b.hex}")
    assert customer.say(_typed(wanted)).text.startswith("So'rovingiz «Shop B»")
    customer.press(f"v2:dmv:{world.entry_a.hex}")
    assert customer.say(_typed(wanted)).text.startswith("So'rovingiz «Shop A»")
    stored = owner.execute(
        "SELECT shop_id, entry_id FROM date_change_request WHERE entry_id IN (%s, %s) ORDER BY created_at",
        (entry_b, world.entry_a),
    ).fetchall()
    assert stored == [(world.shop_b, entry_b), (world.shop_a, world.entry_a)]
    # Each shop's managers hear of their own request only.
    told_b = owner.execute(
        "SELECT recipient FROM outbox_message WHERE shop_id = %s AND dedupe_key LIKE 'date-request:%%'", (world.shop_b,)
    ).fetchall()
    assert told_b == [(tg(owner, world.owner_b),)]


def test_pressing_the_button_of_another_entry_changes_which_entry_the_date_is_for(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    first, first_button, _ = _sale(client, world, owner)
    second, second_button, old = _sale(client, world, owner)
    customer.press(first_button)
    customer.press(second_button)
    assert customer.say(_typed(old + timedelta(days=2))).text.startswith("So'rovingiz «Shop A»")
    assert [row[0] for row in requests(owner, world)] == [uuid.UUID(second)]
    assert first != second
    # One question was open, and it has been answered.
    assert customer.say(_typed(old + timedelta(days=3))).text.startswith(say("uz", "accounts_header"))


def test_each_step_is_measured_without_saying_whose_it_is(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    kinds = ("date_request_opened", "date_request_accepted", "date_request_declined", "promise_changed")

    def measured() -> dict[str, int]:
        rows = owner.execute(
            "SELECT kind, count(*) FROM measure.event WHERE kind = ANY(%s) GROUP BY kind", (list(kinds),)
        ).fetchall()
        return {**dict.fromkeys(kinds, 0), **{str(kind): int(count) for kind, count in rows}}

    before = measured()
    first = ask_a(client, world, owner).json()["id"]
    decline(client, world, world.manager_a, first)
    owner.execute("UPDATE date_change_request SET closed_at = now() - interval '8 days' WHERE id = %s", (first,))
    accept(client, world, world.manager_a, ask_a(client, world, owner).json()["id"])
    change(client, world, world.manager_a, world.entry_a, today() + timedelta(days=100))
    after = measured()
    assert {kind: after[kind] - before[kind] for kind in kinds} == {
        "date_request_opened": 2,
        "date_request_accepted": 1,
        "date_request_declined": 1,
        "promise_changed": 1,
    }


def test_reminders_follow_the_accepted_date(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    """BR-17: a reminder is due only for a debt that is overdue or promised for today, by its current date."""
    link = link_of(owner, world.customer_a)
    owner.execute("UPDATE shop SET reminders_on = true WHERE id = %s", (world.shop_a,))
    late = seed_entry(
        owner, world, world.customer_a, 2, "credit", 8000, promised=today() - timedelta(days=3), days_ago=40
    )
    request = ask(client, world.customer_of_a, link, late, today() + timedelta(days=5)).json()["id"]
    assert accept(client, world, world.manager_a, request).status_code == 200

    refused = manual(client, world, world.customer_a)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "REMINDER_NOT_DUE")
    assert owner.execute("SELECT count(*) FROM reminder WHERE shop_id = %s", (world.shop_a,)).fetchone() == (0,)

    # Moved back to yesterday by the shop, the same debt is overdue again and may be reminded of.
    assert change(client, world, world.manager_a, late, today() - timedelta(days=1)).status_code == 200
    assert manual(client, world, world.customer_a).status_code == 201
