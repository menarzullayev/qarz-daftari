"""A customer's own page, disconnecting, removal of their data, and an owner's totals.

REQ-019, REQ-020, REQ-021, REQ-029, REQ-065; domain rule BR-32.
"""

import uuid
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import money, say

from .conftest import World, as_user
from .test_chat import chat_of
from .test_customers_ledger import detail, key, record, reverse, seed_customer, seed_entry, shop, today
from .test_links import notices

pytestmark = pytest.mark.db

ME = "/api/v1/me/accounts"


def link_of(owner: psycopg.Connection, customer: uuid.UUID) -> uuid.UUID:
    row = owner.execute(
        "SELECT id FROM customer_link WHERE customer_id = %s AND status IN ('active', 'unreachable')", (customer,)
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def attach_waiter(client: TestClient, world: World, customer: uuid.UUID) -> None:
    response = client.post(
        f"{shop(world)}/waiting/{world.waiting_a}/attach",
        json={"customer_id": str(customer)},
        headers={**as_user(world.seller_a), **key()},
    )
    assert response.status_code == 200, response.text


def customer_row(owner: psycopg.Connection, customer: uuid.UUID) -> Any:
    return owner.execute(
        "SELECT display_name, name_norm, phone, status FROM customer WHERE id = %s", (customer,)
    ).fetchone()


def tg_of(owner: psycopg.Connection, user: uuid.UUID) -> Any:
    row = owner.execute("SELECT tg_id FROM app_user WHERE id = %s", (user,)).fetchone()
    assert row is not None
    return row[0]


# --- the page ------------------------------------------------------------------------------------------


def test_a_customer_lists_their_accounts(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    mine = client.get(ME, headers=as_user(world.customer_of_a)).json()
    assert mine == {
        "items": [
            {
                "link_id": str(link_of(owner, world.customer_a)),
                "shop_name": "Shop A",
                "display_name": "Ali",
                "balance": 50000,
            }
        ]
    }
    for nobody in (world.stranger, world.owner_a, world.waiter):
        assert client.get(ME, headers=as_user(nobody)).json() == {"items": []}


def test_a_customer_reads_their_own_account_without_the_shops_private_fields(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    record(client, world, world.customer_a, "credit", 20000, note="ichki izoh: kechikib to'laydi")
    record(client, world, world.customer_a, "payment", 5000)
    record(client, world, world.settled_customer_a, "credit", 777000)  # another customer of the same shop
    link = link_of(owner, world.customer_a)

    body = client.get(f"{ME}/{link}", headers=as_user(world.customer_of_a)).json()
    assert set(body) == {
        "link_id",
        "shop_name",
        "display_name",
        "balance",
        "overdue",
        "payment_history",
        "removal_requested",
        "entries",
        "entries_total",
        "payment_notices",
    }
    assert (body["shop_name"], body["display_name"], body["balance"]) == ("Shop A", "Ali", 65000)
    assert [(e["kind"], e["amount"]) for e in body["entries"]] == [
        ("payment", 5000),
        ("credit", 20000),
        ("credit", 50000),
    ]
    # Neither the seller's note nor who wrote the entry (REQ-045). The payment indicator is shown since
    # the founder's decision of 2026-10-08 (DEC-066); until then it was left out too.
    assert set(body["entries"][0]) == {
        "id",
        "kind",
        "amount",
        "created_at",
        "promised_date",
        "reverses_id",
        "reversed",
        "disputed",
        "dispute",
        "lines",
        "promises",
        "date_request",
    }
    assert "ichki izoh" not in str(body)
    assert "777000" not in str(body)


def _keys(value: Any) -> set[str]:
    """Every key anywhere in a JSON body."""
    if isinstance(value, dict):
        return set(value) | {key for inner in value.values() for key in _keys(inner)}
    if isinstance(value, list):
        return {key for inner in value for key in _keys(inner)}
    return set()


def test_a_customer_sees_their_own_payment_indicator_exactly_as_the_shop_sees_it(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """DEC-066, changing DEC-033: the customer's page carries the indicator of BR-9, about themselves."""
    # Nothing has fallen due yet: no indicator rather than a misleading 0 or 100.
    untested = client.get(f"{ME}/{link_of(owner, world.customer_a)}", headers=as_user(world.customer_of_a))
    assert untested.json()["payment_history"] is None

    customer, person, link = seed_customer(owner, world.shop_a, "Intizomli"), uuid.uuid4(), uuid.uuid4()
    owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (person, uuid.uuid4().int % 10**15))
    owner.execute(
        "INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at) "
        "VALUES (%s, %s, %s, %s, 'active', 2, now())",
        (link, world.shop_a, customer, person),
    )
    seed_entry(owner, world, customer, 1, "credit", 100_000, promised=today() - timedelta(days=10), days_ago=20)
    seed_entry(owner, world, customer, 2, "payment", 75_000, days_ago=15)
    seed_entry(owner, world, customer, 3, "payment", 25_000, days_ago=6)
    # Another customer of the same shop, who paid nothing in time: none of it is in this customer's figures.
    other = seed_customer(owner, world.shop_a, "Kechikkan")
    seed_entry(owner, world, other, 1, "credit", 400_000, promised=today() - timedelta(days=30), days_ago=40)

    mine = client.get(f"{ME}/{link}", headers=as_user(person)).json()["payment_history"]
    assert mine == {
        "on_time_percent": 75,
        "on_time_amount": 75_000,
        "due_amount": 100_000,
        "longest_delay_days": 4,
    }
    assert mine == detail(client, world, customer)["payment_history"], "the same figures the staff see"
    # Still only their own: the other customer of the shop is told nothing of it.
    assert client.get(f"{ME}/{link}", headers=as_user(world.customer_of_a)).status_code == 404


def test_the_customers_page_never_carries_the_note_or_the_author_of_an_entry(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The seller's note and the author stay the shop's own (REQ-045), with the indicator now shown."""
    secret = "ichki izoh: kechikib to'laydi"
    record(client, world, world.customer_a, "credit", 20000, note=secret)
    record(client, world, world.customer_a, "payment", 5000, note="yana bir ichki izoh")
    staff = detail(client, world, world.customer_a)
    seen_by_staff = _keys(staff)
    # The check itself: the staff's answer does carry both, so their absence below means something.
    assert {"note", "author_id"} <= seen_by_staff and secret in str(staff)

    body = client.get(f"{ME}/{link_of(owner, world.customer_a)}", headers=as_user(world.customer_of_a))
    assert body.status_code == 200
    private = {key for key in _keys(body.json()) if "note" in key or "author" in key or key == "membership_id"}
    assert private == set()
    assert "izoh" not in body.text
    authors = owner.execute(
        "SELECT m.id::text, m.user_id::text FROM membership m WHERE m.shop_id = %s", (world.shop_a,)
    ).fetchall()
    for membership, user in authors:
        assert membership not in body.text and user not in body.text


def test_nobody_else_can_open_a_customers_account(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    link = link_of(owner, world.customer_a)
    # Staff of the shop use the staff API; the customer page is not theirs either.
    for other in (world.stranger, world.owner_a, world.seller_a, world.owner_b, world.admin, world.waiter):
        for method, path in (
            ("GET", f"{ME}/{link}"),
            ("POST", f"{ME}/{link}/disconnect"),
            ("POST", f"{ME}/{link}/removal"),
        ):
            response = client.request(method, path, headers=as_user(other))
            assert (response.status_code, response.json()["error"]["code"]) == (404, "NOT_FOUND"), (other, path)
    assert client.get(f"{ME}/{link}").status_code == 401
    assert client.get(f"{ME}/{world.waiting_a}", headers=as_user(world.waiter)).status_code == 404, (
        "waiting is not linked"
    )
    assert client.get(f"{ME}/not-a-uuid", headers=as_user(world.customer_of_a)).status_code == 404
    assert owner.execute("SELECT status FROM customer_link WHERE id = %s", (link,)).fetchone() == ("active",)
    assert customer_row(owner, world.customer_a) == ("Ali", "ali", None, "active")


def test_a_customer_disconnects_through_the_page(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    link = link_of(owner, world.customer_a)
    done = client.post(f"{ME}/{link}/disconnect", headers=as_user(world.customer_of_a))
    assert (done.status_code, done.json()) == (200, {"disconnected": True})
    assert owner.execute("SELECT status FROM customer_link WHERE id = %s", (link,)).fetchone() == ("ended",)
    assert client.get(f"{ME}/{link}", headers=as_user(world.customer_of_a)).status_code == 404
    assert client.post(f"{ME}/{link}/disconnect", headers=as_user(world.customer_of_a)).status_code == 404
    assert customer_row(owner, world.customer_a) == ("Ali", "ali", None, "active"), "the shop keeps its record"


# --- removal (REQ-029, BR-32) --------------------------------------------------------------------------


def test_removal_is_carried_out_at_once_when_nothing_is_owed(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer = world.settled_customer_a
    owner.execute("UPDATE customer SET phone = '+998901234567' WHERE id = %s", (customer,))
    sale = record(client, world, customer, "credit", 30000).json()["entry"]["id"]
    record(client, world, customer, "payment", 30000)
    attach_waiter(client, world, customer)
    personal = client.post(f"{shop(world)}/customers/{customer}/link", headers={**as_user(world.seller_a), **key()})
    assert personal.status_code == 409  # linked now; no stray code is left behind
    link = link_of(owner, customer)

    done = client.post(f"{ME}/{link}/removal", headers=as_user(world.waiter))
    assert (done.status_code, done.json()) == (200, {"removed": True, "waiting_for_balance": None})

    label = f"Anonim {customer.hex[:6].upper()}"
    assert customer_row(owner, customer) == (label, label.lower(), None, "anonymized")
    assert owner.execute(
        "SELECT status, user_id, waiting_name, ended_at IS NOT NULL FROM customer_link WHERE id = %s", (link,)
    ).fetchone() == ("ended", None, None, True)
    assert owner.execute(
        "SELECT status, completed_at IS NOT NULL FROM removal_request WHERE customer_id = %s", (customer,)
    ).fetchall() == [("completed", True)]
    # The person was nothing but this customer: their Telegram identity is forgotten too.
    assert tg_of(owner, world.waiter) is None
    # The amounts stay, so the shop's totals stay correct.
    kept = owner.execute(
        "SELECT kind, amount FROM ledger_entry WHERE customer_id = %s ORDER BY seq", (customer,)
    ).fetchall()
    assert kept == [("credit", 30000), ("payment", 30000)]
    assert owner.execute("SELECT id FROM ledger_entry WHERE id = %s", (sale,)).fetchone() is not None
    logged = owner.execute(
        "SELECT actor_kind FROM activity WHERE shop_id = %s AND action = 'customer.anonymized'", (world.shop_a,)
    ).fetchall()
    assert logged == [("customer",)]

    # Gone for the person, and no longer a customer one can record against or find by name.
    assert client.get(f"{ME}/{link}", headers=as_user(world.waiter)).status_code == 404
    assert client.get(ME, headers=as_user(world.waiter)).json() == {"items": []}
    assert record(client, world, customer, "credit", 1000).status_code == 404
    listed = client.get(f"{shop(world)}/customers", params={"q": "vali"}, headers=as_user(world.owner_a)).json()
    assert listed["items"] == []


def test_removal_waits_for_the_debt_and_is_carried_out_by_the_payment_that_settles_it(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    customer = world.customer_a
    link = link_of(owner, customer)
    asked = client.post(f"{ME}/{link}/removal", headers=as_user(world.customer_of_a))
    assert (asked.status_code, asked.json()) == (200, {"removed": False, "waiting_for_balance": 50000})
    assert customer_row(owner, customer) == ("Ali", "ali", None, "active"), "nothing is removed while a debt stands"
    assert client.get(f"{ME}/{link}", headers=as_user(world.customer_of_a)).json()["removal_requested"] is True

    # Asking again changes nothing.
    again = client.post(f"{ME}/{link}/removal", headers=as_user(world.customer_of_a))
    assert again.json() == {"removed": False, "waiting_for_balance": 50000}
    assert owner.execute("SELECT status FROM removal_request WHERE customer_id = %s", (customer,)).fetchall() == [
        ("waiting",)
    ]

    assert record(client, world, customer, "payment", 20000).status_code == 201
    assert customer_row(owner, customer)[3] == "active", "a part payment does not settle the debt"

    tg_before = tg_of(owner, world.customer_of_a)
    last = record(client, world, customer, "payment", 30000)
    assert last.status_code == 201, last.text
    label = f"Anonim {customer.hex[:6].upper()}"
    assert customer_row(owner, customer) == (label, label.lower(), None, "anonymized")
    assert owner.execute("SELECT status, user_id FROM customer_link WHERE id = %s", (link,)).fetchone() == (
        "ended",
        None,
    )
    assert owner.execute("SELECT status FROM removal_request WHERE customer_id = %s", (customer,)).fetchall() == [
        ("completed",)
    ]
    assert tg_of(owner, world.customer_of_a) is None
    # They were still told about the payment that settled the debt.
    told = notices(owner, world)
    assert told[-1] == (
        str(tg_before),
        say("uz", "n_payment", shop="Shop A", name="Ali", amount=money("uz", 30000), balance=money("uz", 0)),
    )


def test_a_reversal_that_clears_the_debt_also_carries_out_the_removal(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    link = link_of(owner, world.customer_a)
    client.post(f"{ME}/{link}/removal", headers=as_user(world.customer_of_a))
    assert reverse(client, world, world.entry_a).status_code == 201
    assert customer_row(owner, world.customer_a)[3] == "anonymized"


def test_someone_who_is_also_staff_or_a_customer_elsewhere_keeps_their_telegram_identity(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    # seller_a, a member of shop A, is also a customer of shop B.
    in_b = seed_customer(owner, world.shop_b, "Sotuvchi")
    link = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer_link (id, shop_id, customer_id, user_id, status, consent_text_v, consent_at) "
        "VALUES (%s, %s, %s, %s, 'active', 2, now())",
        (link, world.shop_b, in_b, world.seller_a),
    )
    done = client.post(f"{ME}/{link}/removal", headers=as_user(world.seller_a))
    assert done.json()["removed"] is True
    assert customer_row(owner, in_b)[3] == "anonymized"
    assert tg_of(owner, world.seller_a) is not None
    # Still staff of shop A.
    assert record(client, world, world.customer_a, "credit", 1000).status_code == 201


def test_a_customer_asks_for_removal_from_chat(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    asked = customer.say("/ochirish")
    assert asked.text == say("uz", "removal_choose")
    confirm = customer.press(asked.button("O'chirish"), customer.last_message_id)
    assert confirm.text == say("uz", "removal_confirm", shop="Shop A")
    assert customer_row(owner, world.customer_a)[3] == "active", "asking is not doing"
    assert (
        owner.execute("SELECT count(*) FROM removal_request").fetchone()
        == owner.execute("SELECT count(*) FROM removal_request WHERE customer_id <> %s", (world.customer_a,)).fetchone()
    )

    declined = customer.press(confirm.button("Yo'q"))
    assert declined.text == say("uz", "cancelled")
    assert owner.execute(
        "SELECT count(*) FROM removal_request WHERE customer_id = %s", (world.customer_a,)
    ).fetchone() == (0,)

    waiting = customer.press(confirm.button("Ha"))
    assert waiting.text == say("uz", "removal_waiting", shop="Shop A", balance=money("uz", 50000))
    assert owner.execute(
        "SELECT status FROM removal_request WHERE customer_id = %s", (world.customer_a,)
    ).fetchall() == [("waiting",)]
    # Someone else pressing the same button gets nowhere.
    assert chat_of(client, owner, world.stranger).press(confirm.button("Ha")).text == say("uz", "expired")
    assert chat_of(client, owner, world.stranger).say("/ochirish").text == say("uz", "no_accounts")


# --- an owner's combined totals (REQ-065) ---------------------------------------------------------------


def test_an_owner_sees_the_totals_of_the_shops_they_own(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    # owner_a also owns a second shop with one debtor, and is only a seller in shop B.
    second = uuid.uuid4()
    owner.execute("INSERT INTO shop (id, name) VALUES (%s, 'Shop A2')", (second,))
    membership = uuid.uuid4()
    owner.execute(
        "INSERT INTO membership (id, shop_id, user_id, role, status) VALUES (%s, %s, %s, 'owner', 'active')",
        (membership, second, world.owner_a),
    )
    owner.execute(
        "INSERT INTO membership (id, shop_id, user_id, role, status) VALUES (%s, %s, %s, 'seller', 'active')",
        (uuid.uuid4(), world.shop_b, world.owner_a),
    )
    debtor = seed_customer(owner, second, "Qarzdor")
    seed_entry(owner, world, debtor, 1, "credit", 120000, promised=None, shop_id=second, author=membership)
    owner.execute(
        "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor) "
        "SELECT gen_random_uuid(), %s, id, current_date - 3, 'staff' FROM ledger_entry WHERE customer_id = %s",
        (second, debtor),
    )
    in_b = seed_customer(owner, world.shop_b, "B qarzdor")
    author_b = owner.execute(
        "SELECT id FROM membership WHERE shop_id = %s AND role = 'owner'", (world.shop_b,)
    ).fetchone()
    assert author_b is not None
    seed_entry(owner, world, in_b, 1, "credit", 999000, promised=None, shop_id=world.shop_b, author=author_b[0])

    body = client.get("/api/v1/me/owner-totals", headers=as_user(world.owner_a)).json()
    by_name = {item["name"]: item for item in body["items"]}
    assert set(by_name) == {"Shop A", "Shop A2"}, "a shop one only works in is not one's own"
    assert (by_name["Shop A"]["outstanding"], by_name["Shop A"]["debtors"], by_name["Shop A"]["overdue"]) == (
        50000,
        1,
        0,
    )
    assert (by_name["Shop A2"]["outstanding"], by_name["Shop A2"]["overdue"]) == (120000, 120000)
    assert body["total"] == {"outstanding": 170000, "debtors": 2, "overdue": 120000, "due_today": 0}
    assert "999000" not in str(body)

    for not_an_owner in (world.manager_a, world.seller_a, world.stranger, world.customer_of_a):
        assert client.get("/api/v1/me/owner-totals", headers=as_user(not_an_owner)).json() == {
            "items": [],
            "total": {"outstanding": 0, "debtors": 0, "overdue": 0, "due_today": 0},
        }


def test_goods_are_shown_on_the_customers_page_and_in_the_message(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    lines = [
        {"name": "Guruch", "qty": "2.5", "unit": "kg", "unit_price": 12000},
        {"catalog_item_id": str(world.catalog_item_a), "qty": "3", "unit_price": 4000},
    ]
    sale = client.post(
        f"{shop(world)}/customers/{world.customer_a}/entries",
        json={"kind": "credit", "lines": lines},
        headers={**as_user(world.seller_a), **key()},
    )
    assert sale.status_code == 201, sale.text
    stored = sale.json()["entry"]["lines"]
    assert sale.json()["entry"]["amount"] == 42000

    page = client.get(f"{ME}/{link_of(owner, world.customer_a)}", headers=as_user(world.customer_of_a)).json()
    assert page["entries"][0]["lines"] == stored
    assert page["entries"][1]["lines"] == [], "an entry without goods has an empty list"

    told = notices(owner, world)[-1][1]
    assert say("uz", "n_line", name="Guruch", qty="2.5", unit="kg", total=money("uz", 30000)) in told
    assert (
        say("uz", "n_line", name=stored[1]["name"], qty="3", unit=stored[1]["unit"], total=money("uz", 12000)) in told
    )
    assert told.index("Guruch") < told.index("To'lash muddati"), "goods come before the promised date"


def test_the_shop_list_tells_a_client_which_membership_is_the_callers(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    mine = client.get("/api/v1/me/shops", headers=as_user(world.seller_a)).json()["items"]
    assert mine == [
        {
            "shop_id": str(world.shop_a),
            "name": "Shop A",
            "role": "seller",
            "membership_id": str(world.seller_a_membership),
        }
    ]
    entry = record(client, world, world.customer_a, "credit", 1000).json()["entry"]["id"]
    detail = client.get(f"{shop(world)}/customers/{world.customer_a}", headers=as_user(world.seller_a)).json()
    written = next(e for e in detail["entries"] if e["id"] == entry)
    assert written["author_id"] == mine[0]["membership_id"]
