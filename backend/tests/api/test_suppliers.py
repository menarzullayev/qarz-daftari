"""Suppliers and what the shop owes them, through the API (module I), and the stock in dollars.

A supplier's account is append-only, each currency a book of its own; an entry is cancelled by a reversal
that says why. Each rule has the case that works and the case that is refused.
"""

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.domain.languages import LANGUAGES
from qarz.interface.errors import message_text

from .conftest import World, as_user, set_overrides, switch_permissions_on
from .test_customers_ledger import key, read, shop, write
from .test_stock import actions, counted_item, document, item_of, line, mismatches, on, receive, stock
from .test_stock_documents import cancel, owed, supplier
from .test_usd import dollars, platform_on, record

pytestmark = pytest.mark.db

__all__ = ["dollars", "on", "platform_on"]


def suppliers(world: World) -> str:
    return f"{shop(world)}/suppliers"


def entry(
    client: TestClient, world: World, who: Any, kind: str, amount: Any, user: uuid.UUID | None = None, **extra: Any
) -> Any:
    body = {"kind": kind, "amount": amount, **extra}
    return write(client, user or world.manager_a, "POST", f"{suppliers(world)}/{who}/entries", body)


def account(client: TestClient, world: World, who: Any, **params: Any) -> dict[str, Any]:
    response = read(client, world.manager_a, f"{suppliers(world)}/{who}", **params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def cancel_entry(client: TestClient, world: World, who: Any, entry_id: Any, reason: str = "Xato summa") -> Any:
    path = f"{suppliers(world)}/{who}/entries/{entry_id}/cancel"
    return write(client, world.manager_a, "POST", path, {"reason": reason})


# --- the list ----------------------------------------------------------------------------------------------


def test_a_supplier_has_a_name_and_may_have_a_phone_and_a_note(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    made = write(
        client,
        world.manager_a,
        "POST",
        suppliers(world),
        {"name": "  Ulgurji   bozor ", "phone": "90 123 45 67", "note": "Seshanba kuni keladi"},
    )
    assert made.status_code == 201, made.text
    assert made.json() == {
        "id": made.json()["id"],
        "name": "Ulgurji bozor",
        "phone": "+998901234567",
        "note": "Seshanba kuni keladi",
        "status": "active",
        "balances": [],
    }
    same = write(client, world.manager_a, "POST", suppliers(world), {"name": "ULGURJI BOZOR"})
    assert same.status_code == 409 and same.json()["error"]["code"] == "SUPPLIER_NAME_TAKEN"
    for body, field in (
        ({"name": "  "}, "name"),
        ({"name": "x" * 81}, "name"),
        ({"name": "Ali", "phone": "12"}, "phone"),
        ({"name": "Ali", "note": "x" * 201}, "note"),
    ):
        refused = write(client, world.manager_a, "POST", suppliers(world), body)
        assert refused.status_code == 422 and field in refused.json()["error"]["fields"], refused.text
    assert write(client, world.seller_a, "POST", suppliers(world), {"name": "Ali"}).status_code == 403
    assert read(client, world.seller_a, suppliers(world)).status_code == 403, "a seller has no part in suppliers"

    renamed = write(client, world.manager_a, "PUT", f"{suppliers(world)}/{made.json()['id']}", {"name": "Bozor"})
    assert renamed.status_code == 200
    assert (renamed.json()["name"], renamed.json()["phone"], renamed.json()["note"]) == ("Bozor", None, None)
    assert actions(owner, world, "supplier.") == ["supplier.created", "supplier.updated"]


def test_the_list_is_searched_paged_and_carries_what_is_owed(client: TestClient, world: World, on: None) -> None:
    made = [supplier(client, world, f"Ta'minotchi {number}") for number in range(4)]
    assert entry(client, world, made[1], "opening", 300_000).status_code == 201
    assert entry(client, world, made[2], "opening", 200_000).status_code == 201
    assert entry(client, world, made[3], "payment", 50_000).status_code == 201  # an advance: paid before the goods

    first = read(client, world.manager_a, suppliers(world), limit=3).json()
    assert [row["id"] for row in first["suppliers"]] == made[:3] and first["next_cursor"]
    assert [row["balances"] for row in first["suppliers"]] == [
        [],
        [{"currency": "UZS", "balance": 300_000}],
        [{"currency": "UZS", "balance": 200_000}],
    ]
    assert first["totals"] == [{"currency": "UZS", "owed": 500_000}], (
        "debts only: an advance is not netted against them"
    )
    rest = read(client, world.manager_a, suppliers(world), limit=3, cursor=first["next_cursor"]).json()
    assert [row["balances"] for row in rest["suppliers"]] == [[{"currency": "UZS", "balance": -50_000}]]
    assert rest["next_cursor"] is None
    found = read(client, world.manager_a, suppliers(world), q="minotchi 2").json()["suppliers"]
    assert [row["id"] for row in found] == [made[2]]
    for params in ({"limit": 0}, {"status": "gone"}, {"cursor": "x"}, {"q": "x" * 81}):
        assert read(client, world.manager_a, suppliers(world), **params).status_code == 422, params


def test_a_supplier_with_an_open_account_cannot_be_archived_and_an_archived_one_takes_nothing(
    client: TestClient, world: World, on: None
) -> None:
    who = supplier(client, world)
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    opened = entry(client, world, who, "opening", 100_000).json()["entry"]["id"]
    archive = f"{suppliers(world)}/{who}/archive"
    refused = write(client, world.manager_a, "POST", archive)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "SUPPLIER_HAS_BALANCE"
    assert cancel_entry(client, world, who, opened).status_code == 200
    assert write(client, world.manager_a, "POST", archive).json()["status"] == "archived"
    assert read(client, world.manager_a, suppliers(world)).json()["suppliers"] == []
    assert len(read(client, world.manager_a, suppliers(world), status="archived").json()["suppliers"]) == 1

    paid = entry(client, world, who, "payment", 5_000)
    assert paid.status_code == 409 and paid.json()["error"]["code"] == "SUPPLIER_ARCHIVED"
    bought = document(client, world, kind="receipt", supplier_id=who, post=True, lines=[line(rice, "1", 10_000)])
    assert bought.status_code == 409 and bought.json()["error"]["code"] == "SUPPLIER_ARCHIVED"
    assert item_of(client, world, rice)["on_hand"] == "0"
    back = write(client, world.manager_a, "POST", f"{suppliers(world)}/{who}/unarchive")
    assert back.status_code == 200 and back.json()["status"] == "active"
    assert entry(client, world, who, "payment", 5_000).status_code == 201


# --- the account -------------------------------------------------------------------------------------------


def test_receipts_raise_what_is_owed_payments_lower_it_and_the_account_shows_each(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    who = supplier(client, world)
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)], supplier_id=who)
    paid = entry(client, world, who, "payment", 40_000, note="Naqd berildi")
    assert paid.status_code == 201, paid.text
    assert paid.json()["supplier"]["balances"] == [{"currency": "UZS", "balance": 60_000}]
    assert paid.json()["entry"]["in_cash_book"] is False, "the cash book is off: the payment is in the account alone"
    body = account(client, world, who)
    assert [(row["seq"], row["kind"], row["amount"], row["note"]) for row in body["entries"]] == [
        (2, "payment", 40_000, "Naqd berildi"),
        (1, "purchase", 100_000, None),
    ]
    assert body["entries"][1]["document"]["kind"] == "receipt" and body["entries"][1]["document"]["number"] == 1
    # More than is owed is an advance, not a refusal.
    assert entry(client, world, who, "payment", 90_000).status_code == 201
    assert owed(client, world, who) == [{"currency": "UZS", "balance": -30_000}]
    assert actions(owner, world, "supplier.payment") == ["supplier.payment_recorded"] * 2
    page = account(client, world, who, limit=2)
    assert [row["seq"] for row in page["entries"]] == [3, 2] and page["next_cursor"]
    assert [row["seq"] for row in account(client, world, who, limit=2, cursor=page["next_cursor"])["entries"]] == [1]
    assert mismatches(owner, world) == []


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"kind": "purchase", "amount": 1_000}, "kind"),
        ({"kind": "return", "amount": 1_000}, "kind"),
        ({"kind": "reversal", "amount": 1_000}, "kind"),
        ({"kind": "payment", "amount": 0}, "amount"),
        ({"kind": "payment", "amount": -5}, "amount"),
        ({"kind": "payment", "amount": 10**12 + 1}, "amount"),
        ({"kind": "payment", "amount": 1_000, "note": "x" * 201}, "note"),
        ({"kind": "payment", "amount": 1_000, "currency": "EUR"}, "currency"),
        ({"kind": "payment", "amount": 1_000, "currency": "USD"}, "currency"),  # the shop does not work in dollars
    ],
)
def test_a_wrong_entry_is_refused_and_not_stored(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, body: dict[str, Any], field: str
) -> None:
    who = supplier(client, world)
    refused = write(client, world.manager_a, "POST", f"{suppliers(world)}/{who}/entries", body)
    assert refused.status_code == 422 and field in refused.json()["error"]["fields"], refused.text
    assert account(client, world, who)["entries"] == []
    assert entry(client, world, uuid.uuid4(), "payment", 1_000).status_code == 404


def test_an_entry_is_cancelled_by_a_reversal_that_says_why_and_only_once(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    who, other = supplier(client, world), supplier(client, world, "Boshqa")
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    posted = receive(client, world, [line(rice, "10", 10_000)], supplier_id=who)
    paid = entry(client, world, who, "payment", 40_000).json()["entry"]["id"]

    no_reason = cancel_entry(client, world, who, paid, " ")
    assert no_reason.status_code == 422 and "reason" in no_reason.json()["error"]["fields"]
    elsewhere = cancel_entry(client, world, other, paid)
    assert elsewhere.status_code == 404, "another supplier's entry is not there"
    done = cancel_entry(client, world, who, paid, "Ikki marta yozilgan")
    assert done.status_code == 200, done.text
    assert done.json()["entry"]["kind"] == "reversal" and done.json()["entry"]["note"] == "Ikki marta yozilgan"
    assert done.json()["supplier"]["balances"] == [{"currency": "UZS", "balance": 100_000}]
    rows = account(client, world, who)["entries"]
    assert [(row["kind"], row["reversed"], row["reverses_id"]) for row in rows] == [
        ("reversal", False, paid),
        ("payment", True, None),
        ("purchase", False, None),
    ]
    again = cancel_entry(client, world, who, paid)
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_REVERSED"
    reversal = cancel_entry(client, world, who, rows[0]["id"])
    assert reversal.status_code == 409 and reversal.json()["error"]["code"] == "CANNOT_REVERSE_REVERSAL"
    # What a receipt wrote is cancelled with the receipt, goods and all, and not on its own.
    purchase = cancel_entry(client, world, who, rows[2]["id"])
    assert purchase.status_code == 409 and purchase.json()["error"]["code"] == "ENTRY_OF_DOCUMENT"
    assert cancel(client, world, posted["id"]).status_code == 200
    assert owed(client, world, who) == [] and item_of(client, world, rice)["on_hand"] == "0"
    assert "supplier.entry_cancelled" in actions(owner, world, "supplier.")
    assert mismatches(owner, world) == []

    # The database itself holds the account to its rules: nothing is edited or deleted, and a reversal
    # repeats the entry it reverses.
    for statement in ("UPDATE supplier_entry SET amount = 1", "DELETE FROM supplier_entry"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), owner.transaction():
            owner.execute("SET LOCAL ROLE qd_app")
            owner.execute(f"SELECT set_config('qd.shop_id', '{world.shop_a}', true)")
            owner.execute(statement)


def test_paying_and_stating_an_old_debt_are_separate_permissions(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    who = supplier(client, world)
    old = entry(client, world, who, "opening", 100_000).json()["entry"]["id"]
    paid = entry(client, world, who, "payment", 10_000).json()["entry"]["id"]
    switch_permissions_on(owner)
    payer, keeper = world.seller_a, world.manager_a
    set_overrides(owner, world.seller_a_membership, granted=["suppliers.pay", "suppliers.view"])
    set_overrides(owner, world.manager_a_membership, denied=["suppliers.pay"])

    def refused(response: Any, permission: str) -> bool:
        return bool(response.status_code == 403 and response.json()["error"]["fields"] == {"permission": permission})

    assert entry(client, world, who, "payment", 5_000, payer).status_code == 201
    assert refused(entry(client, world, who, "opening", 5_000, payer), "suppliers.manage")
    assert entry(client, world, who, "opening", 5_000, keeper).status_code == 201
    assert refused(entry(client, world, who, "payment", 5_000, keeper), "suppliers.pay")
    # Cancelling needs what recording needed.
    path = f"{suppliers(world)}/{who}/entries"
    assert refused(write(client, payer, "POST", f"{path}/{old}/cancel", {"reason": "x"}), "suppliers.manage")
    assert refused(write(client, keeper, "POST", f"{path}/{paid}/cancel", {"reason": "x"}), "suppliers.pay")
    assert write(client, payer, "POST", f"{path}/{paid}/cancel", {"reason": "x"}).status_code == 200
    assert write(client, keeper, "POST", f"{path}/{old}/cancel", {"reason": "x"}).status_code == 200
    assert owed(client, world, who) == []


def test_a_repeated_payment_request_writes_one_entry(client: TestClient, world: World, on: None) -> None:
    who = supplier(client, world)
    headers = {**as_user(world.manager_a), **key()}
    body = {"kind": "payment", "amount": 7_000}
    first = client.post(f"{suppliers(world)}/{who}/entries", json=body, headers=headers)
    second = client.post(f"{suppliers(world)}/{who}/entries", json=body, headers=headers)
    assert first.status_code == second.status_code == 201 and first.json() == second.json()
    assert len(account(client, world, who)["entries"]) == 1


def test_another_shop_sees_nothing_of_a_shops_suppliers(client: TestClient, world: World, on: None) -> None:
    who = supplier(client, world)
    theirs = f"/api/v1/shops/{world.shop_b}/suppliers"
    assert read(client, world.owner_b, theirs).json()["suppliers"] == []
    assert read(client, world.owner_b, f"{theirs}/{who}").status_code == 404
    refused = write(client, world.owner_b, "POST", f"{theirs}/{who}/entries", {"kind": "payment", "amount": 1_000})
    assert refused.status_code == 404
    assert account(client, world, who)["entries"] == []


# --- dollars -----------------------------------------------------------------------------------------------


def test_dollars_are_a_book_of_their_own_for_a_supplier_and_for_the_cost_of_goods(
    client: TestClient, world: World, on: None, dollars: None, owner: psycopg.Connection
) -> None:
    """A shop that works in dollars buys in dollars: the supplier is owed dollars beside so'm, never
    added together, and an item's cost is kept in the currency of its receipts, one at a time."""
    assert read(client, world.seller_a, f"{stock(world)}/settings").json()["currencies"] == ["UZS", "USD"]
    who = supplier(client, world)
    phone = counted_item(client, world, "Telefon g'ilofi", 60_000)
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)], supplier_id=who)
    bought = receive(client, world, [line(phone, "20", 350)], supplier_id=who, currency="USD", paid=2_000)
    assert (bought["currency"], bought["total"], bought["paid"]) == ("USD", 7_000, 2_000)
    assert owed(client, world, who) == [
        {"currency": "UZS", "balance": 100_000},
        {"currency": "USD", "balance": 5_000},
    ]
    assert entry(client, world, who, "payment", 1_500, currency="USD").status_code == 201
    listed = read(client, world.manager_a, suppliers(world)).json()
    assert listed["totals"] == [{"currency": "UZS", "owed": 100_000}, {"currency": "USD", "owed": 3_500}]

    cost = item_of(client, world, phone)["cost"]
    assert cost == {"currency": "USD", "average": 350, "value": 7_000, "margin": None}, "so'm less cents is no margin"
    # While something is on hand its cost stays in dollars: a so'm receipt of it is refused, whole.
    mixed = document(
        client, world, kind="receipt", post=True, lines=[line(rice, "1", 10_000), line(phone, "1", 40_000)]
    )
    assert mixed.status_code == 409 and mixed.json()["error"]["code"] == "COST_CURRENCY_MISMATCH"
    assert mixed.json()["error"]["fields"] == {"item": phone, "name": "Telefon g'ilofi", "held": "USD", "given": "UZS"}
    assert item_of(client, world, rice)["on_hand"] == "10"
    # Once nothing is on hand the cost starts again in the currency of the next receipt.
    gone = document(client, world, kind="write_off", reason="lost", post=True, lines=[line(phone, "20")])
    assert gone.status_code == 201
    receive(client, world, [line(phone, "2", 45_000)])
    assert item_of(client, world, phone)["cost"] == {
        "currency": "UZS",
        "average": 45_000,
        "value": 90_000,
        "margin": 15_000,
    }
    report = read(client, world.manager_a, f"{stock(world)}/report").json()
    assert report["totals"]["cost"] == [{"currency": "UZS", "value": 190_000}]
    assert mismatches(owner, world) == []


def test_the_report_keeps_the_cost_of_each_currency_apart(
    client: TestClient, world: World, on: None, dollars: None
) -> None:
    phone = counted_item(client, world, "Telefon g'ilofi", 60_000)
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)])
    receive(client, world, [line(phone, "20", 350)], currency="USD")
    totals = read(client, world.manager_a, f"{stock(world)}/report").json()["totals"]
    assert sorted(totals["cost"], key=lambda row: str(row["currency"])) == [
        {"currency": "USD", "value": 7_000},
        {"currency": "UZS", "value": 100_000},
    ]
    assert totals["selling"] == 150_000 + 1_200_000
    assert totals["margin"] == {"selling": 150_000, "cost": 100_000, "margin": 50_000}, "of the so'm-costed goods only"


# --- turning the shop's dollars off ----------------------------------------------------------------------


def turn_dollars(client: TestClient, world: World, on: bool) -> Any:
    return write(client, world.owner_a, "PATCH", shop(world), {"usd_on": on})


def refused_with(response: Any, code: str) -> str:
    assert (response.status_code, response.json()["error"]["code"]) == (409, code), response.text
    return str(response.json()["error"]["message"])


def test_dollars_cannot_be_turned_off_while_a_supplier_account_is_open_in_dollars(
    client: TestClient, world: World, on: None, dollars: None, owner: psycopg.Connection
) -> None:
    """The customers' rule (USD_BALANCE_OPEN) for suppliers: a dollar account that is not at zero would
    stay, unseen and unpayable. The refusal says it is the suppliers', not the customers'."""
    who = supplier(client, world)
    assert entry(client, world, who, "opening", 4_000, user=world.owner_a, currency="USD").status_code == 201
    said = refused_with(turn_dollars(client, world, False), "USD_SUPPLIER_BALANCE_OPEN")
    assert "ta'minotchi" in said and "mijoz" not in said
    assert owner.execute("SELECT usd_on FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == (True,)
    # A change sent with it is not half applied.
    assert write(client, world.owner_a, "PATCH", shop(world), {"usd_on": False, "name": "Boshqa"}).status_code == 409
    assert owner.execute("SELECT name FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == ("Shop A",)

    # Paid ahead is open as well: the supplier owes the shop dollars.
    assert entry(client, world, who, "payment", 5_000, currency="USD").status_code == 201
    assert owed(client, world, who) == [{"currency": "USD", "balance": -1_000}]
    refused_with(turn_dollars(client, world, False), "USD_SUPPLIER_BALANCE_OPEN")

    # At zero it can be turned off; what is owed in so'm is no obstacle.
    assert entry(client, world, who, "opening", 1_000, user=world.owner_a, currency="USD").status_code == 201
    assert entry(client, world, who, "opening", 70_000, user=world.owner_a).status_code == 201
    turned = turn_dollars(client, world, False)
    assert (turned.status_code, turned.json()["usd_on"]) == (200, False)
    assert mismatches(owner, world) == []


def test_dollars_cannot_be_turned_off_while_goods_costed_in_dollars_are_on_hand(
    client: TestClient, world: World, on: None, dollars: None, owner: psycopg.Connection
) -> None:
    phone = counted_item(client, world, "Telefon g'ilofi", 60_000)
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)])
    receive(client, world, [line(phone, "20", 350)], currency="USD")  # paid at once: no supplier is owed
    said = refused_with(turn_dollars(client, world, False), "USD_STOCK_OPEN")
    assert "ombor" in said and "ta'minotchi" not in said and "mijoz" not in said
    assert owner.execute("SELECT usd_on FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == (True,)

    # Part of it gone is not enough; all of it gone is, and the so'm goods on hand are no obstacle.
    part = document(client, world, kind="write_off", reason="lost", post=True, lines=[line(phone, "19")])
    assert part.status_code == 201
    refused_with(turn_dollars(client, world, False), "USD_STOCK_OPEN")
    rest = document(client, world, kind="write_off", reason="lost", post=True, lines=[line(phone, "1")])
    assert rest.status_code == 201
    assert item_of(client, world, rice)["on_hand"] == "10"
    turned = turn_dollars(client, world, False)
    assert (turned.status_code, turned.json()["usd_on"]) == (200, False)


def test_the_refusals_come_in_a_fixed_order_customers_then_suppliers_then_goods(
    client: TestClient, world: World, on: None, dollars: None
) -> None:
    who = supplier(client, world)
    phone = counted_item(client, world, "Telefon g'ilofi", 60_000)
    receive(client, world, [line(phone, "20", 350)], supplier_id=who, currency="USD")  # owed, and on hand
    assert record(client, world, "credit", 500).status_code == 201

    refused_with(turn_dollars(client, world, False), "USD_BALANCE_OPEN")
    assert record(client, world, "payment", 500).status_code == 201
    refused_with(turn_dollars(client, world, False), "USD_SUPPLIER_BALANCE_OPEN")
    assert entry(client, world, who, "payment", 7_000, currency="USD").status_code == 201
    refused_with(turn_dollars(client, world, False), "USD_STOCK_OPEN")


def test_another_shops_dollars_in_the_stock_do_not_hold_this_shops_setting(
    client: TestClient, world: World, on: None, dollars: None, owner: psycopg.Connection
) -> None:
    """The counterpart of the two rules: they read this shop's rows only."""
    theirs = uuid.uuid4()
    owner.execute(
        "INSERT INTO supplier (id, shop_id, name, name_norm) VALUES (%s, %s, 'Boshqa', 'boshqa')",
        (theirs, world.shop_b),
    )
    owner.execute(
        "INSERT INTO supplier_balance (shop_id, supplier_id, currency, balance) VALUES (%s, %s, 'USD', 900)",
        (world.shop_b, theirs),
    )
    try:
        turned = turn_dollars(client, world, False)
        assert (turned.status_code, turned.json()["usd_on"]) == (200, False)
    finally:
        owner.execute("DELETE FROM supplier_balance WHERE supplier_id = %s", (theirs,))
        owner.execute("DELETE FROM supplier WHERE id = %s", (theirs,))


def test_with_the_stock_switched_off_its_dollars_are_not_asked_about(
    client: TestClient, world: World, on: None, dollars: None, owner: psycopg.Connection
) -> None:
    """Switch off, behaviour as before the stock existed: turning dollars off asks about customers
    alone, and nothing reads the stock's tables. What the stock holds in dollars waits, as every
    dollar figure does while dollars are off."""
    who = supplier(client, world)
    phone = counted_item(client, world, "Telefon g'ilofi", 60_000)
    receive(client, world, [line(phone, "20", 350)], supplier_id=who, currency="USD")
    refused_with(turn_dollars(client, world, False), "USD_SUPPLIER_BALANCE_OPEN")
    owner.execute("UPDATE platform_setting SET value = 'false' WHERE key = 'stock_on'")
    turned = turn_dollars(client, world, False)
    assert (turned.status_code, turned.json()["usd_on"]) == (200, False)
    kept = owner.execute("SELECT balance FROM supplier_balance WHERE supplier_id = %s AND currency = 'USD'", (who,))
    assert kept.fetchone() == (7_000,)


@pytest.mark.parametrize("code", ["USD_SUPPLIER_BALANCE_OPEN", "USD_STOCK_OPEN"])
def test_each_refusal_of_turning_dollars_off_has_its_own_words_in_every_language(code: str) -> None:
    said = {lang: message_text(lang, code) for lang in LANGUAGES}
    assert len(set(said.values())) == len(LANGUAGES), "no language reads another's text"
    assert all(text != message_text(lang, "USD_BALANCE_OPEN") for lang, text in said.items())
