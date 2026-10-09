"""The author of a confirmed delivery note's sale, as the database holds it (migration 0048).

`network_note_author` says in SQL whose name the supplier's sale is in: the member who issued the note
while still an active member who holds `credits.record`, the shop's owner otherwise. The application
decides the same in `author_of`; the first test holds the two together over every role, status, state of
the platform switch and stored change. The rest is `network_receipt_finish`: it marks a note received
only for an entry whose author is the one the rule gives, and for nobody else's.
"""

import itertools
import uuid
from collections.abc import Iterator
from dataclasses import replace

import psycopg
import pytest

from qarz.application.errors import NotFound
from qarz.application.network_orders import author_of
from qarz.application.ports import Membership
from qarz.domain import permissions
from qarz.domain.access import Role

from ..conftest import AppSession, Shop
from .test_network_schema import Books, everything, refusal, switches, turn

pytestmark = pytest.mark.db

__all__ = ["switches"]

CREDIT = permissions.CREDITS_RECORD


@pytest.fixture
def permissions_switch(owner: psycopg.Connection) -> Iterator[None]:
    """`permissions_on` as it was, after a test that turns it."""
    before = owner.execute("SELECT value FROM platform_setting WHERE key = 'permissions_on'").fetchone()
    yield
    owner.execute("DELETE FROM platform_setting WHERE key = 'permissions_on'")
    if before is not None:
        turn(owner, "permissions_on", bool(before[0]))


def member(owner: psycopg.Connection, shop: Shop, role: str) -> uuid.UUID:
    user, membership = uuid.uuid4(), uuid.uuid4()
    owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (user, uuid.uuid4().int % 10**15))
    owner.execute(
        "INSERT INTO membership (id, shop_id, user_id, role) VALUES (%s, %s, %s, %s)",
        (membership, shop.shop_id, user, role),
    )
    return membership


def in_sql(owner: psycopg.Connection, shop: uuid.UUID, issuer: uuid.UUID | None) -> uuid.UUID | None:
    row = owner.execute("SELECT network_note_author(%s, %s)", (shop, issuer)).fetchone()
    assert row is not None
    return None if row[0] is None else uuid.UUID(str(row[0]))


def in_python(owner: psycopg.Connection, shop: uuid.UUID, issuer: uuid.UUID) -> uuid.UUID | None:
    """The application's answer, from the same rows read the way its session reads them."""
    switch = owner.execute("SELECT value FROM platform_setting WHERE key = 'permissions_on'").fetchone()
    on = switch is not None and switch[0] is True
    row = owner.execute(
        "SELECT role, permissions_granted, permissions_denied FROM membership "
        "WHERE shop_id = %s AND id = %s AND status = 'active'",
        (shop, issuer),
    ).fetchone()
    standing = (
        None
        if row is None
        else Membership(issuer, Role(row[0]), permissions_on=on, granted=frozenset(row[1]), denied=frozenset(row[2]))
    )
    boss = owner.execute(
        "SELECT id FROM membership WHERE shop_id = %s AND role = 'owner' AND status = 'active'", (shop,)
    ).fetchone()
    try:
        return author_of(issuer, standing, None if boss is None else boss[0])[0].membership_id
    except NotFound:
        return None


OVERRIDES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    ((), ()),
    ((), (CREDIT,)),
    ((CREDIT,), ()),  # a grant of what the role gives anyway: it adds nothing, and takes nothing
    ((), ("payments.record", "network.fulfil")),  # other denials do not count
    (("stock.adjust",), (CREDIT, "payments.record")),
)


def test_the_database_and_the_application_name_the_same_author_in_every_case(
    owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, permissions_switch: None
) -> None:
    a = shop_a.shop_id
    seen: set[tuple[bool, bool]] = set()  # (the issuer stays the author, the switch is on)
    for on, role, status, (granted, denied) in itertools.product(
        (False, True), ("seller", "manager"), ("active", "suspended", "removed", "invited"), OVERRIDES
    ):
        turn(owner, "permissions_on", on)
        issuer = member(owner, shop_a, role)
        owner.execute(
            "UPDATE membership SET permissions_granted = %s, permissions_denied = %s WHERE id = %s",
            (list(granted), list(denied), issuer),
        )
        owner.execute("UPDATE membership SET status = %s WHERE id = %s", (status, issuer))
        case = (on, role, status, granted, denied)
        answer = in_sql(owner, a, issuer)
        assert answer == in_python(owner, a, issuer), case
        assert answer in (issuer, shop_a.member_id), case
        # Said outright as well, so that two answers wrong in the same way would not pass.
        stays = status == "active" and not (on and CREDIT in denied)
        assert (answer == issuer) is stays, case
        seen.add((stays, on))
    assert seen == {(True, False), (True, True), (False, False), (False, True)}
    for on in (False, True):
        turn(owner, "permissions_on", on)
        # The owner who issued a note is its author. A member of another shop, or nobody, is not: the owner.
        assert in_sql(owner, a, shop_a.member_id) == shop_a.member_id == in_python(owner, a, shop_a.member_id)
        assert in_sql(owner, a, shop_b.member_id) == shop_a.member_id == in_python(owner, a, shop_b.member_id)
        assert in_sql(owner, a, uuid.uuid4()) == shop_a.member_id
        assert in_sql(owner, a, None) == shop_a.member_id


def test_a_switch_stored_as_anything_but_true_is_off(
    owner: psycopg.Connection, shop_a: Shop, permissions_switch: None
) -> None:
    """The switch is on only when the stored value is the JSON `true`, as everywhere it is read."""
    issuer = member(owner, shop_a, "seller")
    owner.execute("UPDATE membership SET permissions_denied = %s WHERE id = %s", ([CREDIT], issuer))
    for stored, on in (("true", True), ("false", False), ('"true"', False), ("1", False), ("null", False)):
        owner.execute(
            "INSERT INTO platform_setting (key, value, updated_by) VALUES ('permissions_on', %s::jsonb, 'test') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (stored,),
        )
        expected = shop_a.member_id if on else issuer
        assert in_sql(owner, shop_a.shop_id, issuer) == expected == in_python(owner, shop_a.shop_id, issuer), stored


def test_a_shop_without_an_active_owner_has_no_author_to_fall_back_on(owner: psycopg.Connection, shop_a: Shop) -> None:
    issuer = member(owner, shop_a, "manager")
    owner.execute("UPDATE membership SET status = 'suspended' WHERE id = %s", (shop_a.member_id,))
    assert in_sql(owner, shop_a.shop_id, issuer) == issuer == in_python(owner, shop_a.shop_id, issuer)
    owner.execute("UPDATE membership SET status = 'removed' WHERE id = %s", (issuer,))
    assert in_sql(owner, shop_a.shop_id, issuer) is None
    assert in_python(owner, shop_a.shop_id, issuer) is None


def test_nobody_is_given_the_right_to_ask(as_app: AppSession, shop_a: Shop) -> None:
    """It is used inside `network_receipt_finish` alone; the application decides by its own code."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_app(shop_a.shop_id) as conn:
        conn.execute("SELECT network_note_author(%s, %s)", (shop_a.shop_id, shop_a.member_id))


class Waiting:
    """A note the supplier's manager issued, with money paid on delivery, and the buyer's posted receipt:
    the books of tests/db/test_network_schema.py, with the supplier's entries written by whoever is named."""

    def __init__(self, as_app: AppSession, owner: psycopg.Connection, buyer: Shop, supplier: Shop) -> None:
        self.owner, self.shop = owner, supplier
        self.issuer = member(owner, supplier, "manager")
        self.bystander = member(owner, supplier, "manager")
        self.books = Books(as_app, owner, buyer, replace(supplier, member_id=self.issuer))
        self.note = self.books.note
        self.document = self.books.receipt()

    def entries(self, credit_by: uuid.UUID, paid_by: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
        self.books.supplier = replace(self.shop, member_id=credit_by)
        credit = self.books.sale()
        self.books.supplier = replace(self.shop, member_id=paid_by)
        return credit, self.books.entry("payment", 20_000)

    def finish(self, credit: uuid.UUID, paid: uuid.UUID) -> None:
        self.books.finish(self.document, credit, paid)

    def status(self) -> list[tuple[str]]:
        return self.owner.execute("SELECT DISTINCT status FROM network_note WHERE id = %s", (self.note,)).fetchall()


def test_while_the_issuer_is_active_and_permitted_only_the_issuer_is_accepted_as_the_author(
    as_app: AppSession, owner: psycopg.Connection, shop_a: Shop, shop_b: Shop, switches: None
) -> None:
    w = Waiting(as_app, owner, shop_a, shop_b)
    boss = shop_b.member_id
    before = everything(owner)
    for credit_by, paid_by in (
        (boss, boss),  # the owner, while the issuer still stands: not the owner's to take
        (w.bystander, w.bystander),  # a third member of the shop
        (w.issuer, boss),  # the two entries of one note have one author
        (boss, w.issuer),
        (w.issuer, w.bystander),
        (shop_a.member_id, shop_a.member_id),  # a member of the buyer
    ):
        with refusal("NETWORK_BOOKS_MISMATCH"):
            w.finish(*w.entries(credit_by, paid_by))
    assert everything(owner) == before and w.status() == [("issued",)]
    w.finish(*w.entries(w.issuer, w.issuer))
    assert w.status() == [("received",)]


@pytest.mark.parametrize("left", ["suspended", "removed", "denied"])
def test_once_the_issuer_has_left_or_lost_the_permission_only_the_owner_is_accepted_as_the_author(
    as_app: AppSession,
    owner: psycopg.Connection,
    shop_a: Shop,
    shop_b: Shop,
    switches: None,
    permissions_switch: None,
    left: str,
) -> None:
    w = Waiting(as_app, owner, shop_a, shop_b)
    boss = shop_b.member_id
    if left == "denied":
        owner.execute("UPDATE membership SET permissions_denied = %s WHERE id = %s", ([CREDIT], w.issuer))
        # Stored, and the platform switch still off: the issuer holds the permission and stays the author.
        with refusal("NETWORK_BOOKS_MISMATCH"):
            w.finish(*w.entries(boss, boss))
        turn(owner, "permissions_on", True)
    else:
        owner.execute("UPDATE membership SET status = %s WHERE id = %s", (left, w.issuer))
    before = everything(owner)
    for credit_by, paid_by in (
        (w.issuer, w.issuer),  # the member who issued it: no longer theirs to be the author of
        (w.bystander, w.bystander),  # a third member is never the author, active and permitted as they are
        (boss, w.issuer),
        (w.issuer, boss),
        (shop_a.member_id, shop_a.member_id),
    ):
        with refusal("NETWORK_BOOKS_MISMATCH"):
            w.finish(*w.entries(credit_by, paid_by))
    assert everything(owner) == before and w.status() == [("issued",)]
    w.finish(*w.entries(boss, boss))
    assert w.status() == [("received",)]
