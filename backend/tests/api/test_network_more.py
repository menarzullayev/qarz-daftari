"""The network between shops: dollars, the partner's language, the owner's export, and where the code is
allowed to name a second shop (module J)."""

import re
import uuid
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import World
from .test_customers_ledger import read, write
from .test_exports import ask, no_jobs_left_by_earlier_tests, work, workbook
from .test_network import (
    RICE,
    TOTAL,
    accept,
    confirm,
    deal,
    deliver,
    delivered,
    goods,
    ledger,
    mismatches,
    net,
    new_goods,
    ok,
    on,
    order,
    owed_to_supplier,
    pay,
    refused,
    switch,
    told,
)

pytestmark = pytest.mark.db

__all__ = ["no_jobs_left_by_earlier_tests", "on"]
SOURCES = Path(__file__).resolve().parents[2] / "src" / "qarz"


# --- dollars --------------------------------------------------------------------------------------------


def test_a_dollar_order_needs_both_shops_to_work_in_dollars_and_is_posted_in_dollars(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    switch(owner, "usd_on")
    d = deal(client, world)
    a, b = d.shops
    order_id = order(client, d, [RICE])
    cents = [{"line_no": 1, "qty": "10", "unit_price": 125}]
    # The supplier does not work in dollars: to it they do not exist.
    refused(accept(client, d, order_id, cents, currency="USD"), 422, "VALIDATION")
    owner.execute("UPDATE shop SET usd_on = true WHERE id = %s", (b,))
    # It does now, and the buyer does not: refused, and that is all that is said of the buyer.
    error = refused(accept(client, d, order_id, cents, currency="USD"), 409, "NETWORK_CURRENCY")
    assert error["fields"] == {}
    owner.execute("UPDATE shop SET usd_on = true WHERE id = %s", (a,))
    accepted = ok(accept(client, d, order_id, cents, currency="USD"))
    assert (accepted["currency"], accepted["total"]) == ("USD", 1250)
    note_id = deliver(client, d, order_id, paid=250)
    ok(confirm(client, d, note_id, **new_goods({1: 16_000})))
    # Each book in dollars, and nothing in so'm: the two are never added or converted.
    assert owed_to_supplier(owner, a) == {"USD": 1000}
    customer = owner.execute("SELECT customer_id FROM network_link WHERE shop_id = %s", (b,)).fetchone()
    assert customer is not None
    entries = owner.execute(
        "SELECT kind, amount, currency FROM ledger_entry WHERE shop_id = %s AND customer_id = %s ORDER BY seq",
        (b, customer[0]),
    ).fetchall()
    assert entries == [("credit", 1250, "USD"), ("payment", 250, "USD")]
    rows = ok(read(client, world.owner_a, f"{net(a)}/links/{d.link}"))["reconciliation"]
    assert [(row["currency"], row["own_balance"], row["difference"]) for row in rows] == [("USD", 1000, 0)]
    payment = ok(pay(client, d.buyer, d.link, 400, currency="USD"), 201)
    ok(write(client, world.owner_b, "POST", f"{net(b)}/payments/{payment['id']}/confirm"))
    assert owed_to_supplier(owner, a) == {"USD": 600}
    assert mismatches(owner, a, b) == []


# --- the partner is told in its own language, and only what both hold -----------------------------------------


def test_the_partners_staff_are_told_in_their_own_language_and_only_who_may_act(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE app_user SET lang = 'ru' WHERE id = %s", (world.manager_a,))
    # Shop A is the supplier here: its owner and manager hold `network.fulfil`, its seller does not.
    d = deal(client, world)
    back = ok(write(client, world.owner_a, "POST", f"{net(world.shop_a)}/invites", {"as": "supplier"}), 201)["code"]
    link = ok(write(client, world.owner_b, "POST", f"{net(world.shop_b)}/links", {"code": back, "as": "buyer"}), 201)
    ok(write(client, world.owner_a, "POST", f"{net(world.shop_a)}/links/{link['link']['id']}/accept"))
    draft = {"link_id": link["link"]["id"], "note": "Maxfiy izoh", "lines": [RICE]}
    made = ok(write(client, world.owner_b, "POST", f"{net(world.shop_b)}/drafts", draft), 201)
    ok(write(client, world.owner_b, "POST", f"{net(world.shop_b)}/drafts/{made['id']}/send"))
    assert told(owner, world.owner_a)[-1] == "🧾 «Shop B» dan yangi buyurtma № 1. Ko'rish: «Hamkorlar» bo'limi."
    assert told(owner, world.manager_a) == ["🧾 Новый заказ № 1 от «Shop B». Открыть: раздел «Партнёры»."]
    assert told(owner, world.seller_a) == []
    # Nothing of the order beyond its number is in the message, and it is queued once per person.
    everything = owner.execute("SELECT payload::text, dedupe_key FROM outbox_message WHERE dedupe_key LIKE 'net:%%'")
    rows = everything.fetchall()
    assert not [text for text, _ in rows if "Maxfiy" in text or "Guruch" in text]
    assert len({key for _, key in rows}) == len(rows)
    assert d.link != link["link"]["id"]


# --- the owner's export ------------------------------------------------------------------------------------


def test_the_export_holds_the_shops_own_side_of_the_network_and_nothing_of_the_partners(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, worker_database_url: str, file_root: Path
) -> None:
    job = ask(client, world).json()["id"]
    assert work(worker_database_url, file_root) == 1
    sheets = ["Hamkorlar", "Hamkor buyurtmalari", "Yuk xatlari", "Hamkor to'lovlari"]
    assert not set(sheets) & set(workbook(client, world, job)), "a shop without a link gets the workbook it always got"

    owner.execute("UPDATE shop SET share_phone = '+998901112233' WHERE id = %s", (world.shop_b,))
    d = deal(client, world)
    _, note_id = delivered(client, d, paid=20_000)
    ok(confirm(client, d, note_id, **goods(owner, world.shop_a)))
    ok(pay(client, d.buyer, d.link, 50_000, note="Naqd"), 201)
    # The export is the owner's copy of what is recorded, whether or not the network is switched on now.
    switch(owner, "network_on", "false")
    job = ask(client, world).json()["id"]
    assert work(worker_database_url, file_root) == 1
    book = workbook(client, world, job)
    assert list(book)[-4:] == sheets
    assert book["Hamkorlar"][1][:4] == ["Shop B", "+998901112233", "Xaridor", "active"]
    assert [row[1:5] + row[9:15] for row in book["Hamkor buyurtmalari"][1:]] == [
        ["Xaridor", 1, "received", row[4], 1, "Guruch", "kg", 10, 10, 12_000]
        if row[9] == 1
        else ["Xaridor", 1, "received", row[4], 2, "Shakar", "kg", 5, Decimal("4.5"), 11_000]
        for row in book["Hamkor buyurtmalari"][1:]
    ]
    assert [(row[2], row[4], row[7], row[8], row[10], row[14]) for row in book["Yuk xatlari"][1:]] == [
        (1, "received", TOTAL, 20_000, "Guruch", 120_000),
        (1, "received", TOTAL, 20_000, "Shakar", 49_500),
    ]
    assert [(row[2], row[3], row[6], row[7], row[9]) for row in book["Hamkor to'lovlari"][1:]] == [
        ("Biz", "awaiting", 50_000, "Naqd", "Ha")
    ]
    # Nothing that identifies the partner beyond its name and phone, and none of its rows.
    text = str(book)
    partner_things = owner.execute(
        "SELECT id::text FROM membership WHERE shop_id = %(s)s UNION ALL SELECT id::text FROM customer "
        "WHERE shop_id = %(s)s UNION ALL SELECT %(s)s::text",
        {"s": world.shop_b},
    ).fetchall()
    assert not [thing for (thing,) in partner_things if thing in text]
    customer = owner.execute("SELECT customer_id FROM network_link WHERE shop_id = %s", (world.shop_b,)).fetchone()
    assert customer is not None and ledger(owner, world.shop_b, customer[0]) == [("credit", TOTAL), ("payment", 20_000)]


# --- where the code may name a second shop --------------------------------------------------------------------


def test_only_the_database_moves_the_tenant_and_only_the_confirmation_asks_it_to() -> None:
    """The trust model, held in the source: the tenant setting is written by the application in one
    place (opening a shop's transaction); a second tenant is entered through the database's function in
    one place of the storage; and that is asked for in one place of the application, the confirmation
    of a delivery note."""
    setting, entering, asking = [], [], []
    for path in sorted(SOURCES.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        where = path.relative_to(SOURCES).as_posix()
        setting += [where] * len(re.findall(r"set_config\('qd\.shop_id'", source))
        entering += [where] * source.count("network_enter_peer(")
        asking += [where] * len(re.findall(r"\.network_peer\(", source))
    assert setting == ["infrastructure/db.py"]
    assert entering == ["infrastructure/db_network.py"]
    assert asking == ["application/network_orders.py"]


def test_no_statement_of_the_application_reads_a_shared_table_by_another_shops_identifier() -> None:
    """The storage of the network never binds a shop other than the session's own to a read: the partner
    is named only as an argument of a function of migration 0045, which verifies the link."""
    source = (SOURCES / "infrastructure" / "db_network.py").read_text(encoding="utf-8")
    statements = re.findall(r'"((?:SELECT|INSERT|UPDATE|DELETE)[^"]*)"', source)
    assert statements
    for statement in statements:
        if ":peer" in statement:
            assert re.match(r"SELECT (r\.|network_[a-z_]+\(:shop, )", statement), statement
    assert uuid.UUID(int=0)
