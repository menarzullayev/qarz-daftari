"""In whose name the supplier's side of a confirmed delivery note is written (founder's decision 2).

The note is the shop's commitment, so the buyer's confirmation always writes the supplier's sale. It is in
the name of the member who issued the note only while that member is still an active member of the shop
who holds `credits.record`; otherwise it is in the owner's name, and the supplier's activity log says who
issued the note and that it was posted in the owner's name. Every case goes through the whole
confirmation, so each also shows that the database (`network_receipt_finish`) agrees with the application
about who the author is: it would refuse the note otherwise.
"""

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import World, set_overrides, switch_permissions_on
from .test_customers_ledger import read
from .test_network import (
    TOTAL,
    Deal,
    Side,
    confirm,
    connect,
    delivered,
    item,
    mismatches,
    net,
    new_goods,
    ok,
    on,
    on_hand,
    refused,
    snapshot,
    stock_in,
    switch,
)

pytestmark = pytest.mark.db

__all__ = ["on"]

POSTED_FOR_OWNER = "network.note_posted_for_owner"
PAID = 20_000


def waiting_note(client: TestClient, world: World) -> tuple[Deal, str, str]:
    """Shop A supplies shop B. A's manager accepts the order and issues the note: rice of A's own stock
    and sugar that is no item of A's, 20 000 paid on delivery. Returns the deal, the note and A's rice."""
    linked = connect(client, Side(world.shop_b, world.owner_b), Side(world.shop_a, world.owner_a))
    d = Deal(linked.buyer, Side(world.shop_a, world.manager_a), linked.link)
    rice = item(client, world.shop_a, world.owner_a, "Guruch oliy", price=12_000)
    stock_in(client, world.shop_a, world.owner_a, rice, "50", 9_000)
    _, note_id = delivered(client, d, paid=PAID, own={"supplier_rice": rice})
    return d, note_id, rice


def activity(owner: psycopg.Connection, shop: uuid.UUID) -> list[tuple[Any, ...]]:
    rows = owner.execute(
        "SELECT action, actor_kind, actor_id, subject_type, subject_id, detail FROM activity WHERE shop_id = %s",
        (shop,),
    ).fetchall()
    return sorted(rows, key=repr)


def authors(owner: psycopg.Connection, shop: uuid.UUID, note_id: str) -> dict[str, set[Any]]:
    """Who wrote each thing the confirmation wrote into the supplier's books."""
    entry = owner.execute(
        "SELECT ledger_entry_id FROM network_note WHERE shop_id = %s AND id = %s", (shop, note_id)
    ).fetchone()
    assert entry is not None and entry[0] is not None
    customer = owner.execute("SELECT customer_id FROM ledger_entry WHERE id = %s", (entry[0],)).fetchone()
    assert customer is not None
    found = {
        "sale and payment": owner.execute(
            "SELECT kind, author_id FROM ledger_entry WHERE shop_id = %s AND customer_id = %s", (shop, customer[0])
        ).fetchall(),
        "goods out": owner.execute(
            "SELECT kind, author_id FROM stock_movement WHERE shop_id = %s AND ledger_entry_id = %s", (shop, entry[0])
        ).fetchall(),
        "cash": owner.execute(
            "SELECT direction, author_id FROM cash_entry WHERE shop_id = %s AND created_at >= "
            "(SELECT created_at FROM ledger_entry WHERE id = %s)",
            (shop, entry[0]),
        ).fetchall(),
    }
    assert sorted(kind for kind, _ in found["sale and payment"]) == ["credit", "payment"]
    assert [kind for kind, _ in found["goods out"]] == ["sale"]
    return {what: {author for _, author in rows} for what, rows in found.items()}


def what_the_confirmation_logged(
    client: TestClient, owner: psycopg.Connection, d: Deal, note_id: str
) -> list[tuple[Any, ...]]:
    before = activity(owner, d.supplier.shop)
    received = ok(confirm(client, d, note_id, **new_goods({1: 16_000, 2: 14_000})))
    assert received["status"] == "received"
    after = activity(owner, d.supplier.shop)
    for row in before:
        after.remove(row)
    return after


# What the issuer's standing is when the buyer confirms, and whether the sale is still theirs.
STILL_THE_ISSUERS = {
    # Nothing happened to the member.
    "untouched": [],
    # Every role holds `credits.record`: a manager made a seller may no longer issue a note
    # (`network.fulfil`), and may still record a credit sale.
    "made a seller": ["UPDATE membership SET role = 'seller' WHERE id = %(member)s"],
    # A denial stored while the platform switch is off decides nothing, here as everywhere.
    "denied with the switch off": [
        "UPDATE membership SET permissions_denied = '{credits.record}' WHERE id = %(member)s",
    ],
}
THE_OWNERS = {
    "suspended": ("issuer_not_active", False, ["UPDATE membership SET status = 'suspended' WHERE id = %(member)s"]),
    "removed": ("issuer_not_active", False, ["UPDATE membership SET status = 'removed' WHERE id = %(member)s"]),
    "denied the permission": (
        "issuer_not_permitted",
        True,
        ["UPDATE membership SET permissions_denied = '{credits.record}' WHERE id = %(member)s"],
    ),
    "made a seller and denied": (
        "issuer_not_permitted",
        True,
        [
            "UPDATE membership SET role = 'seller' WHERE id = %(member)s",
            "UPDATE membership SET permissions_denied = '{credits.record}' WHERE id = %(member)s",
        ],
    ),
}


@pytest.mark.parametrize("standing", sorted(STILL_THE_ISSUERS))
def test_while_the_issuer_is_active_and_may_record_a_sale_everything_is_in_their_name_as_before(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, standing: str
) -> None:
    switch(owner, "cash_book_on")
    d, note_id, rice = waiting_note(client, world)
    for statement in STILL_THE_ISSUERS[standing]:
        owner.execute(statement, {"member": world.manager_a_membership})
    logged = what_the_confirmation_logged(client, owner, d, note_id)
    issuer = world.manager_a_membership
    assert authors(owner, d.supplier.shop, note_id) == {
        "sale and payment": {issuer},
        "goods out": {issuer},
        "cash": {issuer},
    }
    # The activity of the supplier is what it always was: the sale and the payment by the issuer, the
    # money in the cash book, and the partner's confirmation. Nothing says "in the owner's name".
    customer = owner.execute(
        "SELECT customer_id FROM network_link WHERE shop_id = %s AND id = %s", (d.supplier.shop, d.link)
    ).fetchone()
    assert customer is not None
    assert [row[:5] for row in logged if row[0] != "cash.entry_recorded"] == [
        ("ledger.credit_recorded", "staff", issuer, "customer", customer[0]),
        ("ledger.payment_recorded", "staff", issuer, "customer", customer[0]),
        ("network.note_received", "system", None, "network_note", uuid.UUID(note_id)),
    ]
    assert {row[2] for row in logged if row[1] == "staff"} == {issuer}
    assert POSTED_FOR_OWNER not in {row[0] for row in logged}
    assert on_hand(owner, rice) == "40"
    assert mismatches(owner, *d.shops) == []


@pytest.mark.parametrize("standing", sorted(THE_OWNERS))
def test_when_the_issuer_has_left_or_may_no_longer_record_a_sale_it_is_in_the_owners_name_and_the_log_says_so(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, standing: str
) -> None:
    reason, permissions_on, statements = THE_OWNERS[standing]
    switch(owner, "cash_book_on")
    d, note_id, rice = waiting_note(client, world)
    if permissions_on:
        switch_permissions_on(owner)
    for statement in statements:
        owner.execute(statement, {"member": world.manager_a_membership})
    logged = what_the_confirmation_logged(client, owner, d, note_id)
    issuer, shop_owner = world.manager_a_membership, world.owner_a_membership
    # The sale is written all the same, whole: the note is the shop's commitment.
    assert authors(owner, d.supplier.shop, note_id) == {
        "sale and payment": {shop_owner},
        "goods out": {shop_owner},
        "cash": {shop_owner},
    }
    assert on_hand(owner, rice) == "40"
    assert mismatches(owner, *d.shops) == []
    assert ok(read(client, d.buyer.user, f"{net(d.buyer.shop)}/notes/{note_id}"))["status"] == "received"
    # One row says both: it is in the name of who issued the note, and its action and detail say the
    # note was posted in the owner's name, and why.
    said = [row for row in logged if row[0] == POSTED_FOR_OWNER]
    assert said == [
        (
            POSTED_FOR_OWNER,
            "staff",
            issuer,
            "network_note",
            uuid.UUID(note_id),
            {"number": 1, "issued_by": str(issuer), "posted_as": str(shop_owner), "reason": reason},
        )
    ]
    # Nothing else of the confirmation is in the departed member's name.
    assert {row[2] for row in logged if row[1] == "staff" and row[0] != POSTED_FOR_OWNER} == {shop_owner}
    # The buyer is shown nothing of it: who writes the supplier's books is the supplier's own business.
    assert owner.execute(
        "SELECT count(*) FROM activity WHERE shop_id = %s AND action = %s", (d.buyer.shop, POSTED_FOR_OWNER)
    ).fetchone() == (0,)


def test_the_permission_asked_is_the_one_issuing_a_note_asks_and_no_other(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """Issuing a note asks for `credits.record` and nothing for the payment handed over on delivery, so
    the rule at confirmation asks the same: a member who may not take payments, and issued a note with
    money paid on it, is still its author, payment included."""
    switch_permissions_on(owner)
    set_overrides(owner, world.manager_a_membership, denied=("payments.record", "network.fulfil"))
    d, note_id, _ = waiting_note_issued_before(client, world, owner)
    logged = what_the_confirmation_logged(client, owner, d, note_id)
    assert authors(owner, d.supplier.shop, note_id)["sale and payment"] == {world.manager_a_membership}
    assert POSTED_FOR_OWNER not in {row[0] for row in logged}


def waiting_note_issued_before(client: TestClient, world: World, owner: psycopg.Connection) -> tuple[Deal, str, str]:
    """As `waiting_note`, for a manager whose changes would refuse the issuing: issued first, with the
    changes set aside, and the changes put back before the buyer confirms."""
    row = owner.execute(
        "SELECT permissions_denied FROM membership WHERE id = %s", (world.manager_a_membership,)
    ).fetchone()
    assert row is not None
    set_overrides(owner, world.manager_a_membership)
    made = waiting_note(client, world)
    set_overrides(owner, world.manager_a_membership, denied=row[0])
    return made


def test_a_shop_with_nobody_to_write_in_the_name_of_writes_nothing(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """The counterpart: the issuer has left and the shop has no active owner. There is no author the rule
    allows, so the supplier's books refuse, nothing is written on either side and the note still waits."""
    d, note_id, rice = waiting_note(client, world)
    owner.execute("UPDATE membership SET status = 'removed' WHERE id = %s", (world.manager_a_membership,))
    owner.execute("UPDATE membership SET status = 'suspended' WHERE id = %s", (world.owner_a_membership,))
    before = snapshot(owner, *d.shops)
    error = refused(confirm(client, d, note_id, **new_goods({1: 16_000, 2: 14_000})), 409, "NETWORK_PARTNER_REFUSED")
    assert error["fields"] == {}
    assert snapshot(owner, *d.shops) == before
    assert on_hand(owner, rice) == "50"
    assert ok(read(client, d.buyer.user, f"{net(d.buyer.shop)}/notes/{note_id}"))["status"] == "issued"
    assert TOTAL > PAID
