"""The contract between the network's screens and its API, point by point (module J, the final pass).

The screens were written against a described contract and guessed in twelve places. Each guess was checked
against the server; where the two disagreed one of them was changed, and each point is held here to what
the server does (the screens' side of the same points is `frontend/src/shared/network/contract.test.tsx`).
The second half is what a security reviewer was asked to look at first: what crosses from one shop to the
other, and in what form (docs/10-operations/security-review.md, "The network between shops").
"""

import json
import re
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import World, as_user, set_overrides, switch_permissions_on
from .test_customers_ledger import read, write
from .test_network import (
    PRICES,
    RICE,
    SUGAR,
    TOTAL,
    Deal,
    Side,
    accept,
    confirm,
    deal,
    deliver,
    delivered,
    goods,
    item,
    ledger,
    net,
    new_goods,
    new_shop,
    ok,
    on,
    order,
    owed_to_supplier,
    owing,
    pay,
    refused,
    snapshot,
    switch,
    told,
)

pytestmark = pytest.mark.db

__all__ = ["on"]

SRC = Path(__file__).resolve().parents[2] / "src" / "qarz"


def keyed(client: TestClient, user: uuid.UUID, method: str, path: str, key: str, body: Any = None) -> Any:
    """A write with a key the test chooses, to repeat it."""
    return client.request(method, path, json=body, headers={**as_user(user), "Idempotency-Key": key})


def link_page(client: TestClient, side: Side, link: str) -> dict[str, Any]:
    return ok(read(client, side.user, f"{net(side.shop)}/links/{link}"))


def cash_entries(owner: psycopg.Connection, shop: uuid.UUID) -> list[tuple[str, str, int]]:
    rows = owner.execute(
        "SELECT direction, method, amount FROM cash_entry WHERE shop_id = %s ORDER BY created_at, id", (shop,)
    ).fetchall()
    return [(str(direction), str(method), int(amount)) for direction, method, amount in rows]


# --- 1: what awaits is money, what waits for a step is a count -------------------------------------------


def test_what_awaits_in_the_reconciliation_is_an_amount_and_what_waits_for_a_step_is_a_count(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    # A first delivery was confirmed, so the buyer owes: the supplier's own ledger takes a customer's
    # payment only up to what that customer owes, here as everywhere.
    d = owing(client, world, owner)
    delivered(client, d, paid=20_000)
    assert pay(client, d.buyer, d.link, 7_000).status_code == 201
    assert pay(client, d.buyer, d.link, 5_000).status_code == 201
    assert pay(client, d.supplier, d.link, 3_000).status_code == 201
    [row] = link_page(client, d.buyer, d.link)["reconciliation"]
    # Sums of money in the row's currency: two payments of the buyer's are 12 000, not "2".
    assert row["awaiting"] == {
        "notes": TOTAL - 20_000,
        "payments_own": 12_000,
        "payments_partner": 3_000,
        "payments_declined": 0,
    }
    [theirs] = link_page(client, d.supplier, d.link)["reconciliation"]
    assert theirs["awaiting"] == {**row["awaiting"], "payments_own": 3_000, "payments_partner": 12_000}
    assert row["agreed"] == theirs["agreed"] == {"delivered": TOTAL, "paid": 0, "balance": TOTAL}
    # The overview counts things: one note for the buyer to answer, two payments for the supplier.
    waiting = ok(read(client, d.buyer.user, net(d.buyer.shop)))["waiting"]
    assert (waiting["notes"], waiting["payments"]) == (1, 1)
    assert ok(read(client, d.supplier.user, net(d.supplier.shop)))["waiting"]["payments"] == 2


# --- 2: how money was paid ---------------------------------------------------------------------------------


def test_how_money_was_paid_is_never_required_and_is_refused_only_where_nothing_was_paid(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """`method` is for the cash book. The server never asks for it: without one, money is cash; with the
    cash book off it is read and nothing is written. It is refused where there is no money to describe."""
    d = deal(client, world)
    # The cash book is off: a note with money paid on delivery is confirmed with a method and without.
    _, first = delivered(client, d, paid=20_000)
    ok(confirm(client, d, first, **new_goods({1: 16_000, 2: 14_000})))
    _, second = delivered(client, d, paid=20_000)
    ok(confirm(client, d, second, **goods(owner, d.buyer.shop), method="card"))
    assert pay(client, d.buyer, d.link, 5_000).status_code == 201
    assert pay(client, d.buyer, d.link, 5_000, method="transfer").status_code == 201
    assert cash_entries(owner, d.buyer.shop) == [] == cash_entries(owner, d.supplier.shop)

    switch(owner, "cash_book_on")
    # On: without a method the money is cash; a method is kept; each side's cash book holds its own side.
    _, third = delivered(client, d, paid=20_000)
    ok(confirm(client, d, third, **goods(owner, d.buyer.shop)))
    _, fourth = delivered(client, d, paid=30_000)
    ok(confirm(client, d, fourth, **goods(owner, d.buyer.shop), method="card"))
    paid = ok(pay(client, d.buyer, d.link, 9_000, method="transfer"), 201)
    ok(write(client, d.supplier.user, "POST", f"{net(d.supplier.shop)}/payments/{paid['id']}/confirm", {}))
    assert cash_entries(owner, d.buyer.shop) == [
        ("expense", "cash", 20_000),
        ("expense", "card", 30_000),
        ("expense", "transfer", 9_000),
    ]
    # Money paid on delivery is one payment between the two shops: the supplier's cash book takes it by
    # the method the buyer named when it confirmed (cash where it named none), not always as cash. A
    # payment recorded apart is different: each side says how its own money moved, and the supplier's own
    # confirmation said nothing, so that one is cash whatever the buyer's side called it.
    assert cash_entries(owner, d.supplier.shop) == [
        ("income", "cash", 20_000),
        ("income", "card", 30_000),
        ("income", "cash", 9_000),
    ]

    # Nothing was paid on delivery: a method describes no money and is refused, on or off.
    _, credit = delivered(client, d)
    error = refused(confirm(client, d, credit, **goods(owner, d.buyer.shop), method="cash"), 422, "VALIDATION")
    assert set(error["fields"]) == {"method"}
    error = refused(confirm(client, d, credit, **goods(owner, d.buyer.shop), method="cheque"), 422, "VALIDATION")
    assert set(error["fields"]) == {"method"}
    ok(confirm(client, d, credit, **goods(owner, d.buyer.shop)))


# --- 4: taking a payment back -------------------------------------------------------------------------------


def test_taking_a_payment_back_is_its_recorders_alone_while_it_waits_and_asks_for_the_right_to_cancel_the_entry(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    d = owing(client, world, owner)
    by_buyer = ok(pay(client, d.buyer, d.link, 10_000), 201)["id"]
    by_supplier = ok(pay(client, d.supplier, d.link, 4_000), 201)["id"]

    def take_back(side: Side, payment: str, user: uuid.UUID | None = None) -> Any:
        return write(client, user or side.user, "POST", f"{net(side.shop)}/payments/{payment}/withdraw")

    # The other side's payment is not one's own to take back.
    refused(take_back(d.supplier, by_buyer), 409, "NETWORK_STATE")
    refused(take_back(d.buyer, by_supplier), 409, "NETWORK_STATE")
    # The buyer's entry is a payment to a supplier: `suppliers.pay` takes it back.
    set_overrides(owner, world.manager_a_membership, denied=("suppliers.pay",))
    error = refused(take_back(d.buyer, by_buyer, world.manager_a), 403, "FORBIDDEN_PERMISSION")
    assert error["fields"] == {"permission": "suppliers.pay"}
    # The supplier's entry is a customer's payment: cancelling it is `entries.cancel`, and the right to
    # record a payment is not enough. (Shop B has only its owner, so the check is made on a mirror link.)
    back = ok(write(client, world.owner_a, "POST", f"{net(world.shop_a)}/invites", {"as": "supplier"}), 201)["code"]
    mirror = ok(write(client, world.owner_b, "POST", f"{net(world.shop_b)}/links", {"code": back, "as": "buyer"}), 201)
    mirror_id = mirror["link"]["id"]
    ok(write(client, world.owner_a, "POST", f"{net(world.shop_a)}/links/{mirror_id}/accept"))
    mirrored = Deal(Side(world.shop_b, world.owner_b), Side(world.shop_a, world.owner_a), mirror_id)
    _, mirror_note = delivered(client, mirrored)
    ok(confirm(client, mirrored, mirror_note, **new_goods({1: 16_000, 2: 14_000})))
    recorded = ok(pay(client, mirrored.supplier, mirror_id, 6_000), 201)["id"]
    set_overrides(owner, world.manager_a_membership, denied=("entries.cancel",))
    error = refused(take_back(Side(world.shop_a, world.manager_a), recorded), 403, "FORBIDDEN_PERMISSION")
    assert error["fields"] == {"permission": "entries.cancel"}
    set_overrides(owner, world.manager_a_membership, denied=("payments.record",))
    assert ok(take_back(Side(world.shop_a, world.manager_a), recorded))["status"] == "withdrawn"

    set_overrides(owner, world.manager_a_membership)
    assert ok(take_back(d.buyer, by_buyer, world.manager_a))["status"] == "withdrawn"
    # Once: a withdrawn payment is not withdrawn again, and an answered one never.
    refused(take_back(d.buyer, by_buyer), 409, "NETWORK_STATE")
    ok(write(client, d.buyer.user, "POST", f"{net(d.buyer.shop)}/payments/{by_supplier}/confirm", {}))
    refused(take_back(d.supplier, by_supplier), 409, "NETWORK_STATE")


# --- 5: a corrected note --------------------------------------------------------------------------------------


def test_a_correction_says_the_whole_note_what_it_leaves_out_is_not_kept_from_the_note_before(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """`paid` left out is nothing paid; `lines` left out are the ORDER's accepted lines. Neither is "as
    the note before it said", which is why the screen always sends both."""
    d = deal(client, world)
    order_id, first = delivered(client, d, paid=20_000)

    def correct(note: str, **body: Any) -> dict[str, Any]:
        path = f"{net(d.supplier.shop)}/notes/{note}/correct"
        return ok(write(client, d.supplier.user, "POST", path, {"reason": "Tuzatish", **body}), 201)

    # Only the sugar, by the number its line has on the ORDER (2): the note's line keeps that number.
    second = correct(first, paid=5_000, lines=[{"line_no": 2, "qty": "3", "unit_price": 11_000}])
    assert (second["total"], second["paid"]) == (33_000, 5_000)
    assert [(line["line_no"], line["name"], line["qty"]) for line in second["lines"]] == [(2, "Shakar", "3")]
    assert second["supersedes_id"] == first
    # A line that is not the order's, or one named twice, is refused; so is a line of nothing.
    for wrong in (
        [{"line_no": 3, "qty": "1", "unit_price": 1_000}],
        [{"line_no": 2, "qty": "1", "unit_price": 1_000}, {"line_no": 2, "qty": "1", "unit_price": 1_000}],
        [{"line_no": 2, "qty": "0", "unit_price": 1_000}],
    ):
        path = f"{net(d.supplier.shop)}/notes/{second['id']}/correct"
        response = write(client, d.supplier.user, "POST", path, {"reason": "Tuzatish", "lines": wrong})
        assert set(refused(response, 422, "VALIDATION")["fields"]) <= {
            "lines.0.line_no",
            "lines.1.line_no",
            "lines.0.qty",
        }
    # Nothing but a reason: NOT the note before it (sugar, 5 000 paid), but the order as accepted, unpaid.
    third = correct(second["id"])
    assert (third["total"], third["paid"], third["terms"]) == (TOTAL, 0, "credit")
    assert [(line["line_no"], line["qty"]) for line in third["lines"]] == [(1, "10"), (2, "4.5")]
    # The buyer's copy says the same, line for line.
    theirs = ok(read(client, d.buyer.user, f"{net(d.buyer.shop)}/notes/{third['id']}"))
    assert [(line["line_no"], line["qty"], line["unit_price"]) for line in theirs["lines"]] == [
        (1, "10", 12_000),
        (2, "4.5", 11_000),
    ]
    assert ok(read(client, d.supplier.user, f"{net(d.supplier.shop)}/orders/{order_id}"))["status"] == "delivered"


# --- 6: a name the catalogue already has ------------------------------------------------------------------


def test_a_name_the_catalogue_has_is_refused_with_the_place_of_the_line_and_the_item_to_use(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = deal(client, world)
    existing = item(client, d.buyer.shop, d.buyer.user, "Shakar")
    # Only the sugar is delivered, so the ORDER's line 2 is the note's first line.
    order_id = order(client, d, [RICE, SUGAR])
    ok(accept(client, d, order_id, [{"line_no": 1, "qty": "0", "unit_price": 12_000}, PRICES[1]]))
    note_id = deliver(client, d, order_id)
    before = snapshot(owner, *d.shops)
    error = refused(confirm(client, d, note_id, **new_goods({2: 14_000})), 409, "CATALOG_NAME_TAKEN")
    # `line` is the place among the note's lines, counted from zero; not the order's line number.
    assert error["fields"] == {"line": "0", "existing_id": existing}
    assert snapshot(owner, *d.shops) == before
    # The item it names can be used as it is.
    ok(confirm(client, d, note_id, lines=[{"line_no": 2, "item_id": error["fields"]["existing_id"]}]))


# --- 7: sending a draft is two writes --------------------------------------------------------------------


def test_sending_a_draft_again_with_the_same_two_keys_sends_one_order(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = deal(client, world)
    key = "contract-" + uuid.uuid4().hex
    drafts, body = f"{net(d.buyer.shop)}/drafts", {"link_id": d.link, "lines": [RICE]}
    made = ok(keyed(client, d.buyer.user, "POST", drafts, key, body), 201)
    sent = ok(keyed(client, d.buyer.user, "POST", f"{drafts}/{made['id']}/send", f"{key}-send"))
    assert (sent["status"], sent["id"], sent["number"]) == ("sent", made["id"], 1)
    notices = told(owner, d.supplier.user)

    # The answer to the second write was lost: the screen sends both again, with the same keys. The
    # draft is gone (it became the order), and the first write is still answered from what was stored.
    assert ok(keyed(client, d.buyer.user, "POST", drafts, key, body), 201) == made
    assert ok(keyed(client, d.buyer.user, "POST", f"{drafts}/{made['id']}/send", f"{key}-send")) == sent
    listed = ok(read(client, d.buyer.user, f"{net(d.buyer.shop)}/orders"))["orders"]
    assert [row["id"] for row in listed] == [made["id"]]
    assert ok(read(client, d.buyer.user, drafts))["drafts"] == []
    assert told(owner, d.supplier.user) == notices, "the supplier is told once"

    # A key is one request's: the same key with another order is refused, and so is sending without one.
    other = {"link_id": d.link, "lines": [SUGAR]}
    refused(keyed(client, d.buyer.user, "POST", drafts, key, other), 409, "IDEMPOTENCY_KEY_REUSED")
    missing = client.post(f"{drafts}/{made['id']}/send", headers=as_user(d.buyer.user))
    assert set(refused(missing, 422, "VALIDATION")["fields"]) == {"Idempotency-Key"}
    # An order that was sent is no draft to send again under a new key.
    refused(write(client, d.buyer.user, "POST", f"{drafts}/{made['id']}/send"), 404, "NOT_FOUND")


# --- 9: a DELETE carries a key, and answers with a body ----------------------------------------------------


def test_deleting_a_draft_and_withdrawing_an_invitation_need_a_key_and_answer_the_same_when_repeated(
    client: TestClient, world: World, on: None
) -> None:
    d = deal(client, world)
    base = net(d.buyer.shop)
    draft = ok(write(client, d.buyer.user, "POST", f"{base}/drafts", {"link_id": d.link, "lines": [RICE]}), 201)
    invite = ok(write(client, d.buyer.user, "POST", f"{base}/invites", {"as": "buyer"}), 201)
    for path, answer in (
        (f"{base}/drafts/{draft['id']}", {"id": draft["id"], "deleted": True}),
        (f"{base}/invites/{invite['id']}", {"id": invite["id"], "revoked": True}),
    ):
        without = client.delete(path, headers=as_user(d.buyer.user))
        assert set(refused(without, 422, "VALIDATION")["fields"]) == {"Idempotency-Key"}
        key = "contract-" + uuid.uuid4().hex
        # No request body; the answer has one, which a client is free not to read.
        assert ok(keyed(client, d.buyer.user, "DELETE", path, key)) == answer
        assert ok(keyed(client, d.buyer.user, "DELETE", path, key)) == answer
        refused(write(client, d.buyer.user, "DELETE", path), 404, "NOT_FOUND")


# --- 10: how much may be typed -------------------------------------------------------------------------------


def test_the_limits_of_what_is_typed_are_the_ones_the_screens_keep_to(
    client: TestClient, world: World, on: None
) -> None:
    d = deal(client, world)
    drafts = f"{net(d.buyer.shop)}/drafts"

    def draft(**body: Any) -> Any:
        return write(client, d.buyer.user, "POST", drafts, {"link_id": d.link, "lines": [RICE], **body})

    def line(name: str) -> dict[str, str]:
        return {"name": name, "unit": "dona", "qty": "1"}

    # A line's name is the catalogue's rule for a good's name: 80 characters, with a letter or a digit,
    # so that the buyer can always take a delivered line as a new item under that name.
    assert draft(lines=[line("x" * 80)]).status_code == 201
    assert set(refused(draft(lines=[line("x" * 81)]), 422, "VALIDATION")["fields"]) == {"lines.0.name"}
    assert set(refused(draft(lines=[line("ь")]), 422, "VALIDATION")["fields"]) == {"lines.0.name"}
    assert draft(note="n" * 200).status_code == 201
    assert set(refused(draft(note="n" * 201), 422, "VALIDATION")["fields"]) == {"note"}
    assert draft(lines=[line(f"Tovar {number}") for number in range(100)]).status_code == 201
    too_many = draft(lines=[line(f"Tovar {number}") for number in range(101)])
    assert set(refused(too_many, 422, "VALIDATION")["fields"]) == {"lines"}
    assert set(refused(draft(lines=[]), 422, "VALIDATION")["fields"]) == {"lines"}

    order_id = order(client, d, [RICE])
    cancel = f"{net(d.buyer.shop)}/orders/{order_id}/cancel"
    for reason in ("xx", "x" * 201):
        response = write(client, d.buyer.user, "POST", cancel, {"reason": reason})
        assert set(refused(response, 422, "VALIDATION")["fields"]) == {"reason"}
    assert ok(write(client, d.buyer.user, "POST", cancel, {"reason": "x" * 200}))["status"] == "cancelled"


# --- 11: when the shop's own account is not shown ---------------------------------------------------------


def test_the_own_balance_is_absent_and_never_zero_when_it_cannot_be_read(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """Two different reasons, which the screen words differently: the link has no row of the shop's own
    books yet (`counterpart` is null), or the member may not see that account."""
    switch_permissions_on(owner)
    d = deal(client, world)
    delivered(client, d)
    # The buyer asked for the link and has done nothing since: no supplier row stands for the partner.
    page = link_page(client, d.buyer, d.link)
    assert page["link"]["counterpart"] is None
    [row] = page["reconciliation"]
    assert "own_balance" not in row and "difference" not in row
    assert row["awaiting"]["notes"] == TOTAL and row["agreed"]["balance"] == 0
    # The supplier's side has one (accepting made it), and its owner sees it; it agrees, with a note on its way.
    [theirs] = link_page(client, d.supplier, d.link)["reconciliation"]
    assert (theirs["own_balance"], theirs["difference"], theirs["awaiting"]["notes"]) == (0, 0, TOTAL)
    # With a row, a member who may not see suppliers' accounts still gets neither figure.
    ok(confirm(client, d, deliver_again(client, d), **new_goods({1: 16_000, 2: 14_000})))
    assert link_page(client, d.buyer, d.link)["link"]["counterpart"]["kind"] == "supplier"
    set_overrides(owner, world.manager_a_membership, denied=("suppliers.view",))
    rows = link_page(client, Side(d.buyer.shop, world.manager_a), d.link)["reconciliation"]
    assert rows and all("own_balance" not in found and "difference" not in found for found in rows)
    [seen] = link_page(client, d.buyer, d.link)["reconciliation"]
    assert (seen["own_balance"], seen["difference"]) == (TOTAL, 0)


def deliver_again(client: TestClient, d: Deal) -> str:
    """The id of the note that waits for the buyer."""
    notes = ok(read(client, d.buyer.user, f"{net(d.buyer.shop)}/notes", status="issued"))["notes"]
    return str(notes[0]["id"])


# --- 12: what a confirmed note links to ---------------------------------------------------------------------


def test_a_received_note_names_the_receipt_to_its_buyer_and_the_customer_to_its_supplier_and_never_the_others(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    d = deal(client, world)
    _, note_id = delivered(client, d)
    ok(confirm(client, d, note_id, **new_goods({1: 16_000, 2: 14_000})))
    mine = ok(read(client, d.buyer.user, f"{net(d.buyer.shop)}/notes/{note_id}"))
    theirs = ok(read(client, d.supplier.user, f"{net(d.supplier.shop)}/notes/{note_id}"))
    assert "stock_document_id" in mine and "ledger_entry_id" not in mine and "customer_id" not in mine
    assert "stock_document_id" not in theirs and {"ledger_entry_id", "customer_id"} <= set(theirs)
    # The receipt is a stock document like any other: it opens for `stock.receive` and not without it,
    # which is why the screen offers the link only to a member who holds it.
    document = f"/api/v1/shops/{d.buyer.shop}/stock/documents/{mine['stock_document_id']}"
    assert ok(read(client, world.manager_a, document))["kind"] == "receipt"
    set_overrides(owner, world.manager_a_membership, denied=("stock.receive", "stock.adjust"))
    refused(read(client, world.manager_a, document), 403, "FORBIDDEN_PERMISSION")
    # The note itself still opens: it is the network's.
    assert ok(read(client, world.manager_a, f"{net(d.buyer.shop)}/notes/{note_id}"))["status"] == "received"


# =============================================================================================================
# What a security reviewer was asked to look at first
# =============================================================================================================

MARKUP = (
    '<b>qalin</b> <a href="tg://user?id=1">bosing</a> *yulduz* _chiziq_ `kod` [x](http://evil.example) {shop} {0} %s'
)


def test_what_one_shop_types_reaches_the_others_telegram_as_plain_text_exactly_as_typed(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """A reason, a note, a shop's name: free text that crosses to the partner's members. It is sent as a
    message with a text and nothing else (no parse mode, no entities, no buttons), so Telegram shows the
    signs as signs; and it is put into the wording after the wording is chosen, so braces in it are not
    fields of the wording."""
    owner.execute("UPDATE shop SET name = %s WHERE id = %s", ("<i>Baraka</i> & {amount}", world.shop_a))
    d = deal(client, world)
    order_id = order(client, d, [RICE])
    cancel = f"{net(d.buyer.shop)}/orders/{order_id}/cancel"
    ok(write(client, d.buyer.user, "POST", cancel, {"reason": MARKUP}))
    said = told(owner, d.supplier.user)[-1]
    assert said == f"«<i>Baraka</i> & {{amount}}» buyurtma № 1 ni bekor qildi. Sabab: {MARKUP}"

    paid = ok(pay(client, d.buyer, d.link, 5_000, note=MARKUP), 201)
    decline = f"{net(d.supplier.shop)}/payments/{paid['id']}/decline"
    ok(write(client, d.supplier.user, "POST", decline, {"reason": MARKUP}))
    assert told(owner, d.buyer.user)[-1].endswith(f"Sabab: {MARKUP}")

    rows = owner.execute("SELECT channel, payload FROM outbox_message WHERE dedupe_key LIKE 'net:%%'").fetchall()
    assert len(rows) >= 5
    for channel, payload in rows:
        assert channel == "telegram"
        # Exactly a text: nothing that would make Telegram read markup, link a name or show a button.
        assert set(payload) == {"text"}, payload


def test_no_message_of_the_service_is_sent_with_a_parse_mode() -> None:
    """The counterpart, for every message there is and will be: nothing in the server chooses a parse
    mode or sends entities, and the bot is made without a default one. With either, the test above would
    still pass for the network's own texts while a partner's `<b>` became bold."""
    offenders = [
        f"{path.relative_to(SRC)}:{number}"
        for path in sorted(SRC.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if re.search(r"parse_mode|ParseMode|DefaultBotProperties|\bentities\b", line)
    ]
    assert offenders == []
    # The check sees what it is for.
    assert re.search(r"parse_mode|ParseMode|DefaultBotProperties|\bentities\b", 'send_message(text, parse_mode="HTML")')
    made = [
        line.strip()
        for path in sorted(SRC.rglob("*.py"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if "Bot(" in line
    ]
    assert made and all(re.search(r"\bBot\((settings\.bot_token|token)\)", line) for line in made), made


def test_no_answer_of_the_network_names_the_partners_shop_its_people_or_their_telegram(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """The database hands the application the partner's members' Telegram chats, to tell them
    (`network_notice_recipients`). They go into the outbox and nowhere else: no answer to either shop
    holds the other shop's identifier, a user or a membership of it, or a Telegram id."""
    d = deal(client, world)
    order_id, note_id = delivered(client, d, paid=20_000)
    ok(confirm(client, d, note_id, **new_goods({1: 16_000, 2: 14_000})))
    payment = ok(pay(client, d.buyer, d.link, 5_000), 201)["id"]
    ok(write(client, d.supplier.user, "POST", f"{net(d.supplier.shop)}/payments/{payment}/confirm", {}))

    def secrets_of(shop: uuid.UUID) -> set[str]:
        rows = owner.execute(
            "SELECT m.id::text, u.id::text, u.tg_id::text FROM membership m JOIN app_user u ON u.id = m.user_id "
            "WHERE m.shop_id = %s",
            (shop,),
        ).fetchall()
        return {str(shop)} | {value for row in rows for value in row if value is not None}

    for side, other in ((d.buyer, d.supplier), (d.supplier, d.buyer)):
        base = net(side.shop)
        paths = ["", f"/links/{d.link}", "/orders", f"/orders/{order_id}", "/notes", f"/notes/{note_id}", "/payments"]
        paths.append(f"/payments/{payment}")
        answers = json.dumps([ok(read(client, side.user, base + path)) for path in paths])
        hidden = secrets_of(other.shop)
        assert len(hidden) >= 4
        assert [value for value in hidden if value in answers] == []
    # Where they do go: the outbox, in a row of the shop that acted, addressed to a member of the other.
    # Nothing of the API reads the outbox back; the worker sends from it.
    queued = owner.execute(
        "SELECT o.shop_id, o.recipient FROM outbox_message o WHERE o.dedupe_key LIKE 'net:%%' AND o.shop_id = ANY(%s)",
        (list(d.shops),),
    ).fetchall()
    assert len(queued) >= 6
    for shop, recipient in queued:
        partner = d.supplier.shop if shop == d.buyer.shop else d.buyer.shop
        assert recipient in secrets_of(partner) and recipient not in secrets_of(shop)


def test_a_received_note_is_in_both_books_line_for_line_as_the_note_says(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """The database marks a note received when the totals of both postings are the note's
    (`network_receipt_finish`). That the LINES are the note's is the application's doing, held here: the
    buyer's receipt has each line of the note with its quantity and price, and the supplier's stock gave
    out exactly those quantities."""
    d = deal(client, world)
    rice = item(client, d.supplier.shop, d.supplier.user, "Guruch")
    sugar = item(client, d.supplier.shop, d.supplier.user, "Shakar")
    for item_id in (rice, sugar):
        ok(
            write(
                client,
                d.supplier.user,
                "POST",
                f"/api/v1/shops/{d.supplier.shop}/stock/documents",
                {"kind": "receipt", "post": True, "lines": [{"item_id": item_id, "qty": "100", "unit_cost": 9_000}]},
            ),
            201,
        )
    _, note_id = delivered(client, d, paid=20_000, own={"supplier_rice": rice, "supplier_sugar": sugar})
    ok(confirm(client, d, note_id, **new_goods({1: 16_000, 2: 14_000})))

    said = owner.execute(
        "SELECT name, qty, unit_price, line_total FROM network_note_line WHERE shop_id = %s AND note_id = %s "
        "ORDER BY line_no",
        (d.buyer.shop, note_id),
    ).fetchall()
    received = owner.execute(
        "SELECT c.name, l.qty, l.unit_cost, l.line_total FROM stock_document_line l "
        "JOIN stock_document s ON s.id = l.document_id JOIN catalog_item c ON c.id = l.item_id "
        "WHERE s.shop_id = %s AND s.origin_ref = %s ORDER BY l.line_no",
        (d.buyer.shop, note_id),
    ).fetchall()
    assert received == said and len(said) == 2
    given = owner.execute(
        "SELECT c.name, -m.qty FROM stock_movement m JOIN catalog_item c ON c.id = m.item_id "
        "WHERE m.shop_id = %s AND m.kind = 'sale' ORDER BY m.line_no",
        (d.supplier.shop,),
    ).fetchall()
    assert given == [(name, qty) for name, qty, _, _ in said]
    # And the money: one credit sale of the total and one payment of what was handed over, to the buyer's
    # account; the buyer owes its supplier the rest.
    customer = owner.execute(
        "SELECT customer_id FROM network_link WHERE shop_id = %s AND id = %s", (d.supplier.shop, d.link)
    ).fetchone()
    assert customer is not None
    assert ledger(owner, d.supplier.shop, customer[0]) == [("credit", TOTAL), ("payment", 20_000)]
    assert owed_to_supplier(owner, d.buyer.shop) == {"UZS": TOTAL - 20_000}


def test_the_suppliers_sale_is_written_in_the_name_of_who_issued_the_note_and_by_nobody_of_the_buyer(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """The one entry written with no member of its shop present (`sell_in`): it carries the authority of
    the note. Its author is the supplier's member who issued the note (the owner once that member has
    left or may no longer record a sale: tests/api/test_network_note_author.py), never a member of the
    buyer; and the buyer's member who confirmed needs no permission of the supplier's and gets none."""
    d = deal(client, world)
    _, note_id = delivered(client, d)
    issuer = owner.execute(
        "SELECT issued_by FROM network_note WHERE shop_id = %s AND id = %s", (d.supplier.shop, note_id)
    ).fetchone()
    assert issuer is not None
    ok(confirm(client, d, note_id, **new_goods({1: 16_000, 2: 14_000})))
    authors = owner.execute(
        "SELECT DISTINCT e.author_id, m.shop_id FROM ledger_entry e JOIN membership m ON m.id = e.author_id "
        "WHERE e.shop_id = %s",
        (d.supplier.shop,),
    ).fetchall()
    assert authors == [(issuer[0], d.supplier.shop)]
    movers = owner.execute(
        "SELECT DISTINCT author_id FROM stock_movement WHERE shop_id = %s", (d.buyer.shop,)
    ).fetchall()
    buyers_members = {
        row[0] for row in owner.execute("SELECT id FROM membership WHERE shop_id = %s", (d.buyer.shop,)).fetchall()
    }
    assert movers and {row[0] for row in movers} <= buyers_members
    # The buyer's member is no member of the supplier, before and after: every route of the supplier's
    # shop answers it as a stranger.
    assert read(client, d.buyer.user, f"{net(d.supplier.shop)}/notes/{note_id}").status_code in (403, 404)
    assert read(client, d.buyer.user, f"/api/v1/shops/{d.supplier.shop}/customers").status_code in (403, 404)


def test_a_third_shop_sees_nothing_of_what_two_others_did(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = deal(client, world)
    order_id, note_id = delivered(client, d)
    ok(confirm(client, d, note_id, **new_goods({1: 16_000, 2: 14_000})))
    payment = ok(pay(client, d.buyer, d.link, 5_000), 201)["id"]
    shop, user = new_shop(owner, "Uchinchi do'kon")
    base = net(shop)
    assert ok(read(client, user, base)) == {
        "links": [],
        "waiting": {"links": 0, "orders": 0, "deliveries": 0, "notes": 0, "rejected_notes": 0, "payments": 0},
        "invites": [],
    }
    for name in ("orders", "notes", "payments"):
        assert ok(read(client, user, f"{base}/{name}"))[name] == []
    for path in (f"/links/{d.link}", f"/orders/{order_id}", f"/notes/{note_id}", f"/payments/{payment}"):
        refused(read(client, user, base + path), 404, "NOT_FOUND")
    for path, body in (
        (f"/orders/{order_id}/cancel", {"reason": "Begona"}),
        (f"/notes/{note_id}/reject", {"reason": "Begona"}),
        (f"/payments/{payment}/confirm", {}),
        (f"/links/{d.link}/end", None),
    ):
        refused(write(client, user, "POST", base + path, body), 404, "NOT_FOUND")
