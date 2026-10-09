"""Where the stock and the suppliers meet the cash book (modules I and H).

Money the stock pays out is an expense of the cash book while the cash book is on, written in the same
transaction and cancelled with what wrote it; with the cash book off it is in the supplier's account
alone. Each rule has the case that works and the case that is refused.
"""

from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from . import test_cash_book as book
from .conftest import World, as_user
from .test_customers_ledger import new_customer, read, write
from .test_goods_lines import chosen, sell
from .test_stock import counted_item, document, line, on, receive, stock, switch
from .test_stock_documents import cancel, supplier
from .test_suppliers import account, cancel_entry, entry

pytestmark = pytest.mark.db

__all__ = ["on"]

PURCHASES = "Ombor: tovar xaridi"
REFUNDS = "Ombor: mijozga qaytarildi"


def cash_rows(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    """The shop's cash entries, oldest first: (category, direction, method, currency, amount, note,
    cancelled, why, of a supplier's payment, of a document)."""
    return owner.execute(
        "SELECT c.name, e.direction, e.method, e.currency, e.amount, e.note, e.cancelled_at IS NOT NULL, "
        "       e.cancel_reason, e.supplier_entry_id IS NOT NULL, e.stock_document_id IS NOT NULL "
        "FROM cash_entry e JOIN cash_category c ON c.id = e.category_id WHERE e.shop_id = %s "
        "ORDER BY e.created_at, e.id",
        (world.shop_a,),
    ).fetchall()


def test_a_client_learns_that_the_stock_is_on_from_a_header_and_only_then(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    before = client.get("/api/v1/me/shops", headers=as_user(world.seller_a))
    assert "x-qarz-stock" not in before.headers
    switch(owner)
    after = client.get("/api/v1/me/shops", headers=as_user(world.seller_a))
    assert after.headers["x-qarz-stock"] == "on"
    assert after.json() == before.json(), "the body is the one it always was"
    switch(owner, "false")
    assert "x-qarz-stock" not in client.get("/api/v1/me/shops", headers=as_user(world.seller_a)).headers


def test_with_the_cash_book_off_a_payment_is_in_the_suppliers_account_alone(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    assert read(client, world.seller_a, f"{stock(world)}/settings").json()["cash_book"] is False
    who = supplier(client, world)
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    paid = entry(client, world, who, "payment", 40_000, method="card")
    assert paid.status_code == 201 and paid.json()["entry"]["in_cash_book"] is False
    receive(client, world, [line(rice, "1", 10_000)])
    assert cash_rows(owner, world) == []
    assert owner.execute("SELECT count(*) FROM cash_category WHERE shop_id = %s", (world.shop_a,)).fetchone() == (0,)
    # Turning the cash book on later copies nothing back, and cancelling the payment then touches nothing there.
    book.switch(owner)
    assert cancel_entry(client, world, who, paid.json()["entry"]["id"]).status_code == 200
    assert cash_rows(owner, world) == []


def test_a_payment_to_a_supplier_is_an_expense_of_the_cash_book_and_is_cancelled_with_it(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    book.switch(owner)
    assert read(client, world.seller_a, f"{stock(world)}/settings").json()["cash_book"] is True
    who = supplier(client, world)
    paid = entry(client, world, who, "payment", 40_000, note="Avans", method="transfer")
    assert paid.status_code == 201, paid.text
    assert paid.json()["entry"]["in_cash_book"] is True
    assert cash_rows(owner, world) == [
        (PURCHASES, "expense", "transfer", "UZS", 40_000, "Avans", False, None, True, False)
    ]
    today = book.day(client, world)
    assert book.figures(book.line(today, "transfer")) == (0, 0, 40_000, -40_000)
    written = today["entries"][0]
    assert (written["source"], written["category"]["name"]) == ("stock", PURCHASES)

    # It is cancelled where it was written, not in the cash book.
    refused = book.cancel(client, world, written["id"])
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "CASH_ENTRY_OF_STOCK"
    assert cash_rows(owner, world)[0][6] is False
    done = cancel_entry(client, world, who, paid.json()["entry"]["id"], "Ikki marta yozilgan")
    assert done.status_code == 200, done.text
    assert cash_rows(owner, world) == [
        (PURCHASES, "expense", "transfer", "UZS", 40_000, "Avans", True, "Ikki marta yozilgan", True, False)
    ]
    assert book.figures(book.line(book.day(client, world), "transfer")) == (0, 0, 0, 0)
    assert [row["in_cash_book"] for row in account(client, world, who)["entries"]] == [False, True]

    # An opening balance is no money paid: nothing in the cash book. A payment without a method is cash.
    assert entry(client, world, who, "opening", 90_000).status_code == 201
    assert entry(client, world, who, "payment", 5_000).status_code == 201
    assert [(row[2], row[4]) for row in cash_rows(owner, world)] == [("transfer", 40_000), ("cash", 5_000)]
    for body, field in (
        ({"kind": "payment", "amount": 1_000, "method": "cheque"}, "method"),
        ({"kind": "opening", "amount": 1_000, "method": "cash"}, "method"),
    ):
        wrong = write(client, world.manager_a, "POST", f"{book.shop(world)}/suppliers/{who}/entries", body)
        assert wrong.status_code == 422 and field in wrong.json()["error"]["fields"], wrong.text
    assert len(cash_rows(owner, world)) == 2


def test_nobody_writes_by_hand_under_the_stocks_own_categories(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """Their totals are what the stock's documents say, so a person records nothing there; and like the
    ledger's own category they can be renamed and nothing else."""
    book.switch(owner)
    who = supplier(client, world)
    assert entry(client, world, who, "payment", 40_000).status_code == 201
    made = [item for item in book.categories(client, world) if item["name"] == PURCHASES]
    assert len(made) == 1 and made[0]["fixed"] is True and made[0]["direction"] == "expense"
    by_hand = book.add(client, world, "expense", 10_000, category_id=made[0]["id"])
    assert by_hand.status_code == 422 and "category_id" in by_hand.json()["error"]["fields"]
    # The shop's own "Tovar xaridi" is still its own, for shops that buy without the stock.
    assert book.add(client, world, "expense", 10_000, name="Tovar xaridi").status_code == 201
    assert len(cash_rows(owner, world)) == 2


def test_a_shop_that_already_named_a_category_so_keeps_it_and_the_stocks_gets_a_number(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    book.switch(owner)
    path = f"{book.base(world)}/categories"
    own = write(client, world.manager_a, "POST", path, {"direction": "expense", "name": PURCHASES})
    assert own.status_code == 201, own.text
    who = supplier(client, world)
    assert entry(client, world, who, "payment", 40_000).status_code == 201
    assert cash_rows(owner, world)[0][0] == f"{PURCHASES} 2"


def categories(owner: psycopg.Connection, world: World) -> list[str]:
    rows = owner.execute(
        "SELECT name FROM cash_category WHERE shop_id = %s ORDER BY direction DESC, created_at, name", (world.shop_a,)
    ).fetchall()
    return sorted(str(name) for (name,) in rows)


@pytest.mark.parametrize(
    ("lang", "purchases", "refunds", "note", "sales"),
    [
        ("tg", "Анбор: хариди мол", "Анбор: ба мизоҷ баргардонида шуд", "Воридоти мол № 1", "Фурӯш"),
        ("kaa", "Sklad: tovar satıp alıw", "Sklad: qarıydarǵa qaytarıldı", "Kiris № 1", "Sawda"),
        ("en", "Stock: goods purchase", "Stock: refund to customer", "Goods receipt No. 1", "Sales"),
        ("uz-Cyrl", "Омбор: товар хариди", "Омбор: мижозга қайтарилди", "Кирим № 1", "Савдо"),
    ],
)
def test_the_categories_and_the_notes_are_written_in_the_shops_language_and_stay_as_written(
    client: TestClient,
    world: World,
    on: None,
    owner: psycopg.Connection,
    lang: str,
    purchases: str,
    refunds: str,
    note: str,
    sales: str,
) -> None:
    """What the stock writes into the cash book and the accounts is in the shop's language at that moment,
    in each of the six (it was Uzbek for every language but Russian). And it is then the shop's own data:
    a shop that changes its language later keeps the names and notes it has, and what is made after the
    change is in the new language."""
    book.switch(owner)
    owner.execute("UPDATE shop SET lang = %s WHERE id = %s", (lang, world.shop_a))
    who = supplier(client, world)
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)], supplier_id=who, paid=30_000)
    assert [(row[0], row[5]) for row in cash_rows(owner, world)] == [(purchases, note)]
    named = categories(owner, world)
    assert purchases in named and sales in named and len(named) == 11, named
    assert refunds not in named, "the stock's second category is made when money is first handed back"
    supplier_note = owner.execute(
        "SELECT note FROM supplier_entry WHERE shop_id = %s ORDER BY created_at, seq", (world.shop_a,)
    ).fetchall()
    assert {text for (text,) in supplier_note if text is not None} == {note}

    # The shop turns to Russian: nothing it has is renamed, and the next thing made is Russian.
    owner.execute("UPDATE shop SET lang = 'ru' WHERE id = %s", (world.shop_a,))
    receive(client, world, [line(rice, "1", 10_000)], supplier_id=who, paid=10_000)
    assert [(row[0], row[5]) for row in cash_rows(owner, world)] == [(purchases, note), (purchases, "Приход № 2")]
    assert categories(owner, world) == named


def test_a_purchase_for_cash_and_what_is_paid_at_once_to_a_supplier_are_in_the_cash_book(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    book.switch(owner)
    who = supplier(client, world)
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    for_cash = receive(client, world, [line(rice, "2", 10_000)], method="card")
    on_credit = receive(client, world, [line(rice, "10", 10_000)], supplier_id=who, paid=30_000)
    unpaid = receive(client, world, [line(rice, "1", 10_000)], supplier_id=who)
    assert cash_rows(owner, world) == [
        (PURCHASES, "expense", "card", "UZS", 20_000, "Kirim № 1", False, None, False, True),
        (PURCHASES, "expense", "cash", "UZS", 30_000, "Kirim № 2", False, None, True, False),
    ]
    assert unpaid["paid"] == 0, "nothing paid, nothing in the cash book"
    # A method without money paid at once has nothing to describe.
    wrong = document(client, world, kind="receipt", supplier_id=who, method="cash", lines=[line(rice, "1", 1_000)])
    assert wrong.status_code == 422 and "method" in wrong.json()["error"]["fields"]

    # Neither is cancelled in the cash book; cancelling the receipt cancels its money with its goods.
    for written in book.day(client, world)["entries"]:
        refused = book.cancel(client, world, written["id"])
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "CASH_ENTRY_OF_STOCK"
    assert cancel(client, world, on_credit["id"], "Xato kirim").status_code == 200
    assert cancel(client, world, for_cash["id"], "Xato kirim").status_code == 200
    assert [(row[4], row[6], row[7]) for row in cash_rows(owner, world)] == [
        (20_000, True, "Xato kirim"),
        (30_000, True, "Xato kirim"),
    ]
    # A draft says how it will be paid and writes nothing until it is posted.
    draft = document(client, world, kind="receipt", method="transfer", lines=[line(rice, "1", 5_000)]).json()
    assert draft["method"] == "transfer" and len(cash_rows(owner, world)) == 2
    posted = write(client, world.manager_a, "POST", f"{stock(world)}/documents/{draft['id']}/post")
    assert posted.status_code == 200 and "method" not in posted.json()
    assert cash_rows(owner, world)[2][:5] == (PURCHASES, "expense", "transfer", "UZS", 5_000)


def test_what_a_customer_is_handed_back_for_returned_goods_is_an_expense_too(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    book.switch(owner)
    customer = new_customer(client, world, "Vali")
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)], supplier_id=supplier(client, world))
    assert sell(client, world, customer, [chosen(rice, "2", 15_000)]).status_code == 201  # owes 30 000
    back = document(
        client,
        world,
        kind="customer_return",
        customer_id=customer,
        paid=15_000,
        post=True,
        lines=[line(rice, "2", 15_000)],
    )
    assert back.status_code == 201, back.text
    # Half lowered the debt: a payment entry of the ledger, but goods came back and no money did, so
    # the cash book takes nothing for it. The other half was handed back: an expense.
    assert cash_rows(owner, world) == [
        (REFUNDS, "expense", "cash", "UZS", 15_000, "Tovar qaytarildi, hujjat № 1", False, None, False, True)
    ]
    # Nor does copying past payments into the book take it for money received.
    copied = write(client, world.owner_a, "POST", f"{book.base(world)}/backfill", {})
    assert copied.status_code == 200, copied.text
    assert len(cash_rows(owner, world)) == 1
    assert cancel(client, world, back.json()["id"], "Xato").status_code == 200
    assert [(row[0], row[6], row[7]) for row in cash_rows(owner, world)] == [(REFUNDS, True, "Xato")]
