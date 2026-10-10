"""What the database itself holds the shared catalogue to (migration 0050), without the application in
between.

The catalogue's items and barcodes belong to no shop and are read by every shop, so they are not under
the tenant policy: these tests are what says a shop's session still cannot write or alter them. A
suggestion is a shop's row: a shop reads its own and no other's, adds one only as a waiting proposal,
and cannot decide, change or delete one. The two functions of the administrators' queue do nothing for
anyone who is not an active administrator, and never say which shop a suggestion came from.
"""

import uuid
from typing import Any

import psycopg
import pytest

from ..conftest import AppSession, Shop, refused

pytestmark = pytest.mark.db

DECIDE = "SELECT outcome, kind, shared_item_id FROM admin_shared_decide(%s, %s, %s, %s, %s, %s, %s, %s, now())"
QUEUE = "SELECT * FROM admin_shared_suggestions(%s, %s, NULL, NULL, 100)"


def _admin(owner: psycopg.Connection, status: str = "active") -> uuid.UUID:
    user_id = uuid.uuid4()
    owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (user_id, uuid.uuid4().int % 10**15))
    owner.execute(
        "INSERT INTO admin_account (user_id, totp_secret, status) VALUES (%s, %s, %s)", (user_id, b"sealed", status)
    )
    return user_id


def _item(owner: psycopg.Connection, name: str | None = None) -> uuid.UUID:
    item = uuid.uuid4()
    name = name or f"Choy {item.hex[:10]}"
    owner.execute(
        "INSERT INTO shared_item (id, name_uz, search_norm, category) VALUES (%s, %s, %s, 'tea')",
        (item, name, name.lower()),
    )
    return item


def _own_item(owner: psycopg.Connection, shop: Shop, name: str) -> uuid.UUID:
    item = uuid.uuid4()
    owner.execute(
        "INSERT INTO catalog_item (id, shop_id, name, name_norm, price) VALUES (%s, %s, %s, %s, 5000)",
        (item, shop.shop_id, name, name.lower()),
    )
    return item


def _suggest_item(
    owner: psycopg.Connection, shop: Shop, name: str, barcode: str | None = None, item: uuid.UUID | None = None
) -> uuid.UUID:
    suggestion = uuid.uuid4()
    owner.execute(
        "INSERT INTO shared_suggestion (id, shop_id, kind, name, name_norm, unit, barcode, item_id) "
        "VALUES (%s, %s, 'item', %s, %s, 'dona', %s, %s)",
        (suggestion, shop.shop_id, name, name.lower(), barcode, item),
    )
    return suggestion


def _suggest_barcode(owner: psycopg.Connection, shop: Shop, shared: uuid.UUID, code: str) -> uuid.UUID:
    suggestion = uuid.uuid4()
    owner.execute(
        "INSERT INTO shared_suggestion (id, shop_id, kind, barcode, shared_item_id) VALUES (%s, %s, 'barcode', %s, %s)",
        (suggestion, shop.shop_id, code, shared),
    )
    return suggestion


def _settle_the_queue(owner: psycopg.Connection) -> None:
    """Nothing waits any more. The test database is one for the whole session and the queue is read a
    page at a time, oldest first: what other tests left waiting would otherwise fill the page."""
    owner.execute(
        "UPDATE shared_suggestion SET status = 'rejected', decided_by = gen_random_uuid(), decided_at = now() "
        "WHERE status = 'pending'"
    )


def _decide(
    session: AppSession, admin: uuid.UUID, suggestion: uuid.UUID, approve: bool = True, **names: Any
) -> tuple[Any, ...]:
    name_ru, name_uz = names.get("name_ru"), names.get("name_uz")
    norm = " ".join(part.lower() for part in (name_ru, name_uz) if part) or None
    with session(None) as conn:
        row = conn.execute(
            DECIDE, (admin, suggestion, approve, uuid.uuid4(), name_ru, name_uz, norm, names.get("category", "tea"))
        ).fetchone()
    assert row is not None
    return tuple(row)


def _status(owner: psycopg.Connection, suggestion: uuid.UUID) -> Any:
    return owner.execute(
        "SELECT status, decided_by, decided_at IS NOT NULL, shared_item_id FROM shared_suggestion WHERE id = %s",
        (suggestion,),
    ).fetchone()


# --- the catalogue is read by a shop and written by none -------------------------------------------------


def test_a_shop_reads_the_catalogue_with_or_without_a_tenant(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    item = _item(owner)
    owner.execute("INSERT INTO shared_barcode (code, item_id) VALUES (%s, %s)", (f"R-{item.hex[:12]}", item))
    for tenant in (shop_a.shop_id, None):
        with as_app(tenant) as conn:
            assert conn.execute("SELECT count(*) FROM shared_item WHERE id = %s", (item,)).fetchone() == (1,)
            assert conn.execute("SELECT count(*) FROM shared_barcode WHERE item_id = %s", (item,)).fetchone() == (1,)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO shared_item (id, name_uz, search_norm) VALUES (gen_random_uuid(), 'Soxta', 'soxta')",
        "UPDATE shared_item SET name_uz = 'Soxta'",
        "UPDATE shared_item SET price_hint = 1",
        "UPDATE shared_item SET status = 'hidden'",
        "DELETE FROM shared_item",
        "TRUNCATE shared_item CASCADE",
        "INSERT INTO shared_barcode (code, item_id) SELECT 'SOXTA-1', id FROM shared_item LIMIT 1",
        "UPDATE shared_barcode SET code = 'SOXTA-2'",
        "DELETE FROM shared_barcode",
    ],
)
def test_a_shop_session_cannot_write_or_alter_the_catalogue(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, statement: str
) -> None:
    _item(owner)
    refused(as_app, statement, shop_a.shop_id)
    refused(as_app, statement)


def test_the_worker_holds_nothing_of_the_catalogue(as_worker: AppSession, shop_a: Shop) -> None:
    for table in ("shared_item", "shared_barcode", "shared_suggestion"):
        refused(as_worker, f"SELECT count(*) FROM {table}", shop_a.shop_id)


def test_the_administrators_role_loads_items_and_reaches_no_suggestion_directly(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop
) -> None:
    key = uuid.uuid4().hex[:16]
    with as_admin(None) as conn:
        conn.execute(
            "INSERT INTO shared_item (id, source_key, name_ru, search_norm) "
            "VALUES (gen_random_uuid(), %s, 'Чай', 'chay')",
            (key,),
        )
        conn.execute("UPDATE shared_item SET price_hint = 9000 WHERE source_key = %s", (key,))
    assert owner.execute("SELECT price_hint FROM shared_item WHERE source_key = %s", (key,)).fetchone() == (9000,)
    for statement in (
        "SELECT count(*) FROM shared_suggestion",
        "UPDATE shared_suggestion SET status = 'approved'",
        "DELETE FROM shared_item",
        "INSERT INTO shared_barcode (code, item_id) SELECT 'ADM-1', id FROM shared_item LIMIT 1",
    ):
        refused(as_admin, statement, shop_a.shop_id)


# --- a suggestion is a shop's own row ---------------------------------------------------------------------


def test_a_shop_proposes_and_reads_only_its_own_suggestions(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    theirs = _suggest_item(owner, shop_b, f"Boshqa do'kon tovari {uuid.uuid4().hex[:8]}")
    mine = uuid.uuid4()
    with as_app(shop_a.shop_id) as conn:
        conn.execute(
            "INSERT INTO shared_suggestion (id, shop_id, kind, name, name_norm, unit) "
            "VALUES (%s, %s, 'item', 'Shakar', 'shakar', 'kg')",
            (mine, shop_a.shop_id),
        )
    with as_app(shop_a.shop_id) as conn:
        assert conn.execute("SELECT id FROM shared_suggestion").fetchall() == [(mine,)]
        assert conn.execute("SELECT count(*) FROM shared_suggestion WHERE id = %s", (theirs,)).fetchone() == (0,)
    with as_app(None) as conn:
        assert conn.execute("SELECT count(*) FROM shared_suggestion").fetchone() == (0,)
    assert _status(owner, mine) == ("pending", None, False, None)


def test_a_shop_cannot_propose_in_another_shops_name(as_app: AppSession, shop_a: Shop, shop_b: Shop) -> None:
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"),
        as_app(shop_a.shop_id) as conn,
    ):
        conn.execute(
            "INSERT INTO shared_suggestion (id, shop_id, kind, name, name_norm, unit) "
            "VALUES (gen_random_uuid(), %s, 'item', 'Shakar', 'shakar', 'kg')",
            (shop_b.shop_id,),
        )


@pytest.mark.parametrize(
    "decision",
    [
        "'approved', gen_random_uuid(), now()",
        "'rejected', gen_random_uuid(), now()",
    ],
)
def test_a_shop_cannot_write_a_suggestion_that_is_already_decided(
    as_app: AppSession, shop_a: Shop, decision: str
) -> None:
    """The policy lets a shop add a proposal that waits and nothing else: it cannot approve its own."""
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"),
        as_app(shop_a.shop_id) as conn,
    ):
        conn.execute(
            "INSERT INTO shared_suggestion (id, shop_id, kind, name, name_norm, unit, status, decided_by, decided_at) "
            f"VALUES (gen_random_uuid(), %s, 'item', 'Shakar', 'shakar', 'kg', {decision})",
            (shop_a.shop_id,),
        )


def test_a_waiting_suggestion_cannot_name_the_catalogue_item_it_would_become(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop
) -> None:
    shared = _item(owner)
    with pytest.raises(psycopg.errors.CheckViolation), as_app(shop_a.shop_id) as conn:
        conn.execute(
            "INSERT INTO shared_suggestion (id, shop_id, kind, name, name_norm, unit, shared_item_id) "
            "VALUES (gen_random_uuid(), %s, 'item', 'Shakar', 'shakar', 'kg', %s)",
            (shop_a.shop_id, shared),
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE shared_suggestion SET status = 'approved', decided_by = gen_random_uuid(), decided_at = now()",
        "UPDATE shared_suggestion SET name = 'Boshqa'",
        "DELETE FROM shared_suggestion",
    ],
)
def test_a_shop_cannot_decide_change_or_delete_its_own_suggestion(
    owner: psycopg.Connection, as_app: AppSession, shop_a: Shop, statement: str
) -> None:
    suggestion = _suggest_item(owner, shop_a, f"Guruch {uuid.uuid4().hex[:8]}")
    refused(as_app, statement, shop_a.shop_id)
    assert _status(owner, suggestion) == ("pending", None, False, None)


def test_a_suggestion_has_no_column_for_a_price_a_quantity_or_a_person(owner: psycopg.Connection) -> None:
    """What leaves a shop is the item's name, unit and barcode. The table could not hold more if asked to."""
    columns = {
        name
        for (name,) in owner.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'shared_suggestion'"
        ).fetchall()
    }
    assert columns == {
        "id", "shop_id", "kind", "name", "name_norm", "unit", "barcode", "shared_item_id", "item_id", "status",
        "decided_by", "decided_at", "created_at",
    }  # fmt: skip


def test_a_shop_proposes_the_same_name_and_the_same_barcode_once(owner: psycopg.Connection, shop_a: Shop) -> None:
    name, shared = f"Un {uuid.uuid4().hex[:8]}", _item(owner)
    _suggest_item(owner, shop_a, name)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _suggest_item(owner, shop_a, name)
    _suggest_barcode(owner, shop_a, shared, "ONCE-1")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _suggest_barcode(owner, shop_a, shared, "ONCE-1")


def test_a_shop_holds_a_catalogue_item_once(owner: psycopg.Connection, shop_a: Shop, shop_b: Shop) -> None:
    shared = _item(owner)
    for shop in (shop_a, shop_b):  # each shop may hold it; none twice
        first = _own_item(owner, shop, f"Birinchi {uuid.uuid4().hex[:8]}")
        owner.execute("UPDATE catalog_item SET shared_item_id = %s WHERE id = %s", (shared, first))
    second = _own_item(owner, shop_a, f"Ikkinchi {uuid.uuid4().hex[:8]}")
    with pytest.raises(psycopg.errors.UniqueViolation):
        owner.execute("UPDATE catalog_item SET shared_item_id = %s WHERE id = %s", (shared, second))


# --- the administrators' queue ----------------------------------------------------------------------------


def test_the_queue_is_empty_for_anyone_who_is_not_an_active_administrator(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop
) -> None:
    _settle_the_queue(owner)
    suggestion = _suggest_item(owner, shop_a, f"Tuz {uuid.uuid4().hex[:8]}")
    admin, disabled = _admin(owner), _admin(owner, "disabled")
    with as_admin(None) as conn:
        assert suggestion in {row[0] for row in conn.execute(QUEUE, (admin, "pending")).fetchall()}
        for nobody in (disabled, shop_a.user_id, uuid.uuid4()):
            assert conn.execute(QUEUE, (nobody, "pending")).fetchall() == []
    for nobody in (disabled, shop_a.user_id, uuid.uuid4()):
        assert _decide(as_admin, nobody, suggestion, name_uz="Tuz") == ("missing", None, None)
    assert _status(owner, suggestion) == ("pending", None, False, None)


def test_the_queue_never_says_which_shop_a_suggestion_came_from(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    _settle_the_queue(owner)
    name = f"Makaron {uuid.uuid4().hex[:8]}"
    mine = _suggest_item(owner, shop_a, name, barcode="4780000000021")
    _suggest_item(owner, shop_b, name)
    with as_admin(None) as conn:
        cursor = conn.execute(QUEUE, (_admin(owner), "pending"))
        columns = [column.name for column in cursor.description or []]
        row = next(dict(zip(columns, found, strict=True)) for found in cursor.fetchall() if found[0] == mine)
    assert columns == [
        "suggestion_id", "kind", "name", "unit", "barcode", "shared_item_id", "item_name_ru", "item_name_uz",
        "item_amount", "status", "created_at", "decided_at", "same",
    ]  # fmt: skip
    assert (row["name"], row["unit"], row["barcode"], row["same"]) == (name, "dona", "4780000000021", 1)
    told = {str(value) for value in row.values()}
    assert not told & {str(shop_a.shop_id), str(shop_b.shop_id), str(shop_a.user_id), "Shop A", "Shop B"}


def test_approving_an_item_writes_it_with_its_barcode_and_ties_the_shops_own_item(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop
) -> None:
    tag = uuid.uuid4().hex[:8]
    own = _own_item(owner, shop_a, f"Moy {tag}")
    suggestion = _suggest_item(owner, shop_a, f"Moy {tag}", barcode=f"B-{tag}", item=own)
    admin = _admin(owner)
    outcome, kind, shared = _decide(as_admin, admin, suggestion, name_ru=f"Масло {tag}", name_uz=f"Moy {tag}")
    assert (outcome, kind) == ("approved", "item") and shared is not None
    assert owner.execute(
        "SELECT name_ru, name_uz, category, unit, source_key, price_hint FROM shared_item WHERE id = %s", (shared,)
    ).fetchone() == (f"Масло {tag}", f"Moy {tag}", "tea", "dona", None, None)
    assert owner.execute("SELECT item_id FROM shared_barcode WHERE code = %s", (f"B-{tag}",)).fetchone() == (shared,)
    assert owner.execute("SELECT shared_item_id FROM catalog_item WHERE id = %s", (own,)).fetchone() == (shared,)
    assert _status(owner, suggestion) == ("approved", admin, True, shared)


def test_a_suggestion_is_decided_once(owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop) -> None:
    tag = uuid.uuid4().hex[:8]
    suggestion = _suggest_item(owner, shop_a, f"Sut {tag}")
    admin = _admin(owner)
    assert _decide(as_admin, admin, suggestion, approve=False)[0] == "rejected"
    before = owner.execute("SELECT count(*) FROM shared_item").fetchone()
    assert _decide(as_admin, admin, suggestion, name_uz=f"Sut {tag}")[0] == "decided"
    assert _decide(as_admin, admin, suggestion, approve=False)[0] == "decided"
    assert owner.execute("SELECT count(*) FROM shared_item").fetchone() == before, "a rejected item is not written"
    assert _status(owner, suggestion) == ("rejected", admin, True, None)


def test_an_item_approved_without_a_name_changes_nothing(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop
) -> None:
    suggestion = _suggest_item(owner, shop_a, f"Qand {uuid.uuid4().hex[:8]}")
    assert _decide(as_admin, _admin(owner), suggestion) == ("unnamed", "item", None)
    assert _status(owner, suggestion) == ("pending", None, False, None)


def test_approving_a_barcode_attaches_it_and_settles_every_shop_that_proposed_the_same(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop, shop_b: Shop
) -> None:
    shared, code = _item(owner), f"C-{uuid.uuid4().hex[:10]}"
    mine = _suggest_barcode(owner, shop_a, shared, code)
    theirs = _suggest_barcode(owner, shop_b, shared, code)
    admin = _admin(owner)
    assert _decide(as_admin, admin, mine) == ("approved", "barcode", shared)
    assert owner.execute("SELECT item_id FROM shared_barcode WHERE code = %s", (code,)).fetchone() == (shared,)
    assert _status(owner, mine) == ("approved", admin, True, shared)
    assert _status(owner, theirs) == ("approved", admin, True, shared)


def test_a_barcode_that_names_another_catalogue_item_is_not_moved(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop
) -> None:
    holder, other, code = _item(owner), _item(owner), f"T-{uuid.uuid4().hex[:10]}"
    owner.execute("INSERT INTO shared_barcode (code, item_id) VALUES (%s, %s)", (code, holder))
    suggestion = _suggest_barcode(owner, shop_a, other, code)
    assert _decide(as_admin, _admin(owner), suggestion) == ("barcode_taken", "barcode", holder)
    assert owner.execute("SELECT item_id FROM shared_barcode WHERE code = %s", (code,)).fetchone() == (holder,)
    assert _status(owner, suggestion) == ("pending", None, False, other)


def test_an_item_whose_barcode_is_taken_is_approved_without_it(
    owner: psycopg.Connection, as_admin: AppSession, shop_a: Shop
) -> None:
    holder, tag = _item(owner), uuid.uuid4().hex[:8]
    owner.execute("INSERT INTO shared_barcode (code, item_id) VALUES (%s, %s)", (f"K-{tag}", holder))
    suggestion = _suggest_item(owner, shop_a, f"Kofe {tag}", barcode=f"K-{tag}")
    outcome, _, shared = _decide(as_admin, _admin(owner), suggestion, name_uz=f"Kofe {tag}")
    assert outcome == "approved" and shared != holder
    assert owner.execute("SELECT item_id FROM shared_barcode WHERE code = %s", (f"K-{tag}",)).fetchone() == (holder,)
