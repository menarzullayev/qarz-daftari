"""The network between shops through the API (module J; BR-80 to BR-96).

Shop A is the buyer and shop B the supplier unless a test says otherwise. Each rule has the case that
works and the case that is refused; a refused step changes nothing on either side. The database's own
part of the trust model is in tests/db/test_network_schema.py, the races in test_network_races.py.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import World, as_user, set_overrides, switch_permissions_on
from .test_customers_ledger import read, write

pytestmark = pytest.mark.db

NETWORK_TABLES = (
    "network_invite", "network_link", "network_order_draft", "network_order", "network_order_line", "network_note",
    "network_note_line", "network_payment", "network_event",
)  # fmt: skip
# What a refused step must leave as it was, in both shops: the network's own rows and both shops' books.
BOOKS = (
    "stock_document", "stock_document_line", "stock_movement", "stock_level", "supplier", "supplier_entry",
    "supplier_balance", "ledger_entry", "customer", "catalog_item", "cash_entry", "activity",
)  # fmt: skip


def switch(owner: psycopg.Connection, key: str, value: str = "true") -> None:
    """Store a switch as an administrator's change would; the `admin_env` fixture removes it afterwards."""
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES (%s, %s::jsonb, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by",
        (key, value, str(uuid.uuid4())),
    )


@pytest.fixture
def on(client: TestClient, owner: psycopg.Connection) -> Iterator[None]:
    switch(owner, "network_on")
    switch(owner, "stock_on")
    yield


def net(shop: uuid.UUID) -> str:
    return f"/api/v1/shops/{shop}/network"


def ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    return body


def refused(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    error: dict[str, Any] = response.json()["error"]
    assert error["code"] == code, response.text
    return error


def snapshot(owner: psycopg.Connection, *shops: uuid.UUID, tables: tuple[str, ...] = (*NETWORK_TABLES, *BOOKS)) -> Any:
    found = []
    for table in tables:
        rows = owner.execute(
            f"SELECT t::text FROM {table} t WHERE shop_id = ANY(%s) ORDER BY t::text", (list(shops),)
        ).fetchall()
        found.append((table, rows))
    return found


def mismatches(owner: psycopg.Connection, *shops: uuid.UUID) -> list[Any]:
    """Where a kept figure differs from its ledger, in any of the shops: every list must be empty."""
    found: list[Any] = []
    for shop in shops:
        found += owner.execute("SELECT * FROM stock_level_mismatches(%s)", (shop,)).fetchall()
        found += owner.execute("SELECT * FROM supplier_balance_mismatches(%s)", (shop,)).fetchall()
        found += owner.execute("SELECT * FROM open_debt_mismatches(%s)", (shop,)).fetchall()
    return found


def new_shop(owner: psycopg.Connection, name: str) -> tuple[uuid.UUID, uuid.UUID]:
    """A further shop with an owner, on trial like the two of the world. Returns the shop and its owner."""
    shop, user = uuid.uuid4(), uuid.uuid4()
    owner.execute("INSERT INTO shop (id, name) VALUES (%s, %s)", (shop, name))
    owner.execute("INSERT INTO app_user (id, tg_id, lang) VALUES (%s, %s, 'uz')", (user, uuid.uuid4().int % 10**15))
    owner.execute(
        "INSERT INTO membership (id, shop_id, user_id, role) VALUES (%s, %s, %s, 'owner')", (uuid.uuid4(), shop, user)
    )
    owner.execute(
        "INSERT INTO subscription (shop_id, state, trial_ends) VALUES (%s, 'trial', current_date + 30)", (shop,)
    )
    return shop, user


def item(
    client: TestClient, shop: uuid.UUID, user: uuid.UUID, name: str, *, price: int = 15_000, unit: str = "kg"
) -> str:
    """A counted item of the shop's catalogue."""
    made = ok(
        write(client, user, "POST", f"/api/v1/shops/{shop}/catalog", {"name": name, "price": price, "unit": unit}), 201
    )
    ok(write(client, user, "PATCH", f"/api/v1/shops/{shop}/stock/items/{made['id']}", {"tracked": True}))
    return str(made["id"])


def stock_in(client: TestClient, shop: uuid.UUID, user: uuid.UUID, item_id: str, qty: str, cost: int) -> None:
    body = {"kind": "receipt", "post": True, "lines": [{"item_id": item_id, "qty": qty, "unit_cost": cost}]}
    ok(write(client, user, "POST", f"/api/v1/shops/{shop}/stock/documents", body), 201)


def on_hand(owner: psycopg.Connection, item_id: Any) -> str:
    row = owner.execute("SELECT on_hand FROM stock_level WHERE item_id = %s", (item_id,)).fetchone()
    return "0" if row is None else format(row[0].normalize(), "f")


def owed_to_supplier(owner: psycopg.Connection, shop: uuid.UUID) -> dict[str, int]:
    rows = owner.execute("SELECT currency, balance FROM supplier_balance WHERE shop_id = %s", (shop,)).fetchall()
    return {str(currency): int(balance) for currency, balance in rows if balance}


def ledger(owner: psycopg.Connection, shop: uuid.UUID, customer: Any) -> list[tuple[str, int]]:
    rows = owner.execute(
        "SELECT kind, amount FROM ledger_entry WHERE shop_id = %s AND customer_id = %s ORDER BY seq", (shop, customer)
    ).fetchall()
    return [(str(kind), int(amount)) for kind, amount in rows]


def told(owner: psycopg.Connection, user: uuid.UUID) -> list[str]:
    """The Telegram messages queued for a person, oldest first."""
    rows = owner.execute(
        "SELECT o.payload ->> 'text' FROM outbox_message o JOIN app_user u ON u.tg_id::text = o.recipient "
        "WHERE u.id = %s AND o.dedupe_key LIKE 'net:%%' ORDER BY o.created_at, o.id",
        (user,),
    ).fetchall()
    return [str(row[0]) for row in rows]


@dataclass(frozen=True)
class Side:
    shop: uuid.UUID
    user: uuid.UUID


@dataclass(frozen=True)
class Deal:
    """A link between a buyer and a supplier, and the people who act for each."""

    buyer: Side
    supplier: Side
    link: str

    @property
    def shops(self) -> tuple[uuid.UUID, uuid.UUID]:
        return self.buyer.shop, self.supplier.shop


def connect(client: TestClient, buyer: Side, supplier: Side) -> Deal:
    """The supplier makes a code, the buyer presents it, the supplier accepts."""
    code = ok(write(client, supplier.user, "POST", f"{net(supplier.shop)}/invites", {"as": "supplier"}), 201)["code"]
    asked = ok(write(client, buyer.user, "POST", f"{net(buyer.shop)}/links", {"code": code, "as": "buyer"}), 201)
    link = asked["link"]["id"]
    ok(write(client, supplier.user, "POST", f"{net(supplier.shop)}/links/{link}/accept"))
    return Deal(buyer, supplier, link)


def deal(client: TestClient, world: World) -> Deal:
    return connect(client, Side(world.shop_a, world.owner_a), Side(world.shop_b, world.owner_b))


def order(client: TestClient, d: Deal, lines: list[dict[str, Any]], **extra: Any) -> str:
    """The buyer writes an order and sends it."""
    draft = ok(
        write(
            client, d.buyer.user, "POST", f"{net(d.buyer.shop)}/drafts", {"link_id": d.link, "lines": lines, **extra}
        ),
        201,
    )
    sent = ok(write(client, d.buyer.user, "POST", f"{net(d.buyer.shop)}/drafts/{draft['id']}/send"))
    assert sent["status"] == "sent"
    return str(sent["id"])


def accept(client: TestClient, d: Deal, order_id: str, lines: list[dict[str, Any]], **extra: Any) -> Any:
    return write(
        client, d.supplier.user, "POST", f"{net(d.supplier.shop)}/orders/{order_id}/accept", {"lines": lines, **extra}
    )


def deliver(client: TestClient, d: Deal, order_id: str, paid: int = 0) -> str:
    note = ok(
        write(client, d.supplier.user, "POST", f"{net(d.supplier.shop)}/orders/{order_id}/deliver", {"paid": paid}), 201
    )
    assert note["status"] == "issued"
    return str(note["id"])


def confirm(client: TestClient, d: Deal, note_id: str, user: uuid.UUID | None = None, **body: Any) -> Any:
    return write(client, user or d.buyer.user, "POST", f"{net(d.buyer.shop)}/notes/{note_id}/confirm", body or None)


RICE = {"name": "Guruch", "unit": "kg", "qty": "10"}
SUGAR = {"name": "Shakar", "unit": "kg", "qty": "5"}
PRICES = [{"line_no": 1, "qty": "10", "unit_price": 12_000}, {"line_no": 2, "qty": "4.5", "unit_price": 11_000}]
TOTAL = 120_000 + 49_500


def delivered(client: TestClient, d: Deal, *, paid: int = 0, own: dict[str, str] | None = None) -> tuple[str, str]:
    """An order for rice and sugar, accepted at 12 000 and 11 000 a kilo (the sugar for 4.5 kg, not 5), and
    its delivery note. `own` ties lines to items: `buyer_rice`, `supplier_rice`, `supplier_sugar`."""
    own = own or {}
    order_id = order(client, d, [{**RICE, "item_id": own.get("buyer_rice")}, SUGAR])
    priced = [
        {**PRICES[0], "item_id": own.get("supplier_rice")},
        {**PRICES[1], "item_id": own.get("supplier_sugar")},
    ]
    assert ok(accept(client, d, order_id, priced))["total"] == TOTAL
    return order_id, deliver(client, d, order_id, paid)


def new_goods(prices: dict[int, int]) -> dict[str, Any]:
    return {"lines": [{"line_no": line_no, "new_price": price} for line_no, price in prices.items()]}


def goods(owner: psycopg.Connection, shop: uuid.UUID) -> dict[str, Any]:
    """How the buyer takes rice and sugar: as the items it already has by those names, or as new ones."""
    lines: list[dict[str, Any]] = []
    for line_no, (name, price) in enumerate((("Guruch", 16_000), ("Shakar", 14_000)), start=1):
        row = owner.execute("SELECT id FROM catalog_item WHERE shop_id = %s AND name = %s", (shop, name)).fetchone()
        lines.append(
            {"line_no": line_no, "new_price": price} if row is None else {"line_no": line_no, "item_id": str(row[0])}
        )
    return {"lines": lines}


# --- the switch ------------------------------------------------------------------------------------------

ROUTES = (
    ("GET", ""), ("POST", "/invites"), ("DELETE", "/invites/{id}"), ("POST", "/links"), ("GET", "/links/{id}"),
    ("POST", "/links/{id}/accept"), ("POST", "/links/{id}/decline"), ("POST", "/links/{id}/end"),
    ("PUT", "/links/{id}/counterpart"), ("GET", "/drafts"), ("POST", "/drafts"), ("GET", "/drafts/{id}"),
    ("PUT", "/drafts/{id}"), ("DELETE", "/drafts/{id}"), ("POST", "/drafts/{id}/send"), ("GET", "/orders"),
    ("GET", "/orders/{id}"), ("POST", "/orders/{id}/accept"), ("POST", "/orders/{id}/decline"),
    ("POST", "/orders/{id}/cancel"), ("POST", "/orders/{id}/deliver"), ("GET", "/notes"), ("GET", "/notes/{id}"),
    ("POST", "/notes/{id}/correct"), ("POST", "/notes/{id}/confirm"), ("POST", "/notes/{id}/reject"),
    ("GET", "/payments"), ("POST", "/payments"), ("GET", "/payments/{id}"), ("POST", "/payments/{id}/confirm"),
    ("POST", "/payments/{id}/decline"), ("POST", "/payments/{id}/withdraw"),
)  # fmt: skip


def test_the_routes_listed_here_are_all_the_routes_of_the_network(client: TestClient) -> None:
    served = {
        (method, route.path)  # type: ignore[attr-defined]
        for route in client.app.routes  # type: ignore[attr-defined]
        if "/network" in getattr(route, "path", "")
        for method in route.methods - {"HEAD", "OPTIONS"}  # type: ignore[attr-defined]
    }
    listed = set()
    for method, path in ROUTES:
        for name in ("invite_id", "link_id", "draft_id", "order_id", "note_id", "payment_id"):
            candidate = (method, "/api/v1/shops/{shop_id}/network" + path.replace("{id}", "{" + name + "}"))
            if candidate in served:
                listed.add(candidate)
        if "{id}" not in path:
            listed.add((method, "/api/v1/shops/{shop_id}/network" + path))
    assert listed == served


@pytest.mark.parametrize("switches", [(), ("network_on",), ("stock_on",)])
def test_with_a_switch_off_no_route_of_the_network_exists(
    client: TestClient, world: World, owner: psycopg.Connection, switches: tuple[str, ...]
) -> None:
    """Off is "no such route" for the owner, for a stranger and for nobody at all, and writes nothing. The
    network needs the stock: its own switch alone opens nothing."""
    for key in switches:
        switch(owner, key)
    before = snapshot(owner, world.shop_a, world.shop_b)
    answers = set()
    for method, path in ROUTES:
        url = net(world.shop_a) + path.replace("{id}", str(uuid.uuid4()))
        for headers in (as_user(world.owner_a), as_user(world.stranger), {}):
            response = client.request(method, url, json={}, headers={**headers, "Idempotency-Key": uuid.uuid4().hex})
            assert response.status_code == 404, (method, path, response.text)
            answers.add(response.text)
    assert len(answers) == 1, "the same answer whoever asks and whatever is asked for"
    assert snapshot(owner, world.shop_a, world.shop_b) == before
    assert "X-Qarz-Network" not in client.get("/api/v1/me/shops", headers=as_user(world.owner_a)).headers


def test_turning_the_network_on_changes_no_existing_answer(
    client: TestClient, world: World, owner: psycopg.Connection
) -> None:
    """The answers a shop already had are byte for byte the same with the network on; the one thing added
    is a header on the caller's shops."""
    switch(owner, "stock_on")
    shop = f"/api/v1/shops/{world.shop_a}"
    rice = item(client, world.shop_a, world.manager_a, "Guruch")
    stock_in(client, world.shop_a, world.manager_a, rice, "3", 9_000)
    supplier = ok(write(client, world.manager_a, "POST", f"{shop}/suppliers", {"name": "Ulgurji"}), 201)["id"]
    paths = (
        "/api/v1/me/shops", shop, f"{shop}/customers", f"{shop}/customers/{world.customer_a}", f"{shop}/overview",
        f"{shop}/catalog", f"{shop}/stock/settings", f"{shop}/stock/items", f"{shop}/stock/items/{rice}",
        f"{shop}/stock/documents", f"{shop}/suppliers", f"{shop}/suppliers/{supplier}", f"{shop}/permissions/mine",
        f"{shop}/subscription", f"{shop}/activity",
    )  # fmt: skip

    def answers() -> list[tuple[int, bytes]]:
        return [(r.status_code, r.content) for r in (client.get(p, headers=as_user(world.owner_a)) for p in paths)]

    before = answers()
    headers = dict(client.get("/api/v1/me/shops", headers=as_user(world.owner_a)).headers)
    switch(owner, "network_on")
    assert answers() == before
    after = dict(client.get("/api/v1/me/shops", headers=as_user(world.owner_a)).headers)
    assert after.pop("x-qarz-network") == "on"
    assert {k: v for k, v in after.items() if k != "x-request-id"} == {
        k: v for k, v in headers.items() if k != "x-request-id"
    }


# --- links -------------------------------------------------------------------------------------------------


def test_two_shops_connect_by_a_code_and_each_learns_a_name_and_a_phone(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE shop SET share_phone = '+998901112233' WHERE id = %s", (world.shop_b,))
    made = ok(write(client, world.owner_b, "POST", f"{net(world.shop_b)}/invites", {"as": "supplier"}), 201)
    assert set(made) == {"id", "as", "expires_at", "code"} and len(made["code"]) >= 40
    # Only the hash of the code is kept.
    assert made["code"] not in str(owner.execute("SELECT t::text FROM network_invite t").fetchall())

    asked = ok(
        write(client, world.owner_a, "POST", f"{net(world.shop_a)}/links", {"code": made["code"], "as": "buyer"}), 201
    )
    link = asked["link"]
    # A request shows the name, and nothing else of the other shop.
    assert link["partner"] == {"name": "Shop B", "phone": None, "removed": False}
    assert (link["role"], link["state"], link["invited"], link["counterpart"]) == ("buyer", "requested", False, None)
    theirs = ok(read(client, world.owner_b, net(world.shop_b)))
    assert [(row["id"], row["role"], row["state"], row["invited"]) for row in theirs["links"]] == [
        (link["id"], "supplier", "requested", True)
    ]
    assert theirs["links"][0]["partner"] == {"name": "Shop A", "phone": None, "removed": False}
    assert theirs["waiting"]["links"] == 1 and theirs["invites"] == []
    assert "Shop A" in told(owner, world.owner_b)[-1]

    # Only the shop that made the invitation answers it.
    refused(
        write(client, world.owner_a, "POST", f"{net(world.shop_a)}/links/{link['id']}/accept"), 409, "NETWORK_STATE"
    )
    accepted = ok(write(client, world.owner_b, "POST", f"{net(world.shop_b)}/links/{link['id']}/accept"))["link"]
    assert accepted["state"] == "active" and accepted["counterpart"]["kind"] == "customer"
    mine = ok(read(client, world.owner_a, f"{net(world.shop_a)}/links/{link['id']}"))
    assert mine["link"]["partner"] == {"name": "Shop B", "phone": "+998901112233", "removed": False}
    assert [event["kind"] for event in mine["events"]] == ["link_requested", "link_accepted"]
    assert [event["by"] for event in mine["events"]] == ["own", "partner"]
    assert mine["events"][1]["member_id"] is None, "the partner's member is never named"
    # The supplier's side made the buyer one of its customers, by the buyer's name.
    customer = owner.execute(
        "SELECT display_name, status FROM customer WHERE id = %s AND shop_id = %s",
        (accepted["counterpart"]["id"], world.shop_b),
    ).fetchone()
    assert customer == ("Shop A", "active")
    # No answer of the network names the other shop's identifier.
    for body in (asked, theirs, accepted, mine):
        assert str(world.shop_a) not in str(body) and str(world.shop_b) not in str(body)


def test_a_code_is_shown_once_works_once_and_fails_the_same_way_for_every_reason(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    shop_c, owner_c = new_shop(owner, "Shop C")
    first = write(client, world.owner_b, "POST", f"{net(world.shop_b)}/invites", {"as": "supplier"})
    code = ok(first, 201)["code"]
    # The same request again answers the same invitation without its code.
    again = client.post(f"{net(world.shop_b)}/invites", json={"as": "supplier"}, headers=first.request.headers)
    assert ok(again, 201) == {**first.json(), "code": None}

    def present(user: uuid.UUID, shop: uuid.UUID, text: str, role: str = "buyer") -> Any:
        return write(client, user, "POST", f"{net(shop)}/links", {"code": text, "as": role})

    before = snapshot(owner, world.shop_a, world.shop_b, shop_c)
    answers = [
        present(world.owner_a, world.shop_a, "x" * 43),  # no such code
        present(world.owner_a, world.shop_a, "not a code"),  # not even the shape of one
        present(world.owner_b, world.shop_b, code),  # one's own
        present(world.owner_a, world.shop_a, code, role="supplier"),  # both would be the supplier
    ]
    ok(present(world.owner_a, world.shop_a, code), 201)
    answers.append(present(owner_c, shop_c, code))  # used
    revoked = ok(write(client, world.owner_b, "POST", f"{net(world.shop_b)}/invites", {"as": "supplier"}), 201)
    ok(write(client, world.owner_b, "DELETE", f"{net(world.shop_b)}/invites/{revoked['id']}"))
    answers.append(present(owner_c, shop_c, revoked["code"]))  # withdrawn
    owner.execute(
        "UPDATE network_invite SET created_at = created_at - interval '3 days', expires_at = now() - interval '1 hour' "
        "WHERE used_at IS NULL AND revoked_at IS NULL"
    )
    for answer in answers:
        refused(answer, 404, "NETWORK_INVITE_INVALID")
    assert len({answer.text for answer in answers}) == 1
    assert snapshot(owner, shop_c, tables=NETWORK_TABLES) == [(table, []) for table in NETWORK_TABLES]
    assert before[1][1] == [], "nothing was linked by the refused attempts"

    # A second code of the same shop does not make a second link in the same direction.
    another = ok(write(client, world.owner_b, "POST", f"{net(world.shop_b)}/invites", {"as": "supplier"}), 201)["code"]
    refused(present(world.owner_a, world.shop_a, another), 409, "NETWORK_LINK_EXISTS")


def test_there_is_no_way_to_look_for_a_shop(client: TestClient, world: World, on: None) -> None:
    """A shop is reached by a code it handed over and by nothing else: no route lists, searches or names
    another shop, and none takes a shop's identifier but the caller's own."""
    paths = [route.path for route in client.app.routes if "/network" in getattr(route, "path", "")]  # type: ignore[attr-defined]
    assert paths and all(path.startswith("/api/v1/shops/{shop_id}/network") for path in paths)
    assert not [path for path in paths if "search" in path or "shops/{shop_id}/network/shops" in path]
    spec = client.app.openapi()  # type: ignore[attr-defined]
    for path in paths:
        for operation in spec["paths"][path].values():
            names = {parameter["name"] for parameter in operation.get("parameters", [])}
            assert not names & {"q", "name", "phone", "shop", "peer", "partner"}, (path, names)
    assert ok(read(client, world.owner_a, net(world.shop_a))) == {
        "links": [],
        "waiting": {"links": 0, "orders": 0, "deliveries": 0, "notes": 0, "rejected_notes": 0, "payments": 0},
        "invites": [],
    }


def test_declining_and_ending_a_link_show_nothing_new_and_stop_new_work(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    owner.execute("UPDATE shop SET share_phone = '+998901112233' WHERE id = %s", (world.shop_b,))
    code = ok(write(client, world.owner_b, "POST", f"{net(world.shop_b)}/invites", {"as": "supplier"}), 201)["code"]
    link = ok(write(client, world.owner_a, "POST", f"{net(world.shop_a)}/links", {"code": code, "as": "buyer"}), 201)[
        "link"
    ]
    declined = ok(write(client, world.owner_b, "POST", f"{net(world.shop_b)}/links/{link['id']}/decline"))["link"]
    assert declined["state"] == "declined"
    mine = ok(read(client, world.owner_a, f"{net(world.shop_a)}/links/{link['id']}"))["link"]
    assert (mine["state"], mine["partner"]["phone"], mine["counterpart"]) == ("declined", None, None)
    assert owner.execute("SELECT count(*) FROM customer WHERE shop_id = %s", (world.shop_b,)).fetchone() == (0,)
    for step in ("accept", "decline", "end"):
        refused(
            write(client, world.owner_b, "POST", f"{net(world.shop_b)}/links/{link['id']}/{step}"), 409, "NETWORK_STATE"
        )

    d = deal(client, world)
    order_id, note_id = delivered(client, d)
    ended = ok(write(client, world.owner_a, "POST", f"{net(world.shop_a)}/links/{d.link}/end"))["link"]
    assert (ended["state"], ended["ended_by"]) == ("ended", "own")
    theirs = ok(read(client, world.owner_b, f"{net(world.shop_b)}/links/{d.link}"))
    assert (theirs["link"]["state"], theirs["link"]["ended_by"]) == ("ended", "partner")
    # History stays readable to both; what waited is closed, and says why.
    for side in (d.buyer, d.supplier):
        seen = ok(read(client, side.user, f"{net(side.shop)}/orders/{order_id}"))
        assert (seen["status"], seen["closed_reason"], seen["delivery_note"]) == ("cancelled", "link ended", None)
        assert ok(read(client, side.user, f"{net(side.shop)}/notes/{note_id}"))["status"] == "void"
    before = snapshot(owner, *d.shops)
    draft = {"link_id": d.link, "lines": [RICE]}
    refused(write(client, world.owner_a, "POST", f"{net(world.shop_a)}/drafts", draft), 409, "NETWORK_STATE")
    refused(confirm(client, d, note_id, **goods(owner, d.buyer.shop)), 409, "NETWORK_STATE")
    refused(
        write(client, world.owner_a, "POST", f"{net(world.shop_a)}/payments", {"link_id": d.link, "amount": 10_000}),
        409,
        "NETWORK_STATE",
    )
    refused(write(client, world.owner_b, "POST", f"{net(world.shop_b)}/links/{d.link}/end"), 409, "NETWORK_STATE")
    assert snapshot(owner, *d.shops) == before
    # The phone a side knew stays; an ended link teaches nothing further.
    assert ok(read(client, world.owner_a, f"{net(world.shop_a)}/links/{d.link}"))["link"]["partner"] == {
        "name": "Shop B", "phone": "+998901112233", "removed": False,
    }  # fmt: skip


def test_a_shop_says_which_of_its_own_rows_the_partner_is(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    shop = f"/api/v1/shops/{world.shop_a}"
    supplier = ok(write(client, world.manager_a, "POST", f"{shop}/suppliers", {"name": "Eski hamkor"}), 201)["id"]
    d = deal(client, world)
    # The buyer asked and has no row yet: it may name one of its suppliers, never a row of another shop.
    theirs = owner.execute("SELECT customer_id FROM network_link WHERE shop_id = %s", (world.shop_b,)).fetchone()
    assert theirs is not None
    path = f"{net(world.shop_a)}/links/{d.link}/counterpart"
    for foreign in (theirs[0], world.customer_a, uuid.uuid4()):
        refused(write(client, world.owner_a, "PUT", path, {"counterpart_id": str(foreign)}), 422, "VALIDATION")
    attached = ok(write(client, world.owner_a, "PUT", path, {"counterpart_id": supplier}))["link"]
    assert attached["counterpart"] == {"kind": "supplier", "id": supplier}
    assert owner.execute("SELECT linked_shop_id FROM supplier WHERE id = %s", (supplier,)).fetchone() == (world.shop_b,)
    refused(write(client, world.owner_a, "PUT", path, {"counterpart_id": supplier}), 409, "NETWORK_STATE")


# --- an order, a delivery note, and its confirmation ------------------------------------------------------------


def test_an_order_is_delivered_and_confirmed_and_both_books_take_it_in_one_step(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = deal(client, world)
    a, b = d.shops
    mine = item(client, a, world.manager_a, "Guruch")
    theirs = item(client, b, world.owner_b, "Guruch oliy", price=12_000)
    stock_in(client, b, world.owner_b, theirs, "50", 9_000)

    wanted = [{**RICE, "item_id": mine}, SUGAR]
    body = {"link_id": d.link, "note": "Ertaga kerak", "lines": wanted}
    draft = ok(write(client, world.manager_a, "POST", f"{net(a)}/drafts", body), 201)
    assert draft["status"] == "draft"
    # A draft is the buyer's alone.
    assert ok(read(client, world.owner_b, f"{net(b)}/orders"))["orders"] == []
    assert owner.execute("SELECT count(*) FROM network_order_draft WHERE shop_id = %s", (b,)).fetchone() == (0,)
    sent = ok(write(client, world.manager_a, "POST", f"{net(a)}/drafts/{draft['id']}/send"))
    order_id = sent["id"]
    assert (sent["number"], sent["status"], sent["role"]) == (1, "sent", "buyer")

    incoming = ok(read(client, world.owner_b, f"{net(b)}/orders/{order_id}"))
    assert (incoming["role"], incoming["partner"], incoming["note"]) == ("supplier", {"name": "Shop A"}, "Ertaga kerak")
    # The supplier sees what was asked for, and nothing of the buyer's catalogue.
    assert [(line["name"], line["qty"], line["item_id"]) for line in incoming["lines"]] == [
        ("Guruch", "10", None),
        ("Shakar", "5", None),
    ]
    assert ok(read(client, world.owner_b, net(b)))["waiting"]["orders"] == 1

    priced = [{**PRICES[0], "item_id": theirs}, PRICES[1]]
    accepted = ok(accept(client, d, order_id, priced))
    assert (accepted["status"], accepted["currency"], accepted["total"]) == ("accepted", "UZS", TOTAL)
    seen = ok(read(client, world.manager_a, f"{net(a)}/orders/{order_id}"))
    # The buyer sees the supplier's changes beside what it asked for, and its own item, not the supplier's.
    shown = ("qty", "accepted_qty", "unit_price", "line_total", "changed", "item_id")
    assert [tuple(line[name] for name in shown) for line in seen["lines"]] == [
        ("10", "10", 12_000, 120_000, False, mine),
        ("5", "4.5", 11_000, 49_500, True, None),
    ]  # fmt: skip

    books = snapshot(owner, a, b, tables=BOOKS[:10])
    note_id = deliver(client, d, order_id, paid=20_000)
    note = ok(read(client, world.manager_a, f"{net(a)}/notes/{note_id}"))
    assert (note["number"], note["status"], note["total"], note["paid"], note["terms"]) == (
        1,
        "issued",
        TOTAL,
        20_000,
        "part",
    )
    assert [(line["name"], line["qty"], line["unit_price"], line["line_total"]) for line in note["lines"]] == [
        ("Guruch", "10", 12_000, 120_000),
        ("Shakar", "4.5", 11_000, 49_500),
    ]
    assert snapshot(owner, a, b, tables=BOOKS[:10]) == books, "a note that waits is in nobody's books"
    assert ok(read(client, world.manager_a, net(a)))["waiting"]["notes"] == 1

    received = ok(confirm(client, d, note_id, user=world.manager_a, **new_goods({2: 14_000})))
    assert received["status"] == "received"
    # The buyer: a posted receipt for the note, its goods in stock at the note's prices, and the supplier
    # owed the total less what was paid on delivery.
    document = owner.execute(
        "SELECT id, kind, status, total, paid, origin_ref, supplier_id FROM stock_document WHERE shop_id = %s", (a,)
    ).fetchall()
    assert [row[1:6] for row in document] == [("receipt", "posted", TOTAL, 20_000, uuid.UUID(note_id))]
    assert received["stock_document_id"] == str(document[0][0])
    sugar = owner.execute("SELECT id FROM catalog_item WHERE shop_id = %s AND name = 'Shakar'", (a,)).fetchone()
    assert sugar is not None
    assert (on_hand(owner, mine), on_hand(owner, sugar[0])) == ("10", "4.5")
    assert owed_to_supplier(owner, a) == {"UZS": TOTAL - 20_000}
    # The supplier: a credit sale to the buyer's account and the payment made on delivery, both in the name
    # of the member who issued the note; its counted rice out of stock; the sugar it does not count, not.
    customer = owner.execute("SELECT customer_id FROM network_link WHERE shop_id = %s", (b,)).fetchone()
    assert customer is not None
    assert ledger(owner, b, customer[0]) == [("credit", TOTAL), ("payment", 20_000)]
    assert on_hand(owner, theirs) == "40"
    their_note = ok(read(client, world.owner_b, f"{net(b)}/notes/{note_id}"))
    assert (their_note["status"], their_note["customer_id"]) == ("received", str(customer[0]))
    assert "stock_document_id" not in their_note and "ledger_entry_id" not in received
    for side in (d.buyer, d.supplier):
        assert ok(read(client, side.user, f"{net(side.shop)}/orders/{order_id}"))["status"] == "received"
    assert mismatches(owner, a, b) == []

    # Both sides agree, and the reconciliation says so: this shop's books beside what both confirmed.
    for side in (d.buyer, d.supplier):
        rows = ok(read(client, side.user, f"{net(side.shop)}/links/{d.link}"))["reconciliation"]
        assert rows == [
            {
                "currency": "UZS",
                "agreed": {"delivered": TOTAL, "paid": 20_000, "balance": TOTAL - 20_000},
                "awaiting": {"notes": 0, "payments_own": 0, "payments_partner": 0, "payments_declined": 0},
                "own_balance": TOTAL - 20_000,
                "difference": 0,
            }
        ]
    # Every step is in both shops' activity logs, and the other side was told each time.
    for shop in (a, b):
        logged = owner.execute(
            "SELECT action FROM activity WHERE shop_id = %s AND action LIKE 'network.%%' ORDER BY at, id", (shop,)
        ).fetchall()
        assert [row[0] for row in logged if row[0].startswith(("network.order", "network.note"))] == [
            *(["network.order_drafted"] if shop == a else []),
            "network.order_sent",
            "network.order_accepted",
            "network.note_issued",
            "network.note_received",
        ]
    assert [text.split(" ", 1)[0] for text in told(owner, world.owner_b)][-2:] == ["🧾", "✅"]
    # The owner reads its log as before: the partner's steps are there as the system's, with nobody named.
    for side in (d.buyer, d.supplier):
        log = ok(read(client, side.user, f"/api/v1/shops/{side.shop}/activity"))
        assert "network.note_received" in str(log)
    assert "169 500" in told(owner, world.owner_a)[-1] and "Shop B" in told(owner, world.owner_a)[-1]

    # Once.
    before = snapshot(owner, a, b)
    refused(confirm(client, d, note_id, **new_goods({2: 14_000})), 409, "NETWORK_STATE")
    reject = write(client, world.owner_a, "POST", f"{net(a)}/notes/{note_id}/reject", {"reason": "Kech qoldi"})
    refused(reject, 409, "NETWORK_STATE")
    assert snapshot(owner, a, b) == before


def test_a_note_paid_in_full_leaves_nothing_owed_and_one_on_credit_owes_all(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = deal(client, world)
    _, paid_note = delivered(client, d, paid=TOTAL)
    assert ok(read(client, world.owner_a, f"{net(world.shop_a)}/notes/{paid_note}"))["terms"] == "paid"
    ok(confirm(client, d, paid_note, **goods(owner, d.buyer.shop)))
    assert owed_to_supplier(owner, world.shop_a) == {}
    _, credit_note = delivered(client, d)
    assert ok(read(client, world.owner_a, f"{net(world.shop_a)}/notes/{credit_note}"))["terms"] == "credit"
    # The goods are the buyer's own items by now: the lines are tied to them.
    rice, sugar = (
        owner.execute("SELECT id FROM catalog_item WHERE shop_id = %s AND name = %s", (world.shop_a, name)).fetchone()
        for name in ("Guruch", "Shakar")
    )
    assert rice is not None and sugar is not None
    chosen = {"lines": [{"line_no": 1, "item_id": str(rice[0])}, {"line_no": 2, "item_id": str(sugar[0])}]}
    ok(confirm(client, d, credit_note, **chosen))
    assert owed_to_supplier(owner, world.shop_a) == {"UZS": TOTAL}
    assert (on_hand(owner, rice[0]), on_hand(owner, sugar[0])) == ("20", "9")
    assert mismatches(owner, *d.shops) == []


@pytest.mark.parametrize("failing", ["buyer", "supplier"])
def test_if_either_side_cannot_be_written_neither_is(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, failing: str
) -> None:
    """The confirmation is one transaction over both shops. Here one side's books refuse, each in turn and
    whichever shop is written first: nothing is written anywhere and the note still waits."""
    for buyer, supplier in (
        (Side(world.shop_a, world.owner_a), Side(world.shop_b, world.owner_b)),
        (Side(world.shop_b, world.owner_b), Side(world.shop_a, world.owner_a)),
    ):
        d = connect(client, buyer, supplier)
        theirs = item(client, supplier.shop, supplier.user, f"Guruch {uuid.uuid4().hex[:5]}")
        stock_in(client, supplier.shop, supplier.user, theirs, "50", 9_000)
        _, note_id = delivered(client, d, paid=20_000, own={"supplier_rice": theirs})
        customer = owner.execute(
            "SELECT customer_id FROM network_link WHERE shop_id = %s AND id = %s", (supplier.shop, d.link)
        ).fetchone()
        assert customer is not None
        goods = new_goods({1: 16_000, 2: 14_000})
        if failing == "supplier":
            # The supplier archived the customer the link stands on: its ledger refuses the sale.
            owner.execute("UPDATE customer SET status = 'archived' WHERE id = %s", (customer[0],))
            before = snapshot(owner, *d.shops)
            error = refused(confirm(client, d, note_id, **goods), 409, "NETWORK_PARTNER_REFUSED")
            # Why is the supplier's business: the buyer is told nothing of it.
            assert error["fields"] == {}
        else:
            # The buyer's own catalogue already has a "Shakar": the new item of the receipt is refused,
            # after the supplier's side was written when the supplier is the lower shop.
            ok(
                write(
                    client,
                    buyer.user,
                    "POST",
                    f"/api/v1/shops/{buyer.shop}/catalog",
                    {"name": "Shakar", "price": 14_000, "unit": "kg"},
                ),
                201,
            )
            before = snapshot(owner, *d.shops)
            refused(confirm(client, d, note_id, **goods), 409, "CATALOG_NAME_TAKEN")
        assert snapshot(owner, *d.shops) == before
        assert ok(read(client, buyer.user, f"{net(buyer.shop)}/notes/{note_id}"))["status"] == "issued"
        assert on_hand(owner, theirs) == "50"
        assert mismatches(owner, *d.shops) == []


def test_a_rejected_note_posts_nothing_and_a_corrected_one_takes_its_place(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = deal(client, world)
    a, b = d.shops
    order_id, note_id = delivered(client, d)
    books = snapshot(owner, a, b, tables=BOOKS[:10])
    # A reason is required, and the supplier does not answer its own note.
    path = f"{net(a)}/notes/{note_id}/reject"
    refused(write(client, world.owner_a, "POST", path, {"reason": " "}), 422, "VALIDATION")
    refused(
        write(client, world.owner_b, "POST", f"{net(b)}/notes/{note_id}/reject", {"reason": "Yo'q"}),
        409,
        "NETWORK_STATE",
    )
    refused(write(client, world.owner_b, "POST", f"{net(b)}/notes/{note_id}/confirm"), 409, "NETWORK_STATE")
    counted = [{"line_no": 2, "received_qty": "4"}]
    rejected = ok(write(client, world.owner_a, "POST", path, {"reason": "Shakar yarim kilo kam", "lines": counted}))
    assert (rejected["status"], rejected["reject_reason"]) == ("rejected", "Shakar yarim kilo kam")
    seen = ok(read(client, world.owner_b, f"{net(b)}/notes/{note_id}"))
    assert (seen["status"], seen["reject_reason"], seen["lines"][1]["received_qty"]) == (
        "rejected",
        "Shakar yarim kilo kam",
        "4",
    )
    assert ok(read(client, world.owner_b, net(b)))["waiting"]["rejected_notes"] == 1
    assert "Shakar yarim kilo kam" in told(owner, world.owner_b)[-1]
    assert snapshot(owner, a, b, tables=BOOKS[:10]) == books
    refused(confirm(client, d, note_id, **goods(owner, d.buyer.shop)), 409, "NETWORK_STATE")

    correct = f"{net(b)}/notes/{note_id}/correct"
    refused(write(client, world.owner_b, "POST", correct, {"reason": ""}), 422, "VALIDATION")
    refused(
        write(client, world.owner_a, "POST", f"{net(a)}/notes/{note_id}/correct", {"reason": "O'zim"}),
        409,
        "NETWORK_STATE",
    )
    lines = [{"line_no": 1, "qty": "10", "unit_price": 12_000}, {"line_no": 2, "qty": "4", "unit_price": 11_000}]
    corrected = ok(write(client, world.owner_b, "POST", correct, {"reason": "Qayta tortildi", "lines": lines}), 201)
    assert (corrected["number"], corrected["status"], corrected["total"], corrected["supersedes_id"]) == (
        2,
        "issued",
        164_000,
        note_id,
    )
    # The first note is as it was, marked superseded; it cannot be answered or corrected again.
    old = ok(read(client, world.owner_a, f"{net(a)}/notes/{note_id}"))
    assert (old["status"], old["total"], old["lines"][1]["qty"]) == ("superseded", TOTAL, "4.5")
    refused(write(client, world.owner_b, "POST", correct, {"reason": "Yana bir marta"}), 409, "NETWORK_STATE")
    ok(confirm(client, d, corrected["id"], **goods(owner, d.buyer.shop)))
    assert owed_to_supplier(owner, a) == {"UZS": 164_000}
    assert ok(read(client, world.owner_a, f"{net(a)}/orders/{order_id}"))["status"] == "received"
    assert mismatches(owner, a, b) == []


def test_an_order_is_cancelled_or_declined_only_while_nothing_of_it_is_received(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = deal(client, world)
    a, b = d.shops
    first = order(client, d, [RICE])
    # Each side closes with its own word, and neither with the other's.
    refused(
        write(client, world.owner_a, "POST", f"{net(a)}/orders/{first}/decline", {"reason": "Kerak emas"}),
        404,
        "NOT_FOUND",
    )
    refused(
        write(client, world.owner_b, "POST", f"{net(b)}/orders/{first}/cancel", {"reason": "Kerak emas"}),
        404,
        "NOT_FOUND",
    )
    refused(
        write(client, world.owner_b, "POST", f"{net(b)}/orders/{first}/decline", {"reason": "x"}), 422, "VALIDATION"
    )
    declined = ok(write(client, world.owner_b, "POST", f"{net(b)}/orders/{first}/decline", {"reason": "Tovar yo'q"}))
    assert (declined["status"], declined["closed_reason"]) == ("declined", "Tovar yo'q")
    assert ok(read(client, world.owner_a, f"{net(a)}/orders/{first}"))["closed_reason"] == "Tovar yo'q"
    refused(accept(client, d, first, [PRICES[0]]), 409, "NETWORK_STATE")

    second, note_id = delivered(client, d)
    cancel = f"{net(a)}/orders/{second}/cancel"
    # A note waits for its answer: the order is not closed around it.
    refused(write(client, world.owner_a, "POST", cancel, {"reason": "Fikrim o'zgardi"}), 409, "NETWORK_STATE")
    ok(write(client, world.owner_a, "POST", f"{net(a)}/notes/{note_id}/reject", {"reason": "Tovar kelmadi"}))
    cancelled = ok(write(client, world.owner_a, "POST", cancel, {"reason": "Fikrim o'zgardi"}))
    assert (cancelled["status"], cancelled["delivery_note"]) == ("cancelled", None)
    assert ok(read(client, world.owner_b, f"{net(b)}/notes/{note_id}"))["status"] == "void"

    third, last = delivered(client, d)
    ok(confirm(client, d, last, **goods(owner, d.buyer.shop)))
    before = snapshot(owner, a, b)
    refused(
        write(client, world.owner_a, "POST", f"{net(a)}/orders/{third}/cancel", {"reason": "Kech"}),
        409,
        "NETWORK_STATE",
    )
    refused(
        write(client, world.owner_b, "POST", f"{net(b)}/orders/{third}/decline", {"reason": "Kech"}),
        409,
        "NETWORK_STATE",
    )
    assert snapshot(owner, a, b) == before


def test_what_is_typed_into_an_order_or_an_answer_is_checked(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = deal(client, world)
    a, b = d.shops
    theirs = item(client, b, world.owner_b, "Guruch", unit="qop")
    before = snapshot(owner, a, b)
    for lines, field in (
        ([], "lines"),
        ([{**RICE, "qty": "0"}], "lines.0.qty"),
        ([{**RICE, "qty": "1.2345"}], "lines.0.qty"),
        ([{**RICE, "unit": "tonna"}], "lines.0.unit"),
        ([{**RICE, "name": " "}], "lines.0.name"),
        # An item of the other shop is no item of this one.
        ([{**RICE, "item_id": theirs}], "lines.0.item_id"),
    ):
        error = refused(
            write(client, world.owner_a, "POST", f"{net(a)}/drafts", {"link_id": d.link, "lines": lines}),
            422,
            "VALIDATION",
        )
        assert field in error["fields"], error
    error = refused(
        write(client, world.owner_a, "POST", f"{net(a)}/drafts", {"link_id": str(uuid.uuid4()), "lines": [RICE]}),
        422,
        "VALIDATION",
    )
    assert "link_id" in error["fields"]
    assert snapshot(owner, a, b) == before

    order_id = order(client, d, [RICE, SUGAR])
    before = snapshot(owner, a, b)
    mine = item(client, a, world.owner_a, "Shakar")
    for lines, field in (
        ([PRICES[0]], "lines"),  # every line is answered
        ([PRICES[0], {**PRICES[1], "line_no": 3}], "lines.1.line_no"),
        ([PRICES[0], {**PRICES[0]}], "lines.1.line_no"),
        ([PRICES[0], {**PRICES[1], "unit_price": -1}], "lines.1.unit_price"),
        ([{**PRICES[0], "qty": "0"}, {**PRICES[1], "qty": "0"}], "lines"),  # nothing would be delivered
        ([PRICES[0], {**PRICES[1], "item_id": mine}], "lines.1.item_id"),  # the buyer's item, in the supplier's answer
        ([{**PRICES[0], "item_id": theirs}, PRICES[1]], "lines.0.item_id"),  # counted in sacks, ordered in kilos
    ):
        error = refused(accept(client, d, order_id, lines), 422, "VALIDATION")
        assert field in error["fields"], error
    refused(accept(client, d, order_id, PRICES, currency="USD"), 422, "VALIDATION")
    after = snapshot(owner, a, b)
    assert [rows for table, rows in after if table.startswith("network")] == [
        rows for table, rows in before if table.startswith("network")
    ]
    ok(accept(client, d, order_id, PRICES))
    # More is paid on delivery than the note is for.
    refused(
        write(client, world.owner_b, "POST", f"{net(b)}/orders/{order_id}/deliver", {"paid": TOTAL + 1}),
        422,
        "VALIDATION",
    )
    note_id = deliver(client, d, order_id)
    for body, field in (
        ({"lines": [{"line_no": 1, "new_price": 16_000}]}, "lines.1.item_id"),  # a line with no item
        ({"lines": [{"line_no": 9, "new_price": 1}]}, "lines.0.line_no"),
        ({"lines": [{"line_no": 1, "new_price": 1, "item_id": mine}]}, "lines.0.item_id"),
        ({"lines": [{"line_no": 1, "item_id": theirs}, {"line_no": 2, "new_price": 1}]}, "lines.0.item_id"),
    ):
        error = refused(confirm(client, d, note_id, **body), 422, "VALIDATION")
        assert field in error["fields"], error
    assert ok(read(client, world.owner_a, f"{net(a)}/notes/{note_id}"))["status"] == "issued"


def test_a_shop_that_refuses_sales_beyond_its_stock_is_refused_a_note_for_more_than_it_holds(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = deal(client, world)
    a, b = d.shops
    theirs = item(client, b, world.owner_b, "Guruch")
    stock_in(client, b, world.owner_b, theirs, "6", 9_000)
    ok(write(client, world.owner_b, "PUT", f"/api/v1/shops/{b}/stock/settings", {"refuse_negative": True}))
    order_id = order(client, d, [RICE])
    ok(accept(client, d, order_id, [{**PRICES[0], "item_id": theirs}]))
    error = refused(
        write(client, world.owner_b, "POST", f"{net(b)}/orders/{order_id}/deliver"), 409, "STOCK_INSUFFICIENT"
    )
    assert (error["fields"]["on_hand"], error["fields"]["wanted"]) == ("6", "10")
    stock_in(client, b, world.owner_b, theirs, "4", 9_000)
    note_id = deliver(client, d, order_id)
    # Sold over the counter meanwhile: the delivered goods have left all the same, and the count says so.
    owner.execute("UPDATE shop SET stock_refuse_negative = false WHERE id = %s", (b,))
    ok(confirm(client, d, note_id, **new_goods({1: 16_000})))
    assert on_hand(owner, theirs) == "0"
    assert mismatches(owner, a, b) == []


# --- payments ------------------------------------------------------------------------------------------------


def pay(client: TestClient, side: Side, link: str, amount: int, **extra: Any) -> Any:
    return write(client, side.user, "POST", f"{net(side.shop)}/payments", {"link_id": link, "amount": amount, **extra})


def owing(client: TestClient, world: World, owner: psycopg.Connection) -> Deal:
    """A link over which a delivery on credit was confirmed: the buyer owes the supplier the total."""
    d = deal(client, world)
    _, note_id = delivered(client, d)
    ok(confirm(client, d, note_id, **goods(owner, d.buyer.shop)))
    return d


def reconciled(client: TestClient, side: Side, link: str) -> dict[str, Any]:
    rows = ok(read(client, side.user, f"{net(side.shop)}/links/{link}"))["reconciliation"]
    assert [row["currency"] for row in rows] == ["UZS"]
    found: dict[str, Any] = rows[0]
    return found


def test_a_payment_is_in_the_recorders_books_at_once_and_in_the_others_only_when_confirmed(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = owing(client, world, owner)
    a, b = d.shops
    customer = owner.execute("SELECT customer_id FROM network_link WHERE shop_id = %s", (b,)).fetchone()
    assert customer is not None
    payment = ok(pay(client, d.buyer, d.link, 50_000, note="Naqd berildi"), 201)
    assert (payment["status"], payment["recorded_by"], payment["in_own_books"]) == ("awaiting", "own", True)
    # The buyer's books moved; the supplier's did not, and it is asked.
    assert owed_to_supplier(owner, a) == {"UZS": TOTAL - 50_000}
    assert ledger(owner, b, customer[0]) == [("credit", TOTAL)]
    theirs = ok(read(client, world.owner_b, f"{net(b)}/payments"))["payments"]
    assert [(row["id"], row["status"], row["recorded_by"], row["in_own_books"], row["amount"]) for row in theirs] == [
        (payment["id"], "awaiting", "partner", False, 50_000)
    ]
    assert ok(read(client, world.owner_b, net(b)))["waiting"]["payments"] == 1
    assert "50 000" in told(owner, world.owner_b)[-1]
    # The two books disagree, and each side is shown that they do, in its own terms.
    mine, other = reconciled(client, d.buyer, d.link), reconciled(client, d.supplier, d.link)
    assert (mine["own_balance"], mine["agreed"]["balance"], mine["difference"]) == (TOTAL - 50_000, TOTAL, -50_000)
    assert mine["awaiting"] == {"notes": 0, "payments_own": 50_000, "payments_partner": 0, "payments_declined": 0}
    assert (other["own_balance"], other["agreed"]["balance"], other["difference"]) == (TOTAL, TOTAL, 0)
    assert other["awaiting"] == {"notes": 0, "payments_own": 0, "payments_partner": 50_000, "payments_declined": 0}

    # The side that recorded it does not confirm it.
    path = f"/payments/{payment['id']}"
    refused(write(client, world.owner_a, "POST", f"{net(a)}{path}/confirm"), 409, "NETWORK_STATE")
    refused(write(client, world.owner_a, "POST", f"{net(a)}{path}/decline", {"reason": "O'zim"}), 409, "NETWORK_STATE")
    refused(write(client, world.owner_b, "POST", f"{net(b)}{path}/withdraw"), 409, "NETWORK_STATE")
    confirmed = ok(write(client, world.owner_b, "POST", f"{net(b)}{path}/confirm", {"method": "card"}))
    assert (confirmed["status"], confirmed["in_own_books"]) == ("confirmed", True)
    assert ledger(owner, b, customer[0]) == [("credit", TOTAL), ("payment", 50_000)]
    for side in (d.buyer, d.supplier):
        row = reconciled(client, side, d.link)
        assert (row["own_balance"], row["agreed"]["balance"], row["difference"]) == (TOTAL - 50_000,) * 2 + (0,)
        assert row["awaiting"] == {"notes": 0, "payments_own": 0, "payments_partner": 0, "payments_declined": 0}
    before = snapshot(owner, a, b)
    refused(write(client, world.owner_b, "POST", f"{net(b)}{path}/confirm"), 409, "NETWORK_STATE")
    refused(write(client, world.owner_a, "POST", f"{net(a)}{path}/withdraw"), 409, "NETWORK_STATE")
    assert snapshot(owner, a, b) == before
    assert mismatches(owner, a, b) == []


def test_the_supplier_may_record_the_payment_and_the_buyer_confirm_it(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = owing(client, world, owner)
    a, b = d.shops
    payment = ok(pay(client, d.supplier, d.link, 69_500), 201)
    assert (payment["recorded_by"], payment["role"], payment["in_own_books"]) == ("own", "supplier", True)
    assert owed_to_supplier(owner, a) == {"UZS": TOTAL}, "the buyer's books wait for the buyer"
    # A customer cannot pay more than is owed: the supplier's own ledger says so, as for any customer.
    refused(pay(client, d.supplier, d.link, TOTAL), 409, "EXCEEDS_BALANCE")
    ok(write(client, world.owner_a, "POST", f"{net(a)}/payments/{payment['id']}/confirm"))
    assert owed_to_supplier(owner, a) == {"UZS": 100_000}
    assert reconciled(client, d.buyer, d.link)["difference"] == 0
    assert mismatches(owner, a, b) == []


def test_a_declined_payment_stays_in_the_recorders_books_and_is_shown_as_a_difference(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = owing(client, world, owner)
    a, b = d.shops
    payment = ok(pay(client, d.buyer, d.link, 50_000), 201)
    path = f"{net(b)}/payments/{payment['id']}/decline"
    refused(write(client, world.owner_b, "POST", path, {"reason": ""}), 422, "VALIDATION")
    before = snapshot(owner, b, tables=BOOKS)
    declined = ok(write(client, world.owner_b, "POST", path, {"reason": "Pul kelmadi"}))
    assert (declined["status"], declined["decline_reason"], declined["in_own_books"]) == (
        "declined",
        "Pul kelmadi",
        False,
    )
    after = snapshot(owner, b, tables=BOOKS)
    assert [rows for table, rows in after if table != "activity"] == [
        rows for table, rows in before if table != "activity"
    ]
    # Nothing is reconciled by itself: the buyer's books still hold the payment, and it is told so.
    mine = ok(read(client, world.owner_a, f"{net(a)}/payments/{payment['id']}"))
    assert (mine["status"], mine["decline_reason"], mine["in_own_books"]) == ("declined", "Pul kelmadi", True)
    assert owed_to_supplier(owner, a) == {"UZS": TOTAL - 50_000}
    row = reconciled(client, d.buyer, d.link)
    assert (row["difference"], row["awaiting"]["payments_declined"]) == (-50_000, 50_000)
    assert "Pul kelmadi" in told(owner, world.owner_a)[-1]
    # The buyer cancels its own entry the ordinary way, and the difference is gone.
    supplier = owner.execute("SELECT supplier_id FROM network_link WHERE shop_id = %s", (a,)).fetchone()
    entry = owner.execute("SELECT id FROM supplier_entry WHERE shop_id = %s AND kind = 'payment'", (a,)).fetchone()
    assert supplier is not None and entry is not None
    cancel = f"/api/v1/shops/{a}/suppliers/{supplier[0]}/entries/{entry[0]}/cancel"
    ok(write(client, world.owner_a, "POST", cancel, {"reason": "Hamkor tasdiqlamadi"}))
    row = reconciled(client, d.buyer, d.link)
    assert (row["difference"], row["awaiting"]["payments_declined"]) == (0, 0)


@pytest.mark.parametrize("recorder", ["buyer", "supplier"])
def test_a_payment_taken_back_while_it_waits_leaves_the_recorders_books_as_they_were(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, recorder: str
) -> None:
    d = owing(client, world, owner)
    a, b = d.shops
    side, other = (d.buyer, d.supplier) if recorder == "buyer" else (d.supplier, d.buyer)
    customer = owner.execute("SELECT customer_id FROM network_link WHERE shop_id = %s", (b,)).fetchone()
    assert customer is not None
    payment = ok(pay(client, side, d.link, 50_000), 201)
    withdrawn = ok(write(client, side.user, "POST", f"{net(side.shop)}/payments/{payment['id']}/withdraw"))
    assert (withdrawn["status"], withdrawn["in_own_books"]) == ("withdrawn", False)
    assert owed_to_supplier(owner, a) == {"UZS": TOTAL}
    assert sum(
        amount if kind == "credit" else -amount for kind, amount in ledger(owner, b, customer[0]) if kind != "reversal"
    ) in (TOTAL, TOTAL - 50_000)
    assert reconciled(client, side, d.link)["difference"] == 0
    seen = ok(read(client, other.user, f"{net(other.shop)}/payments/{payment['id']}"))
    assert seen["status"] == "withdrawn"
    refused(
        write(client, other.user, "POST", f"{net(other.shop)}/payments/{payment['id']}/confirm"), 409, "NETWORK_STATE"
    )
    assert mismatches(owner, a, b) == []


def test_a_payment_is_checked_before_anything_is_written(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = owing(client, world, owner)
    before = snapshot(owner, *d.shops)
    for body, field in (({"amount": 0}, "amount"), ({"amount": 50}, "amount"), ({"note": "x" * 201}, "note")):
        error = refused(pay(client, d.buyer, d.link, **{"amount": 10_000, **body}), 422, "VALIDATION")
        assert field in error["fields"]
    refused(pay(client, d.buyer, d.link, 10_000, currency="USD"), 422, "VALIDATION")
    error = refused(pay(client, d.buyer, str(uuid.uuid4()), 10_000), 422, "VALIDATION")
    assert "link_id" in error["fields"]
    assert snapshot(owner, *d.shops) == before


# --- who may do what ------------------------------------------------------------------------------------------


def test_links_are_the_owners_and_the_rest_is_the_managers_by_default(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    refused(
        write(client, world.manager_a, "POST", f"{net(world.shop_a)}/invites", {"as": "buyer"}), 403, "FORBIDDEN_ROLE"
    )
    refused(read(client, world.seller_a, net(world.shop_a)), 403, "FORBIDDEN_ROLE")
    d = deal(client, world)
    seen = ok(read(client, world.manager_a, net(world.shop_a)))
    assert "invites" not in seen, "open invitations are shown to who manages links"
    refused(write(client, world.manager_a, "POST", f"{net(world.shop_a)}/links/{d.link}/end"), 403, "FORBIDDEN_ROLE")
    order_id = order(client, Deal(Side(world.shop_a, world.manager_a), d.supplier, d.link), [RICE])
    refused(
        write(client, world.seller_a, "POST", f"{net(world.shop_a)}/orders/{order_id}/cancel", {"reason": "Sinov"}),
        403,
        "FORBIDDEN_ROLE",
    )


def test_confirming_asks_for_the_books_own_permissions_as_well(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """The network is never a way around the stock's or the ledgers' own permissions."""
    switch_permissions_on(owner)
    d = deal(client, world)
    _, note_id = delivered(client, d, paid=20_000)
    goods = new_goods({1: 16_000, 2: 14_000})
    before = snapshot(owner, *d.shops)

    def attempt(denied: tuple[str, ...], granted: tuple[str, ...] = ()) -> Any:
        set_overrides(owner, world.manager_a_membership, granted=granted, denied=denied)
        return confirm(client, d, note_id, user=world.manager_a, **goods)

    for denied, missing in (
        (("network.confirm",), "network.confirm"),
        (("stock.receive",), "stock.receive"),
        (("suppliers.pay",), "suppliers.pay"),  # something was paid on delivery
    ):
        error = refused(attempt(denied), 403, "FORBIDDEN_PERMISSION")
        assert error["fields"] == {"permission": missing}
        assert snapshot(owner, *d.shops) == before
    # A seller the owner gave exactly what the step needs may take it.
    set_overrides(
        owner, world.seller_a_membership, granted=("network.view", "network.confirm", "stock.receive", "suppliers.pay")
    )
    ok(confirm(client, d, note_id, user=world.seller_a, **goods))

    # A payment: `network.confirm` and the book's own permission, on each side.
    set_overrides(owner, world.manager_a_membership, denied=("suppliers.pay",))
    error = refused(pay(client, Side(world.shop_a, world.manager_a), d.link, 10_000), 403, "FORBIDDEN_PERMISSION")
    assert error["fields"] == {"permission": "suppliers.pay"}
    set_overrides(owner, world.manager_a_membership, denied=("credits.record",))
    assert pay(client, Side(world.shop_a, world.manager_a), d.link, 10_000).status_code == 201


def test_issuing_a_note_asks_for_the_right_to_sell_on_credit(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    switch_permissions_on(owner)
    d = connect(client, Side(world.shop_b, world.owner_b), Side(world.shop_a, world.owner_a))
    order_id = order(client, d, [RICE])
    ok(accept(client, d, order_id, [PRICES[0]]))
    set_overrides(owner, world.manager_a_membership, denied=("credits.record",))
    path = f"{net(world.shop_a)}/orders/{order_id}/deliver"
    error = refused(write(client, world.manager_a, "POST", path), 403, "FORBIDDEN_PERMISSION")
    assert error["fields"] == {"permission": "credits.record"}
    set_overrides(owner, world.manager_a_membership, denied=("network.fulfil",))
    error = refused(write(client, world.manager_a, "POST", path), 403, "FORBIDDEN_PERMISSION")
    assert error["fields"] == {"permission": "network.fulfil"}
    # The note is in the name of the member who issued it, and so is the sale it becomes, whatever
    # happens to that member afterwards: nobody of the supplier is there when the buyer confirms.
    set_overrides(owner, world.manager_a_membership)
    note_id = ok(write(client, world.manager_a, "POST", path), 201)["id"]
    owner.execute("UPDATE membership SET status = 'removed' WHERE id = %s", (world.manager_a_membership,))
    ok(confirm(client, d, note_id, **new_goods({1: 16_000})))
    authors = owner.execute(
        "SELECT DISTINCT author_id FROM ledger_entry WHERE shop_id = %s AND note LIKE 'Yuk xati%%'", (world.shop_a,)
    ).fetchall()
    assert authors == [(world.manager_a_membership,)]


# --- one shop never reaches another's side ----------------------------------------------------------------------


def test_a_shop_that_is_no_party_gets_the_answer_a_missing_thing_gets(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    """Shop C's owner, the most a stranger to the link can be, names everything of the link between A and
    B through C's own shop: each answer is the 404 of an identifier that never existed, and nothing moves."""
    shop_c, owner_c = new_shop(owner, "Shop C")
    d = owing(client, world, owner)
    order_id, note_id = delivered(client, d, paid=10_000)
    payment = ok(pay(client, d.buyer, d.link, 30_000), 201)["id"]
    draft = ok(
        write(client, world.owner_a, "POST", f"{net(world.shop_a)}/drafts", {"link_id": d.link, "lines": [RICE]}), 201
    )["id"]
    invite = ok(write(client, world.owner_b, "POST", f"{net(world.shop_b)}/invites", {"as": "buyer"}), 201)["id"]
    real = {"link": d.link, "order": order_id, "note": note_id, "payment": payment, "draft": draft, "invite": invite}
    bodies: dict[str, Any] = {
        "/drafts/{id}": {"link_id": d.link, "lines": [RICE]},
        "/orders/{id}/accept": {"lines": [PRICES[0], PRICES[1]]},
        "/orders/{id}/decline": {"reason": "Begona"},
        "/orders/{id}/cancel": {"reason": "Begona"},
        "/notes/{id}/correct": {"reason": "Begona"},
        "/notes/{id}/confirm": new_goods({1: 16_000, 2: 14_000}),
        "/notes/{id}/reject": {"reason": "Begona"},
        "/payments/{id}/decline": {"reason": "Begona"},
        "/links/{id}/counterpart": {"counterpart_id": str(world.customer_a)},
    }
    before = snapshot(owner, world.shop_a, world.shop_b, shop_c)
    for method, path in ROUTES:
        if "{id}" not in path:
            continue
        kind = path.split("/")[1].rstrip("s")
        answers = []
        for identifier in (real[kind], str(uuid.uuid4())):
            url = net(shop_c) + path.replace("{id}", identifier)
            answers.append(
                client.request(
                    method,
                    url,
                    json=bodies.get(path),
                    headers={**as_user(owner_c), "Idempotency-Key": uuid.uuid4().hex},
                )
            )
        assert [answer.status_code for answer in answers] == [404, 404], (method, path, answers[0].text)
        assert answers[0].json() == answers[1].json(), (method, path)
    # Naming the link of others in a body is no better.
    for path, body in (
        ("/drafts", {"link_id": d.link, "lines": [RICE]}),
        ("/payments", {"link_id": d.link, "amount": 10_000}),
    ):
        response = client.post(
            net(shop_c) + path, json=body, headers={**as_user(owner_c), "Idempotency-Key": uuid.uuid4().hex}
        )
        missing = client.post(
            net(shop_c) + path,
            json={**body, "link_id": str(uuid.uuid4())},
            headers={**as_user(owner_c), "Idempotency-Key": uuid.uuid4().hex},
        )
        assert response.status_code == missing.status_code == 422 and response.json() == missing.json()
    # Its lists are empty, and it is told nothing.
    for path in ("", "/orders", "/notes", "/payments", "/drafts"):
        body = ok(client.get(net(shop_c) + path, headers=as_user(owner_c)))
        assert not any(body.get(key) for key in ("links", "orders", "notes", "payments", "drafts")), body
    assert snapshot(owner, world.shop_a, world.shop_b, shop_c) == before
    assert told(owner, owner_c) == []


def test_a_member_of_one_side_cannot_reach_or_act_as_the_other_side(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = owing(client, world, owner)
    a, b = d.shops
    order_id, note_id = delivered(client, d, paid=10_000)
    payment = ok(pay(client, d.buyer, d.link, 30_000), 201)["id"]
    before = snapshot(owner, a, b)
    # Through the partner's shop: the caller is nobody there, as for any shop it is no member of.
    for path in ("", f"/links/{d.link}", f"/orders/{order_id}", f"/notes/{note_id}", f"/payments/{payment}"):
        refused(client.get(net(b) + path, headers=as_user(world.owner_a)), 404, "NOT_FOUND")
        refused(client.get(net(a) + path, headers=as_user(world.owner_b)), 404, "NOT_FOUND")
    refused(write(client, world.owner_a, "POST", f"{net(b)}/payments/{payment}/confirm"), 404, "NOT_FOUND")
    refused(write(client, world.owner_b, "POST", f"{net(a)}/notes/{note_id}/confirm"), 404, "NOT_FOUND")
    # Through its own shop, taking the step that is the partner's: refused, and nothing changes.
    refused(accept(client, Deal(d.supplier, d.buyer, d.link), order_id, PRICES), 409, "NETWORK_STATE")
    refused(
        write(client, world.owner_a, "POST", f"{net(a)}/notes/{note_id}/correct", {"reason": "O'zim tuzataman"}),
        409,
        "NETWORK_STATE",
    )
    refused(write(client, world.owner_a, "POST", f"{net(a)}/payments/{payment}/confirm"), 409, "NETWORK_STATE")
    refused(write(client, world.owner_b, "POST", f"{net(b)}/notes/{note_id}/confirm"), 409, "NETWORK_STATE")
    assert snapshot(owner, a, b) == before

    # What each side reads is its own copy: none of the partner's rows, members, customers or stock.
    mine = ok(read(client, world.owner_a, f"{net(a)}/notes/{note_id}"))
    theirs = ok(read(client, world.owner_b, f"{net(b)}/notes/{note_id}"))
    secrets = {
        "b": [str(row[0]) for row in owner.execute(
            "SELECT id FROM membership WHERE shop_id = %(s)s UNION ALL SELECT id FROM customer WHERE shop_id = %(s)s "
            "UNION ALL SELECT id FROM catalog_item WHERE shop_id = %(s)s "
            "UNION ALL SELECT id FROM supplier WHERE shop_id = %(s)s",
            {"s": b}).fetchall()],
        "a": [str(row[0]) for row in owner.execute(
            "SELECT id FROM membership WHERE shop_id = %(s)s UNION ALL SELECT id FROM customer WHERE shop_id = %(s)s "
            "UNION ALL SELECT id FROM catalog_item WHERE shop_id = %(s)s "
            "UNION ALL SELECT id FROM supplier WHERE shop_id = %(s)s",
            {"s": a}).fetchall()],
    }  # fmt: skip
    for path in (
        "",
        f"/links/{d.link}",
        "/orders",
        f"/orders/{order_id}",
        "/notes",
        "/payments",
        f"/payments/{payment}",
    ):
        seen_by_a = client.get(net(a) + path, headers=as_user(world.owner_a)).text + str(mine)
        seen_by_b = client.get(net(b) + path, headers=as_user(world.owner_b)).text + str(theirs)
        assert not [secret for secret in (*secrets["b"], str(b)) if secret in seen_by_a], path
        assert not [secret for secret in (*secrets["a"], str(a)) if secret in seen_by_b], path


# --- a shop that is not in good standing ---------------------------------------------------------------------------


def test_a_limited_shop_answers_what_was_sent_to_it_and_starts_nothing(
    client: TestClient, world: World, on: None, owner: psycopg.Connection
) -> None:
    d = owing(client, world, owner)
    a, b = d.shops
    _, note_id = delivered(client, d)
    waiting = order(client, d, [RICE])
    theirs = ok(pay(client, d.supplier, d.link, 20_000), 201)["id"]

    def limit(shop: uuid.UUID, state: str = "limited") -> None:
        owner.execute(
            "UPDATE subscription SET state = %s, trial_ends = NULL, paid_through = NULL WHERE shop_id = %s",
            (state, shop),
        )

    limit(a)
    before = snapshot(owner, a, b)
    for response in (
        write(client, world.owner_a, "POST", f"{net(a)}/invites", {"as": "buyer"}),
        write(client, world.owner_a, "POST", f"{net(a)}/drafts", {"link_id": d.link, "lines": [RICE]}),
        write(client, world.owner_a, "POST", f"{net(a)}/links", {"code": "x" * 43, "as": "buyer"}),
    ):
        refused(response, 402, "SUBSCRIPTION_LIMITED")
    assert snapshot(owner, a, b) == before
    # What already waits for it, it still answers: a note, a payment; and it may cancel and end.
    ok(confirm(client, d, note_id, **goods(owner, d.buyer.shop)))
    ok(write(client, world.owner_a, "POST", f"{net(a)}/payments/{theirs}/confirm"))
    ok(write(client, world.owner_a, "POST", f"{net(a)}/orders/{waiting}/cancel", {"reason": "Obuna tugadi"}))
    assert ok(read(client, world.owner_a, net(a)))["links"][0]["state"] == "active"

    # A limited supplier does not accept or deliver: a delivery is a new credit sale (BR-29).
    limit(a, "trial")
    owner.execute("UPDATE subscription SET trial_ends = current_date + 30 WHERE shop_id = %s", (a,))
    limit(b)
    again = order(client, d, [RICE])
    refused(accept(client, d, again, [PRICES[0]]), 402, "SUBSCRIPTION_LIMITED")
    # A suspended shop does nothing; and nothing is confirmed into its books either.
    limit(b, "trial")
    owner.execute("UPDATE subscription SET trial_ends = current_date + 30 WHERE shop_id = %s", (b,))
    ok(accept(client, d, again, [PRICES[0]]))
    note_id = deliver(client, d, again)
    limit(b, "suspended")
    refused(write(client, world.owner_b, "POST", f"{net(b)}/links/{d.link}/end"), 403, "SHOP_SUSPENDED")
    before = snapshot(owner, a, b)
    rice_only = {"lines": goods(owner, a)["lines"][:1]}
    refused(confirm(client, d, note_id, **rice_only), 409, "NETWORK_PARTNER_REFUSED")
    assert snapshot(owner, a, b) == before


def test_the_partners_customer_row_counts_toward_the_free_plan(
    client: TestClient, world: World, on: None, owner: psycopg.Connection, free_plan: Any
) -> None:
    """The supplier's row for the buyer is an ordinary customer: with the free plan full, a link is not
    accepted (BR-34), and nothing of the request changes."""
    owner.execute("UPDATE subscription SET state = 'limited', trial_ends = NULL WHERE shop_id = %s", (world.shop_a,))
    held = owner.execute(
        "SELECT count(*) FROM customer WHERE shop_id = %s AND status = 'active'", (world.shop_a,)
    ).fetchone()
    assert held is not None
    free_plan(int(held[0]))
    code = ok(write(client, world.owner_a, "POST", f"{net(world.shop_a)}/invites", {"as": "supplier"}), 201)["code"]
    link = ok(write(client, world.owner_b, "POST", f"{net(world.shop_b)}/links", {"code": code, "as": "buyer"}), 201)[
        "link"
    ]["id"]
    before = snapshot(owner, world.shop_a, world.shop_b)
    error = refused(
        write(client, world.owner_a, "POST", f"{net(world.shop_a)}/links/{link}/accept"), 402, "FREE_PLAN_FULL"
    )
    assert error["fields"] == {"limit": str(held[0])}
    assert snapshot(owner, world.shop_a, world.shop_b) == before
    # Naming a customer the shop already has takes no further place.
    accepted = ok(
        write(
            client,
            world.owner_a,
            "POST",
            f"{net(world.shop_a)}/links/{link}/accept",
            {"counterpart_id": str(world.settled_customer_a)},
        )
    )
    assert accepted["link"]["counterpart"] == {"kind": "customer", "id": str(world.settled_customer_a)}
