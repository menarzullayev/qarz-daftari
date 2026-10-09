"""How the ledger feeds the cash book, the chat's `/kassa`, dollars, the export and the backfill
(expansion module H; BR-48 to BR-50).

A customer's payment is money received: with the cash book on it is in the book from the transaction that
records it, and leaves the balances with the transaction that reverses it. With the cash book off the
ledger answers what it always answered and writes nothing here.

In `world`, Ali (`customer_a`) owes 50 000 so'm; Vali (`settled_customer_a`) owes nothing.
"""

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import money, say
from qarz.domain.money import Currency

from .. import xlsx_reader
from .conftest import World, as_user, set_overrides, switch_permissions_on
from .test_cash_book import (
    actions,
    add,
    added,
    base,
    cancel,
    cash_rows_of_the_world,
    category,
    day,
    figures,
    line,
    refused,
    summary,
    switch,
)
from .test_chat import chat_of
from .test_customers_ledger import key, read, record, reverse, shop, today, write
from .test_exports import (
    ask,
    no_jobs_left_by_earlier_tests,  # noqa: F401  (a fixture)
    work,
    workbook,
)
from .test_payment_notices import accept, seed_notice
from .test_usd import dollars, platform_on  # noqa: F401  (fixtures)

pytestmark = pytest.mark.db


@pytest.fixture
def on(client: TestClient, owner: psycopg.Connection) -> None:
    switch(owner)


def pay(client: TestClient, world: World, amount: int, **extra: Any) -> Any:
    return record(client, world, world.customer_a, "payment", amount, **extra)


def cash(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    """The book's rows as the ledger wrote them: what, how, how much, whose payment, and whether it stands."""
    return owner.execute(
        "SELECT e.direction, e.method, e.currency, e.amount, e.ledger_entry_id, e.cancelled_at IS NULL, "
        "c.system_key, e.author_id, e.day FROM cash_entry e JOIN cash_category c ON c.id = e.category_id "
        "WHERE e.shop_id = %s ORDER BY e.created_at, e.id",
        (world.shop_a,),
    ).fetchall()


# --- with the switch off the ledger is what it was -------------------------------------------------------


def test_with_the_cash_book_off_a_payment_is_answered_as_before_and_writes_nothing_here(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    off = pay(client, world, 20_000)
    assert off.status_code == 201, off.text
    assert "method" not in off.json()["entry"]
    assert cash_rows_of_the_world(owner, world) == (0, 0)
    # The same payment with the book on differs in one thing: it says how the money came.
    switch(owner)
    with_book = pay(client, world, 20_000)
    assert with_book.status_code == 201, with_book.text

    def shape(body: Any) -> Any:
        """The body's keys, all the way down, without its values."""
        if isinstance(body, dict):
            return {name: shape(value) for name, value in body.items()}
        return [shape(item) for item in body] if isinstance(body, list) else None

    assert shape(with_book.json()["entry"]) == {**shape(off.json()["entry"]), "method": None}
    assert shape(with_book.json()["customer"]) == shape(off.json()["customer"])


@pytest.mark.parametrize("value", ["card", "cash", None, "cheque", 5])
def test_with_the_cash_book_off_a_method_is_a_field_the_api_does_not_know(
    client: TestClient, world: World, owner: psycopg.Connection, value: Any
) -> None:
    """Exactly the answer the same request got before the field existed: that of any unknown field."""
    named = pay(client, world, 20_000, method=value)
    unknown = pay(client, world, 20_000, metod=value)
    assert named.status_code == unknown.status_code == 422
    assert named.json()["error"]["code"] == unknown.json()["error"]["code"] == "VALIDATION"
    if isinstance(value, str) or value is None:
        assert named.json()["error"]["fields"] == {"method": unknown.json()["error"]["fields"]["metod"]}
    assert owner.execute(
        "SELECT count(*) FROM ledger_entry WHERE shop_id = %s AND kind = 'payment'", (world.shop_a,)
    ).fetchone() == (0,)
    # With the book on the very same request is a payment by card.
    if value == "card":
        switch(owner)
        assert pay(client, world, 20_000, method=value).status_code == 201


def test_with_the_cash_book_off_the_bot_has_no_such_command(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    chat = chat_of(client, owner, world.manager_a)
    assert chat.say("/kassa").text == chat.say("/nosuchcommand").text == say("uz", "help")
    assert chat.say("/yordam").text == say("uz", "help")
    # A payment whose note is a way of paying is a payment with that note, and nothing else.
    said = chat.say("Ali -20000 karta")
    assert said.text == say(
        "uz", "payment_saved", shop="Shop A", name="Ali", amount=money("uz", 20000), balance=money("uz", 30000)
    )
    assert cash_rows_of_the_world(owner, world) == (0, 0)


# --- a payment is money received ---------------------------------------------------------------------------


def test_a_payment_is_income_of_the_book_in_the_category_of_repaid_debt(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    paid = pay(client, world, 20_000)
    assert paid.status_code == 201, paid.text
    assert paid.json()["entry"]["method"] == "cash"
    assert cash(owner, world) == [
        ("income", "cash", "UZS", 20_000, uuid.UUID(paid.json()["entry"]["id"]), True, "debt_repaid",
         world.seller_a_membership, today()),
    ]  # fmt: skip
    # The first use of the book, even by a seller's payment, is what writes its categories.
    assert cash_rows_of_the_world(owner, world) == (1, 10)
    book = day(client, world)
    assert figures(line(book, "cash")) == (0, 20_000, 0, 20_000)
    assert book["entries"] == [
        {
            **book["entries"][0],
            "direction": "income",
            "method": "cash",
            "amount": 20_000,
            "category": {"id": category(client, world, "income", "Qarz qaytdi"), "name": "Qarz qaytdi"},
            "source": "ledger",
            "customer": {"id": str(world.customer_a), "display_name": "Ali"},
            "cancelled": None,
        }
    ]
    # The ledger's own line of the activity log says it; the book adds no second line for the same act.
    assert actions(owner, world) == []


@pytest.mark.parametrize("method", ["cash", "card", "transfer"])
def test_a_payment_goes_to_the_balance_of_the_way_it_was_paid(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, method: str
) -> None:
    paid = pay(client, world, 20_000, method=method)
    assert paid.status_code == 201, paid.text
    assert paid.json()["entry"]["method"] == method
    book = day(client, world)
    assert {item["method"]: item["closing"] for item in book["balances"]} == {
        "cash": 0,
        "card": 0,
        "transfer": 0,
        method: 20_000,
    }


@pytest.mark.parametrize("method", ["cheque", "", "CARD", "karta"])
def test_a_payment_by_a_way_that_is_not_one_is_refused(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, method: str
) -> None:
    fields = refused(pay(client, world, 20_000, method=method), 422, "VALIDATION")
    assert fields == {"method": "must be cash, card or transfer"}
    assert cash(owner, world) == []
    assert owner.execute(
        "SELECT count(*) FROM ledger_entry WHERE shop_id = %s AND kind = 'payment'", (world.shop_a,)
    ).fetchone() == (0,)


def test_a_credit_sale_is_not_money_and_takes_no_method(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    sale = record(client, world, world.customer_a, "credit", 30_000)
    assert sale.status_code == 201, sale.text
    assert "method" not in sale.json()["entry"]
    assert cash(owner, world) == []
    fields = refused(record(client, world, world.customer_a, "credit", 30_000, method="cash"), 422, "VALIDATION")
    assert fields == {"method": "only a payment has a method"}
    assert cash(owner, world) == []


def test_a_repeated_payment_is_in_the_book_once(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    headers = {**as_user(world.seller_a), **key()}
    path = f"{shop(world)}/customers/{world.customer_a}/entries"
    first = client.post(path, json={"kind": "payment", "amount": 20_000, "method": "card"}, headers=headers)
    again = client.post(path, json={"kind": "payment", "amount": 20_000, "method": "card"}, headers=headers)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    assert len(cash(owner, world)) == 1
    # The same key for the same payment by another way is another request.
    other = client.post(path, json={"kind": "payment", "amount": 20_000, "method": "cash"}, headers=headers)
    refused(other, 409, "IDEMPOTENCY_KEY_REUSED")
    assert len(cash(owner, world)) == 1


def test_a_payment_the_ledger_refuses_leaves_nothing_in_the_book(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    refused(pay(client, world, 60_000), 409, "EXCEEDS_BALANCE")
    assert cash(owner, world) == []


def test_reversing_a_payment_cancels_its_entry_and_only_that_does(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    paid = pay(client, world, 20_000, method="card").json()["entry"]["id"]
    of_ledger = day(client, world)["entries"][0]
    # Not by hand: what is in the book for a payment is what the ledger says.
    refused(cancel(client, world, of_ledger["id"], user=world.owner_a), 409, "CASH_ENTRY_OF_LEDGER")
    assert cash(owner, world)[0][5] is True

    assert reverse(client, world, paid).status_code == 201
    assert cash(owner, world)[0][5] is False
    book = day(client, world)
    assert figures(line(book, "card")) == (0, 0, 0, 0)
    # It stays in the list, marked, with who reversed the payment and no reason of its own.
    assert book["entries"][0]["cancelled"] == {
        "at": book["entries"][0]["cancelled"]["at"],
        "by": str(world.manager_a_membership),
        "reason": None,
    }
    refused(cancel(client, world, of_ledger["id"], user=world.owner_a), 409, "CASH_ENTRY_OF_LEDGER")


def test_reversing_a_credit_sale_touches_nothing_in_the_book(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    pay(client, world, 20_000)
    sale = record(client, world, world.customer_a, "credit", 30_000).json()["entry"]["id"]
    assert reverse(client, world, sale).status_code == 201
    assert [row[5] for row in cash(owner, world)] == [True]


def test_a_payment_recorded_with_the_book_on_is_cancelled_even_when_reversed_with_it_off(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    """Otherwise turning the switch off and on would leave money in the book that the shop gave back."""
    paid = pay(client, world, 20_000).json()["entry"]["id"]
    switch(owner, "false")
    assert reverse(client, world, paid).status_code == 201
    assert cash(owner, world)[0][5] is False
    switch(owner)
    assert figures(line(day(client, world), "cash")) == (0, 0, 0, 0)


def test_an_accepted_payment_notice_is_money_received_too(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    notice = seed_notice(owner, world, 20_000)
    assert accept(client, world, world.manager_a, notice).status_code == 200
    assert [row[:4] for row in cash(owner, world)] == [("income", "cash", "UZS", 20_000)]


def test_whose_payment_it_is_is_shown_only_to_someone_who_may_see_the_customers(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    pay(client, world, 20_000)
    assert day(client, world)["entries"][0]["customer"] == {"id": str(world.customer_a), "display_name": "Ali"}
    switch_permissions_on(owner)
    set_overrides(owner, world.manager_a_membership, denied=["ledger.view"])
    hidden = day(client, world)["entries"][0]
    assert (hidden["source"], hidden["customer"], hidden["amount"]) == ("ledger", None, 20_000)


# --- the chat ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "method"),
    [
        ("Ali -20000", "cash"),
        ("Ali -20000 karta", "card"),
        ("Ali -20000 Karta", "card"),
        ("Али 20 000 оплатил картой", "card"),
        ("Ali 20000 berdi o'tkazma", "transfer"),
        ("Ali -20000 naqd", "cash"),
        # A note that says more than a way of paying names none: the money is taken to be cash.
        ("Ali -20000 karta emas", "cash"),
        ("Ali -20000 kartasi bloklangan", "cash"),
    ],
)
def test_a_payment_typed_to_the_bot_is_cash_unless_its_note_is_a_way_of_paying(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, message: str, method: str
) -> None:
    said = chat_of(client, owner, world.seller_a).say(message)
    # The reply is the one a payment always got.
    assert said.text == say(
        "uz", "payment_saved", shop="Shop A", name="Ali", amount=money("uz", 20000), balance=money("uz", 30000)
    )
    assert [row[:4] for row in cash(owner, world)] == [("income", method, "UZS", 20_000)]


def test_a_sale_typed_to_the_bot_whose_note_is_a_way_of_paying_is_still_a_sale(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    chat_of(client, owner, world.seller_a).say("Ali 20000 karta")
    assert cash(owner, world) == []
    assert owner.execute(
        "SELECT kind, amount, note FROM ledger_entry WHERE shop_id = %s ORDER BY seq DESC LIMIT 1", (world.shop_a,)
    ).fetchone() == ("credit", 20_000, "karta")


@pytest.mark.parametrize("message", ["chiqim 50000 ijara", "kirim 50000", "Kassa 50000"])
def test_the_bot_never_writes_to_the_cash_book_from_a_message(
    client: TestClient, world: World, owner: psycopg.Connection, on: None, message: str
) -> None:
    """A message with a name and an amount is about a customer, whatever the name is: the book is written
    in the Mini App and the panel, so the two can never be taken for each other."""
    chat_of(client, owner, world.manager_a).say(message)
    assert cash(owner, world) == []
    assert owner.execute("SELECT count(*) FROM cash_entry WHERE shop_id = %s", (world.shop_a,)).fetchone() == (0,)


def test_kassa_shows_todays_totals_and_balances_to_a_member_who_may_read_the_book(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    yesterday = (today() - timedelta(days=1)).isoformat()
    added(client, world, "income", 400_000, day=yesterday)
    added(client, world, "income", 500_000)
    added(client, world, "expense", 300_000, name="Ijara")
    added(client, world, "income", 120_000, method="card")
    wrong = added(client, world, "expense", 999_000, name="Ish haqi", method="transfer")
    assert cancel(client, world, wrong["id"]).status_code == 201

    said = chat_of(client, owner, world.manager_a).say("/kassa")

    def so_m(amount: int) -> str:
        return money("uz", amount)

    assert said.text == "\n".join(
        [
            say("uz", "cash_today", shop="Shop A", date=today().strftime("%d.%m.%Y")),
            "",
            f"Naqd: kirim {so_m(500_000)}, chiqim {so_m(300_000)}, qoldiq {so_m(600_000)}",
            f"Karta: kirim {so_m(120_000)}, chiqim {so_m(0)}, qoldiq {so_m(120_000)}",
            # The transfer was cancelled: the way of paying holds nothing and is not listed.
            f"Jami: kirim {so_m(620_000)}, chiqim {so_m(300_000)}, qoldiq {so_m(720_000)}",
        ]
    )
    assert chat_of(client, owner, world.owner_a).say("/kassa").text == said.text
    # And the help now names the command.
    assert chat_of(client, owner, world.manager_a).say("/yordam").text == "\n".join(
        [say("uz", "help"), say("uz", "cash_help")]
    )


def test_kassa_is_answered_in_the_persons_language(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    added(client, world, "income", 500_000)
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.manager_a,))
    said = chat_of(client, owner, world.manager_a).say("/kassa")
    assert said.text.splitlines()[3] == say(
        "ru",
        "cash_line",
        method="Наличные",
        income=money("ru", 500_000),
        expense=money("ru", 0),
        closing=money("ru", 500_000),
    )


def test_kassa_tells_a_seller_nothing_of_the_book(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    added(client, world, "income", 500_000)
    said = chat_of(client, owner, world.seller_a).say("/kassa")
    assert said.text == say("uz", "cash_forbidden")
    assert money("uz", 500_000) not in said.text
    # Someone who works in no shop is offered to open one, as for any command of a shop.
    assert chat_of(client, owner, world.stranger).say("/kassa").text == say("uz", "no_shops")
    # A seller the owner let read the book reads it.
    switch_permissions_on(owner)
    set_overrides(owner, world.seller_a_membership, granted=["cash.view"])
    assert money("uz", 500_000) in chat_of(client, owner, world.seller_a).say("/kassa").text


def test_kassa_in_a_suspended_shop_is_for_its_owner_alone(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    added(client, world, "income", 500_000)
    owner.execute("UPDATE subscription SET state = 'suspended' WHERE shop_id = %s", (world.shop_a,))
    assert money("uz", 500_000) not in chat_of(client, owner, world.manager_a).say("/kassa").text
    assert money("uz", 500_000) in chat_of(client, owner, world.owner_a).say("/kassa").text


# --- dollars ---------------------------------------------------------------------------------------------


def test_so_m_and_dollars_are_two_books_that_never_add_up(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    on: None,
    dollars: None,  # noqa: F811
) -> None:
    assert read(client, world.manager_a, f"{base(world)}/categories").json()["currencies"] == ["UZS", "USD"]
    added(client, world, "income", 500_000)
    added(client, world, "income", 125_050, currency="USD")
    added(client, world, "expense", 20_000, name="Transport", currency="USD", method="card")

    book = day(client, world)
    assert [(item["currency"], item["method"]) for item in book["balances"]] == [
        ("UZS", "cash"),
        ("UZS", "card"),
        ("UZS", "transfer"),
        ("USD", "cash"),
        ("USD", "card"),
        ("USD", "transfer"),
    ]
    assert figures(line(book, "cash")) == (0, 500_000, 0, 500_000)
    assert figures(line(book, "cash", "USD")) == (0, 125_050, 0, 125_050)
    assert figures(line(book, "card", "USD")) == (0, 0, 20_000, -20_000)
    assert book["totals"] == [
        {"currency": "UZS", "opening": 0, "income": 500_000, "expense": 0, "closing": 500_000, "count": 1},
        {"currency": "USD", "opening": 0, "income": 125_050, "expense": 20_000, "closing": 105_050, "count": 2},
    ]
    # No figure anywhere in the answer is the two added together.
    assert 625_050 not in [value for item in book["balances"] + book["totals"] for value in item.values()]

    report = summary(client, world, today(), today())
    assert [(row["category"]["name"], row["currency"], row["amount"]) for row in report["categories"]] == [
        ("Savdo", "USD", 125_050),
        ("Transport", "USD", 20_000),
        ("Savdo", "UZS", 500_000),
    ]
    assert report["days"] == [
        {"date": today().isoformat(), "currency": "USD", "income": 125_050, "expense": 20_000},
        {"date": today().isoformat(), "currency": "UZS", "income": 500_000, "expense": 0},
    ]
    text = chat_of(client, owner, world.manager_a).say("/kassa").text
    assert money("uz", 500_000) in text and money("uz", 105_050, Currency.USD) in text


@pytest.mark.parametrize(("amount", "accepted"), [(1, True), (1_000_000, True), (0, False), (1_000_001, False)])
def test_a_dollar_amount_is_whole_cents_inside_the_dollar_range(
    client: TestClient,
    world: World,
    on: None,
    dollars: None,  # noqa: F811
    amount: int,
    accepted: bool,
) -> None:
    response = add(client, world, "income", amount, currency="USD")
    assert response.status_code == (201 if accepted else 422), response.text
    # The so'm range is another: a hundred so'm is the least, and one cent is not a so'm amount.
    assert add(client, world, "income", 1).status_code == 422


def test_a_shop_without_dollars_cannot_write_them_and_keeps_what_it_wrote(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    on: None,
    dollars: None,  # noqa: F811
) -> None:
    added(client, world, "income", 125_050, currency="USD")
    assert write(client, world.owner_a, "PATCH", shop(world), {"usd_on": False}).status_code == 200
    assert read(client, world.manager_a, f"{base(world)}/categories").json()["currencies"] == ["UZS"]
    assert refused(add(client, world, "income", 5_000, currency="USD"), 422, "VALIDATION") == {
        "currency": "must be UZS"
    }
    # Money written in dollars does not vanish from the book when dollars are turned off.
    assert figures(line(day(client, world), "cash", "USD")) == (0, 125_050, 0, 125_050)


def test_a_dollar_payment_is_dollar_income(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    on: None,
    dollars: None,  # noqa: F811
) -> None:
    owed = record(client, world, world.settled_customer_a, "credit", 30_000, currency="USD")
    assert owed.status_code == 201, owed.text
    paid = record(client, world, world.settled_customer_a, "payment", 12_500, currency="USD", method="transfer")
    assert paid.status_code == 201, paid.text
    assert [row[:4] for row in cash(owner, world)] == [("income", "transfer", "USD", 12_500)]
    assert figures(line(day(client, world), "transfer", "USD")) == (0, 12_500, 0, 12_500)
    assert figures(line(day(client, world), "transfer")) == (0, 0, 0, 0)


# --- the ledger's past ---------------------------------------------------------------------------------------


def backfill(client: TestClient, world: World, **body: Any) -> Any:
    return write(client, world.owner_a, "POST", f"{base(world)}/backfill", body)


def test_payments_made_before_the_book_are_copied_in_once_when_the_owner_asks(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    first = pay(client, world, 10_000).json()["entry"]["id"]
    undone = pay(client, world, 5_000).json()["entry"]["id"]
    assert reverse(client, world, undone).status_code == 201
    record(client, world, world.customer_a, "credit", 7_000)
    # A payment of three days ago, as the ledger holds it.
    old = uuid.uuid4()
    owner.execute(
        "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id, created_at) "
        "VALUES (%s, %s, %s, 90, 'payment', 3000, %s, now() - interval '3 days')",
        (old, world.shop_a, world.customer_a, world.owner_a_membership),
    )
    switch(owner)
    # Nothing copies them unasked: the book starts empty.
    assert day(client, world)["entries"] == []
    during = pay(client, world, 2_000, method="card").json()["entry"]["id"]

    assert backfill(client, world).json() == {"written": 2}
    rows = {row[4]: row for row in cash(owner, world)}
    assert set(rows) == {uuid.UUID(first), old, uuid.UUID(during)}
    # Each on the day it was paid, as cash, by whoever recorded it; the reversed one is not money.
    assert rows[old][:4] + rows[old][6:] == (
        "income", "cash", "UZS", 3_000, "debt_repaid", world.owner_a_membership, today() - timedelta(days=3),
    )  # fmt: skip
    assert rows[uuid.UUID(first)][7:] == (world.seller_a_membership, today())
    assert rows[uuid.UUID(during)][1] == "card"
    # Asked again, it writes nothing: a payment is in the book once.
    assert backfill(client, world).json() == {"written": 0}
    assert len(cash(owner, world)) == 3
    assert actions(owner, world) == ["cash.backfilled", "cash.backfilled"]
    assert owner.execute(
        "SELECT detail FROM activity WHERE shop_id = %s AND action = 'cash.backfilled' ORDER BY at, id",
        (world.shop_a,),
    ).fetchall() == [({"entries": 2},), ({"entries": 0},)]
    # A copied payment is cancelled like any other: by reversing it.
    assert reverse(client, world, first).status_code == 201
    assert (
        rows[uuid.UUID(first)][5] is True and {row[4]: row[5] for row in cash(owner, world)}[uuid.UUID(first)] is False
    )


def test_the_backfill_leaves_out_what_was_paid_before_a_day_and_another_shops_payments(
    client: TestClient, world: World, owner: psycopg.Connection, on: None
) -> None:
    for days_ago, amount in ((10, 1_000), (3, 2_000)):
        owner.execute(
            "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id, created_at) "
            "VALUES (gen_random_uuid(), %s, %s, %s, 'payment', %s, %s, now() - make_interval(days => %s))",
            (world.shop_a, world.customer_a, 50 + days_ago, amount, world.owner_a_membership, days_ago),
        )
    since = (today() - timedelta(days=5)).isoformat()
    assert backfill(client, world, since=since).json() == {"written": 1}
    assert [row[3] for row in cash(owner, world)] == [2_000]
    ahead = backfill(client, world, since=(today() + timedelta(days=1)).isoformat())
    assert refused(ahead, 422, "VALIDATION") == {"since": "IN_FUTURE"}
    assert owner.execute("SELECT count(*) FROM cash_entry WHERE shop_id = %s", (world.shop_b,)).fetchone() == (0,)


# --- the export ----------------------------------------------------------------------------------------------


def test_the_shops_export_carries_its_cash_book_and_only_when_it_has_one(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    worker_database_url: str,
    file_root: Path,
    on: None,
    no_jobs_left_by_earlier_tests: None,  # noqa: F811
) -> None:
    before = ask(client, world).json()
    assert work(worker_database_url, file_root) == 1
    assert "Kassa" not in workbook(client, world, before["id"])
    owner.execute("DELETE FROM export_job WHERE shop_id = %s", (world.shop_a,))

    added(client, world, "income", 500_000, note="Kunlik savdo")
    wrong = added(client, world, "expense", 300_000, name="Ijara", method="card")
    assert cancel(client, world, wrong["id"], "Ikki marta yozilgan").status_code == 201
    paid = pay(client, world, 20_000, method="transfer").json()["entry"]["id"]

    job = ask(client, world).json()
    assert work(worker_database_url, file_root) == 1
    sheet = workbook(client, world, job["id"])["Kassa"]
    assert sheet[0][:10] == [
        "Kun", "Yozilgan vaqt", "Yo'nalish", "To'lov usuli", "Valyuta", "Summa", "Toifa", "Izoh",
        "Bekor qilinganmi", "Bekor qilish sababi",
    ]  # fmt: skip
    rows = [[row[0], *row[2:10], row[12] if len(row) > 12 else None] for row in sheet[1:]]
    day_text = today().isoformat()
    assert rows == [
        [day_text, "Kirim", "Naqd", "UZS", 500_000, "Savdo", "Kunlik savdo", "Yo'q", None, None],
        [day_text, "Chiqim", "Karta", "UZS", 300_000, "Ijara", None, "Ha", "Ikki marta yozilgan", None],
        [day_text, "Kirim", "O'tkazma", "UZS", 20_000, "Qarz qaytdi", None, "Yo'q", None, paid],
    ]
    # Another shop's export holds nothing of this book.
    theirs = client.post(f"/api/v1/shops/{world.shop_b}/exports", headers={**as_user(world.owner_b), **key()})
    assert theirs.status_code == 201, theirs.text
    assert work(worker_database_url, file_root) == 1
    served = client.get(
        client.get(
            f"/api/v1/shops/{world.shop_b}/exports/{theirs.json()['id']}/download", headers=as_user(world.owner_b)
        ).json()["url"]
    )
    assert "Kassa" not in xlsx_reader.read(served.content)
