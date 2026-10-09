"""The network between shops under concurrency (module J).

Every step of one link takes the link's locks first, lower shop first, and the one step that writes two
shops' books writes the lower shop first. So two answers to one note take turns and exactly one wins;
two confirmations between the same two shops in opposite directions do not wait for each other in a
ring; and a confirmation and an ordinary sale that touch the same customer and the same goods finish
both. A deadlock would come back as a 500: each test asserts the exact statuses.
"""

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import World, as_user
from .test_customers_ledger import another_client, key
from .test_network import (
    PRICES,
    RICE,
    TOTAL,
    Side,
    accept,
    connect,
    deal,
    deliver,
    delivered,
    goods,
    item,
    ledger,
    mismatches,
    net,
    ok,
    on,
    on_hand,
    order,
    owed_to_supplier,
    stock_in,
)

pytestmark = pytest.mark.db

__all__ = ["on"]
ROUNDS = 4
Call = Callable[[threading.Barrier], Any]


def together(*calls: Call) -> list[Any]:
    """Run the calls at the same moment, each in a thread of its own."""
    barrier = threading.Barrier(len(calls))
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(call, barrier) for call in calls]
        return [future.result(timeout=60) for future in futures]


def post(which: TestClient, user: uuid.UUID, path: str, body: Any = None) -> Call:
    def call(barrier: threading.Barrier) -> Any:
        barrier.wait(timeout=10)
        return which.post(path, json=body, headers={**as_user(user), **key()})

    return call


def documents(owner: psycopg.Connection, shop: uuid.UUID, note_id: str) -> int:
    row = owner.execute(
        "SELECT count(*) FROM stock_document WHERE shop_id = %s AND origin_ref = %s", (shop, note_id)
    ).fetchone()
    assert row is not None
    return int(row[0])


def note_status(owner: psycopg.Connection, note_id: str) -> list[str]:
    """The status of a note on both sides: one value, since the two copies never differ."""
    rows = owner.execute("SELECT DISTINCT status FROM network_note WHERE id = %s", (note_id,)).fetchall()
    return [str(row[0]) for row in rows]


def test_two_confirmations_of_one_note_at_once_post_it_once(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, app_database_url: str
) -> None:
    d = deal(client, world)
    a, b = d.shops
    # The goods are in the buyer's catalogue already, so both requests say the same thing.
    for name in ("Guruch", "Shakar"):
        item(client, a, world.owner_a, name)
    customer = owner.execute("SELECT customer_id FROM network_link WHERE shop_id = %s", (b,)).fetchone()
    assert customer is not None
    with another_client(app_database_url) as second:
        for round_no in range(1, ROUNDS + 1):
            _, note_id = delivered(client, d)
            path = f"{net(a)}/notes/{note_id}/confirm"
            body = goods(owner, a)
            answers = together(post(client, world.owner_a, path, body), post(second, world.manager_a, path, body))
            assert sorted(answer.status_code for answer in answers) == [200, 409], [answer.text for answer in answers]
            loser = next(answer for answer in answers if answer.status_code == 409)
            assert loser.json()["error"]["code"] == "NETWORK_STATE"
            assert documents(owner, a, note_id) == 1
            assert owed_to_supplier(owner, a) == {"UZS": TOTAL * round_no}
            assert ledger(owner, b, customer[0]) == [("credit", TOTAL)] * round_no
    assert mismatches(owner, a, b) == []


@pytest.mark.parametrize("rival", ["correction", "ending"])
def test_a_confirmation_and_a_step_that_withdraws_the_note_at_once_leave_one_of_them(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, app_database_url: str, rival: str
) -> None:
    """The buyer confirms while the supplier corrects the same note, or ends the link (which voids it).
    Whichever comes first stands; the books hold the delivery exactly when the confirmation won."""
    wins = {"correction": 201, "ending": 200}[rival]
    outcomes = set()
    with another_client(app_database_url) as second:
        for _ in range(ROUNDS):
            d = deal(client, world)
            a, b = d.shops
            _, note_id = delivered(client, d)
            if rival == "correction":
                other = post(second, world.owner_b, f"{net(b)}/notes/{note_id}/correct", {"reason": "Qayta tortildi"})
            else:
                other = post(second, world.owner_b, f"{net(b)}/links/{d.link}/end")
            confirmed, withdrawn = together(
                post(client, world.owner_a, f"{net(a)}/notes/{note_id}/confirm", goods(owner, a)), other
            )
            statuses = (confirmed.status_code, withdrawn.status_code)
            outcomes.add(statuses)
            if confirmed.status_code == 200:
                # Confirmed first: a correction is then too late; ending the link is still allowed, and
                # leaves what was received as it is.
                assert withdrawn.status_code == (409 if rival == "correction" else 200), withdrawn.text
                assert note_status(owner, note_id) == ["received"]
                assert documents(owner, a, note_id) == 1
            else:
                assert statuses == (409, wins), (confirmed.text, withdrawn.text)
                assert confirmed.json()["error"]["code"] == "NETWORK_STATE"
                assert note_status(owner, note_id) == ["superseded" if rival == "correction" else "void"]
                assert documents(owner, a, note_id) == 0
            assert mismatches(owner, a, b) == []
            if withdrawn.status_code != 200 or rival == "correction":
                ok(client.post(f"{net(b)}/links/{d.link}/end", headers={**as_user(world.owner_b), **key()}))
    assert outcomes, "at least one round ran"


def test_confirmations_in_opposite_directions_at_once_both_finish(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, app_database_url: str
) -> None:
    """A buys from B and B buys from A. Each confirms the other's note at the same moment: each
    transaction writes both shops, and both write the lower shop first, so neither waits for the other in
    a ring. The same counted goods are on both notes, so they meet on the same item rows too."""
    side_a, side_b = Side(world.shop_a, world.owner_a), Side(world.shop_b, world.owner_b)
    a_buys, b_buys = connect(client, side_a, side_b), connect(client, side_b, side_a)
    rice = {side.shop: item(client, side.shop, side.user, "Guruch") for side in (side_a, side_b)}
    for side in (side_a, side_b):
        stock_in(client, side.shop, side.user, rice[side.shop], "1000", 9_000)
    with another_client(app_database_url) as second:
        for round_no in range(1, ROUNDS + 1):
            notes = {}
            for d in (a_buys, b_buys):
                order_id = order(client, d, [{**RICE, "item_id": rice[d.buyer.shop]}])
                ok(accept(client, d, order_id, [{**PRICES[0], "item_id": rice[d.supplier.shop]}]))
                notes[d.link] = deliver(client, d, order_id, paid=20_000)
            answers = together(
                post(client, side_a.user, f"{net(side_a.shop)}/notes/{notes[a_buys.link]}/confirm"),
                post(second, side_b.user, f"{net(side_b.shop)}/notes/{notes[b_buys.link]}/confirm"),
            )
            assert [answer.status_code for answer in answers] == [200, 200], [answer.text for answer in answers]
            for side in (side_a, side_b):
                # Ten kilos came in and ten went out, every round.
                assert on_hand(owner, rice[side.shop]) == "1000"
                assert owed_to_supplier(owner, side.shop) == {"UZS": 100_000 * round_no}
    assert mismatches(owner, world.shop_a, world.shop_b) == []


def test_a_confirmation_and_the_suppliers_own_sale_at_once_both_finish(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, app_database_url: str
) -> None:
    """While the buyer confirms, the supplier sells the same goods to the same customer over the counter
    and takes a payment from them: they meet on the customer's account and on the item, in the one order
    every writer takes them."""
    d = connect(client, Side(world.shop_b, world.owner_b), Side(world.shop_a, world.owner_a))
    buyer, supplier = d.buyer, d.supplier
    rice = item(client, supplier.shop, supplier.user, "Guruch")
    stock_in(client, supplier.shop, supplier.user, rice, "1000", 9_000)
    mine = item(client, buyer.shop, buyer.user, "Guruch")
    customer = owner.execute("SELECT customer_id FROM network_link WHERE shop_id = %s", (supplier.shop,)).fetchone()
    assert customer is not None
    entries = f"/api/v1/shops/{supplier.shop}/customers/{customer[0]}/entries"
    sale = {"kind": "credit", "lines": [{"catalog_item_id": rice, "qty": "1", "unit_price": 15_000}]}
    ok(client.post(entries, json=sale, headers={**as_user(world.seller_a), **key()}), 201)
    with another_client(app_database_url) as second, another_client(app_database_url) as third:
        for round_no in range(1, ROUNDS + 1):
            order_id = order(client, d, [{**RICE, "item_id": mine}])
            ok(accept(client, d, order_id, [{**PRICES[0], "item_id": rice}]))
            note_id = deliver(client, d, order_id)
            answers = together(
                post(client, buyer.user, f"{net(buyer.shop)}/notes/{note_id}/confirm"),
                post(second, world.seller_a, entries, sale),
                post(third, world.manager_a, entries, {"kind": "payment", "amount": 1_000}),
            )
            assert [answer.status_code for answer in answers] == [200, 201, 201], [answer.text for answer in answers]
            assert on_hand(owner, rice) == format(1000 - 1 - 11 * round_no)
            assert on_hand(owner, mine) == format(10 * round_no)
    assert mismatches(owner, buyer.shop, supplier.shop) == []


def test_two_payments_and_a_confirmation_at_once_keep_one_order_of_locks(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, app_database_url: str
) -> None:
    """A payment recorded by each side and a note confirmed, all over one link at one moment: the link's
    locks come before either shop's own, for all three."""
    d = deal(client, world)
    a, b = d.shops
    _, first = delivered(client, d)
    ok(
        client.post(
            f"{net(a)}/notes/{first}/confirm", json=goods(owner, a), headers={**as_user(world.owner_a), **key()}
        )
    )
    with another_client(app_database_url) as second, another_client(app_database_url) as third:
        for round_no in range(1, ROUNDS + 1):
            _, note_id = delivered(client, d)
            answers = together(
                post(client, world.owner_a, f"{net(a)}/notes/{note_id}/confirm", goods(owner, a)),
                post(second, world.manager_a, f"{net(a)}/payments", {"link_id": d.link, "amount": 10_000}),
                post(third, world.owner_b, f"{net(b)}/payments", {"link_id": d.link, "amount": 5_000}),
            )
            assert [answer.status_code for answer in answers] == [200, 201, 201], [answer.text for answer in answers]
            assert owed_to_supplier(owner, a) == {"UZS": TOTAL * (round_no + 1) - 10_000 * round_no}
    assert mismatches(owner, a, b) == []
