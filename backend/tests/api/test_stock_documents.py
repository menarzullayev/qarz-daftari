"""Stock documents through the API: receipts, returns, write-offs and stocktakes (module I).

A document is a draft, then posted, then cancelled. Posting writes movements and the money that goes with
them; cancelling reverses both; nothing posted is edited. Each rule has the case that works and the case
that is refused.
"""

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import World, as_user, set_overrides, switch_permissions_on
from .test_customers_ledger import detail, key, new_customer, read, reverse, shop, write
from .test_goods_lines import chosen, sell
from .test_stock import (
    actions,
    counted_item,
    document,
    item_of,
    line,
    mismatches,
    movements,
    new_item,
    on,
    receive,
    stock,
)

pytestmark = pytest.mark.db

__all__ = ["on"]


def supplier(client: TestClient, world: World, name: str = "Ulgurji bozor") -> str:
    response = write(client, world.manager_a, "POST", f"{shop(world)}/suppliers", {"name": name})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def owed(client: TestClient, world: World, supplier_id: str) -> list[dict[str, Any]]:
    body = read(client, world.manager_a, f"{shop(world)}/suppliers/{supplier_id}").json()
    balances: list[dict[str, Any]] = body["supplier"]["balances"]
    return balances


def act(client: TestClient, world: World, doc: Any, what: str, user: uuid.UUID | None = None, **body: Any) -> Any:
    return write(client, user or world.manager_a, "POST", f"{stock(world)}/documents/{doc}/{what}", body or None)


def cancel(client: TestClient, world: World, doc: Any, reason: str = "Xato kiritilgan") -> Any:
    return act(client, world, doc, "cancel", reason=reason)


def get(client: TestClient, world: World, doc: Any, user: uuid.UUID | None = None) -> dict[str, Any]:
    response = read(client, user or world.manager_a, f"{stock(world)}/documents/{doc}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def counts(owner: psycopg.Connection, world: World) -> dict[str, int]:
    found = {}
    for table in ("stock_document", "stock_document_line", "stock_movement", "supplier_entry", "catalog_item"):
        row = owner.execute(f"SELECT count(*) FROM {table} WHERE shop_id = %s", (world.shop_a,)).fetchone()
        assert row is not None
        found[table] = int(row[0])
    return found


# --- a purchase receipt ---------------------------------------------------------------------------------------


def test_a_receipt_is_a_draft_until_it_is_posted_and_only_then_moves_anything(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    who = supplier(client, world)
    made = document(client, world, kind="receipt", supplier_id=who, lines=[line(rice, "10", 10_000)])
    assert made.status_code == 201, made.text
    draft = made.json()
    assert (draft["status"], draft["number"], draft["total"], draft["paid"]) == ("draft", 1, 100_000, 0)
    assert draft["supplier"] == {"id": who, "name": "Ulgurji bozor"}
    assert draft["lines"] == [
        {
            "line_no": 1,
            "item": {"id": rice, "name": "Guruch", "unit": "kg"},
            "qty": "10",
            "unit_cost": 10_000,
            "line_total": 100_000,
        }
    ]
    assert item_of(client, world, rice)["on_hand"] == "0" and owed(client, world, who) == []

    # A draft is replaced as a whole.
    changed = write(
        client,
        world.manager_a,
        "PUT",
        f"{stock(world)}/documents/{draft['id']}",
        {"kind": "receipt", "supplier_id": who, "paid": 30_000, "lines": [line(rice, "12.5", 8_000)]},
    )
    assert changed.status_code == 200, changed.text
    assert (changed.json()["total"], changed.json()["paid"], changed.json()["lines"][0]["qty"]) == (
        100_000,
        30_000,
        "12.5",
    )

    posted = act(client, world, draft["id"], "post")
    assert posted.status_code == 200 and posted.json()["status"] == "posted", posted.text
    body = item_of(client, world, rice)
    assert body["on_hand"] == "12.5" and body["cost"] == {
        "currency": "UZS",
        "average": 8_000,
        "value": 100_000,
        "margin": 7_000,
    }
    assert owed(client, world, who) == [{"currency": "UZS", "balance": 70_000}], "the total owed, less what was paid"
    assert actions(owner, world, "stock.document") == [
        "stock.document_created",
        "stock.document_changed",
        "stock.document_posted",
    ]

    # Posted is final: it cannot be edited or posted again.
    again = act(client, world, draft["id"], "post")
    assert again.status_code == 409 and again.json()["error"]["code"] == "DOCUMENT_NOT_DRAFT"
    edited = write(
        client,
        world.manager_a,
        "PUT",
        f"{stock(world)}/documents/{draft['id']}",
        {"kind": "receipt", "supplier_id": who, "lines": [line(rice, "1", 1)]},
    )
    assert edited.status_code == 409 and edited.json()["error"]["code"] == "DOCUMENT_NOT_DRAFT"
    assert item_of(client, world, rice)["on_hand"] == "12.5"
    assert mismatches(owner, world) == []


def test_the_average_cost_is_weighted_over_receipts(client: TestClient, world: World, on: None) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)])
    receive(client, world, [line(rice, "30", 12_000)])
    cost = item_of(client, world, rice)["cost"]
    assert cost == {"currency": "UZS", "average": 11_500, "value": 460_000, "margin": 3_500}
    newest = movements(client, world, rice)[0]
    assert newest["document"]["kind"] == "receipt" and newest["document"]["number"] == 2
    assert newest["cost"] == {"currency": "UZS", "unit_cost": 12_000, "total": 360_000, "value_after": 460_000}


def test_a_receipt_without_a_supplier_is_paid_in_full(client: TestClient, world: World, on: None) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    posted = receive(client, world, [line(rice, "2", 10_000)])
    assert (posted["supplier"], posted["total"], posted["paid"]) == (None, 20_000, 20_000)
    part = document(client, world, kind="receipt", paid=5_000, lines=[line(rice, "2", 10_000)])
    assert part.status_code == 422 and "paid" in part.json()["error"]["fields"]


def test_a_receipt_line_can_name_a_good_the_catalogue_does_not_have(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    posted = document(
        client,
        world,
        kind="receipt",
        post=True,
        lines=[
            {
                "new_item": {"name": "Makaron", "unit": "paket", "price": 9_000, "barcode": "4780000000014"},
                "qty": "20",
                "unit_cost": 6_000,
            },
            line(world.catalog_item_a, "30", 2_500),
        ],
    )
    assert posted.status_code == 201, posted.text
    pasta = posted.json()["lines"][0]["item"]
    assert pasta["name"] == "Makaron" and pasta["unit"] == "paket"
    body = item_of(client, world, pasta["id"])
    assert (body["tracked"], body["on_hand"], body["barcodes"], body["price"]) == (True, "20", ["4780000000014"], 9_000)
    # Receiving an item the catalogue had turns its counting on.
    bread = item_of(client, world, world.catalog_item_a)
    assert (bread["tracked"], bread["on_hand"]) == (True, "30")
    assert "catalog.item.created" in actions(owner, world, "catalog.item")

    # The same name again is the catalogue's refusal, with the item that has it, and nothing is half made.
    before = counts(owner, world)
    taken = document(
        client,
        world,
        kind="receipt",
        post=True,
        lines=[
            {"new_item": {"name": "Sut", "price": 9_000}, "qty": "1", "unit_cost": 1},
            {"new_item": {"name": "makaron", "price": 1_000}, "qty": "1", "unit_cost": 1},
        ],
    )
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "CATALOG_NAME_TAKEN"
    assert taken.json()["error"]["fields"] == {"existing_id": pasta["id"], "line": "1"}
    assert counts(owner, world) == before, "the first new item of the refused document was not kept either"


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"kind": "gift"}, "kind"),
        ({"lines": []}, "lines"),
        ({"doc_date": "2020-01-01"}, "doc_date"),
        ({"doc_date": "2999-01-01"}, "doc_date"),
        ({"paid": 999_999_999}, "paid"),
        ({"paid": -1}, "paid"),
        ({"reason": "lost"}, "reason"),
        ({"customer_id": str(uuid.uuid4())}, "customer_id"),
        ({"supplier_id": str(uuid.uuid4())}, "supplier_id"),
        ({"currency": "EUR"}, "currency"),
        ({"note": "x" * 201}, "note"),
        ({"lines": [{"qty": "1", "unit_cost": 100}]}, "lines.0.item_id"),
        ({"lines": [{"item_id": str(uuid.uuid4()), "qty": "1", "unit_cost": 100}]}, "lines.0.item_id"),
        ({"lines": [{"item_id": "ITEM", "qty": "0", "unit_cost": 100}]}, "lines.0.qty"),
        ({"lines": [{"item_id": "ITEM", "qty": "1.2345", "unit_cost": 100}]}, "lines.0.qty"),
        ({"lines": [{"item_id": "ITEM", "qty": "1"}]}, "lines.0.unit_cost"),
        ({"lines": [{"item_id": "ITEM", "qty": "1", "unit_cost": -5}]}, "lines.0.unit_cost"),
        (
            {"lines": [{"new_item": {"name": " ", "price": 0, "unit": "bog'"}, "qty": "1", "unit_cost": 1}]},
            "lines.0.new_item.name",
        ),
    ],
)
def test_a_wrong_receipt_is_refused_and_nothing_is_stored(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, change: dict[str, Any], field: str
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    before = counts(owner, world)
    body: dict[str, Any] = {"kind": "receipt", "lines": [line(rice, "1", 10_000)], "post": True, **change}
    for row in body["lines"]:
        if row.get("item_id") == "ITEM":
            row["item_id"] = rice
    refused = write(client, world.manager_a, "POST", f"{stock(world)}/documents", body)
    assert refused.status_code == 422 and field in refused.json()["error"]["fields"], refused.text
    assert counts(owner, world) == before
    assert item_of(client, world, rice)["on_hand"] == "0"


def test_cancelling_a_receipt_takes_its_goods_and_its_debt_back_and_says_why(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    who = supplier(client, world)
    receive(client, world, [line(rice, "10", 10_000)])
    wrong = receive(client, world, [line(rice, "10", 30_000)], supplier_id=who, paid=100_000)
    assert item_of(client, world, rice)["cost"]["average"] == 20_000
    assert owed(client, world, who) == [{"currency": "UZS", "balance": 200_000}]

    no_reason = act(client, world, wrong["id"], "cancel", reason="  ")
    assert no_reason.status_code == 422 and "reason" in no_reason.json()["error"]["fields"]
    cancelled = cancel(client, world, wrong["id"], "Narx xato yozilgan")
    assert cancelled.status_code == 200, cancelled.text
    assert (cancelled.json()["status"], cancelled.json()["cancel_reason"]) == ("cancelled", "Narx xato yozilgan")
    body = item_of(client, world, rice)
    assert body["on_hand"] == "10" and body["cost"]["average"] == 10_000, "the average is what it was before"
    assert owed(client, world, who) == [], "the purchase and the payment are both reversed"
    entries = read(client, world.manager_a, f"{shop(world)}/suppliers/{who}").json()["entries"]
    assert [(row["kind"], row["amount"], row["reversed"], row["note"]) for row in entries] == [
        ("reversal", 300_000, False, "Narx xato yozilgan"),
        ("reversal", 100_000, False, "Narx xato yozilgan"),
        ("payment", 100_000, True, "Kirim № 2"),
        ("purchase", 300_000, True, None),
    ]
    again = cancel(client, world, wrong["id"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "DOCUMENT_CANCELLED"
    assert "stock.document_cancelled" in actions(owner, world, "stock.document")
    assert mismatches(owner, world) == []


def test_a_receipt_whose_goods_were_sold_cannot_be_cancelled(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """Cancelling would say the goods never came, while the books show them sold: refused, whole."""
    customer = new_customer(client, world, "Vali")
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    tea = counted_item(client, world, "Choy", 8_000)
    who = supplier(client, world)
    posted = receive(client, world, [line(tea, "5", 5_000), line(rice, "10", 10_000)], supplier_id=who)
    assert sell(client, world, customer, [chosen(rice, "0.5", 15_000)]).status_code == 201

    refused = cancel(client, world, posted["id"])
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "STOCK_ALREADY_USED"
    assert refused.json()["error"]["fields"] == {"item": rice, "name": "Guruch", "on_hand": "9.5", "wanted": "10"}
    assert get(client, world, posted["id"])["status"] == "posted"
    assert item_of(client, world, tea)["on_hand"] == "5", "the line that could have gone back stayed too"
    assert owed(client, world, who) == [{"currency": "UZS", "balance": 125_000}]
    assert mismatches(owner, world) == []


def test_a_draft_is_thrown_away_by_cancelling_it(client: TestClient, world: World, on: None) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    draft = document(client, world, kind="receipt", lines=[line(rice, "10", 10_000)]).json()
    dropped = cancel(client, world, draft["id"], "Kerak emas")
    assert dropped.status_code == 200 and dropped.json()["status"] == "cancelled"
    assert dropped.json()["lines"][0]["qty"] == "10", "what it said can still be read"
    assert item_of(client, world, rice)["on_hand"] == "0" and movements(client, world, rice) == []
    assert act(client, world, draft["id"], "post").json()["error"]["code"] == "DOCUMENT_NOT_DRAFT"
    listed = read(client, world.manager_a, f"{stock(world)}/documents", status="cancelled").json()["documents"]
    assert [row["id"] for row in listed] == [draft["id"]]


def test_documents_are_numbered_per_kind_and_listed_newest_first(client: TestClient, world: World, on: None) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    who = supplier(client, world)
    first = receive(client, world, [line(rice, "5", 10_000)])
    second = receive(client, world, [line(rice, "5", 10_000)], supplier_id=who)
    lost = document(client, world, kind="write_off", reason="lost", post=True, lines=[line(rice, "1")]).json()
    assert (first["number"], second["number"], lost["number"]) == (1, 2, 1)

    every = read(client, world.manager_a, f"{stock(world)}/documents").json()["documents"]
    assert [(row["kind"], row["number"]) for row in every] == [("write_off", 1), ("receipt", 2), ("receipt", 1)]
    assert "total" not in every[0], "a write-off carries no price"
    receipts = read(client, world.manager_a, f"{stock(world)}/documents", kind="receipt", limit=1).json()
    assert [row["number"] for row in receipts["documents"]] == [2] and receipts["next_cursor"]
    rest = read(
        client, world.manager_a, f"{stock(world)}/documents", kind="receipt", limit=1, cursor=receipts["next_cursor"]
    ).json()
    assert [row["number"] for row in rest["documents"]] == [1] and rest["next_cursor"] is None
    of_supplier = read(client, world.manager_a, f"{stock(world)}/documents", supplier_id=who).json()["documents"]
    assert [row["number"] for row in of_supplier] == [2]
    for params in ({"kind": "gift"}, {"status": "open"}, {"limit": 0}, {"cursor": "x"}):
        assert read(client, world.manager_a, f"{stock(world)}/documents", **params).status_code == 422, params
    assert read(client, world.manager_a, f"{stock(world)}/documents/{uuid.uuid4()}").status_code == 404
    # Another shop's document is not there.
    other = read(client, world.owner_b, f"/api/v1/shops/{world.shop_b}/stock/documents/{first['id']}")
    assert other.status_code == 404


# --- a return to the supplier ---------------------------------------------------------------------------------


def test_the_list_is_narrowed_by_kind_state_and_supplier_and_never_beyond_the_shop(
    client: TestClient, world: World, on: None
) -> None:
    """The three filters of the documents' list together, and what they refuse. A filter is a narrowing of
    the shop's own documents: another shop's supplier, or another shop's member, finds nothing of them."""
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    who, other = supplier(client, world), supplier(client, world, "Boshqa ulgurji")
    cash = receive(client, world, [line(rice, "5", 10_000)])
    credit = receive(client, world, [line(rice, "5", 10_000)], supplier_id=who)
    draft = document(client, world, kind="receipt", supplier_id=who, lines=[line(rice, "1", 10_000)]).json()
    back = document(client, world, kind="supplier_return", supplier_id=who, post=True, lines=[line(rice, "1", 10_000)])
    dropped = document(client, world, kind="write_off", reason="lost", lines=[line(rice, "1")]).json()
    assert cancel(client, world, dropped["id"], "Kerak emas").status_code == 200

    def listed(user: uuid.UUID = world.manager_a, shop_id: uuid.UUID = world.shop_a, **params: Any) -> list[str]:
        response = read(client, user, f"/api/v1/shops/{shop_id}/stock/documents", **params)
        assert response.status_code == 200, response.text
        return [row["id"] for row in response.json()["documents"]]

    assert listed(status="draft") == [draft["id"]]
    assert listed(status="cancelled") == [dropped["id"]]
    assert listed(status="posted") == [back.json()["id"], credit["id"], cash["id"]]
    assert listed(supplier_id=who) == [back.json()["id"], draft["id"], credit["id"]]
    assert listed(supplier_id=who, kind="receipt") == [draft["id"], credit["id"]]
    assert listed(supplier_id=who, kind="receipt", status="posted") == [credit["id"]]
    assert listed(kind="write_off", status="draft") == [], "the only write-off was thrown away"
    assert listed(supplier_id=other) == [], "a supplier nothing was bought from"
    assert listed(supplier_id=str(uuid.uuid4())) == [], "a supplier that does not exist narrows to nothing"
    # The filters page as the list does: one at a time, in the same order, with nothing skipped.
    first = read(client, world.manager_a, f"{stock(world)}/documents", supplier_id=who, limit=2).json()
    rest = read(
        client, world.manager_a, f"{stock(world)}/documents", supplier_id=who, limit=2, cursor=first["next_cursor"]
    ).json()
    assert [row["id"] for row in first["documents"] + rest["documents"]] == listed(supplier_id=who)
    assert rest["next_cursor"] is None

    # What is not a kind or a state is refused by name, not answered with an empty or an unfiltered list.
    for params, field in (({"kind": "gift"}, "kind"), ({"status": "open"}, "status"), ({"kind": ""}, "kind")):
        refused = read(client, world.manager_a, f"{stock(world)}/documents", **params)
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "VALIDATION", params
        assert list(refused.json()["error"]["fields"]) == [field], params
    assert read(client, world.manager_a, f"{stock(world)}/documents", supplier_id="x").status_code == 422

    # The other shop: its owner sees none of these, whatever they ask for, and nobody of this shop is
    # shown the other shop's list by naming their own supplier there.
    assert listed(world.owner_b, world.shop_b) == []
    for params in ({"supplier_id": who}, {"kind": "receipt"}, {"status": "posted"}, {"status": "draft"}):
        assert listed(world.owner_b, world.shop_b, **params) == [], params
    assert read(client, world.owner_b, f"{stock(world)}/documents", supplier_id=who).status_code in (403, 404)
    foreign = read(client, world.manager_a, f"/api/v1/shops/{world.shop_b}/stock/documents", supplier_id=who)
    assert foreign.status_code in (403, 404)


def test_a_return_to_a_supplier_takes_goods_out_at_the_average_and_lowers_the_debt_by_its_own_price(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    who = supplier(client, world)
    receive(client, world, [line(rice, "10", 10_000)], supplier_id=who)
    receive(client, world, [line(rice, "10", 12_000)], supplier_id=who)  # average 11 000

    back = document(client, world, kind="supplier_return", supplier_id=who, post=True, lines=[line(rice, "4", 12_000)])
    assert back.status_code == 201, back.text
    body = item_of(client, world, rice)
    assert body["on_hand"] == "16" and body["cost"]["value"] == 176_000 and body["cost"]["average"] == 11_000
    assert owed(client, world, who) == [{"currency": "UZS", "balance": 220_000 - 48_000}]

    too_much = document(
        client, world, kind="supplier_return", supplier_id=who, post=True, lines=[line(rice, "16.001", 1)]
    )
    assert too_much.status_code == 409 and too_much.json()["error"]["code"] == "STOCK_INSUFFICIENT"
    nobody = document(client, world, kind="supplier_return", post=True, lines=[line(rice, "1", 1)])
    assert nobody.status_code == 422 and "supplier_id" in nobody.json()["error"]["fields"]

    assert cancel(client, world, back.json()["id"]).status_code == 200
    assert item_of(client, world, rice)["on_hand"] == "20" and item_of(client, world, rice)["cost"]["value"] == 220_000
    assert owed(client, world, who) == [{"currency": "UZS", "balance": 220_000}]
    assert mismatches(owner, world) == []


# --- a write-off ----------------------------------------------------------------------------------------------


def test_a_write_off_names_its_reason_and_never_goes_below_zero(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)])
    gone = document(
        client, world, kind="write_off", reason="expired", note="Namlangan", post=True, lines=[line(rice, "1.5")]
    )
    assert gone.status_code == 201, gone.text
    assert item_of(client, world, rice)["on_hand"] == "8.5"
    newest = movements(client, world, rice)[0]
    assert (newest["kind"], newest["reason"], newest["qty"]) == ("write_off", "expired", "-1.5")
    assert newest["cost"]["total"] == 15_000

    for body, field in (
        ({"kind": "write_off", "lines": [line(rice, "1")]}, "reason"),
        ({"kind": "write_off", "reason": "stolen", "lines": [line(rice, "1")]}, "reason"),
        ({"kind": "write_off", "reason": "lost", "lines": [line(rice, "1", 100)]}, "lines.0.unit_cost"),
        ({"kind": "write_off", "reason": "lost", "paid": 5, "lines": [line(rice, "1")]}, "paid"),
    ):
        refused = write(client, world.manager_a, "POST", f"{stock(world)}/documents", body)
        assert refused.status_code == 422 and field in refused.json()["error"]["fields"], refused.text
    beyond = document(client, world, kind="write_off", reason="lost", post=True, lines=[line(rice, "8.501")])
    assert beyond.status_code == 409 and beyond.json()["error"]["code"] == "STOCK_INSUFFICIENT"
    uncounted = new_item(client, world, "Tuz")
    refused = document(client, world, kind="write_off", reason="lost", post=True, lines=[line(uncounted, "1")])
    assert refused.status_code == 422 and refused.json()["error"]["fields"] == {
        "lines.0.item_id": "not counted in stock"
    }

    assert cancel(client, world, gone.json()["id"]).status_code == 200
    assert item_of(client, world, rice)["on_hand"] == "10" and item_of(client, world, rice)["cost"]["value"] == 100_000
    assert mismatches(owner, world) == []


# --- a stocktake ----------------------------------------------------------------------------------------------


def test_a_stocktake_shows_the_differences_first_and_corrects_the_books_when_posted(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    customer = new_customer(client, world, "Vali")
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    tea = counted_item(client, world, "Choy", 8_000)
    oil = counted_item(client, world, "Yog'", 30_000, "l")
    receive(client, world, [line(rice, "10", 10_000), line(tea, "6", 5_000), line(oil, "3", 25_000)])

    draft = document(client, world, kind="stocktake", lines=[line(rice, "9.2"), line(tea, "7"), line(oil, "3")]).json()
    preview = [(row["item"]["name"], row["qty"], row["expected"], row["difference"]) for row in draft["lines"]]
    assert preview == [("Guruch", "9.2", "10", "-0.8"), ("Choy", "7", "6", "1"), ("Yog'", "3", "3", "0")]
    assert item_of(client, world, rice)["on_hand"] == "10", "a preview changes nothing"

    # The books move between the count and the posting: the correction is against the books as they are.
    assert sell(client, world, customer, [chosen(rice, "0.3", 15_000)]).status_code == 201
    assert get(client, world, draft["id"])["lines"][0]["difference"] == "-0.5"
    posted = act(client, world, draft["id"], "post").json()
    assert [(row["expected"], row["difference"]) for row in posted["lines"]] == [
        ("9.7", "-0.5"),
        ("6", "1"),
        ("3", "0"),
    ]
    assert [item_of(client, world, item)["on_hand"] for item in (rice, tea, oil)] == ["9.2", "7", "3"]
    assert movements(client, world, rice)[0]["kind"] == "correction"
    assert len(movements(client, world, oil)) == 1, "the books agreed: no movement"
    assert item_of(client, world, tea)["cost"] == {
        "currency": "UZS",
        "average": 5_000,
        "value": 35_000,
        "margin": 3_000,
    }

    twice = document(client, world, kind="stocktake", lines=[line(rice, "1"), line(rice, "2")])
    assert twice.status_code == 422 and "lines.1.item_id" in twice.json()["error"]["fields"]
    negative = document(client, world, kind="stocktake", lines=[line(rice, "-1")])
    assert negative.status_code == 422

    assert cancel(client, world, draft["id"]).status_code == 200
    assert [item_of(client, world, item)["on_hand"] for item in (rice, tea, oil)] == ["9.7", "6", "3"]
    assert mismatches(owner, world) == []


def test_a_stocktake_that_found_more_cannot_be_cancelled_once_the_extra_is_gone(
    client: TestClient, world: World, on: None
) -> None:
    tea = counted_item(client, world, "Choy", 8_000)
    found = document(client, world, kind="stocktake", post=True, lines=[line(tea, "2")]).json()
    assert (
        document(client, world, kind="write_off", reason="lost", post=True, lines=[line(tea, "1")]).status_code == 201
    )
    refused = cancel(client, world, found["id"])
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "STOCK_ALREADY_USED"


# --- a customer's return --------------------------------------------------------------------------------------


def test_a_customers_return_puts_goods_back_and_lowers_the_debt_by_an_ordinary_payment(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    customer = new_customer(client, world, "Vali")
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)])
    assert sell(client, world, customer, [chosen(rice, "4", 15_000)]).status_code == 201  # owes 60 000

    back = document(
        client, world, kind="customer_return", customer_id=customer, post=True, lines=[line(rice, "1", 15_000)]
    )
    assert back.status_code == 201, back.text
    body = back.json()
    assert (body["total"], body["paid"], body["customer"]["name"]) == (15_000, 0, "Vali")
    assert item_of(client, world, rice)["on_hand"] == "7"
    assert item_of(client, world, rice)["cost"]["average"] == 10_000, "it came back at the average, which stays"
    account = detail(client, world, customer)
    assert account["balance"] == 45_000
    newest = account["entries"][0]
    assert (newest["kind"], newest["amount"], newest["note"]) == ("payment", 15_000, "Tovar qaytarildi, hujjat № 1")
    assert newest["id"] == body["ledger_entry_id"]

    # The entry belongs to the document: it is cancelled there, with the goods, and not on its own.
    alone = reverse(client, world, newest["id"], world.manager_a)
    assert alone.status_code == 409 and alone.json()["error"]["code"] == "ENTRY_OF_DOCUMENT"
    assert detail(client, world, customer)["balance"] == 45_000

    cancelled = cancel(client, world, body["id"])
    assert cancelled.status_code == 200, cancelled.text
    account = detail(client, world, customer)
    assert account["balance"] == 60_000
    assert (account["entries"][0]["kind"], account["entries"][0]["reverses_id"]) == ("reversal", newest["id"])
    assert item_of(client, world, rice)["on_hand"] == "6"
    assert mismatches(owner, world) == []


def test_a_return_cannot_lower_the_debt_below_zero_and_what_is_given_back_in_money_is_apart(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    customer = new_customer(client, world, "Vali")
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    receive(client, world, [line(rice, "10", 10_000)])
    assert sell(client, world, customer, [chosen(rice, "1", 15_000)]).status_code == 201  # owes 15 000
    before = counts(owner, world)

    more = document(
        client, world, kind="customer_return", customer_id=customer, post=True, lines=[line(rice, "2", 15_000)]
    )
    assert more.status_code == 409 and more.json()["error"]["code"] == "EXCEEDS_BALANCE", "the ledger's own rule"
    assert counts(owner, world) == before and item_of(client, world, rice)["on_hand"] == "9"

    # Two come back: one against the debt, the other paid back in money.
    part = document(
        client,
        world,
        kind="customer_return",
        customer_id=customer,
        paid=15_000,
        post=True,
        lines=[line(rice, "2", 15_000)],
    )
    assert part.status_code == 201, part.text
    assert detail(client, world, customer)["balance"] == 0
    assert item_of(client, world, rice)["on_hand"] == "11"
    # All in money: the ledger is not touched at all.
    cash = document(
        client,
        world,
        kind="customer_return",
        customer_id=customer,
        paid=15_000,
        post=True,
        lines=[line(rice, "1", 15_000)],
    )
    assert cash.status_code == 201 and cash.json()["ledger_entry_id"] is None

    for change, field in (
        ({"customer_id": None}, "customer_id"),
        ({"customer_id": str(uuid.uuid4())}, "customer_id"),
        ({"paid": 14_950}, "paid"),  # 50 so'm against the debt: below the ledger's smallest entry
        ({"currency": "USD"}, "currency"),
    ):
        body = {
            "kind": "customer_return",
            "customer_id": customer,
            "post": True,
            "lines": [line(rice, "1", 15_000)],
            **change,
        }
        refused = write(client, world.manager_a, "POST", f"{stock(world)}/documents", body)
        assert refused.status_code == 422 and field in refused.json()["error"]["fields"], refused.text
    archived = document(
        client,
        world,
        kind="customer_return",
        customer_id=str(world.archived_customer_a),
        post=True,
        lines=[line(rice, "1", 1_000)],
    )
    assert archived.status_code == 409 and archived.json()["error"]["code"] == "CUSTOMER_ARCHIVED"
    assert mismatches(owner, world) == []


def test_returned_goods_that_were_sold_again_keep_the_return_from_being_cancelled(
    client: TestClient, world: World, on: None
) -> None:
    customer = new_customer(client, world, "Vali")
    tea = counted_item(client, world, "Choy", 8_000)
    receive(client, world, [line(tea, "1", 5_000)])
    assert sell(client, world, customer, [chosen(tea, "1", 8_000)]).status_code == 201
    back = document(
        client, world, kind="customer_return", customer_id=customer, post=True, lines=[line(tea, "1", 8_000)]
    ).json()
    assert sell(client, world, customer, [chosen(tea, "1", 8_000)]).status_code == 201
    refused = cancel(client, world, back["id"])
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "STOCK_ALREADY_USED"
    assert detail(client, world, customer)["balance"] == 8_000, "the ledger was not touched"


# --- who may write which document -----------------------------------------------------------------------------


def test_each_kind_of_document_needs_its_own_permission(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """With the permission matrix on: receiving opens receipts and returns to suppliers, adjusting opens
    write-offs, stocktakes and customers' returns; one does not open the other, to write, post or cancel."""
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    who = supplier(client, world)
    receipt = receive(client, world, [line(rice, "10", 10_000)])
    lost = document(client, world, kind="write_off", reason="lost", post=True, lines=[line(rice, "1")]).json()
    draft_receipt = document(client, world, kind="receipt", lines=[line(rice, "1", 1)]).json()
    draft_count = document(client, world, kind="stocktake", lines=[line(rice, "9")]).json()

    switch_permissions_on(owner)
    receiver, adjuster = world.seller_a, world.manager_a
    set_overrides(owner, world.seller_a_membership, granted=["stock.receive"])
    set_overrides(owner, world.manager_a_membership, denied=["stock.receive", "suppliers.pay", "stock.costs.view"])

    def refused(response: Any, permission: str) -> bool:
        return bool(response.status_code == 403 and response.json()["error"]["fields"] == {"permission": permission})

    write_off = {"kind": "write_off", "reason": "lost", "lines": [line(rice, "1")]}
    a_receipt = {"kind": "receipt", "lines": [line(rice, "1", 10_000)]}
    assert refused(document(client, world, receiver, **write_off), "stock.adjust")
    assert refused(document(client, world, adjuster, **a_receipt), "stock.receive")
    assert refused(act(client, world, draft_count["id"], "post", receiver), "stock.adjust")
    assert refused(act(client, world, draft_receipt["id"], "post", adjuster), "stock.receive")
    assert refused(act(client, world, lost["id"], "cancel", receiver, reason="x"), "stock.adjust")
    assert refused(act(client, world, receipt["id"], "cancel", adjuster, reason="x"), "stock.receive")
    assert item_of(client, world, rice, world.owner_a)["on_hand"] == "9"

    assert document(client, world, receiver, **a_receipt, post=True).status_code == 201
    assert document(client, world, adjuster, **write_off, post=True).status_code == 201
    # Paying a supplier on a receipt is the payment's permission, which this receiver does not hold.
    paid = document(client, world, receiver, **a_receipt, supplier_id=who, paid=5_000, post=True)
    assert refused(paid, "suppliers.pay")
    on_credit = document(client, world, receiver, **a_receipt, supplier_id=who, post=True)
    assert on_credit.status_code == 201, on_credit.text
    # The author of a draft reads back the prices they typed; once it is posted, only cost-viewers do.
    mine = document(client, world, receiver, **a_receipt).json()
    assert mine["lines"][0]["unit_cost"] == 10_000 and mine["total"] == 10_000
    assert "unit_cost" not in on_credit.json()["lines"][0] and "total" not in on_credit.json()
    assert "total" not in get(client, world, mine["id"], adjuster)


def test_a_repeated_request_writes_one_document(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    rice = counted_item(client, world, "Guruch", 15_000, "kg")
    body = {"kind": "receipt", "post": True, "lines": [line(rice, "10", 10_000)]}
    headers = {**as_user(world.manager_a), **key()}
    first = client.post(f"{stock(world)}/documents", json=body, headers=headers)
    second = client.post(f"{stock(world)}/documents", json=body, headers=headers)
    assert first.status_code == second.status_code == 201 and first.json() == second.json()
    assert item_of(client, world, rice)["on_hand"] == "10"
    other = client.post(f"{stock(world)}/documents", json={**body, "note": "boshqa"}, headers=headers)
    assert other.status_code == 409 and other.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
