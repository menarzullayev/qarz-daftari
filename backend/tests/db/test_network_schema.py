"""What the database itself holds the network between shops to (migration 0045), without the application
in between.

This is the one module where what a shop does reaches another shop's rows, so these tests are about
isolation first: a shop reads only its own copies; the application role cannot write a shared table; each
function that crosses refuses a caller that is not the tenant it names, a shop that is no party, a member
of another shop, the wrong side and the wrong state; the tenant setting moves to a second shop only for
the supplier of a note its buyer is confirming; and a note is marked received only when both books hold
what it says.
"""

import hashlib
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from psycopg import errors

from ..conftest import AppSession, Shop, _seed_shop

pytestmark = pytest.mark.db

SHARED_TABLES = (
    "network_link", "network_order", "network_order_line", "network_note", "network_note_line", "network_payment",
    "network_event",
)  # fmt: skip
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


@pytest.fixture
def switches(owner: psycopg.Connection) -> Iterator[None]:
    """`network_on` and `stock_on`, for this test only."""
    before = owner.execute("SELECT key, value FROM platform_setting WHERE key IN ('network_on', 'stock_on')").fetchall()
    turn(owner, "network_on", True)
    turn(owner, "stock_on", True)
    yield
    owner.execute("DELETE FROM platform_setting WHERE key IN ('network_on', 'stock_on')")
    for key, value in before:
        owner.execute(
            "INSERT INTO platform_setting (key, value, updated_by) VALUES (%s, %s::jsonb, 'test')",
            (key, json.dumps(value)),
        )


def turn(owner: psycopg.Connection, key: str, on: bool) -> None:
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES (%s, %s::jsonb, 'test') "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, json.dumps(on)),
    )


@pytest.fixture
def shop_c(owner: psycopg.Connection) -> Shop:
    return _seed_shop(owner, "C")


@contextmanager
def refusal(code: str) -> Iterator[None]:
    """The function refuses with exactly this code, and says nothing else."""
    with pytest.raises(psycopg.Error) as caught:
        yield
    assert caught.value.sqlstate == "QN000", caught.value
    assert str(caught.value.diag.message_primary) == code


def call(conn: psycopg.Connection, function: str, *args: Any) -> Any:
    marks = ", ".join(["%s"] * len(args))
    row = conn.execute(f"SELECT {function}({marks})", args).fetchone()
    assert row is not None
    return row[0]


def lines(*rows: dict[str, Any]) -> psycopg.types.json.Jsonb:
    return psycopg.types.json.Jsonb(list(rows))


@dataclass(frozen=True)
class Pair:
    """A link between a buyer and a supplier, as far as it has been taken."""

    buyer: Shop
    supplier: Shop
    link: uuid.UUID
    supplier_row: uuid.UUID  # the buyer's row in `supplier` for the partner
    item: uuid.UUID  # the buyer's own catalogue item, counted in kg


def invite(as_app: AppSession, shop: Shop, as_role: str, *, expires: timedelta = timedelta(hours=48)) -> str:
    code = uuid.uuid4().hex
    with as_app(shop.shop_id) as conn:
        conn.execute(
            "INSERT INTO network_invite (id, shop_id, code_hash, as_role, created_by, created_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                uuid.uuid4(),
                shop.shop_id,
                hashlib.sha256(code.encode()).digest(),
                as_role,
                shop.member_id,
                NOW,
                NOW + expires,
            ),
        )
    return code


def redeem(as_app: AppSession, shop: Shop, code: str, role: str, *, at: datetime = NOW) -> uuid.UUID:
    with as_app(shop.shop_id) as conn:
        link = call(
            conn,
            "network_invite_redeem",
            shop.shop_id,
            hashlib.sha256(code.encode()).digest(),
            role,
            shop.member_id,
            at,
        )
    assert isinstance(link, uuid.UUID)
    return link


def linked(as_app: AppSession, owner: psycopg.Connection, buyer: Shop, supplier: Shop) -> Pair:
    """An active link: the supplier invited, the buyer presented the code, the supplier accepted, and each
    side said which row of its own books the other is."""
    link = redeem(as_app, buyer, invite(as_app, supplier, "supplier"), "buyer")
    with as_app(supplier.shop_id) as conn:
        call(conn, "network_link_decide", supplier.shop_id, buyer.shop_id, link, True, supplier.member_id, NOW)
        call(conn, "network_link_attach", supplier.shop_id, link, supplier.customer_id, False, supplier.member_id)
    supplier_row, item = uuid.uuid4(), uuid.uuid4()
    owner.execute(
        "INSERT INTO supplier (id, shop_id, name, name_norm) VALUES (%s, %s, 'Hamkor', %s)",
        (supplier_row, buyer.shop_id, f"hamkor {supplier_row.hex[:6]}"),
    )
    owner.execute(
        "INSERT INTO catalog_item (id, shop_id, name, name_norm, unit, price, tracked) "
        "VALUES (%s, %s, 'Guruch', %s, 'kg', 15000, true)",
        (item, buyer.shop_id, f"guruch {item.hex[:6]}"),
    )
    with as_app(buyer.shop_id) as conn:
        call(conn, "network_link_attach", buyer.shop_id, link, supplier_row, False, buyer.member_id)
    return Pair(buyer, supplier, link, supplier_row, item)


def sent_order(as_app: AppSession, pair: Pair) -> uuid.UUID:
    order = uuid.uuid4()
    with as_app(pair.buyer.shop_id) as conn:
        number = call(
            conn,
            "network_order_send",
            pair.buyer.shop_id,
            pair.supplier.shop_id,
            pair.link,
            order,
            pair.buyer.member_id,
            "Ertaga kerak",
            None,
            lines(
                {"name": "Guruch", "unit": "kg", "qty": "10", "item_id": str(pair.item)},
                {"name": "Shakar", "unit": "kg", "qty": "5.500"},
            ),
            NOW,
        )
    assert number >= 1
    return order


def accepted_order(as_app: AppSession, pair: Pair) -> uuid.UUID:
    order = sent_order(as_app, pair)
    with as_app(pair.supplier.shop_id) as conn:
        total = call(
            conn,
            "network_order_accept",
            pair.supplier.shop_id,
            pair.buyer.shop_id,
            order,
            pair.supplier.member_id,
            "UZS",
            lines(
                {"line_no": 1, "qty": "10", "unit_price": 12_000},
                {"line_no": 2, "qty": "4.5", "unit_price": 11_000},
            ),
            NOW,
        )
    assert total == 120_000 + 49_500
    return order


def issued_note(as_app: AppSession, pair: Pair, *, paid: int = 0) -> tuple[uuid.UUID, uuid.UUID]:
    order, note = accepted_order(as_app, pair), uuid.uuid4()
    with as_app(pair.supplier.shop_id) as conn:
        call(
            conn,
            "network_note_issue",
            pair.supplier.shop_id,
            pair.buyer.shop_id,
            order,
            note,
            pair.supplier.member_id,
            pair.supplier.customer_id,
            paid,
            None,
            None,
            NOW,
        )
    return order, note


def rows(owner: psycopg.Connection, table: str, shop: Shop) -> int:
    row = owner.execute(f"SELECT count(*) FROM {table} WHERE shop_id = %s", (shop.shop_id,)).fetchone()
    assert row is not None
    return int(row[0])


def everything(owner: psycopg.Connection) -> list[Any]:
    """Every row of the shared tables, for "nothing changed"."""
    found: list[Any] = []
    for table in SHARED_TABLES:
        key = "id" if table == "network_event" else "1"
        found.append(owner.execute(f"SELECT t::text FROM {table} t ORDER BY shop_id, {key}, t::text").fetchall())
    return found


# --- a shop reads its own side and nothing else -----------------------------------------------------------


def test_each_side_holds_its_own_copy_and_a_third_shop_holds_nothing(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, shop_c: Shop, switches: None
) -> None:
    pair = linked(as_app, owner, shop_a, shop_b)
    order, note = issued_note(as_app, pair, paid=20_000)

    for shop, role in ((shop_a, "buyer"), (shop_b, "supplier")):
        with as_app(shop.shop_id) as conn:
            assert conn.execute("SELECT shop_id, role, state FROM network_link").fetchall() == [
                (shop.shop_id, role, "active")
            ]
            assert conn.execute("SELECT shop_id, role, status FROM network_order").fetchall() == [
                (shop.shop_id, role, "delivered")
            ]
            assert conn.execute("SELECT shop_id, role, status, total, paid FROM network_note").fetchall() == [
                (shop.shop_id, role, "issued", 169_500, 20_000)
            ]
            assert {row[0] for row in conn.execute("SELECT shop_id FROM network_order_line").fetchall()} == {
                shop.shop_id
            }
            assert {row[0] for row in conn.execute("SELECT shop_id FROM network_event").fetchall()} == {shop.shop_id}
            # Asking for the other side by identifier finds nothing, and is not an error.
            other = shop_b if shop is shop_a else shop_a
            for table in SHARED_TABLES:
                assert conn.execute(f"SELECT 1 FROM {table} WHERE shop_id = %s", (other.shop_id,)).fetchall() == []

    with as_app(shop_c.shop_id) as conn:
        for table in (*SHARED_TABLES, "network_invite", "network_order_draft"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,), table
        for table, identifier in (("network_link", pair.link), ("network_order", order), ("network_note", note)):
            assert conn.execute(f"SELECT 1 FROM {table} WHERE id = %s", (identifier,)).fetchall() == []


def test_a_copy_holds_only_what_that_side_may_know(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, switches: None
) -> None:
    owner.execute("UPDATE shop SET share_phone = '+998901112233' WHERE id = %s", (shop_b.shop_id,))
    pair = linked(as_app, owner, shop_a, shop_b)
    _, note = issued_note(as_app, pair)

    buyer_link = owner.execute(
        "SELECT peer_name, peer_phone, supplier_id, customer_id, requested_by, decided_by FROM network_link "
        "WHERE shop_id = %s",
        (shop_a.shop_id,),
    ).fetchone()
    # The buyer knows the supplier's name and contact phone, its own supplier row, and its own member.
    assert buyer_link == ("Shop B", "+998901112233", pair.supplier_row, None, shop_a.member_id, None)
    supplier_link = owner.execute(
        "SELECT peer_name, peer_phone, supplier_id, customer_id, requested_by, decided_by FROM network_link "
        "WHERE shop_id = %s",
        (shop_b.shop_id,),
    ).fetchone()
    assert supplier_link == ("Shop A", None, None, shop_b.customer_id, None, shop_b.member_id)

    # The buyer's own catalogue item is on the buyer's lines and never on the supplier's; the supplier's
    # customer and member are on the supplier's note and never on the buyer's.
    assert owner.execute(
        "SELECT line_no, item_id FROM network_order_line WHERE shop_id = %s ORDER BY line_no", (shop_a.shop_id,)
    ).fetchall() == [(1, pair.item), (2, None)]
    assert (
        owner.execute(
            "SELECT item_id FROM network_order_line WHERE shop_id = %s AND item_id IS NOT NULL", (shop_b.shop_id,)
        ).fetchall()
        == []
    )
    assert owner.execute(
        "SELECT issued_by, customer_id FROM network_note WHERE id = %s ORDER BY role", (note,)
    ).fetchall() == [(None, None), (shop_b.member_id, shop_b.customer_id)]
    # A step is recorded on both sides; the partner's member is never named.
    events = owner.execute(
        "SELECT shop_id, by_peer, member_id FROM network_event WHERE subject_id = %s AND kind = 'note_issued'", (note,)
    ).fetchall()
    assert sorted(events, key=lambda row: row[1]) == [
        (shop_b.shop_id, False, shop_b.member_id),
        (shop_a.shop_id, True, None),
    ]
    for shop_id in (shop_a.shop_id, shop_b.shop_id):
        logged = owner.execute(
            "SELECT actor_kind, actor_id FROM activity WHERE shop_id = %s AND action = 'network.note_issued'",
            (shop_id,),
        ).fetchall()
        assert logged == [("staff", shop_b.member_id) if shop_id == shop_b.shop_id else ("system", None)]


# --- the application role writes nothing two shops share ---------------------------------------------------


@pytest.mark.parametrize("table", SHARED_TABLES)
def test_the_application_cannot_write_a_shared_table(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, switches: None, table: str
) -> None:
    pair = linked(as_app, owner, shop_a, shop_b)
    issued_note(as_app, pair)
    column = {"network_link": "state", "network_event": "kind"}.get(table, "shop_id")
    for statement in (
        f"UPDATE {table} SET {column} = {column}",
        f"DELETE FROM {table}",
        f"INSERT INTO {table} SELECT * FROM {table}",
    ):
        with pytest.raises(errors.InsufficientPrivilege), as_app(shop_a.shop_id) as conn:
            conn.execute(statement)


def test_the_application_cannot_link_a_supplier_to_a_shop_itself(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop
) -> None:
    supplier_row = uuid.uuid4()
    owner.execute(
        "INSERT INTO supplier (id, shop_id, name, name_norm) VALUES (%s, %s, 'X', 'x')", (supplier_row, shop_a.shop_id)
    )
    with pytest.raises(errors.InsufficientPrivilege), as_app(shop_a.shop_id) as conn:
        conn.execute("UPDATE supplier SET linked_shop_id = %s WHERE id = %s", (shop_b.shop_id, supplier_row))


def test_a_draft_and_an_invitation_are_ordinary_tenant_rows(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, switches: None
) -> None:
    pair = linked(as_app, owner, shop_a, shop_b)
    draft = uuid.uuid4()
    with as_app(shop_a.shop_id) as conn:
        conn.execute(
            "INSERT INTO network_order_draft (id, shop_id, link_id, lines, created_by) VALUES (%s, %s, %s, '[]', %s)",
            (draft, shop_a.shop_id, pair.link, shop_a.member_id),
        )
    invite(as_app, shop_a, "buyer")
    with as_app(shop_b.shop_id) as conn:
        assert conn.execute("SELECT count(*) FROM network_order_draft").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM network_invite WHERE shop_id = %s", (shop_a.shop_id,)).fetchone() == (
            0,
        )
        assert conn.execute("UPDATE network_order_draft SET note = 'x' WHERE id = %s", (draft,)).rowcount == 0
        changed = conn.execute("UPDATE network_invite SET revoked_at = now() WHERE shop_id = %s", (shop_a.shop_id,))
        assert changed.rowcount == 0
    # A draft tagged as another shop is refused by row-level security.
    with pytest.raises(errors.InsufficientPrivilege, match="row-level security"), as_app(shop_b.shop_id) as conn:
        conn.execute(
            "INSERT INTO network_order_draft (id, shop_id, link_id, lines, created_by) VALUES (%s, %s, %s, '[]', %s)",
            (uuid.uuid4(), shop_a.shop_id, pair.link, shop_a.member_id),
        )
    # Marking an invitation used is the redeeming function's alone.
    with pytest.raises(errors.InsufficientPrivilege), as_app(shop_a.shop_id) as conn:
        conn.execute("UPDATE network_invite SET used_at = now()")


# --- invitations ---------------------------------------------------------------------------------------


def test_a_code_works_once_and_every_reason_it_does_not_is_the_same_refusal(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, shop_c: Shop, switches: None
) -> None:
    code = invite(as_app, shop_b, "supplier")
    digest = hashlib.sha256(code.encode()).digest()
    before = everything(owner)

    def present(shop: Shop, what: bytes, role: str = "buyer", at: datetime = NOW) -> None:
        with as_app(shop.shop_id) as conn:
            call(conn, "network_invite_redeem", shop.shop_id, what, role, shop.member_id, at)

    with refusal("NETWORK_INVITE_INVALID"):  # no such code
        present(shop_a, hashlib.sha256(b"guess").digest())
    with refusal("NETWORK_INVITE_INVALID"):  # the shop's own code
        present(shop_b, digest)
    with refusal("NETWORK_INVITE_INVALID"):  # both would be the supplier
        present(shop_a, digest, role="supplier")
    with refusal("NETWORK_INVITE_INVALID"):  # too late
        present(shop_a, digest, at=NOW + timedelta(hours=49))
    assert everything(owner) == before

    present(shop_a, digest)
    with refusal("NETWORK_INVITE_INVALID"):  # used
        present(shop_c, digest)
    assert rows(owner, "network_link", shop_c) == 0

    withdrawn = invite(as_app, shop_b, "supplier")
    with as_app(shop_b.shop_id) as conn:
        conn.execute("UPDATE network_invite SET revoked_at = now() WHERE used_at IS NULL")
    with refusal("NETWORK_INVITE_INVALID"):
        present(shop_c, hashlib.sha256(withdrawn.encode()).digest())

    # Two shops have one live link in each direction: a second code of the same shop does not add one.
    again = invite(as_app, shop_b, "supplier")
    with refusal("NETWORK_LINK_EXISTS"):
        present(shop_a, hashlib.sha256(again.encode()).digest())


def test_a_request_shows_names_only_and_the_phone_comes_with_acceptance(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, switches: None
) -> None:
    owner.execute("UPDATE shop SET share_phone = '+998900000001' WHERE id = %s", (shop_a.shop_id,))
    owner.execute("UPDATE shop SET share_phone = '+998900000002' WHERE id = %s", (shop_b.shop_id,))
    link = redeem(as_app, shop_a, invite(as_app, shop_b, "supplier"), "buyer")
    seen = "SELECT shop_id, state, invited, peer_name, peer_phone FROM network_link WHERE id = %s ORDER BY peer_name"
    assert owner.execute(seen, (link,)).fetchall() == [
        (shop_b.shop_id, "requested", True, "Shop A", None),
        (shop_a.shop_id, "requested", False, "Shop B", None),
    ]
    # Only the shop that made the invitation answers.
    with refusal("NETWORK_STATE"), as_app(shop_a.shop_id) as conn:
        call(conn, "network_link_decide", shop_a.shop_id, shop_b.shop_id, link, True, shop_a.member_id, NOW)
    with as_app(shop_b.shop_id) as conn:
        call(conn, "network_link_decide", shop_b.shop_id, shop_a.shop_id, link, False, shop_b.member_id, NOW)
    # Declined: nothing more is learnt, and the answer is given once.
    assert owner.execute(seen, (link,)).fetchall() == [
        (shop_b.shop_id, "declined", True, "Shop A", None),
        (shop_a.shop_id, "declined", False, "Shop B", None),
    ]
    with refusal("NETWORK_STATE"), as_app(shop_b.shop_id) as conn:
        call(conn, "network_link_decide", shop_b.shop_id, shop_a.shop_id, link, True, shop_b.member_id, NOW)

    second = redeem(as_app, shop_a, invite(as_app, shop_b, "supplier"), "buyer")
    with as_app(shop_b.shop_id) as conn:
        call(conn, "network_link_decide", shop_b.shop_id, shop_a.shop_id, second, True, shop_b.member_id, NOW)
    assert owner.execute(seen, (second,)).fetchall() == [
        (shop_b.shop_id, "active", True, "Shop A", "+998900000001"),
        (shop_a.shop_id, "active", False, "Shop B", "+998900000002"),
    ]


# --- every function refuses the wrong caller ---------------------------------------------------------------


def _attempts(pair: Pair, order: uuid.UUID, note: uuid.UUID, payment: uuid.UUID) -> dict[str, Any]:
    """Each crossing function, called the way `actor` would call it about the pair's objects. `actor` is
    the shop that acts and `peer` the shop it names as its partner."""

    def build(actor: Shop, peer: Shop, member: uuid.UUID) -> dict[str, tuple[Any, ...]]:
        a, p = actor.shop_id, peer.shop_id
        return {
            "network_lock": (a, p, pair.link),
            "network_link_decide": (a, p, pair.link, True, member, NOW),
            "network_link_end": (a, p, pair.link, member, NOW),
            "network_link_attach": (a, pair.link, pair.supplier_row, False, member),
            "network_notice_recipients": (a, p, pair.link),
            "network_order_send": (a, p, pair.link, uuid.uuid4(), member, None, None, ONE_LINE, NOW),
            "network_order_accept": (a, p, order, member, "UZS", ONE_PRICE, NOW),
            "network_order_close": (a, p, order, member, "sabab", NOW),
            "network_note_issue": (a, p, order, uuid.uuid4(), member, actor.customer_id, 0, "tuzatish", None, NOW),
            "network_note_reject": (a, p, note, member, "kam keldi", None, NOW),
            "network_enter_peer": (a, p, note),
            "network_receipt_finish": (a, p, note, member, uuid.uuid4(), uuid.uuid4(), None, NOW),
            "network_payment_record": (a, p, pair.link, uuid.uuid4(), member, 1000, "UZS", None, uuid.uuid4(), NOW),
            "network_payment_decide": (a, p, payment, member, False, "yo'q", None, NOW),
            "network_payment_withdraw": (a, p, payment, member, NOW),
        }  # fmt: skip

    return {"build": build}


ONE_LINE = psycopg.types.json.Jsonb([{"name": "X", "unit": "kg", "qty": "1"}])
ONE_PRICE = psycopg.types.json.Jsonb([{"line_no": 1, "qty": "1", "unit_price": 1}])
CROSSING = (
    "network_lock", "network_link_decide", "network_link_end", "network_link_attach", "network_notice_recipients",
    "network_order_send", "network_order_accept", "network_order_close", "network_note_issue", "network_note_reject",
    "network_enter_peer", "network_receipt_finish", "network_payment_record", "network_payment_decide",
    "network_payment_withdraw",
)  # fmt: skip

NAMING_A_MEMBER = [
    name for name in CROSSING if name not in ("network_lock", "network_notice_recipients", "network_enter_peer")
]


def _world(
    as_app: AppSession, owner: psycopg.Connection, buyer: Shop, supplier: Shop
) -> tuple[Pair, uuid.UUID, uuid.UUID, uuid.UUID]:
    """A link with an issued note and a payment the buyer recorded and the supplier has not answered."""
    pair = linked(as_app, owner, buyer, supplier)
    order, note = issued_note(as_app, pair)
    payment, entry = uuid.uuid4(), uuid.uuid4()
    owner.execute(
        "INSERT INTO supplier_entry (id, shop_id, supplier_id, seq, kind, amount, author_id) "
        "VALUES (%s, %s, %s, 1, 'payment', 30000, %s)",
        (entry, buyer.shop_id, pair.supplier_row, buyer.member_id),
    )
    with as_app(buyer.shop_id) as conn:
        call(
            conn, "network_payment_record", buyer.shop_id, supplier.shop_id, pair.link, payment, buyer.member_id,
            30_000, "UZS", None, entry, NOW,
        )  # fmt: skip
    return pair, order, note, payment


@pytest.mark.parametrize("function", CROSSING)
def test_a_shop_that_is_no_party_is_refused_as_if_nothing_were_there(
    as_app: AppSession,
    owner: psycopg.Connection,
    shop_a: Shop,
    shop_b: Shop,
    shop_c: Shop,
    switches: None,
    function: str,
) -> None:
    pair, order, note, payment = _world(as_app, owner, shop_a, shop_b)
    build = _attempts(pair, order, note, payment)["build"]
    before = everything(owner)
    # Shop C names each of the two as its partner, and names an object that does not exist at all.
    for peer in (shop_a, shop_b):
        with refusal("NETWORK_NOT_FOUND"), as_app(shop_c.shop_id) as conn:
            call(conn, function, *build(shop_c, peer, shop_c.member_id)[function])
    nothing = Pair(shop_c, shop_a, uuid.uuid4(), pair.supplier_row, pair.item)
    missing = _attempts(nothing, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())["build"]
    if function not in ("network_payment_record", "network_order_send"):
        with refusal("NETWORK_NOT_FOUND"), as_app(shop_c.shop_id) as conn:
            call(conn, function, *missing(shop_c, shop_a, shop_c.member_id)[function])
    assert everything(owner) == before
    assert rows(owner, "network_event", shop_c) == 0


@pytest.mark.parametrize("function", CROSSING)
def test_a_function_acts_only_for_the_tenant_of_the_transaction(
    as_app: AppSession,
    owner: psycopg.Connection,
    shop_a: Shop,
    shop_b: Shop,
    shop_c: Shop,
    switches: None,
    function: str,
) -> None:
    """Naming another shop as the one that acts is refused: from a stranger's transaction, from the
    partner's, and from one with no tenant at all."""
    pair, order, note, payment = _world(as_app, owner, shop_a, shop_b)
    build = _attempts(pair, order, note, payment)["build"]
    before = everything(owner)
    for tenant in (shop_c.shop_id, shop_b.shop_id, None):
        with refusal("NETWORK_NOT_FOUND"), as_app(tenant) as conn:
            call(conn, function, *build(shop_a, shop_b, shop_a.member_id)[function])
    assert everything(owner) == before


@pytest.mark.parametrize("function", NAMING_A_MEMBER)
def test_a_member_of_another_shop_cannot_be_named_as_the_one_who_acts(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, switches: None, function: str
) -> None:
    pair, order, note, payment = _world(as_app, owner, shop_a, shop_b)
    build = _attempts(pair, order, note, payment)["build"]
    before = everything(owner)
    for actor, peer in ((shop_a, shop_b), (shop_b, shop_a)):
        with refusal("NETWORK_NOT_FOUND"), as_app(actor.shop_id) as conn:
            call(conn, function, *build(actor, peer, peer.member_id)[function])
    owner.execute("UPDATE membership SET status = 'suspended' WHERE id = %s", (shop_a.member_id,))
    with refusal("NETWORK_NOT_FOUND"), as_app(shop_a.shop_id) as conn:
        call(conn, function, *build(shop_a, shop_b, shop_a.member_id)[function])
    assert everything(owner) == before


@pytest.mark.parametrize("function", CROSSING)
@pytest.mark.parametrize("switch", ["network_on", "stock_on"])
def test_with_a_switch_off_every_function_refuses(
    as_app: AppSession,
    owner: psycopg.Connection,
    shop_a: Shop,
    shop_b: Shop,
    switches: None,
    function: str,
    switch: str,
) -> None:
    pair, order, note, payment = _world(as_app, owner, shop_a, shop_b)
    build = _attempts(pair, order, note, payment)["build"]
    before = everything(owner)
    turn(owner, switch, False)
    for actor, peer in ((shop_a, shop_b), (shop_b, shop_a)):
        with refusal("NETWORK_NOT_FOUND"), as_app(actor.shop_id) as conn:
            call(conn, function, *build(actor, peer, actor.member_id)[function])
    assert everything(owner) == before


def test_each_step_belongs_to_one_side_and_one_state(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, switches: None
) -> None:
    pair, order, note, payment = _world(as_app, owner, shop_a, shop_b)
    build = _attempts(pair, order, note, payment)["build"]
    before = everything(owner)
    wrong_side = {
        shop_b: ("network_order_send", "network_note_reject", "network_enter_peer", "network_receipt_finish",
                 "network_payment_withdraw"),
        shop_a: ("network_order_accept", "network_note_issue", "network_payment_decide", "network_link_decide"),
    }  # fmt: skip
    for actor, functions in wrong_side.items():
        peer = shop_a if actor is shop_b else shop_b
        for function in functions:
            args = build(actor, peer, actor.member_id)[function]
            with refusal("NETWORK_STATE"), as_app(actor.shop_id) as conn:
                call(conn, function, *args)
    # The right side, the wrong moment: an order that was delivered is not accepted again, and a note
    # that waits for its answer blocks closing the order.
    with refusal("NETWORK_STATE"), as_app(shop_b.shop_id) as conn:
        call(conn, "network_order_accept", *build(shop_b, shop_a, shop_b.member_id)["network_order_accept"])
    for actor, peer in ((shop_a, shop_b), (shop_b, shop_a)):
        with refusal("NETWORK_STATE"), as_app(actor.shop_id) as conn:
            call(conn, "network_order_close", *build(actor, peer, actor.member_id)["network_order_close"])
    assert everything(owner) == before


# --- the second tenant -------------------------------------------------------------------------------------


def tenant(conn: psycopg.Connection) -> str:
    row = conn.execute("SELECT current_setting('qd.shop_id', true)").fetchone()
    assert row is not None
    return str(row[0])


def test_the_tenant_moves_only_to_the_supplier_of_a_note_its_buyer_is_confirming(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, shop_c: Shop, switches: None
) -> None:
    _, order, note, _ = _world(as_app, owner, shop_a, shop_b)
    a, b, c = shop_a.shop_id, shop_b.shop_id, shop_c.shop_id

    with as_app(a) as conn:
        call(conn, "network_enter_peer", a, b, note)
        assert tenant(conn) == str(b)
        # As the supplier, the transaction sees the supplier's rows and none of the buyer's.
        assert conn.execute("SELECT id FROM shop").fetchall() == [(b,)]
        assert conn.execute("SELECT shop_id FROM network_note").fetchall() == [(b,)]
        assert conn.execute("SELECT count(*) FROM customer WHERE shop_id = %s", (a,)).fetchone() == (0,)
        call(conn, "network_leave_peer", a, b, note)
        assert tenant(conn) == str(a)
        assert conn.execute("SELECT id FROM shop").fetchall() == [(a,)]

    # Not to any other shop, not for any other note, not from the supplier's side, not by a stranger.
    for acting, home, peer, which, code in (
        (a, a, c, note, "NETWORK_NOT_FOUND"),
        (a, a, b, uuid.uuid4(), "NETWORK_NOT_FOUND"),
        (a, a, b, order, "NETWORK_NOT_FOUND"),
        (b, b, a, note, "NETWORK_STATE"),
        (c, c, b, note, "NETWORK_NOT_FOUND"),
        (c, c, a, note, "NETWORK_NOT_FOUND"),
        (c, a, b, note, "NETWORK_NOT_FOUND"),
    ):
        with refusal(code), as_app(acting) as conn:
            call(conn, "network_enter_peer", home, peer, which)

    # Leaving is only the way back from an entry made in this transaction.
    for acting, home, peer in ((b, a, b), (a, a, b), (c, a, b), (b, c, b)):
        with refusal("NETWORK_NOT_FOUND"), as_app(acting) as conn:
            call(conn, "network_leave_peer", home, peer, note)
    with refusal("NETWORK_NOT_FOUND"), as_app(a) as conn:
        call(conn, "network_enter_peer", a, b, note)
        call(conn, "network_leave_peer", c, b, note)

    # The setting ends with the transaction, like the tenant itself.
    with as_app(a) as conn:
        call(conn, "network_enter_peer", a, b, note)
    with as_app(None) as conn:
        assert tenant(conn) in ("", "None")
        assert conn.execute("SELECT count(*) FROM network_note").fetchone() == (0,)


def test_the_tenant_does_not_move_for_a_note_that_is_not_waiting(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, switches: None
) -> None:
    _, _, note, _ = _world(as_app, owner, shop_a, shop_b)
    a, b = shop_a.shop_id, shop_b.shop_id
    with as_app(a) as conn:
        call(conn, "network_note_reject", a, b, note, shop_a.member_id, "Kam keldi", None, NOW)
    with refusal("NETWORK_STATE"), as_app(a) as conn:
        call(conn, "network_enter_peer", a, b, note)

    second = linked(as_app, owner, shop_b, shop_a)  # the other direction, ended before anything is confirmed
    _, other = issued_note(as_app, second)
    with as_app(a) as conn:
        call(conn, "network_link_end", a, b, second.link, shop_a.member_id, NOW)
    with refusal("NETWORK_STATE"), as_app(b) as conn:
        call(conn, "network_enter_peer", b, a, other)


def test_a_note_is_received_only_when_both_books_say_what_it_says(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, switches: None
) -> None:
    pair = linked(as_app, owner, shop_a, shop_b)
    _, note = issued_note(as_app, pair, paid=20_000)
    a, b = shop_a.shop_id, shop_b.shop_id

    def document(
        total: int = 169_500, paid: int = 20_000, origin: uuid.UUID = note, status: str = "posted"
    ) -> uuid.UUID:
        document_id = uuid.uuid4()
        owner.execute(
            "INSERT INTO stock_document (id, shop_id, kind, number, status, doc_date, supplier_id, total, paid, draft, "
            "  origin_ref, created_by, posted_by, posted_at) "
            "VALUES (%s, %s, 'receipt', (SELECT coalesce(max(number), 0) + 1 FROM stock_document WHERE shop_id = %s), "
            "  %s, current_date, %s, %s, %s, %s, %s, %s, %s, now())",
            (
                document_id, a, a, status, pair.supplier_row, total, paid,
                None if status == "posted" else "{}", origin, shop_a.member_id, shop_a.member_id,
            ),
        )  # fmt: skip
        return document_id

    def entry(kind: str, amount: int, seq: int, customer: uuid.UUID = shop_b.customer_id) -> uuid.UUID:
        entry_id = uuid.uuid4()
        owner.execute(
            "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (entry_id, b, customer, seq, kind, amount, shop_b.member_id),
        )
        return entry_id

    def finish(document_id: uuid.UUID, credit: uuid.UUID, payment: uuid.UUID | None) -> None:
        with as_app(a) as conn:
            call(conn, "network_receipt_finish", a, b, note, shop_a.member_id, document_id, credit, payment, NOW)

    credit, paid = entry("credit", 169_500, 1), entry("payment", 20_000, 2)
    stranger = uuid.uuid4()
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Boshqa', 'boshqa')", (stranger, b)
    )
    before = everything(owner)
    # A receipt answers one note, so each wrong one is tried by itself and taken away again.
    for wrong in ({"total": 169_000}, {"paid": 0}, {"origin": uuid.uuid4()}, {"status": "draft"}):
        made = document(**wrong)  # type: ignore[arg-type]
        with refusal("NETWORK_BOOKS_MISMATCH"):
            finish(made, credit, paid)
        owner.execute("DELETE FROM stock_document WHERE id = %s", (made,))
    with refusal("NETWORK_BOOKS_MISMATCH"):
        finish(uuid.uuid4(), credit, paid)
    good = document()
    for wrong_credit, wrong_paid in (
        (entry("credit", 169_000, 3), paid),  # another amount
        (entry("credit", 169_500, 1, stranger), paid),  # another customer's account
        (paid, paid),  # not a credit sale
        (uuid.uuid4(), paid),
        (credit, None),  # what was paid on delivery is missing
        (credit, entry("payment", 19_000, 4)),
    ):
        with refusal("NETWORK_BOOKS_MISMATCH"):
            finish(good, wrong_credit, wrong_paid)
    assert everything(owner) == before

    finish(good, credit, paid)
    assert owner.execute(
        "SELECT shop_id, status, document_id, ledger_entry_id, decided_by FROM network_note "
        "WHERE id = %s ORDER BY role",
        (note,),
    ).fetchall() == [(a, "received", good, None, shop_a.member_id), (b, "received", None, credit, None)]
    assert owner.execute("SELECT DISTINCT status FROM network_order WHERE shop_id IN (%s, %s)", (a, b)).fetchall() == [
        ("received",)
    ]
    with refusal("NETWORK_STATE"):  # once
        finish(good, credit, paid)


# --- a delivery note does not change ------------------------------------------------------------------------


def test_what_a_note_says_cannot_be_changed_by_anyone(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, switches: None
) -> None:
    pair = linked(as_app, owner, shop_a, shop_b)
    order, note = issued_note(as_app, pair)
    # Not even by the owner of the tables: the trigger refuses it.
    for change in ("total = total + 1", "paid = 1", "currency = 'USD'", "number = 9", "issued_at = now()"):
        with pytest.raises(errors.CheckViolation, match="does not change once it is issued"):
            owner.execute(f"UPDATE network_note SET {change} WHERE id = %s", (note,))
    for change in ("qty = qty + 1", "unit_price = 1", "line_total = 1", "name = 'x'"):
        with pytest.raises(errors.CheckViolation, match="a line of a delivery note does not change"):
            owner.execute(f"UPDATE network_note_line SET {change} WHERE note_id = %s", (note,))

    # A correction is a new note with a reason; the one before it is superseded, and stays as it was.
    a, b = shop_a.shop_id, shop_b.shop_id
    corrected = uuid.uuid4()
    with refusal("NETWORK_STATE"), as_app(b) as conn:  # a correction says why
        call(
            conn,
            "network_note_issue",
            b,
            a,
            order,
            uuid.uuid4(),
            shop_b.member_id,
            shop_b.customer_id,
            0,
            None,
            None,
            NOW,
        )
    with as_app(b) as conn:
        call(
            conn, "network_note_issue", b, a, order, corrected, shop_b.member_id, shop_b.customer_id, 0,
            "Shakar kam yuklangan",
            lines({"line_no": 1, "qty": "10", "unit_price": 12_000}, {"line_no": 2, "qty": "4", "unit_price": 11_000}),
            NOW,
        )  # fmt: skip
    assert owner.execute(
        "SELECT id, status, total, supersedes_id FROM network_note WHERE shop_id = %s ORDER BY number", (a,)
    ).fetchall() == [(note, "superseded", 169_500, None), (corrected, "issued", 164_000, note)]
    with pytest.raises(errors.CheckViolation, match="does not go from superseded"):
        owner.execute("UPDATE network_note SET status = 'issued' WHERE id = %s", (note,))

    def issue(conn: psycopg.Connection, paid: int, what: Any) -> None:
        seller = shop_b.member_id
        call(conn, "network_note_issue", b, a, order, uuid.uuid4(), seller, shop_b.customer_id, paid, "yana", what, NOW)

    # A line the order does not have, a line twice, or nothing delivered: refused whole.
    for bad in (
        lines({"line_no": 3, "qty": "1", "unit_price": 1}),
        lines({"line_no": 1, "qty": "1", "unit_price": 1}, {"line_no": 1, "qty": "2", "unit_price": 1}),
        lines({"line_no": 1, "qty": "0", "unit_price": 1}),
    ):
        with refusal("NETWORK_INVALID"), as_app(b) as conn:
            issue(conn, 0, bad)
    with refusal("NETWORK_INVALID"), as_app(b) as conn:  # more paid than the note is for
        issue(conn, 169_501, None)


# --- payments -----------------------------------------------------------------------------------------------


def test_a_payment_needs_the_recording_shops_own_entry_and_moves_nothing_on_the_other_side(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, switches: None
) -> None:
    pair = linked(as_app, owner, shop_a, shop_b)
    a, b = shop_a.shop_id, shop_b.shop_id

    def supplier_entry(amount: int, seq: int, kind: str = "payment") -> uuid.UUID:
        entry_id = uuid.uuid4()
        owner.execute(
            "INSERT INTO supplier_entry (id, shop_id, supplier_id, seq, kind, amount, author_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (entry_id, a, pair.supplier_row, seq, kind, amount, shop_a.member_id),
        )
        return entry_id

    def record(entry_id: uuid.UUID, amount: int = 40_000, payment: uuid.UUID | None = None) -> uuid.UUID:
        payment = payment or uuid.uuid4()
        with as_app(a) as conn:
            payer = shop_a.member_id
            call(conn, "network_payment_record", a, b, pair.link, payment, payer, amount, "UZS", None, entry_id, NOW)
        return payment

    good = supplier_entry(40_000, 1)
    for wrong in (uuid.uuid4(), supplier_entry(39_000, 2), supplier_entry(40_000, 3, kind="opening")):
        with refusal("NETWORK_BOOKS_MISMATCH"):
            record(wrong)
    payment = record(good)
    with pytest.raises(errors.UniqueViolation):  # one entry stands for one payment
        record(good)
    assert owner.execute(
        "SELECT shop_id, recorded_by_own, status, supplier_entry_id, ledger_entry_id FROM network_payment "
        "WHERE id = %s ORDER BY role",
        (payment,),
    ).fetchall() == [(a, True, "awaiting", good, None), (b, False, "awaiting", None, None)]
    # Nothing was written to the supplier's ledger.
    assert rows(owner, "ledger_entry", shop_b) == 0

    # The side that recorded it does not answer it, and takes it back only after cancelling its own entry.
    with refusal("NETWORK_STATE"), as_app(a) as conn:
        call(conn, "network_payment_decide", a, b, payment, shop_a.member_id, True, None, good, NOW)
    with refusal("NETWORK_BOOKS_MISMATCH"), as_app(a) as conn:
        call(conn, "network_payment_withdraw", a, b, payment, shop_a.member_id, NOW)
    # Confirming needs the confirming shop's own entry: a customer payment of the same amount.
    with refusal("NETWORK_BOOKS_MISMATCH"), as_app(b) as conn:
        call(conn, "network_payment_decide", b, a, payment, shop_b.member_id, True, None, uuid.uuid4(), NOW)
    with refusal("NETWORK_INVALID"), as_app(b) as conn:  # declining says why
        call(conn, "network_payment_decide", b, a, payment, shop_b.member_id, False, None, None, NOW)
    credit, paid = uuid.uuid4(), uuid.uuid4()
    for entry_id, seq, kind in ((credit, 1, "credit"), (paid, 2, "payment")):
        owner.execute(
            "INSERT INTO ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id) "
            "VALUES (%s, %s, %s, %s, %s, 40000, %s)",
            (entry_id, b, shop_b.customer_id, seq, kind, shop_b.member_id),
        )
    with as_app(b) as conn:
        call(conn, "network_payment_decide", b, a, payment, shop_b.member_id, True, None, paid, NOW)
    assert owner.execute(
        "SELECT shop_id, status, supplier_entry_id, ledger_entry_id FROM network_payment WHERE id = %s ORDER BY role",
        (payment,),
    ).fetchall() == [(a, "confirmed", good, None), (b, "confirmed", None, paid)]
    with refusal("NETWORK_STATE"), as_app(b) as conn:
        call(conn, "network_payment_decide", b, a, payment, shop_b.member_id, False, "kech", None, NOW)


# --- ending a link, and erasing a shop --------------------------------------------------------------------


def test_ending_a_link_closes_what_waited_and_leaves_both_histories(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, switches: None
) -> None:
    pair, order, note, payment = _world(as_app, owner, shop_a, shop_b)
    a, b = shop_a.shop_id, shop_b.shop_id
    with as_app(b) as conn:
        call(conn, "network_link_end", b, a, pair.link, shop_b.member_id, NOW)
    assert owner.execute(
        "SELECT shop_id, state, ended_by, ended_by_peer FROM network_link WHERE id = %s ORDER BY role", (pair.link,)
    ).fetchall() == [(a, "ended", None, True), (b, "ended", shop_b.member_id, False)]
    both = (order, note, payment)
    assert owner.execute(
        "SELECT DISTINCT status, closed_reason FROM network_order WHERE id = %s", both[:1]
    ).fetchall() == [("cancelled", "link ended")]
    assert owner.execute("SELECT DISTINCT status FROM network_note WHERE id = %s", both[1:2]).fetchall() == [("void",)]
    assert owner.execute("SELECT DISTINCT status FROM network_payment WHERE id = %s", both[2:]).fetchall() == [
        ("lapsed",)
    ]
    assert owner.execute("SELECT linked_shop_id FROM supplier WHERE id = %s", (pair.supplier_row,)).fetchone() == (
        None,
    )
    # Both still read their own history; nothing new can be done across it.
    for shop in (shop_a, shop_b):
        with as_app(shop.shop_id) as conn:
            assert conn.execute("SELECT count(*) FROM network_order").fetchone() == (1,)
            assert conn.execute("SELECT count(*) FROM network_event WHERE kind = 'link_ended'").fetchone() == (1,)
    build = _attempts(pair, order, note, payment)["build"]
    before = everything(owner)
    for actor, peer in ((shop_a, shop_b), (shop_b, shop_a)):
        for function in CROSSING:
            if function in ("network_lock", "network_notice_recipients"):
                continue
            with refusal("NETWORK_STATE"), as_app(actor.shop_id) as conn:
                call(conn, function, *build(actor, peer, actor.member_id)[function])
    assert everything(owner) == before
    # The contact phone a side already had stays as it was; nothing is added.
    assert owner.execute(
        "SELECT count(*) FROM network_link WHERE id = %s AND peer_name IS NULL", (pair.link,)
    ).fetchone() == (0,)


def test_erasing_a_shop_removes_its_side_and_shows_the_partner_as_removed(
    as_app: AppSession, as_worker: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, switches: None
) -> None:
    owner.execute("UPDATE shop SET share_phone = '+998901112233' WHERE id = %s", (shop_b.shop_id,))
    pair, _, _, _ = _world(as_app, owner, shop_a, shop_b)
    a, b = shop_a.shop_id, shop_b.shop_id
    owner.execute("UPDATE network_link SET counterpart_made = true WHERE shop_id = %s", (a,))
    owner.execute("UPDATE supplier SET phone = '+998901112233' WHERE id = %s", (pair.supplier_row,))
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() - interval '1 hour' WHERE id = %s", (b,)
    )
    with as_worker(None) as conn:
        assert call(conn, "erase_shop", b) is True

    for table in (*SHARED_TABLES, "network_invite", "network_order_draft"):
        assert rows(owner, table, shop_b) == 0, table
    # The buyer keeps its own side: the history, with nothing that identifies the partner.
    assert owner.execute(
        "SELECT state, peer_removed, peer_name, peer_phone, ended_by_peer FROM network_link WHERE shop_id = %s", (a,)
    ).fetchone() == ("ended", True, None, None, True)
    assert owner.execute("SELECT status, closed_reason FROM network_order WHERE shop_id = %s", (a,)).fetchall() == [
        ("cancelled", "partner removed")
    ]
    assert owner.execute("SELECT status FROM network_note WHERE shop_id = %s", (a,)).fetchall() == [("void",)]
    assert owner.execute("SELECT status FROM network_payment WHERE shop_id = %s", (a,)).fetchall() == [("lapsed",)]
    assert owner.execute(
        "SELECT linked_shop_id, phone FROM supplier WHERE id = %s", (pair.supplier_row,)
    ).fetchone() == (
        None,
        None,
    )
    # Its own posted books are untouched: the payment it wrote on the supplier's account still stands.
    assert rows(owner, "supplier_entry", shop_a) == 1
    assert owner.execute(
        "SELECT kind, by_peer FROM network_event WHERE shop_id = %s AND kind = 'partner_removed'", (a,)
    ).fetchall() == [("partner_removed", True)]
