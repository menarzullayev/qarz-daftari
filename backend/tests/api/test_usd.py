"""US dollars beside so'm, through the API and the chat (expansion module F).

Two things must both be on for a shop to work in dollars: the platform switch `usd_on` and the shop's own
setting. With either off the service is what it was: no answer has a dollar field, no request may name
dollars, and the bot does not read "$" as a currency. With both on, a customer has a so'm balance and a
dollar balance that never meet: a payment reduces the debt of its own currency, limits and overdue are per
currency, and no figure anywhere is a sum of the two.

In `world`, Ali (`customer_a`) owes 50 000 so'm promised a week from today and is linked to a Telegram
account; Vali (`settled_customer_a`) owes nothing.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from qarz.application.chat_texts import both, money, say
from qarz.domain.money import NBSP, Currency
from qarz.domain.promise import tashkent_date
from qarz.interface.errors import message_text

from .conftest import World, as_user
from .test_chat import chat_of
from .test_customers_ledger import key, read, shop, write
from .test_exports import ask, work, workbook

pytestmark = pytest.mark.db

USD = Currency.USD


@pytest.fixture
def platform_on(owner: psycopg.Connection) -> Iterator[None]:
    """The administrator has turned dollars on for the platform. Each shop still decides for itself."""
    owner.execute("INSERT INTO platform_setting (key, value, updated_by) VALUES ('usd_on', 'true', 'test')")
    try:
        yield
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key = 'usd_on'")


@pytest.fixture
def dollars(client: TestClient, world: World, platform_on: None) -> None:
    """Shop A works in dollars: the platform switch is on and its owner has said so."""
    answer = write(client, world.owner_a, "PATCH", shop(world), {"usd_on": True})
    assert answer.status_code == 200, answer.text
    assert answer.json()["usd_on"] is True


def record(
    client: TestClient,
    world: World,
    kind: str,
    amount: int,
    currency: str | None = "USD",
    *,
    customer: uuid.UUID | None = None,
    user: uuid.UUID | None = None,
    **more: Any,
) -> Any:
    body: dict[str, Any] = {"kind": kind, "amount": amount, **more}
    if currency is not None:
        body["currency"] = currency
    return write(
        client, user or world.manager_a, "POST", f"{shop(world)}/customers/{customer or world.customer_a}/entries", body
    )


def detail(client: TestClient, world: World, customer: uuid.UUID | None = None) -> dict[str, Any]:
    answer = read(client, world.manager_a, f"{shop(world)}/customers/{customer or world.customer_a}")
    assert answer.status_code == 200, answer.text
    body: dict[str, Any] = answer.json()
    return body


def keys_named(body: Any, names: frozenset[str] = frozenset({"usd", "currency", "usd_on"})) -> set[str]:
    """Every key of the body, at any depth, that is one of the dollar fields."""
    if isinstance(body, dict):
        return {k for k in body if k in names} | {found for v in body.values() for found in keys_named(v, names)}
    if isinstance(body, list):
        return {found for item in body for found in keys_named(item, names)}
    return set()


def link_of(owner: psycopg.Connection, world: World) -> uuid.UUID:
    row = owner.execute(
        "SELECT id FROM customer_link WHERE customer_id = %s AND status = 'active'", (world.customer_a,)
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def usd_rows(owner: psycopg.Connection, world: World) -> list[tuple[Any, ...]]:
    return owner.execute(
        "SELECT kind, amount FROM ledger_entry WHERE shop_id = %s AND currency = 'USD' ORDER BY seq", (world.shop_a,)
    ).fetchall()


# --- off: the service is what it was ---------------------------------------------------------------------


def _answers(client: TestClient, world: World, owner: psycopg.Connection) -> dict[str, Any]:
    """The reads a shop and its customer make, for looking at their shape."""
    link = link_of(owner, world)
    paths = {
        "shop": (world.owner_a, shop(world)),
        "customers": (world.manager_a, f"{shop(world)}/customers"),
        "customer": (world.manager_a, f"{shop(world)}/customers/{world.customer_a}"),
        "overview": (world.manager_a, f"{shop(world)}/overview"),
        "debtors": (world.manager_a, f"{shop(world)}/overview/debtors"),
        "credit": (world.manager_a, f"{shop(world)}/credit-settings"),
        "overdue_report": (world.manager_a, f"{shop(world)}/reports/overdue"),
        "notices": (world.manager_a, f"{shop(world)}/payment-notices"),
        "accounts": (world.customer_of_a, "/api/v1/me/accounts"),
        "account": (world.customer_of_a, f"/api/v1/me/accounts/{link}"),
        "owner_totals": (world.owner_a, "/api/v1/me/owner-totals"),
    }
    answers: dict[str, Any] = {}
    for name, (user, path) in paths.items():
        answer = read(client, user, path)
        assert answer.status_code == 200, (name, answer.text)
        answers[name] = answer.json()
    return answers


def _refusals_of_a_shop_without_dollars(client: TestClient, world: World, owner: psycopg.Connection) -> None:
    before = owner.execute("SELECT count(*) FROM ledger_entry").fetchone()
    for currency in ("USD", "EUR", "usd", ""):
        refused = record(client, world, "credit", 5_000, currency)
        assert (refused.status_code, refused.json()["error"]["code"]) == (422, "VALIDATION"), currency
        # It does not say that dollars exist.
        assert refused.json()["error"]["fields"] == {"currency": "must be UZS"}
    assert read(client, world.manager_a, f"{shop(world)}/overview/debtors", currency="USD").status_code == 422
    limit = write(
        client, world.manager_a, "PATCH", f"{shop(world)}/customers/{world.customer_a}", {"credit_limit_usd": 5_000}
    )
    assert (limit.status_code, limit.json()["error"]["fields"]) == (422, {"credit_limit_usd": "unknown field"})
    default = write(
        client, world.manager_a, "PATCH", f"{shop(world)}/credit-settings", {"default_credit_limit_usd": 5_000}
    )
    assert (default.status_code, default.json()["error"]["fields"]) == (
        422,
        {"default_credit_limit_usd": "unknown field"},
    )
    notice = client.post(
        f"/api/v1/me/accounts/{link_of(owner, world)}/payment-notices",
        json={"amount": 500, "currency": "USD"},
        headers=as_user(world.customer_of_a),
    )
    assert (notice.status_code, notice.json()["error"]["fields"]) == (422, {"currency": "must be UZS"})
    assert owner.execute("SELECT count(*) FROM ledger_entry").fetchone() == before
    assert owner.execute("SELECT count(*) FROM payment_notice WHERE currency = 'USD'").fetchone() == (0,)
    # Naming so'm is allowed, means what leaving it out means, and is not echoed back.
    named = record(client, world, "credit", 5_000, "UZS")
    assert named.status_code == 201 and keys_named(named.json()) == set()
    assert named.json()["customer"]["balance"] == 55_000


def test_with_the_platform_switch_off_nothing_knows_of_dollars(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    answers = _answers(client, world, owner)
    assert keys_named(answers) == set(), "no answer of any shop has a dollar field"
    assert set(answers["shop"]) == {"id", "name", "lang", "default_promise_days"}
    # The owner cannot turn on what the platform does not offer: the field does not exist.
    refused = write(client, world.owner_a, "PATCH", shop(world), {"usd_on": True})
    assert (refused.status_code, refused.json()["error"]["fields"]) == (422, {"usd_on": "unknown field"})
    assert owner.execute("SELECT usd_on FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == (False,)
    _refusals_of_a_shop_without_dollars(client, world, owner)


def test_with_the_shops_setting_off_the_shop_is_as_before(
    client: TestClient, world: World, owner: psycopg.Connection, platform_on: None
) -> None:
    """The platform offers dollars and this shop has not taken them: only the offer is visible."""
    answers = _answers(client, world, owner)
    assert answers.pop("shop") == {
        "id": str(world.shop_a),
        "name": "Shop A",
        "lang": "uz",
        "default_promise_days": 30,
        "usd_on": False,
    }
    assert keys_named(answers) == set()
    _refusals_of_a_shop_without_dollars(client, world, owner)


def test_the_answers_of_a_shop_without_dollars_are_the_same_whatever_the_platform_switch(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    off = _answers(client, world, owner)
    owner.execute("INSERT INTO platform_setting (key, value, updated_by) VALUES ('usd_on', 'true', 'test')")
    try:
        on = _answers(client, world, owner)
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key = 'usd_on'")
    assert on.pop("shop") == {**off.pop("shop"), "usd_on": False}
    assert on == off


@pytest.mark.parametrize("text", ["Ali 50$", "Ali $50", "Ali 50.5$", "Ali -20$", "Ali 20$ berdi"])
def test_without_dollars_the_bot_does_not_read_a_dollar_sign(
    client: TestClient, world: World, owner: psycopg.Connection, platform_on: None, text: str
) -> None:
    """The platform switch is on and the shop's setting is off: the message is answered as it always was."""
    seller = chat_of(client, owner, world.seller_a)
    before = owner.execute("SELECT count(*) FROM ledger_entry").fetchone()
    assert seller.say(text).text in {say("uz", "parse_hint"), say("uz", "parse_amount_not_whole")}
    assert owner.execute("SELECT count(*) FROM ledger_entry").fetchone() == before


def test_without_dollars_a_message_with_the_word_usd_is_a_som_entry_as_before(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    seller = chat_of(client, owner, world.seller_a)
    assert seller.say("Ali 5 k usd").text.startswith(f"✅ Shop A\nAli: +5{NBSP}000{NBSP}so'm")
    assert usd_rows(owner, world) == []


# --- the shop's setting ------------------------------------------------------------------------------------


def test_only_the_owner_turns_dollars_on(
    client: TestClient, world: World, owner: psycopg.Connection, platform_on: None
) -> None:
    for user in (world.manager_a, world.seller_a):
        refused = write(client, user, "PATCH", shop(world), {"usd_on": True})
        assert (refused.status_code, refused.json()["error"]["code"]) == (403, "FORBIDDEN_ROLE")
    assert write(client, world.owner_b, "PATCH", shop(world), {"usd_on": True}).status_code == 404
    assert owner.execute("SELECT usd_on FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == (False,)

    turned = write(client, world.owner_a, "PATCH", shop(world), {"usd_on": True})
    assert (turned.status_code, turned.json()["usd_on"]) == (200, True)
    assert owner.execute("SELECT usd_on FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == (True,)
    assert owner.execute("SELECT usd_on FROM shop WHERE id = %s", (world.shop_b,)).fetchone() == (False,), "one shop"
    actions = owner.execute("SELECT action FROM activity WHERE shop_id = %s ORDER BY action", (world.shop_a,))
    assert "shop.dollars_on" in {row[0] for row in actions.fetchall()}


def test_dollars_cannot_be_turned_off_while_anyone_owes_dollars(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    assert record(client, world, "credit", 5_000).status_code == 201
    refused = write(client, world.owner_a, "PATCH", shop(world), {"usd_on": False})
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "USD_BALANCE_OPEN")
    assert refused.json()["error"]["message"] == say("uz", "USD_BALANCE_OPEN"), "the refusal says why"
    assert "dollar" in refused.json()["error"]["message"].lower()
    assert owner.execute("SELECT usd_on FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == (True,)
    # A change sent with it is not half applied.
    both_fields = write(client, world.owner_a, "PATCH", shop(world), {"usd_on": False, "name": "Boshqa"})
    assert both_fields.status_code == 409
    assert owner.execute("SELECT name FROM shop WHERE id = %s", (world.shop_a,)).fetchone() == ("Shop A",)

    # Paid to the last cent, it can be turned off; the so'm debt that remains is no obstacle.
    assert record(client, world, "payment", 5_000).status_code == 201
    turned = write(client, world.owner_a, "PATCH", shop(world), {"usd_on": False})
    assert (turned.status_code, turned.json()["usd_on"]) == (200, False)
    assert detail(client, world)["balance"] == 50_000
    assert record(client, world, "credit", 5_000).status_code == 422, "and dollars are refused again"


def test_dollar_debts_are_kept_and_guarded_while_the_platform_switch_is_off(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    """The administrator turns the switch off while a customer owes dollars: nothing is lost or mis-shown."""
    sale = record(client, world, "credit", 5_000, customer=world.settled_customer_a).json()["entry"]
    owner.execute("UPDATE platform_setting SET value = 'false' WHERE key = 'usd_on'")
    seen = detail(client, world, world.settled_customer_a)
    assert keys_named(seen) == set() and seen["balance"] == 0 and seen["entries"] == [] and seen["entries_total"] == 0
    # Hidden, the entry cannot be acted on...
    assert write(client, world.manager_a, "POST", f"{shop(world)}/entries/{sale['id']}/reversal").status_code == 404
    assert record(client, world, "payment", 5_000, customer=world.settled_customer_a).status_code == 422
    # ...and the customer still owes: no archiving while a debt stands in any currency (INV-13).
    archived = write(client, world.manager_a, "POST", f"{shop(world)}/customers/{world.settled_customer_a}/archive")
    assert (archived.status_code, archived.json()["error"]["code"]) == (409, "CUSTOMER_HAS_BALANCE")
    assert usd_rows(owner, world) == [("credit", 5_000)]
    # On again, it is all there.
    owner.execute("UPDATE platform_setting SET value = 'true' WHERE key = 'usd_on'")
    assert detail(client, world, world.settled_customer_a)["usd"]["balance"] == 5_000


# --- the ledger: two balances that never meet ----------------------------------------------------------------


def test_a_customer_owes_som_and_dollars_at_once_and_each_is_its_own(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    sold = record(client, world, "credit", 12_050)  # 120.50 $
    assert sold.status_code == 201, sold.text
    body = sold.json()
    assert (body["entry"]["amount"], body["entry"]["currency"]) == (12_050, "USD")
    assert body["customer"]["balance"] == 50_000, "the so'm balance is untouched"
    assert body["customer"]["usd"] == {"balance": 12_050, "credit_limit": None}
    assert owner.execute(
        "SELECT currency, amount FROM ledger_entry WHERE id = %s", (body["entry"]["id"],)
    ).fetchone() == ("USD", 12_050)

    # A payment in so'm reduces so'm; a payment in dollars reduces dollars.
    in_som = record(client, world, "payment", 20_000, None).json()
    assert "currency" not in in_som["entry"]
    assert (in_som["customer"]["balance"], in_som["customer"]["usd"]["balance"]) == (30_000, 12_050)
    in_dollars = record(client, world, "payment", 2_050).json()
    assert (in_dollars["customer"]["balance"], in_dollars["customer"]["usd"]["balance"]) == (30_000, 10_000)

    seen = detail(client, world)
    assert (seen["balance"], seen["usd"]["balance"]) == (30_000, 10_000)
    assert [(e["kind"], e["amount"], e.get("currency", "UZS")) for e in seen["entries"]] == [
        ("payment", 2_050, "USD"),
        ("payment", 20_000, "UZS"),
        ("credit", 12_050, "USD"),
        ("credit", 50_000, "UZS"),
    ]
    assert [e["seq"] for e in seen["entries"]] == [4, 3, 2, 1], "one sequence for the account"
    assert owner.execute("SELECT * FROM open_debt_mismatches(%s)", (world.shop_a,)).fetchall() == []


def test_a_payment_cannot_exceed_the_debt_of_its_own_currency(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    """50 000 so'm are owed and 10.00 $: 10.01 $ is too much, and so is any dollar at all for Vali."""
    assert record(client, world, "credit", 1_000).status_code == 201
    for customer, amount in ((world.customer_a, 1_001), (world.settled_customer_a, 1)):
        refused = record(client, world, "payment", amount, customer=customer)
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "EXCEEDS_BALANCE"), amount
    # And 50 001 so'm is too much though dollars are owed as well.
    assert record(client, world, "payment", 50_001, None).json()["error"]["code"] == "EXCEEDS_BALANCE"
    assert usd_rows(owner, world) == [("credit", 1_000)]
    assert record(client, world, "payment", 1_000).json()["customer"]["usd"]["balance"] == 0


def test_one_dollar_entry_is_a_cent_to_ten_thousand_dollars(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    assert record(client, world, "credit", 1).status_code == 201
    assert record(client, world, "credit", 1_000_000).status_code == 201
    for amount in (0, -5, 1_000_001, 100_000_000):
        refused = record(client, world, "credit", amount)
        assert (refused.status_code, list(refused.json()["error"]["fields"])) == (422, ["amount"]), amount
    assert (
        client.post(
            f"{shop(world)}/customers/{world.customer_a}/entries",
            json={"kind": "credit", "amount": 12.5, "currency": "USD"},
            headers={**as_user(world.manager_a), **key()},
        ).status_code
        == 422
    ), "cents are whole: a fraction is not an amount"
    # The so'm range is what it was: 50 is a dollar amount and not a so'm one, and the so'm cap stands.
    assert record(client, world, "credit", 50, None).status_code == 422
    assert record(client, world, "credit", 100_000_000, None).status_code == 201
    assert record(client, world, "credit", 100_000_001, None).status_code == 422
    assert usd_rows(owner, world) == [("credit", 1), ("credit", 1_000_000)]


def test_each_currency_has_a_credit_limit_of_its_own(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    customer = f"{shop(world)}/customers/{world.customer_a}"
    settings = f"{shop(world)}/credit-settings"
    assert write(client, world.manager_a, "PATCH", settings, {"sellers_may_exceed": False}).status_code == 200
    # A so'm limit just above what Ali owes: it says nothing about dollars.
    assert write(client, world.manager_a, "PATCH", customer, {"credit_limit": 60_000}).status_code == 200
    free = record(client, world, "credit", 900_000, user=world.seller_a)
    assert free.status_code == 201 and "limit_warning" not in free.json()

    # A dollar limit of 9 500.00 $: the seller is stopped above it, with both figures in cents.
    limited = write(client, world.manager_a, "PATCH", customer, {"credit_limit_usd": 950_000})
    assert limited.json()["usd"] == {"balance": 900_000, "credit_limit": 950_000}
    assert limited.json()["credit_limit"] == 60_000
    stopped = record(client, world, "credit", 50_001, user=world.seller_a)
    assert (stopped.status_code, stopped.json()["error"]["code"]) == (409, "LIMIT_REACHED")
    assert stopped.json()["error"]["fields"] == {"limit": "950000", "balance": "950001"}
    assert record(client, world, "credit", 50_000, user=world.seller_a).status_code == 201, "at the limit is within it"
    warned = record(client, world, "credit", 100, user=world.manager_a)
    assert warned.json()["limit_warning"] == {"limit": 950_000, "balance": 950_100}
    # And the dollar limit says nothing about so'm: a so'm sale within the so'm limit passes.
    assert record(client, world, "credit", 10_000, None, user=world.seller_a).status_code == 201
    assert record(client, world, "credit", 1_000, None, user=world.seller_a).json()["error"]["code"] == "LIMIT_REACHED"

    # The shop's default dollar limit applies where the customer has none.
    for bad in (99, 100_000_001, 12.5, "500"):
        assert write(client, world.manager_a, "PATCH", settings, {"default_credit_limit_usd": bad}).status_code == 422
    default = write(client, world.manager_a, "PATCH", settings, {"default_credit_limit_usd": 10_000})
    assert default.json()["usd"] == {"default_credit_limit": 10_000, "limit_bounds": [100, 100_000_000]}
    assert default.json()["default_credit_limit"] is None, "the so'm default is another setting"
    over = record(client, world, "credit", 10_001, customer=world.settled_customer_a, user=world.seller_a)
    assert over.json()["error"]["code"] == "LIMIT_REACHED"
    assert (
        record(client, world, "credit", 10_000, customer=world.settled_customer_a, user=world.seller_a).status_code
        == 201
    )


def test_a_reversal_undoes_an_entry_in_its_own_currency(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    sale = record(client, world, "credit", 5_000).json()["entry"]
    paid = record(client, world, "payment", 2_000).json()["entry"]
    # Reversing the sale would leave 20.00 $ paid against nothing: refused, whatever is owed in so'm.
    refused = write(client, world.manager_a, "POST", f"{shop(world)}/entries/{sale['id']}/reversal")
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "WOULD_GO_NEGATIVE")
    undone = write(client, world.manager_a, "POST", f"{shop(world)}/entries/{paid['id']}/reversal").json()
    assert (undone["entry"]["amount"], undone["entry"]["currency"]) == (2_000, "USD")
    assert (undone["customer"]["balance"], undone["customer"]["usd"]["balance"]) == (50_000, 5_000)
    assert write(client, world.manager_a, "POST", f"{shop(world)}/entries/{sale['id']}/reversal").status_code == 201
    assert owner.execute(
        "SELECT kind, currency FROM ledger_entry WHERE shop_id = %s AND seq >= 4 ORDER BY seq", (world.shop_a,)
    ).fetchall() == [("reversal", "USD"), ("reversal", "USD")]
    assert (detail(client, world)["balance"], detail(client, world)["usd"]["balance"]) == (50_000, 0)


def test_a_dollar_sale_has_no_goods_lines_yet(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    lines = [{"name": "Non", "qty": "2", "unit": "dona", "unit_price": 250}]
    refused = record(client, world, "credit", 500, lines=lines)
    assert (refused.status_code, refused.json()["error"]["fields"]) == (422, {"lines": "not available in dollars yet"})
    assert usd_rows(owner, world) == []
    # Nor can they be added afterwards to a sale recorded by its amount.
    sale = record(client, world, "credit", 500).json()["entry"]
    later = write(client, world.manager_a, "POST", f"{shop(world)}/entries/{sale['id']}/lines", {"lines": lines})
    assert (later.status_code, later.json()["error"]["fields"]) == (422, {"entry": "not available in dollars yet"})
    assert owner.execute("SELECT count(*) FROM goods_line WHERE entry_id = %s", (sale["id"],)).fetchone() == (0,)
    # A so'm sale has them as before.
    assert record(client, world, "credit", 500, None, lines=lines).status_code == 201


def test_the_refusal_of_goods_on_a_dollar_sale_says_why_in_the_readers_language(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    """The code, the status and the fields are a validation error's, as before; the words explain."""
    lines = [{"name": "Non", "qty": "2", "unit": "dona", "unit_price": 250}]
    sale = record(client, world, "credit", 500).json()["entry"]
    for lang in ("uz", "ru", "tg", "kaa", "en", "uz-Cyrl"):
        owner.execute("UPDATE app_user SET lang = %s WHERE id = %s", (lang, world.manager_a))
        said = message_text(lang, "GOODS_NOT_IN_DOLLARS")
        refused = record(client, world, "credit", 500, lines=lines)
        assert (refused.status_code, refused.json()["error"]) == (
            422,
            {"code": "VALIDATION", "message": said, "fields": {"lines": "not available in dollars yet"}},
        )
        later = write(client, world.manager_a, "POST", f"{shop(world)}/entries/{sale['id']}/lines", {"lines": lines})
        assert (later.status_code, later.json()["error"]) == (
            422,
            {"code": "VALIDATION", "message": said, "fields": {"entry": "not available in dollars yet"}},
        )
        assert said != message_text(lang, "VALIDATION"), "not the words of any validation error"
    assert message_text("uz", "GOODS_NOT_IN_DOLLARS").startswith(
        "Dollardagi nasiyaga mahsulotlar ro'yxati qo'shilmaydi:"
    )
    assert "so'mda" in message_text("uz", "GOODS_NOT_IN_DOLLARS") and "в сумах" in message_text(
        "ru", "GOODS_NOT_IN_DOLLARS"
    )
    # Other fields that are wrong too are still named: the refusal is the same one, better worded.
    both_wrong = record(client, world, "credit", 0, lines=lines)
    assert set(both_wrong.json()["error"]["fields"]) == {"lines", "amount"}
    assert both_wrong.json()["error"]["message"] == message_text("uz-Cyrl", "GOODS_NOT_IN_DOLLARS")


def test_any_other_validation_error_keeps_the_general_words(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    """The counterpart: only goods on a dollar sale are worded so. A dollar sale with a wrong amount, a
    so'm sale with wrong goods and a sale in a shop without dollars read as they always did."""
    general = message_text("uz", "VALIDATION")
    too_large = record(client, world, "credit", 10_000_001)
    assert (too_large.status_code, too_large.json()["error"]["message"]) == (422, general)
    bad_lines = record(client, world, "credit", 500, None, lines=[{"name": "", "qty": "2", "unit_price": 250}])
    assert (bad_lines.status_code, bad_lines.json()["error"]["message"]) == (422, general)
    write(client, world.owner_a, "PATCH", shop(world), {"usd_on": False})
    lines = [{"name": "Non", "qty": "2", "unit": "dona", "unit_price": 250}]
    off = record(client, world, "credit", 500, lines=lines)
    assert (off.status_code, off.json()["error"]) == (
        422,
        {"code": "VALIDATION", "message": general, "fields": {"currency": "must be UZS"}},
    ), "without dollars the answer does not say that dollars exist"


def test_totals_and_lists_show_each_currency_and_never_their_sum(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    today = tashkent_date(datetime.now(UTC))
    # Vali owes dollars only, due today; Ali owes 50 000 so'm and 30.00 $.
    assert (
        record(client, world, "credit", 9_000, customer=world.settled_customer_a, promised_date=str(today)).status_code
        == 201
    )
    assert record(client, world, "credit", 3_000).status_code == 201

    overview = read(client, world.manager_a, f"{shop(world)}/overview").json()
    assert overview == {
        "outstanding": 50_000,
        "debtors": 1,
        "overdue": {"amount": 0, "customers": 0},
        "due_today": 0,
        "usd": {"outstanding": 12_000, "debtors": 2, "overdue": {"amount": 0, "customers": 0}, "due_today": 9_000},
    }

    in_som = read(client, world.manager_a, f"{shop(world)}/overview/debtors").json()["items"]
    assert [(i["display_name"], i["balance"], i["usd"]["balance"]) for i in in_som] == [("Ali", 50_000, 3_000)]
    in_dollars = read(client, world.manager_a, f"{shop(world)}/overview/debtors", currency="USD").json()["items"]
    assert [(i["display_name"], i["balance"], i["usd"]["balance"]) for i in in_dollars] == [
        ("Vali", 0, 9_000),
        ("Ali", 50_000, 3_000),
    ], "largest dollar debt first, and each row still shows both"
    assert in_dollars[0]["usd"]["overdue"]["due_today"] == 9_000 and in_dollars[0]["overdue"]["due_today"] == 0
    assert read(client, world.manager_a, f"{shop(world)}/overview/debtors", currency="EUR").status_code == 422

    listed = {c["display_name"]: c for c in read(client, world.manager_a, f"{shop(world)}/customers").json()["items"]}
    assert (listed["Ali"]["balance"], listed["Ali"]["usd"]) == (50_000, {"balance": 3_000, "credit_limit": None})
    assert (listed["Vali"]["balance"], listed["Vali"]["usd"]["balance"]) == (0, 9_000)

    totals = read(client, world.owner_a, "/api/v1/me/owner-totals").json()
    assert totals["total"]["outstanding"] == 50_000 and totals["total"]["usd"]["outstanding"] == 12_000
    assert totals["items"][0]["usd"] == {"outstanding": 12_000, "debtors": 2, "overdue": 0, "due_today": 9_000}

    period = read(
        client, world.manager_a, f"{shop(world)}/reports/period", **{"from": str(today), "to": str(today)}
    ).json()
    assert period["usd"]["credit"] == {"amount": 12_000, "count": 2, "customers": 2}
    assert period["usd"]["outstanding"]["end"] == 12_000
    assert [d["balance"] for d in period["usd"]["top_debtors"]] == [9_000, 3_000]
    assert period["credit"]["amount"] % 1_000 == 0 and period["outstanding"]["end"] == 50_000, (
        "so'm figures hold no cents"
    )
    overdue = read(client, world.manager_a, f"{shop(world)}/reports/overdue").json()
    assert set(overdue) == {"as_of", "total", "bands", "usd"} and set(overdue["usd"]) == {"total", "bands"}

    # A customer who owes only dollars cannot be archived (INV-13).
    archived = write(client, world.manager_a, "POST", f"{shop(world)}/customers/{world.settled_customer_a}/archive")
    assert archived.json()["error"]["code"] == "CUSTOMER_HAS_BALANCE"


# --- the customer's side -------------------------------------------------------------------------------------


def test_the_customer_sees_each_balance_and_tells_the_shop_of_a_dollar_payment(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    assert record(client, world, "credit", 12_000).status_code == 201
    link = link_of(owner, world)
    mine = as_user(world.customer_of_a)
    accounts = client.get("/api/v1/me/accounts", headers=mine).json()["items"]
    assert [(a["balance"], a["usd"]) for a in accounts] == [(50_000, {"balance": 12_000})]
    account = client.get(f"/api/v1/me/accounts/{link}", headers=mine).json()
    assert (account["balance"], account["usd"]["balance"]) == (50_000, 12_000)
    assert [(e["amount"], e.get("currency")) for e in account["entries"]] == [(12_000, "USD"), (50_000, None)]

    notices = f"/api/v1/me/accounts/{link}/payment-notices"
    # More dollars than are owed is refused, though 50 000 so'm are owed; so is an unknown currency.
    over = client.post(notices, json={"amount": 12_001, "currency": "USD"}, headers=mine)
    assert (over.status_code, over.json()["error"]["code"]) == (409, "EXCEEDS_BALANCE")
    assert client.post(notices, json={"amount": 500, "currency": "EUR"}, headers=mine).status_code == 422
    sent = client.post(notices, json={"amount": 2_050, "currency": "USD"}, headers=mine)
    assert sent.status_code == 201 and (sent.json()["amount"], sent.json()["currency"]) == (2_050, "USD")
    told = owner.execute(
        "SELECT payload->>'text' FROM outbox_message WHERE dedupe_key LIKE %s", (f"notice:{sent.json()['id']}%",)
    ).fetchall()
    assert told and all(money("uz", 2_050, USD) in text and money("uz", 12_000, USD) in text for (text,) in told)

    waiting = read(client, world.manager_a, f"{shop(world)}/payment-notices").json()["items"]
    assert [(n["amount"], n["currency"], n["customer_balance"]) for n in waiting] == [(2_050, "USD", 12_000)]
    accepted = write(client, world.manager_a, "POST", f"{shop(world)}/payment-notices/{sent.json()['id']}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["entry"]["currency"] == "USD"
    assert (accepted.json()["customer"]["balance"], accepted.json()["customer"]["usd"]["balance"]) == (50_000, 9_950)
    assert usd_rows(owner, world) == [("credit", 12_000), ("payment", 2_050)]


# --- the chat ------------------------------------------------------------------------------------------------


def test_in_a_shop_with_dollars_the_bot_records_dollars_and_still_reads_som(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    seller = chat_of(client, owner, world.seller_a)
    sold = seller.say("Ali 50.5$")
    assert sold.text.startswith(f"✅ Shop A\nAli: +{money('uz', 5_050, USD)}"), sold.text
    assert money("uz", 5_050, USD) == f"50.50{NBSP}$"
    assert usd_rows(owner, world) == [("credit", 5_050)]
    # The balance the reply states is the dollar balance, not the so'm one and not a sum.
    assert sold.text.count(money("uz", 5_050, USD)) == 2 and "50 000" not in sold.text.replace(NBSP, " ")

    paid = seller.say("Ali 20 usd berdi")
    assert money("uz", 2_000, USD) in paid.text and money("uz", 3_050, USD) in paid.text
    assert usd_rows(owner, world) == [("credit", 5_050), ("payment", 2_000)]

    # Without a dollar mark it is so'm, as it always was.
    assert seller.say("Ali 45000").text.startswith(f"✅ Shop A\nAli: +45{NBSP}000{NBSP}so'm")
    assert usd_rows(owner, world) == [("credit", 5_050), ("payment", 2_000)]

    # What could be read two ways is asked about, and nothing is recorded.
    for text, reply in (
        ("Ali 1.250$", "parse_ambiguous_usd"),
        ("Ali 50$ so'm", "parse_ambiguous_usd"),
        ("Ali 50.1234$", "parse_amount_too_precise"),
        ("Ali 20000$", "amount_range_usd"),
    ):
        assert seller.say(text).text == say("uz", reply), text
    # A payment above the dollar debt is refused like any other.
    assert seller.say("Ali -31$").text == say("uz", "EXCEEDS_BALANCE")
    assert usd_rows(owner, world) == [("credit", 5_050), ("payment", 2_000)]


def test_the_customers_own_list_states_both_balances_side_by_side(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    customer = chat_of(client, owner, world.customer_of_a)
    assert money("uz", 50_000) in customer.say("/qarzim").text
    assert "$" not in customer.say("/qarzim").text, "nothing is owed in dollars: nothing is said of them"
    assert record(client, world, "credit", 12_050).status_code == 201
    shown = customer.say("/qarzim").text
    assert both("uz", 50_000, 12_050) in shown
    assert both("uz", 50_000, 12_050) == f"50{NBSP}000{NBSP}so'm va 120.50{NBSP}$"
    assert both("ru", 50_000, 12_050) == f"50{NBSP}000{NBSP}сум и 120.50{NBSP}$"
    assert both("uz", 50_000, None) == both("uz", 50_000, 0) == money("uz", 50_000), "without dollars: as before"
    assert both("uz", 0, 12_050) == money("uz", 12_050, USD)


# --- reminders -------------------------------------------------------------------------------------------------


def test_one_reminder_states_what_is_due_in_each_currency(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    today = tashkent_date(datetime.now(UTC))
    owner.execute("UPDATE shop SET reminders_on = true WHERE id = %s", (world.shop_a,))
    # 12.00 $ due today; the 50 000 so'm are due in a week and are not mentioned.
    assert record(client, world, "credit", 1_200, promised_date=str(today)).status_code == 201
    sent = write(
        client, world.manager_a, "POST", f"{shop(world)}/reminders/manual", {"customer_id": str(world.customer_a)}
    )
    assert sent.status_code == 201, sent.text
    assert sent.json() == {"sent": True, "channel": "telegram", "amount": 0, "usd": {"amount": 1_200}}
    assert owner.execute(
        "SELECT amount, amount_usd FROM reminder WHERE customer_id = %s", (world.customer_a,)
    ).fetchall() == [(0, 1_200)]
    text = owner.execute(
        "SELECT payload->>'text' FROM outbox_message WHERE dedupe_key LIKE 'reminder:manual:%%' AND shop_id = %s",
        (world.shop_a,),
    ).fetchone()
    assert text is not None and money("uz", 1_200, USD) in text[0] and "so'm" not in text[0]

    # Both due: one message, two amounts, no sum. (A second manual reminder today is refused: INV-14.)
    owner.execute(
        "UPDATE promise SET promised_date = %s WHERE entry_id = %s", (today - timedelta(days=1), world.entry_a)
    )
    owner.execute("SELECT refresh_open_debts(ARRAY[%s::uuid])", (world.customer_a,))
    again = write(
        client, world.manager_a, "POST", f"{shop(world)}/reminders/manual", {"customer_id": str(world.customer_a)}
    )
    assert again.json()["error"]["code"] == "REMINDER_LIMIT_REACHED"


def test_a_customer_reachable_only_by_sms_is_not_reminded_of_dollars_by_sms(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    """An SMS states so'm only for now: a customer who owes only dollars and has no Telegram is unreachable."""
    today = tashkent_date(datetime.now(UTC))
    owner.execute("UPDATE shop SET reminders_on = true, sms_on = true WHERE id = %s", (world.shop_a,))
    owner.execute("UPDATE customer SET phone = '+998901112233' WHERE id = %s", (world.settled_customer_a,))
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('sms_on', 'true', 'test'), "
        "('sms_monthly_quota', '100', 'test')"
    )
    try:
        assert (
            record(
                client, world, "credit", 1_200, customer=world.settled_customer_a, promised_date=str(today)
            ).status_code
            == 201
        )
        listed = read(client, world.manager_a, f"{shop(world)}/reminders/unreachable").json()["items"]
        assert [(i["display_name"], i["amount"], i["usd"]) for i in listed] == [("Vali", 0, {"amount": 1_200})]
        refused = write(
            client,
            world.manager_a,
            "POST",
            f"{shop(world)}/reminders/manual",
            {"customer_id": str(world.settled_customer_a)},
        )
        assert refused.json()["error"]["code"] == "CUSTOMER_UNREACHABLE"
        assert owner.execute(
            "SELECT count(*) FROM outbox_message WHERE channel = 'sms' AND shop_id = %s", (world.shop_a,)
        ).fetchone() == (0,)
    finally:
        owner.execute("DELETE FROM platform_setting WHERE key IN ('sms_on', 'sms_monthly_quota')")


# --- the export ------------------------------------------------------------------------------------------------


def test_the_export_has_dollar_columns_only_for_a_shop_with_dollar_entries(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    worker_database_url: str,
    file_root: Path,
    platform_on: None,
) -> None:
    # The worker takes waiting jobs of any shop: those that other tests left are closed first.
    owner.execute(
        "UPDATE export_job SET status = 'failed', error = 'interrupted' WHERE status IN ('queued', 'running')"
    )
    before = ask(client, world)
    assert work(worker_database_url, file_root) == 1
    plain_book = workbook(client, world, before.json()["id"])
    ledger_sheet, customers_sheet = list(plain_book)[2], list(plain_book)[1]
    width = len(plain_book[ledger_sheet][0])
    assert "Valyuta" not in plain_book[ledger_sheet][0] and len(plain_book[customers_sheet][0]) == 7
    assert not any("$" in str(cell) for sheet in plain_book.values() for row in sheet for cell in row)

    assert write(client, world.owner_a, "PATCH", shop(world), {"usd_on": True}).status_code == 200

    assert record(client, world, "credit", 12_050).status_code == 201
    after = ask(client, world)
    assert after.status_code in (200, 201, 202), after.text
    assert work(worker_database_url, file_root) == 1
    book = workbook(client, world, after.json()["id"])
    header, rows = book[ledger_sheet][0], book[ledger_sheet][1:]
    assert len(header) == width + 1 and header[-1] == "Valyuta"
    assert [(row[3], row[-1]) for row in rows] == [(50_000, "UZS"), (120.5, "USD")], "dollars and cents, not cents"
    people = {row[0]: row for row in book[customers_sheet][1:]}
    assert book[customers_sheet][0][-2:] == ["Nasiya limiti ($)", "Qarzi ($)"]
    assert (people["Ali"][4], people["Ali"][-1]) == (50_000, 120.5), "the so'm debt and the dollar debt, two cells"
    summary = {row[0]: row[1] for row in book[next(iter(book))] if len(row) >= 2}
    assert (summary["Jami qarz"], summary["Jami qarz ($)"]) == (50_000, 120.5)


# --- disputes and date requests ----------------------------------------------------------------------------


def test_a_dispute_and_a_date_request_about_a_dollar_entry_say_so(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    sale = record(client, world, "credit", 12_050).json()["entry"]
    link, mine = link_of(owner, world), as_user(world.customer_of_a)
    later = tashkent_date(datetime.now(UTC)) + timedelta(days=60)
    asked = client.post(
        f"/api/v1/me/accounts/{link}/date-requests",
        json={"entry_id": sale["id"], "requested_date": str(later)},
        headers=mine,
    )
    assert asked.status_code == 201, asked.text
    disputed = client.post(
        f"/api/v1/me/accounts/{link}/disputes",
        json={"entry_id": sale["id"], "reason": "Bunday olmaganman"},
        headers=mine,
    )
    assert disputed.status_code == 201, disputed.text

    requests = read(client, world.manager_a, f"{shop(world)}/date-requests").json()["items"]
    assert [(r["amount"], r["currency"]) for r in requests] == [(12_050, "USD")]
    disputes = read(client, world.manager_a, f"{shop(world)}/disputes").json()["items"]
    assert [(d["amount"], d["currency"]) for d in disputes] == [(12_050, "USD")]
    # The managers are told the amount in dollars, never as 12 050 so'm.
    told = owner.execute(
        "SELECT payload->>'text' FROM outbox_message WHERE dedupe_key LIKE %s", (f"dispute:{disputed.json()['id']}%",)
    ).fetchall()
    assert told and all(money("uz", 12_050, USD) in text and "so'm" not in text for (text,) in told)

    # While the shop does not show dollars, both wait unseen, and the customer cannot open another.
    owner.execute("UPDATE platform_setting SET value = 'false' WHERE key = 'usd_on'")
    assert read(client, world.manager_a, f"{shop(world)}/date-requests").json()["items"] == []
    assert read(client, world.manager_a, f"{shop(world)}/disputes").json()["items"] == []
    other = record(client, world, "credit", 5_000, None).json()["entry"]
    assert client.post(
        f"/api/v1/me/accounts/{link}/disputes", json={"entry_id": sale["id"], "reason": "Yana bir bor"}, headers=mine
    ).status_code in (404, 409)
    assert "currency" not in other


# --- the customer's read-only link (module B) --------------------------------------------------------------


def test_the_public_page_shows_each_currency_by_itself(
    client: TestClient, world: World, owner: psycopg.Connection, dollars: None
) -> None:
    from .test_customer_shares import make, switch, view

    switch(owner)
    today = tashkent_date(datetime.now(UTC))
    assert record(client, world, "credit", 12_050, promised_date=str(today)).status_code == 201
    assert record(client, world, "payment", 2_050).status_code == 201
    token = make(client, world, world.customer_a)

    # The negative case the page once had: an account with entries of two currencies must be read book
    # by book, not handed whole to a calculation that refuses it.
    shown = view(client, token)
    assert shown.status_code == 200, shown.text
    body = shown.json()
    assert (body["balance"], body["overdue"]) == (50_000, {"amount": 0, "due_today": 0})
    assert body["usd"] == {"balance": 10_000, "overdue": {"amount": 0, "due_today": 10_000}}
    assert [(e["kind"], e["amount"], e.get("currency")) for e in body["entries"]] == [
        ("payment", 2_050, "USD"),
        ("credit", 12_050, "USD"),
        ("credit", 50_000, None),
    ]
    assert body["entries_total"] == 3
    assert 60_000 not in (body["balance"], body["usd"]["balance"]), "never a sum of so'm and cents"

    # While the shop does not show dollars, the page is the so'm book alone, in the shape it always had.
    owner.execute("UPDATE platform_setting SET value = 'false' WHERE key = 'usd_on'")
    hidden = view(client, token)
    assert hidden.status_code == 200, hidden.text
    assert keys_named(hidden.json()) == set()
    assert (hidden.json()["balance"], hidden.json()["entries_total"]) == (50_000, 1)
    assert [e["amount"] for e in hidden.json()["entries"]] == [50_000]
