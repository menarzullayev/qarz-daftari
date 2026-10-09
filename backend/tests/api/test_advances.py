"""Advances: a customer may pay more than they owe where the shop has chosen to accept that (INV-3).

The founder's decision of 2026-10-10. Off by default and the owner's to change. While it is off every
answer is what it was before the decision; while it is on, a payment beyond the debt is asked about
first and then kept as the customer's advance, a balance below zero in that currency's book that the
next credit sales use up, oldest first.
"""

import uuid
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import money, owed, say

from .conftest import World, as_user
from .test_chat import chat_of, entries
from .test_customer_account import ME, link_of
from .test_customers_ledger import detail, key, read, record, reverse, shop, today, write
from .test_reports import period
from .test_usd import dollars, platform_on  # noqa: F401  (fixtures)
from .test_usd import record as record_in

pytestmark = pytest.mark.db

MAX_ENTRY = 100_000_000


def settings(client: TestClient, world: World, body: dict[str, Any], user: uuid.UUID | None = None) -> Any:
    return write(client, user or world.owner_a, "PATCH", f"{shop(world)}/credit-settings", body)


@pytest.fixture
def accepting(client: TestClient, world: World) -> None:
    """Shop A's owner has turned "accept advances" on."""
    answer = settings(client, world, {"accept_advances": True})
    assert (answer.status_code, answer.json()["accept_advances"]) == (200, True), answer.text


def code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def books(owner: psycopg.Connection, customer: Any) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT kind, amount, currency FROM ledger_entry WHERE customer_id = %s ORDER BY seq", (customer,)
    ).fetchall()


def stored_advances(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT customer_id, currency, amount FROM customer_advance WHERE shop_id = %s ORDER BY currency, amount",
        (world.shop_a,),
    ).fetchall()


def agree(owner: psycopg.Connection) -> None:
    """The kept figures are the ledger's: open debts and advances both."""
    assert owner.execute("SELECT * FROM open_debt_mismatches(NULL)").fetchall() == []
    assert owner.execute("SELECT * FROM customer_advance_mismatches(NULL)").fetchall() == []
    assert owner.execute("SELECT open_debt_mismatch_count()").fetchone() == (0,)


def overview(client: TestClient, world: World) -> dict[str, Any]:
    answer = read(client, world.owner_a, f"{shop(world)}/overview")
    assert answer.status_code == 200, answer.text
    return dict(answer.json())


def listed(client: TestClient, world: World, **params: Any) -> list[tuple[str, int]]:
    answer = read(client, world.owner_a, f"{shop(world)}/overview/debtors", **params)
    assert answer.status_code == 200, answer.text
    return [(item["display_name"], item["balance"]) for item in answer.json()["items"]]


# --- the switch: off by default, the owner's, and not turned off under a standing advance -----------------


def test_a_shop_does_not_accept_advances_until_its_owner_says_so(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    current = read(client, world.seller_a, f"{shop(world)}/credit-settings").json()
    assert current["accept_advances"] is False
    assert owner.execute("SELECT accept_advances FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == (False,)

    # A manager changes the limits, and may not change this.
    refused = settings(client, world, {"accept_advances": True}, world.manager_a)
    assert code(refused) == (403, "FORBIDDEN_ROLE")
    assert refused.json()["error"]["fields"] == {"needed_role": "owner"}
    mixed = settings(client, world, {"default_credit_limit": 70_000, "accept_advances": True}, world.manager_a)
    assert code(mixed) == (403, "FORBIDDEN_ROLE")
    assert owner.execute(
        "SELECT accept_advances, default_credit_limit FROM shop WHERE id = %s", (world.shop_a,)
    ).fetchone() == (False, None)
    assert code(settings(client, world, {"accept_advances": True}, world.seller_a))[0] == 403
    assert settings(client, world, {"accept_advances": "yes"}).status_code == 422

    done = settings(client, world, {"accept_advances": True})
    assert (done.status_code, done.json()["accept_advances"]) == (200, True)
    # The manager's own settings still work, and leave the switch where it is.
    kept = settings(client, world, {"sellers_may_exceed": True}, world.manager_a)
    assert (kept.status_code, kept.json()["accept_advances"]) == (200, True)
    # Shop B is another shop.
    assert owner.execute("SELECT accept_advances FROM shop WHERE id = %s", (world.shop_b,)).fetchone() == (False,)


def test_advances_are_not_turned_off_while_one_stands(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    paid = record(client, world, world.customer_a, "payment", 60_000, advance=True)
    assert paid.status_code == 201, paid.text

    refused = settings(client, world, {"accept_advances": False, "default_credit_limit": 70_000})
    assert code(refused) == (409, "ADVANCES_STAND")
    # Nothing of the refused request was stored, the other field included.
    assert owner.execute(
        "SELECT accept_advances, default_credit_limit FROM shop WHERE id = %s", (world.shop_a,)
    ).fetchone() == (True, None)

    # Settled either way (here: the payment is taken back), the owner may turn it off again.
    assert reverse(client, world, paid.json()["entry"]["id"]).status_code == 201
    assert stored_advances(owner, world) == []
    done = settings(client, world, {"accept_advances": False})
    assert (done.status_code, done.json()["accept_advances"]) == (200, False)
    assert code(record(client, world, world.customer_a, "payment", 50_001, advance=True)) == (409, "EXCEEDS_BALANCE")


def test_the_database_keeps_the_rule_for_a_writer_that_does_not_ask(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """Under the application's own checks: no row puts a book below zero in a shop that does not accept it."""
    row = (
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
        "VALUES (gen_random_uuid(), %s, %s, 2, 'payment', 50001, %s)"
    )
    values = (world.shop_a, world.customer_a, world.seller_a_membership)
    with pytest.raises(psycopg.errors.CheckViolation, match="accepts advances"), owner.transaction():
        owner.execute(row, values)
    assert books(owner, world.customer_a) == [("credit", 50_000, "UZS")]

    owner.execute("UPDATE shop SET accept_advances = true WHERE id = %s", (world.shop_a,))
    owner.execute(row, values)
    assert stored_advances(owner, world) == [(world.customer_a, "UZS", 1)]
    with pytest.raises(psycopg.errors.CheckViolation, match="advances stand"), owner.transaction():
        owner.execute("UPDATE shop SET accept_advances = false WHERE id = %s", (world.shop_a,))
    agree(owner)


# --- switched off: every answer is what it was ---------------------------------------------------------------


def test_without_the_setting_a_payment_beyond_the_debt_is_refused_whatever_the_request_says(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = overview(client, world)
    for extra in ({}, {"advance": True}, {"advance": False}):
        assert code(record(client, world, world.customer_a, "payment", 50_100, **extra)) == (409, "EXCEEDS_BALANCE")
        assert code(record(client, world, world.settled_customer_a, "payment", 100, **extra)) == (
            409,
            "EXCEEDS_BALANCE",
        )
    assert books(owner, world.customer_a) == [("credit", 50_000, "UZS")]
    assert books(owner, world.settled_customer_a) == []
    assert overview(client, world) == before
    assert "advances" not in before
    assert listed(client, world, in_credit=True) == []
    assert stored_advances(owner, world) == []

    # And the reversal of a sale that is already paid for still waits for the payment to be taken back.
    assert record(client, world, world.customer_a, "payment", 50_000).status_code == 201
    assert code(reverse(client, world, world.entry_a)) == (409, "WOULD_GO_NEGATIVE")
    said = chat_of(client, owner, world.seller_a).say("Ali -100")
    assert said.text == say("uz", "EXCEEDS_BALANCE") and said.buttons == {}


# --- switched on: asked first, then kept ------------------------------------------------------------------------


def test_a_payment_beyond_the_debt_is_asked_about_and_then_kept_as_an_advance(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    asked = record(client, world, world.customer_a, "payment", 65_000)
    assert code(asked) == (409, "ADVANCE_NOT_CONFIRMED")
    assert asked.json()["error"]["fields"] == {"debt": "50000", "over": "15000", "advance": "15000"}
    assert code(record(client, world, world.customer_a, "payment", 65_000, advance=False)) == (
        409,
        "ADVANCE_NOT_CONFIRMED",
    )
    assert books(owner, world.customer_a) == [("credit", 50_000, "UZS")], "asking writes nothing"

    kept = record(client, world, world.customer_a, "payment", 65_000, advance=True)
    assert kept.status_code == 201, kept.text
    assert kept.json()["customer"]["balance"] == -15_000
    assert books(owner, world.customer_a) == [("credit", 50_000, "UZS"), ("payment", 65_000, "UZS")]
    assert stored_advances(owner, world) == [(world.customer_a, "UZS", 15_000)]
    agree(owner)

    # A further payment by a customer already in credit names what is beyond the debt and the total.
    again = record(client, world, world.customer_a, "payment", 1_000)
    assert again.json()["error"]["fields"] == {"debt": "0", "over": "1000", "advance": "16000"}

    # A payment within the debt needs no question, with the field or without it.
    assert record(client, world, world.settled_customer_a, "credit", 9_000).status_code == 201
    assert record(client, world, world.settled_customer_a, "payment", 9_000, advance=True).status_code == 201
    assert books(owner, world.settled_customer_a) == [("credit", 9_000, "UZS"), ("payment", 9_000, "UZS")]


def test_only_a_payment_can_be_an_advance(client: TestClient, world: World, accepting: None) -> None:
    refused = record(client, world, world.customer_a, "credit", 5_000, advance=True)
    assert code(refused) == (422, "VALIDATION")
    assert refused.json()["error"]["fields"] == {"advance": "only a payment can be kept as an advance"}
    assert record(client, world, world.customer_a, "payment", 5_000, advance="yes").status_code == 422


def test_a_repeated_confirmed_payment_is_written_once(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    headers = {**as_user(world.seller_a), **key()}
    path, body = f"{shop(world)}/customers/{world.customer_a}/entries", {"kind": "payment", "amount": 80_000}
    first = client.post(path, json={**body, "advance": True}, headers=headers)
    second = client.post(path, json={**body, "advance": True}, headers=headers)
    assert (first.status_code, second.status_code) == (201, 201) and first.json() == second.json()
    # The same key for the same payment without the confirmation is another request.
    assert code(client.post(path, json=body, headers=headers)) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert books(owner, world.customer_a) == [("credit", 50_000, "UZS"), ("payment", 80_000, "UZS")]


def test_one_customer_is_never_in_credit_by_more_than_one_entry_may_be(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    customer = world.settled_customer_a
    assert record(client, world, customer, "payment", MAX_ENTRY, advance=True).status_code == 201
    for extra in ({}, {"advance": True}):
        assert code(record(client, world, customer, "payment", 100, **extra)) == (409, "ADVANCE_TOO_LARGE")
    assert stored_advances(owner, world) == [(customer, "UZS", MAX_ENTRY)]
    # Once part of it is used, there is room again.
    assert record(client, world, customer, "credit", 40_000).json()["customer"]["balance"] == 40_000 - MAX_ENTRY
    assert record(client, world, customer, "payment", 40_000, advance=True).status_code == 201
    assert code(record(client, world, customer, "payment", 100, advance=True)) == (409, "ADVANCE_TOO_LARGE")


# --- what reads a balance ------------------------------------------------------------------------------------------


def test_the_advance_is_used_up_by_the_next_sales_oldest_first_and_nothing_is_overdue_meanwhile(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    customer = world.settled_customer_a
    assert record(client, world, customer, "payment", 50_000, advance=True).json()["customer"]["balance"] == -50_000
    first = record(client, world, customer, "credit", 20_000)
    assert first.json()["customer"]["balance"] == -30_000
    assert "limit_warning" not in first.json()
    # Covered from the moment it is written: nothing of it is open.
    assert owner.execute("SELECT count(*) FROM open_debt WHERE customer_id = %s", (customer,)).fetchone() == (0,)
    assert stored_advances(owner, world) == [(customer, "UZS", 30_000)]
    shown = detail(client, world, customer)
    assert (shown["balance"], shown["overdue"]) == (-30_000, {"amount": 0, "since": None, "days": 0, "due_today": 0})

    second = record(client, world, customer, "credit", 45_000)
    assert second.json()["customer"]["balance"] == 15_000
    assert owner.execute(
        "SELECT entry_id::text, remaining FROM open_debt WHERE customer_id = %s", (customer,)
    ).fetchall() == [(second.json()["entry"]["id"], 15_000)], "the older sale is covered first, the newer in part"
    assert stored_advances(owner, world) == []
    agree(owner)


def test_an_old_sale_a_customer_in_credit_has_already_covered_is_not_overdue_and_not_reminded_of(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    assert record(client, world, world.customer_a, "payment", 70_000, advance=True).status_code == 201
    # Long past its promised date.
    owner.execute(
        "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor, created_at) "
        "VALUES (gen_random_uuid(), %s, %s, %s, 'staff', now())",
        (world.shop_a, world.entry_a, today() - timedelta(days=40)),
    )
    shown = detail(client, world, world.customer_a)
    assert (shown["balance"], shown["overdue"]["amount"]) == (-20_000, 0)
    assert overview(client, world)["overdue"] == {"amount": 0, "customers": 0}
    assert listed(client, world, overdue=True) == []
    late = read(client, world.owner_a, f"{shop(world)}/reports/overdue").json()
    assert world.customer_a.hex not in str(late).replace("-", ""), late
    # A reminder is built from what is overdue: there is nothing to remind of.
    owner.execute("UPDATE shop SET reminders_on = true WHERE id = %s", (world.shop_a,))
    sent = write(
        client, world.manager_a, "POST", f"{shop(world)}/reminders/manual", {"customer_id": str(world.customer_a)}
    )
    assert code(sent) == (409, "REMINDER_NOT_DUE")
    assert owner.execute("SELECT count(*) FROM reminder WHERE shop_id = %s", (world.shop_a,)).fetchone() == (0,)


def test_what_is_owed_and_what_is_held_are_shown_side_by_side_and_never_netted(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    before = overview(client, world)
    assert (before["outstanding"], before["debtors"]) == (50_000, 1)
    assert record(client, world, world.settled_customer_a, "payment", 30_000, advance=True).status_code == 201

    after = overview(client, world)
    # What Ali owes is not reduced by what the shop holds of someone else's money.
    assert (after["outstanding"], after["debtors"]) == (50_000, 1)
    assert after["advances"] == {"amount": 30_000, "customers": 1}
    assert {key: value for key, value in after.items() if key != "advances"} == before

    owing, in_credit = listed(client, world), listed(client, world, in_credit=True)
    assert [balance for _, balance in owing] == [50_000]
    assert [balance for _, balance in in_credit] == [-30_000]
    assert {name for name, _ in owing}.isdisjoint({name for name, _ in in_credit})
    item = read(client, world.owner_a, f"{shop(world)}/overview/debtors", in_credit=True).json()["items"][0]
    assert item["overdue"] == {"amount": 0, "since": None, "days": 0, "due_today": 0}
    # The list of customers gives each their own signed balance.
    everyone = read(client, world.owner_a, f"{shop(world)}/customers").json()["items"]
    assert sorted(person["balance"] for person in everyone) == [-30_000, 50_000]

    report = period(client, world.owner_a, world.shop_a, today(), today())
    assert report["outstanding"]["end"] == 50_000
    assert report["advances"] == {"start": 0, "end": 30_000}
    # The movements still reconcile, on the position as a whole.
    assert (
        report["net_change"] == report["credit"]["amount"] + report["opening"]["amount"] - report["payments"]["amount"]
    )


def test_the_list_of_customers_in_credit_is_paged_largest_first(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    amounts = [4_000, 9_000, 2_000]
    for amount in amounts:
        person = write(client, world.seller_a, "POST", f"{shop(world)}/customers", {"display_name": f"Haqdor {amount}"})
        assert record(client, world, person.json()["id"], "payment", amount, advance=True).status_code == 201
    path = f"{shop(world)}/overview/debtors"
    first = read(client, world.owner_a, path, in_credit=True, limit=2).json()
    assert [item["balance"] for item in first["items"]] == [-9_000, -4_000]
    rest = read(client, world.owner_a, path, in_credit=True, limit=2, cursor=first["next_cursor"]).json()
    assert ([item["balance"] for item in rest["items"]], rest["next_cursor"]) == ([-2_000], None)
    assert read(client, world.owner_a, path, in_credit=True, limit=0).status_code == 422
    assert read(client, world.owner_a, path, in_credit=True, currency="USD").status_code == 422
    assert read(client, world.owner_b, path, in_credit=True).status_code in (403, 404)


def test_a_report_of_a_shop_that_holds_no_advance_says_nothing_of_advances(client: TestClient, world: World) -> None:
    assert "advances" not in period(client, world.owner_a, world.shop_a, today(), today())
    assert "advances" not in overview(client, world)


def test_an_advance_gives_room_under_the_credit_limit(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    """The limit bounds what the customer owes after the sale: what they paid ahead is not owed."""
    customer = world.settled_customer_a
    limited = write(client, world.manager_a, "PATCH", f"{shop(world)}/customers/{customer}", {"credit_limit": 10_000})
    assert limited.status_code == 200, limited.text
    assert settings(client, world, {"sellers_may_exceed": False}).status_code == 200
    assert record(client, world, customer, "payment", 20_000, advance=True).status_code == 201
    within = record(client, world, customer, "credit", 30_000)
    assert (within.status_code, within.json()["customer"]["balance"]) == (201, 10_000)
    assert "limit_warning" not in within.json()
    # And not a so'm more: the seller is stopped where the debt itself would pass the limit.
    assert code(record(client, world, customer, "credit", 100)) == (409, "LIMIT_REACHED")


# --- reversals -----------------------------------------------------------------------------------------------------


def test_reversing_a_sale_that_the_advance_covered_gives_the_advance_back(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    customer = world.settled_customer_a
    assert record(client, world, customer, "payment", 50_000, advance=True).status_code == 201
    sale = record(client, world, customer, "credit", 20_000).json()["entry"]["id"]
    undone = reverse(client, world, sale)
    assert (undone.status_code, undone.json()["customer"]["balance"]) == (201, -50_000)
    assert stored_advances(owner, world) == [(customer, "UZS", 50_000)]
    agree(owner)


def test_reversing_a_paid_sale_leaves_the_payment_as_an_advance_only_where_advances_are_accepted(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    assert record(client, world, world.customer_a, "payment", 50_000).status_code == 201
    assert code(reverse(client, world, world.entry_a)) == (409, "WOULD_GO_NEGATIVE")
    assert settings(client, world, {"accept_advances": True}).status_code == 200
    undone = reverse(client, world, world.entry_a)
    assert (undone.status_code, undone.json()["customer"]["balance"]) == (201, -50_000)
    assert stored_advances(owner, world) == [(world.customer_a, "UZS", 50_000)]


def test_reversing_the_advance_after_sales_used_it_leaves_those_sales_owed_with_their_own_dates(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    customer = world.settled_customer_a
    paid = record(client, world, customer, "payment", 50_000, advance=True).json()["entry"]["id"]
    first = record(client, world, customer, "credit", 20_000).json()["entry"]
    second = record(client, world, customer, "credit", 45_000).json()["entry"]
    assert detail(client, world, customer)["balance"] == 15_000

    undone = reverse(client, world, paid)
    assert (undone.status_code, undone.json()["customer"]["balance"]) == (201, 65_000)
    assert owner.execute(
        "SELECT entry_id::text, remaining, promised_date::text FROM open_debt WHERE customer_id = %s "
        "ORDER BY remaining",
        (customer,),
    ).fetchall() == [(first["id"], 20_000, first["promised_date"]), (second["id"], 45_000, second["promised_date"])]
    assert stored_advances(owner, world) == []
    # A second reversal of the same payment is refused as ever, and nothing else moves.
    assert code(reverse(client, world, paid)) == (409, "ALREADY_REVERSED")
    agree(owner)


def test_a_reversal_cannot_put_a_customer_in_credit_beyond_the_cap(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    customer = world.settled_customer_a
    sale = record(client, world, customer, "credit", 50_000).json()["entry"]["id"]
    assert record(client, world, customer, "payment", MAX_ENTRY, advance=True).status_code == 201
    assert record(client, world, customer, "payment", 50_000, advance=True).status_code == 201
    assert detail(client, world, customer)["balance"] == -MAX_ENTRY
    assert code(reverse(client, world, sale)) == (409, "ADVANCE_TOO_LARGE")
    assert detail(client, world, customer)["balance"] == -MAX_ENTRY


# --- archive and removal: a customer in credit is not settled ----------------------------------------------


def test_a_customer_in_credit_cannot_be_archived_until_it_is_settled(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    customer = world.settled_customer_a
    paid = record(client, world, customer, "payment", 7_000, advance=True).json()["entry"]["id"]
    archive = f"{shop(world)}/customers/{customer}/archive"
    assert code(write(client, world.manager_a, "POST", archive)) == (409, "CUSTOMER_HAS_BALANCE")
    assert owner.execute("SELECT status FROM customer WHERE id = %s", (customer,)).fetchone() == ("active",)
    assert reverse(client, world, paid).status_code == 201
    assert write(client, world.manager_a, "POST", archive).status_code == 200


def test_a_removal_request_of_a_customer_in_credit_waits_until_it_is_settled(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    """Held money is an open account like owed money: the data goes when the balance is nothing, either way."""
    paid = record(client, world, world.customer_a, "payment", 58_000, advance=True).json()["entry"]["id"]
    link = link_of(owner, world.customer_a)
    asked = client.post(f"{ME}/{link}/removal", headers=as_user(world.customer_of_a))
    assert (asked.status_code, asked.json()) == (200, {"removed": False, "waiting_for_balance": -8_000})
    assert owner.execute("SELECT status FROM customer WHERE id = %s", (world.customer_a,)).fetchone() == ("active",)

    # A sale that uses up part of the advance does not settle it; the one that uses the rest does.
    assert record(client, world, world.customer_a, "credit", 5_000).status_code == 201
    assert owner.execute("SELECT status FROM customer WHERE id = %s", (world.customer_a,)).fetchone() == ("active",)
    assert record(client, world, world.customer_a, "credit", 3_000).status_code == 201
    assert owner.execute("SELECT status FROM customer WHERE id = %s", (world.customer_a,)).fetchone() == ("anonymized",)
    del paid


# --- chat ------------------------------------------------------------------------------------------------------------


def test_in_chat_a_payment_beyond_the_debt_is_asked_about_and_never_recorded_silently(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    before = entries(owner, world.shop_a)
    seller = chat_of(client, owner, world.seller_a)
    asked = seller.say("Ali -65000")
    assert asked.text == say(
        "uz",
        "advance_confirm",
        shop="Shop A",
        name="Ali",
        amount=money("uz", 65_000),
        debt=money("uz", 50_000),
        over=money("uz", 15_000),
    )
    assert list(asked.buttons) == [say("uz", "advance_yes"), say("uz", "cancel")]
    assert entries(owner, world.shop_a) == before, "the question writes nothing"

    # Another member cannot answer it.
    stolen = chat_of(client, owner, world.manager_a).press(asked.button(say("uz", "advance_yes")))
    assert stolen.text == say("uz", "expired")
    assert entries(owner, world.shop_a) == before

    saved = seller.press(asked.button(say("uz", "advance_yes")))
    assert saved.text == say(
        "uz", "payment_saved", shop="Shop A", name="Ali", amount=money("uz", 65_000), balance=owed("uz", -15_000)
    )
    assert "-" not in owed("uz", -15_000) and money("uz", 15_000) in owed("uz", -15_000)
    assert entries(owner, world.shop_a)[-1] == ("Ali", "payment", 65_000, world.seller_a_membership)
    # The button works once.
    assert seller.press(asked.button(say("uz", "advance_yes"))).text == say("uz", "expired")
    assert len(entries(owner, world.shop_a)) == len(before) + 1


def test_the_question_about_an_advance_can_be_cancelled(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    before = entries(owner, world.shop_a)
    seller = chat_of(client, owner, world.seller_a)
    asked = seller.say("Ali -65000")
    assert seller.press(asked.button(say("uz", "cancel"))).text == say("uz", "cancelled")
    assert seller.press(asked.button(say("uz", "advance_yes"))).text == say("uz", "expired")
    assert entries(owner, world.shop_a) == before


def test_the_customer_is_told_of_their_advance_in_words_not_by_a_minus_sign(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    assert record(client, world, world.customer_a, "payment", 65_000, advance=True).status_code == 201
    row = owner.execute(
        "SELECT payload->>'text' FROM outbox_message WHERE shop_id = %s AND payload->>'text' LIKE %s "
        "ORDER BY created_at DESC LIMIT 1",
        (world.shop_a, "%" + money("uz", 65_000) + "%"),
    ).fetchone()
    assert row is not None
    assert owed("uz", -15_000) in row[0] and "-15" not in row[0]
    assert owed("uz", -15_000) == say("uz", "in_credit", zero=money("uz", 0), amount=money("uz", 15_000))
    # Their own list of accounts says the same.
    mine = chat_of(client, owner, world.customer_of_a).say("/qarzim").text
    assert owed("uz", -15_000) in mine and "-15" not in mine


@pytest.mark.parametrize("lang", ["uz", "uz-Cyrl", "ru", "tg", "kaa", "en"])
def test_every_language_says_an_advance_in_words(lang: str) -> None:
    text = owed(lang, -15_000)
    assert money(lang, 15_000) in text and money(lang, 0) in text and "-" not in text
    assert owed(lang, 15_000) == money(lang, 15_000) and owed(lang, 0) == money(lang, 0)
    for name in ("advance_confirm", "advance_yes", "ADVANCE_TOO_LARGE"):
        assert say(lang, name, shop="S", name="N", amount="1", debt="2", over="3")


# --- a customer's own payment notice stays within the debt -------------------------------------------------


def test_a_customers_own_notice_of_payment_still_cannot_exceed_the_debt(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    """Kept refused in this pass: an advance is made by the shop's staff, who see the money."""
    sent = client.post(
        f"{ME}/{link_of(owner, world.customer_a)}/payment-notices",
        json={"amount": 50_100},
        headers={**as_user(world.customer_of_a), **key()},
    )
    assert code(sent) == (409, "EXCEEDS_BALANCE"), sent.text
    assert owner.execute("SELECT count(*) FROM payment_notice WHERE shop_id = %s", (world.shop_a,)).fetchone() == (0,)


# --- two currencies: two books ------------------------------------------------------------------------------------


def test_an_advance_in_som_never_offsets_a_dollar_debt(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    dollars: None,  # noqa: F811
    accepting: None,
) -> None:
    customer = world.settled_customer_a
    assert record_in(client, world, "credit", 12_000, customer=customer).status_code == 201
    kept = record_in(client, world, "payment", 40_000, None, customer=customer, advance=True)
    assert kept.status_code == 201, kept.text
    assert (kept.json()["customer"]["balance"], kept.json()["customer"]["usd"]["balance"]) == (-40_000, 12_000)
    assert stored_advances(owner, world) == [(customer, "UZS", 40_000)]
    assert owner.execute(
        "SELECT currency, remaining FROM open_debt WHERE customer_id = %s", (customer,)
    ).fetchall() == [("USD", 12_000)]

    totals = overview(client, world)
    assert totals["advances"] == {"amount": 40_000, "customers": 1}
    assert (totals["usd"]["outstanding"], totals["usd"]["debtors"]) == (12_000, 1)
    assert "advances" not in totals["usd"]
    # In the list of those in credit in so'm the customer's dollar debt is beside it, unchanged.
    item = read(client, world.owner_a, f"{shop(world)}/overview/debtors", in_credit=True).json()["items"][0]
    assert (item["balance"], item["usd"]["balance"]) == (-40_000, 12_000)
    assert listed(client, world, in_credit=True, currency="USD") == []

    # A dollar payment is compared with the dollar debt alone: the so'm advance does not change it.
    asked = record_in(client, world, "payment", 12_500, customer=customer)
    assert asked.json()["error"]["fields"] == {"debt": "12000", "over": "500", "advance": "500"}
    assert record_in(client, world, "payment", 12_500, customer=customer, advance=True).status_code == 201
    assert stored_advances(owner, world) == [(customer, "USD", 500), (customer, "UZS", 40_000)]
    assert overview(client, world)["usd"]["advances"] == {"amount": 500, "customers": 1}
    agree(owner)

    # Dollars held for a customer are dollars the shop still has to settle: they cannot be switched off.
    assert code(write(client, world.owner_a, "PATCH", shop(world), {"usd_on": False})) == (409, "USD_BALANCE_OPEN")


def test_a_customer_picked_from_the_list_is_asked_about_an_advance_like_any_other(
    client: TestClient, world: World, owner: psycopg.Connection, accepting: None
) -> None:
    """Part of a name: first which customer, then, as the payment is more than they owe, whether to keep
    the rest. Two questions, and still nothing is written until the second is answered with yes."""
    before = entries(owner, world.shop_a)
    seller = chat_of(client, owner, world.seller_a)
    which = seller.say("Al -50500")
    assert which.text == say("uz", "pick_customer", shop="Shop A", name="Al", amount=money("uz", 50_500))
    asked = seller.press(which.button("Ali"))
    assert money("uz", 500) in asked.text and money("uz", 50_000) in asked.text
    assert list(asked.buttons) == [say("uz", "advance_yes"), say("uz", "cancel")]
    assert entries(owner, world.shop_a) == before
    saved = seller.press(asked.button(say("uz", "advance_yes")))
    assert owed("uz", -500) in saved.text
    assert entries(owner, world.shop_a)[-1][1:3] == ("payment", 50_500)
    assert len(entries(owner, world.shop_a)) == len(before) + 1
