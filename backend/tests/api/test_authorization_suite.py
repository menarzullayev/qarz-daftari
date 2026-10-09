"""The authorization and tenant suite (NFR-013, launch criterion 5). Blocking.

Every registered operation is exercised according to its scope:

- shop operations: as each role, as a suspended member, as the owner of another shop, as a customer, as a
  platform administrator without support access, as a stranger, and without signing in;
- self operations: without signing in, and as any signed-in user;
- public operations: without signing in, with data that is not validly signed;
- link operations (opened by a secret link instead of a sign-in): without the secret, with one that is
  not a live link's, and as every kind of signed-in caller. Each is answered exactly as for a route that
  does not exist, and changes nothing;
- administrator operations: as every kind of non-administrator (owner, manager, seller, customer,
  stranger), as everyone who has some but not all of what makes an administrator (on the allow-list
  without an account, without the second factor, with a disabled account, with an expired or closed admin
  session, with another administrator's session, with an account but off the allow-list), and without
  signing in. Each is answered exactly as for a route that does not exist, and changes nothing;
- the door to the administrator's side (enrolling and passing the second factor): as everyone who is not
  on the allow-list or whose account is disabled, and without signing in.

An operation that is registered but not described here fails the suite, and so does an API route that is
not bound to a registered operation, so nothing can be added without being checked.
"""

import base64
import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from qarz.application.operations import all_operations
from qarz.domain.access import Role, lowest_role_with
from qarz.domain.promise import tashkent_date

from ..receipt_samples import JPEG
from .conftest import (
    ADMIN_API,
    AdminEnv,
    World,
    allow_list,
    as_user,
    current_file_root,
    elevate,
    make_admin,
    switch_permissions_on,
)

pytestmark = pytest.mark.db

ROLE_ORDER = [Role.SELLER, Role.MANAGER, Role.OWNER]


@dataclass(frozen=True)
class Call:
    """A valid request for a shop operation, addressed to the given shop using shop A's resources."""

    method: str
    path: Callable[[World, uuid.UUID], str]
    json: dict[str, Any] | None = None
    changes_data: bool = False
    ok_status: int = 200
    # Sent as `multipart/form-data` instead of JSON: the fields, and the one file part.
    form: tuple[dict[str, str], dict[str, tuple[str, bytes, str]]] | None = None
    # Puts shop A into the state the call needs (for example a pending transfer). Runs as the owner role.
    prepare: Callable[[psycopg.Connection, World], None] | None = None
    # Callers whose role is allowed but who are refused for another stated reason: caller -> (status, code).
    refused: tuple[tuple[str, int, str], ...] = ()
    # A body that is not JSON: the bytes of an uploaded file.
    content: bytes | None = None


@dataclass(frozen=True)
class PlainCall:
    method: str
    path: str
    json: dict[str, Any] | None = None
    ok_status: int = 200
    needs_key: bool = False
    returns_own_id: bool = True


def _pending_transfer(owner: psycopg.Connection, world: World) -> None:
    owner.execute(
        "INSERT INTO ownership_transfer (id, shop_id, from_membership, to_membership, expires_at) "
        "VALUES (gen_random_uuid(), %s, %s, %s, now() + interval '1 day')",
        (world.shop_a, world.owner_a_membership, world.manager_a_membership),
    )


def _dispute_id(world: World) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"suite-dispute:{world.entry_a}")


def _open_dispute(owner: psycopg.Connection, world: World) -> None:
    owner.execute(
        "INSERT INTO dispute (id, shop_id, entry_id, reason) VALUES (%s, %s, %s, 'Men buni olmaganman')",
        (_dispute_id(world), world.shop_a, world.entry_a),
    )


def _reminders_due(owner: psycopg.Connection, world: World) -> None:
    """Reminders on, and customer_a (linked) overdue: the promised date of entry_a moves into the past."""
    owner.execute("UPDATE shop SET reminders_on = true WHERE id = %s", (world.shop_a,))
    owner.execute(
        "INSERT INTO promise (id, shop_id, entry_id, promised_date, actor) "
        "VALUES (gen_random_uuid(), %s, %s, current_date - 3, 'staff')",
        (world.shop_a, world.entry_a),
    )


def _date_request_id(world: World) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"suite-date-request:{world.entry_a}")


def _open_date_request(owner: psycopg.Connection, world: World) -> None:
    # entry_a is promised a week from the database's today; the request asks for a later day.
    owner.execute(
        "INSERT INTO date_change_request (id, shop_id, entry_id, requested_date, reason) "
        "VALUES (%s, %s, %s, current_date + 30, 'Oylik kechikdi')",
        (_date_request_id(world), world.shop_a, world.entry_a),
    )


def _deletion_pending(owner: psycopg.Connection, world: World) -> None:
    owner.execute(
        "UPDATE shop SET status = 'deletion_pending', deletion_due = now() + interval '30 days' WHERE id = %s",
        (world.shop_a,),
    )


def _notice_id(world: World) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"suite-notice:{world.entry_a}")


def _export_job_id(world: World) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"suite-export:{world.entry_a}")


def _finished_export(owner: psycopg.Connection, world: World) -> None:
    """An export of shop A that the worker has finished: a workbook kept for a week."""
    file_id = uuid.uuid5(uuid.NAMESPACE_URL, f"suite-export-file:{world.entry_a}")
    owner.execute(
        "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime, delete_after) "
        "VALUES (%s, %s, 'export', 'ee/suite-export', %s, 4, "
        "'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', now() + interval '7 days')",
        (file_id, world.shop_a, hashlib.sha256(b"xlsx").digest()),
    )
    owner.execute(
        "INSERT INTO export_job (id, shop_id, requested_by, status, file_id, attempts, row_count, started_at, "
        "finished_at) VALUES (%s, %s, %s, 'done', %s, 1, 1, now(), now())",
        (_export_job_id(world), world.shop_a, world.owner_a_membership, file_id),
    )


def _sent_notice(owner: psycopg.Connection, world: World) -> None:
    """Ali says he paid 20 000 of the 50 000 he owes, and sent a receipt with it."""
    content = b"%PDF-1.4 suite receipt"
    token = uuid.uuid5(uuid.NAMESPACE_URL, f"suite-receipt:{world.entry_a}").hex * 2
    target = current_file_root() / token[:2] / token
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    file_id = uuid.uuid5(uuid.NAMESPACE_URL, f"suite-file:{world.entry_a}")
    owner.execute(
        "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime, delete_after) "
        "VALUES (%s, %s, 'payment_notice', %s, %s, %s, 'application/pdf', now() + interval '104 days')",
        (file_id, world.shop_a, f"{token[:2]}/{token}", hashlib.sha256(content).digest(), len(content)),
    )
    owner.execute(
        "INSERT INTO payment_notice (id, shop_id, customer_id, amount, file_id) VALUES (%s, %s, %s, 20000, %s)",
        (_notice_id(world), world.shop_a, world.customer_a, file_id),
    )


def _cash_today(shop: uuid.UUID) -> str:
    """A valid period for the cash book's summary: today, in Tashkent."""
    day = tashkent_date(datetime.now(UTC))
    return f"/api/v1/shops/{shop}/cash/summary?from={day}&to={day}"


def _last_week(shop: uuid.UUID) -> str:
    """A valid period for the report: the seven Tashkent days that end today."""
    last = tashkent_date(datetime.now(UTC))
    return f"/api/v1/shops/{shop}/reports/period?from={last - timedelta(days=6)}&to={last}"


SUITE_IMPORT = b"Ism,Qarz summasi\nImport Mijoz,70000\n"


def _import_id(world: World) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"suite-import:{world.shop_a}")


SUITE_PLAN = "5u1te0000000000000000000000p1an"


def _stored_import(owner: psycopg.Connection, world: World, status: str) -> None:
    token = uuid.uuid5(uuid.NAMESPACE_URL, f"suite-import-file:{world.shop_a}").hex * 2
    target = current_file_root() / token[:2] / token
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(SUITE_IMPORT)
    file_id = uuid.uuid5(uuid.NAMESPACE_URL, f"suite-import-row:{world.shop_a}")
    owner.execute(
        "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime, delete_after) "
        "VALUES (%s, %s, 'import', %s, %s, %s, 'text/csv', now() + interval '30 days')",
        (file_id, world.shop_a, f"{token[:2]}/{token}", hashlib.sha256(SUITE_IMPORT).digest(), len(SUITE_IMPORT)),
    )
    owner.execute(
        "INSERT INTO import_batch (id, shop_id, status, file_id, summary, author_id, applied_at, plan, preview) "
        "VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s = 'applied' THEN now() END, "
        "CASE WHEN %s = 'validated' THEN %s END, CASE WHEN %s = 'validated' THEN '{\"rows\": []}'::jsonb END)",
        (
            _import_id(world),
            world.shop_a,
            status,
            file_id,
            '{"format": "csv", "rows": 1, "errors": [], "created_customers": []}',
            world.owner_a_membership,
            status,
            status,
            SUITE_PLAN,
            status,
        ),
    )


def _validated_import(owner: psycopg.Connection, world: World) -> None:
    _stored_import(owner, world, "validated")


def _applied_import(owner: psycopg.Connection, world: World) -> None:
    _stored_import(owner, world, "applied")


def _share_token(world: World) -> str:
    """The secret of customer_a's read-only link in this test's world: 43 URL-safe characters, as the
    application makes them. Its own for each world, because the database lives for the whole session."""
    digest = hashlib.sha256(f"suite-share:{world.customer_a}".encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _links_on(owner: psycopg.Connection, world: World) -> None:
    """The platform switch `customer_links_on`, as an administrator would have turned it on. The row is
    signed with a user identifier, so the `admin_env` fixture removes it after the test."""
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('customer_links_on', 'true', %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by",
        (str(world.admin),),
    )


def _live_share(owner: psycopg.Connection, world: World) -> None:
    """The switch on, and customer_a holding a read-only link made by the manager."""
    _links_on(owner, world)
    owner.execute(
        "INSERT INTO customer_share (id, shop_id, customer_id, token_hash, created_by, expires_at) "
        "VALUES (gen_random_uuid(), %s, %s, %s, %s, now() + interval '90 days')",
        (
            world.shop_a,
            world.customer_a,
            hashlib.sha256(_share_token(world).encode()).digest(),
            world.manager_a_membership,
        ),
    )


def _cash_category_id(world: World) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"suite-cash-category:{world.shop_a}")


def _cash_spare_category_id(world: World) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"suite-cash-spare-category:{world.shop_a}")


def _cash_entry_id(world: World) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"suite-cash-entry:{world.shop_a}")


def _cash_on(owner: psycopg.Connection, world: World) -> None:
    """The platform switch `cash_book_on`, as an administrator would have turned it on. The row is signed
    with a user identifier, so the `admin_env` fixture removes it after the test."""
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('cash_book_on', 'true', %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by",
        (str(world.admin),),
    )


def _cash_book(owner: psycopg.Connection, world: World) -> None:
    """The switch on, and shop A's cash book in use: its categories, one of them never written under, and
    one expense the manager recorded."""
    _cash_on(owner, world)
    owner.execute(
        "INSERT INTO cash_category (id, shop_id, direction, name, name_norm, system_key) VALUES "
        "(gen_random_uuid(), %(shop)s, 'income', 'Qarz qaytdi', 'qarz qaytdi', 'debt_repaid'), "
        "(%(used)s, %(shop)s, 'expense', 'Ijara', 'ijara', NULL), "
        "(%(spare)s, %(shop)s, 'expense', 'Transport', 'transport', NULL)",
        {"shop": world.shop_a, "used": _cash_category_id(world), "spare": _cash_spare_category_id(world)},
    )
    owner.execute(
        "INSERT INTO cash_entry (id, shop_id, direction, method, amount, category_id, day, author_id) "
        "VALUES (%s, %s, 'expense', 'cash', 300000, %s, current_date, %s)",
        (_cash_entry_id(world), world.shop_a, _cash_category_id(world), world.manager_a_membership),
    )


def _support_id(world: World) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"suite-support:{world.shop_a}")


def _open_support(owner: psycopg.Connection, world: World) -> None:
    """The world's administrator holds an open support access to shop A, opened a minute ago."""
    owner.execute(
        "INSERT INTO support_access (id, shop_id, admin_id, reason, starts_at, ends_at) "
        "VALUES (%s, %s, %s, 'Suite uchun', now() - interval '1 minute', now() + interval '1 hour')",
        (_support_id(world), world.shop_a, world.admin),
    )


def _supplier_id(world: World) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"suite-supplier:{world.shop_a}")


def _stock_document_id(world: World) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"suite-stock-document:{world.shop_a}")


def _supplier_entry_id(world: World) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"suite-supplier-entry:{world.shop_a}")


def _stock(owner: psycopg.Connection, world: World) -> None:
    """The platform switch `stock_on`, and in shop A what the stock's calls need: "Non" counted and with
    a barcode, a supplier with one payment on their account, and a stocktake still in draft. With the
    switch off none of these routes exists: tests/api/test_stock.py."""
    owner.execute(
        "INSERT INTO platform_setting (key, value, updated_by) VALUES ('stock_on', 'true', %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by",
        (str(world.admin),),
    )
    owner.execute("UPDATE catalog_item SET tracked = true WHERE id = %s", (world.catalog_item_a,))
    owner.execute(
        "INSERT INTO catalog_barcode (shop_id, code, item_id) VALUES (%s, 'SUITE-1', %s)",
        (world.shop_a, world.catalog_item_a),
    )
    owner.execute(
        "INSERT INTO supplier (id, shop_id, name, name_norm) VALUES (%s, %s, 'Ulgurji', 'ulgurji')",
        (_supplier_id(world), world.shop_a),
    )
    owner.execute(
        "INSERT INTO supplier_entry (id, shop_id, supplier_id, seq, kind, amount, author_id) "
        "VALUES (%s, %s, %s, 1, 'payment', 20000, %s)",
        (_supplier_entry_id(world), world.shop_a, _supplier_id(world), world.manager_a_membership),
    )
    owner.execute(
        "INSERT INTO stock_document (id, shop_id, kind, number, doc_date, draft, created_by) "
        "VALUES (%s, %s, 'stocktake', 1, current_date, %s::jsonb, %s)",
        (
            _stock_document_id(world),
            world.shop_a,
            json.dumps({"lines": [{"item_id": str(world.catalog_item_a), "qty": "3", "unit_cost": None}]}),
            world.manager_a_membership,
        ),
    )


def _stock_archived_supplier(owner: psycopg.Connection, world: World) -> None:
    _stock(owner, world)
    owner.execute("UPDATE supplier SET status = 'archived' WHERE id = %s", (_supplier_id(world),))


def _stock_settled_supplier(owner: psycopg.Connection, world: World) -> None:
    """Archiving needs a settled account: the payment of `_stock` is met by a purchase of the same amount."""
    _stock(owner, world)
    owner.execute(
        "INSERT INTO supplier_entry (id, shop_id, supplier_id, seq, kind, amount, author_id) "
        "VALUES (gen_random_uuid(), %s, %s, 2, 'opening', 20000, %s)",
        (world.shop_a, _supplier_id(world), world.manager_a_membership),
    )


def _stocktake(world: World) -> dict[str, Any]:
    return {"kind": "stocktake", "lines": [{"item_id": str(world.catalog_item_a), "qty": "4"}]}


def _net_id(world: World, what: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"suite-network-{what}:{world.shop_a}")


def _net_code(world: World) -> str:
    return f"suite-network-code-{world.shop_a.hex}"


# Every identifier of the network a call of the suite names: none of them is shop B's to use.
NETWORK_THINGS = (
    "invite", "link-buys", "link-sells", "link-asked", "draft", "order-sent", "order-incoming", "order-accepted",
    "note-incoming", "note-issued", "payment-theirs", "payment-own",
)  # fmt: skip


def _network(owner: psycopg.Connection, world: World) -> None:
    """The switches `network_on` and `stock_on`, and shop A linked both ways to a third shop, C, with one
    thing of the network in each state a call needs. The partner is not shop B on purpose: every call the
    suite then sends through shop B about these things is a shop that is no party to them, and must get
    the 404 of a thing that does not exist. A fourth shop, D, has asked A for a link and holds a code A
    may present. With a switch off none of these routes exists: tests/api/test_network.py."""
    for key in ("network_on", "stock_on"):
        owner.execute(
            "INSERT INTO platform_setting (key, value, updated_by) VALUES (%s, 'true', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by",
            (key, str(world.admin)),
        )
    a, mine = world.shop_a, world.manager_a_membership
    c, d = _net_id(world, "shop-c"), _net_id(world, "shop-d")
    staff = {}
    for shop, name in ((c, "Shop C"), (d, "Shop D")):
        user, staff[shop] = uuid.uuid4(), uuid.uuid4()
        owner.execute("INSERT INTO shop (id, name) VALUES (%s, %s)", (shop, name))
        owner.execute("INSERT INTO app_user (id, tg_id) VALUES (%s, %s)", (user, uuid.uuid4().int % 10**15))
        owner.execute(
            "INSERT INTO membership (id, shop_id, user_id, role) VALUES (%s, %s, %s, 'owner')",
            (staff[shop], shop, user),
        )
        owner.execute(
            "INSERT INTO subscription (shop_id, state, trial_ends) VALUES (%s, 'trial', current_date + 30)", (shop,)
        )
    theirs = c
    their_customer, supplier = _net_id(world, "customer-c"), _net_id(world, "supplier")
    owner.execute(
        "INSERT INTO customer (id, shop_id, display_name, name_norm) VALUES (%s, %s, 'Shop A', 'shop a')",
        (their_customer, c),
    )
    owner.execute(
        "INSERT INTO supplier (id, shop_id, name, name_norm, linked_shop_id) VALUES (%s, %s, 'Shop C', 'shop c', %s)",
        (supplier, a, c),
    )
    owner.execute("UPDATE catalog_item SET tracked = true WHERE id = %s", (world.catalog_item_a,))
    buys, sells, asked = _net_id(world, "link-buys"), _net_id(world, "link-sells"), _net_id(world, "link-asked")
    link = (
        "INSERT INTO network_link (shop_id, id, peer_shop_id, role, state, invited, peer_name, supplier_id, "
        "  customer_id, order_seq, note_seq, requested_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 3, 1, now())"
    )
    for row in (
        (a, buys, c, "buyer", "active", False, "Shop C", supplier, None),
        (c, buys, a, "supplier", "active", True, "Shop A", None, their_customer),
        (a, sells, c, "supplier", "active", True, "Shop C", None, None),
        (c, sells, a, "buyer", "active", False, "Shop A", None, None),
        (a, asked, d, "supplier", "requested", True, "Shop D", None, None),
        (d, asked, a, "buyer", "requested", False, "Shop A", None, None),
    ):
        owner.execute(link, row)
    invite = (
        "INSERT INTO network_invite (id, shop_id, code_hash, as_role, created_by, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, now() + interval '1 day')"
    )
    owner.execute(
        invite, (_net_id(world, "invite"), a, hashlib.sha256(f"suite-own-{a}".encode()).digest(), "buyer", mine)
    )
    code = hashlib.sha256(_net_code(world).encode()).digest()
    owner.execute(invite, (uuid.uuid4(), d, code, "supplier", staff[d]))
    owner.execute(
        "INSERT INTO network_order_draft (id, shop_id, link_id, lines, created_by) VALUES (%s, %s, %s, %s::jsonb, %s)",
        (
            _net_id(world, "draft"),
            a,
            buys,
            json.dumps([{"name": "Un", "unit": "kg", "qty": "5", "item_id": None}]),
            mine,
        ),
    )
    # Orders: one A sent, one that came to A, one A accepted, and two that were delivered (one each way).
    orders = (
        ("order-sent", buys, 1, "sent", "buyer"),
        ("order-incoming", sells, 1, "sent", "supplier"),
        ("order-accepted", sells, 2, "accepted", "supplier"),
        ("order-delivered", sells, 3, "delivered", "supplier"),
        ("order-arrived", buys, 2, "delivered", "buyer"),
    )
    for name, link_id, number, status, role in orders:
        priced = status != "sent"
        for shop, side in ((a, role), (theirs, "buyer" if role == "supplier" else "supplier")):
            owner.execute(
                "INSERT INTO network_order (shop_id, id, link_id, role, number, status, currency, total, sent_at, "
                "  sent_by, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), %s, now())",
                (
                    shop,
                    _net_id(world, name),
                    link_id,
                    side,
                    number,
                    status,
                    "UZS" if priced else None,
                    5000 if priced else None,
                    {a: mine, theirs: staff[c]}[shop] if side == "buyer" else None,
                ),
            )
            owner.execute(
                "INSERT INTO network_order_line (shop_id, order_id, line_no, name, unit, qty, item_id, accepted_qty, "
                "  unit_price) VALUES (%s, %s, 1, 'Non', 'dona', 1, %s, %s, %s)",
                (
                    shop,
                    _net_id(world, name),
                    world.catalog_item_a if shop == a and side == "buyer" else None,
                    1 if priced else None,
                    5000 if priced else None,
                ),
            )
    # Delivery notes: one issued to A (it confirms or rejects), one A issued (it corrects).
    for name, order_name, link_id, role in (
        ("note-incoming", "order-arrived", buys, "buyer"),
        ("note-issued", "order-delivered", sells, "supplier"),
    ):
        for shop, side in ((a, role), (theirs, "buyer" if role == "supplier" else "supplier")):
            seller = side == "supplier"
            owner.execute(
                "INSERT INTO network_note (shop_id, id, link_id, order_id, role, number, status, currency, total, "
                "  paid, issued_at, issued_by, customer_id) "
                "VALUES (%s, %s, %s, %s, %s, 1, 'issued', 'UZS', 5000, 0, now(), %s, %s)",
                (
                    shop,
                    _net_id(world, name),
                    link_id,
                    _net_id(world, order_name),
                    side,
                    ({a: mine, theirs: staff[c]}[shop]) if seller else None,
                    their_customer if seller and shop == theirs else None,
                ),
            )
            owner.execute(
                "INSERT INTO network_note_line (shop_id, note_id, line_no, name, unit, qty, unit_price, line_total, "
                "  item_id) VALUES (%s, %s, 1, 'Non', 'dona', 1, 5000, 5000, %s)",
                (shop, _net_id(world, name), world.catalog_item_a if shop == a and side == "buyer" else None),
            )
    # Payments over the link A buys through: one C recorded (A answers), one A recorded (A may take back).
    entry = _net_id(world, "supplier-entry")
    owner.execute(
        "INSERT INTO supplier_entry (id, shop_id, supplier_id, seq, kind, amount, author_id) "
        "VALUES (%s, %s, %s, 1, 'payment', 20000, %s)",
        (entry, a, supplier, mine),
    )
    for name, own, amount in (("payment-theirs", False, 10000), ("payment-own", True, 20000)):
        for shop, side, recorded in ((a, "buyer", own), (theirs, "supplier", not own)):
            owner.execute(
                "INSERT INTO network_payment (shop_id, id, link_id, role, recorded_by_own, status, amount, currency, "
                "  recorded_at, supplier_entry_id) VALUES (%s, %s, %s, %s, %s, 'awaiting', %s, 'UZS', now(), %s)",
                (shop, _net_id(world, name), buys, side, recorded, amount, entry if shop == a and own else None),
            )


def _net(path: str, thing: str | None = None) -> Callable[[World, uuid.UUID], str]:
    """The path of a call of the network, naming one of the things `_network` made."""

    def build(world: World, shop: uuid.UUID) -> str:
        found = path if thing is None else path.replace("{id}", str(_net_id(world, thing)))
        return f"/api/v1/shops/{shop}/network{found}"

    return build


def _permissions_on(owner: psycopg.Connection, world: World) -> None:
    """The permission matrix exists only while its switch is on; off, its routes answer 404 to everyone
    (tests/api/test_permissions.py)."""
    switch_permissions_on(owner)


CALLS: dict[str, Call] = {
    # The network between shops is behind `network_on` and `stock_on`; the suite turns both on and links
    # shop A to a third shop (`_network`). With a switch off: tests/api/test_network.py.
    "network.overview": Call("GET", _net(""), prepare=_network),
    "network.links.read": Call("GET", _net("/links/{id}", "link-buys"), prepare=_network),
    "network.invites.create": Call("POST", _net("/invites"), {"as": "supplier"}, True, 201, prepare=_network),
    "network.invites.revoke": Call("DELETE", _net("/invites/{id}", "invite"), None, True, prepare=_network),
    # The code is shop D's (`_body`): shop B may present it as well as shop A, and names nothing of A's.
    "network.links.request": Call("POST", _net("/links"), None, True, 201, prepare=_network),
    "network.links.accept": Call("POST", _net("/links/{id}/accept", "link-asked"), None, True, prepare=_network),
    "network.links.decline": Call("POST", _net("/links/{id}/decline", "link-asked"), None, True, prepare=_network),
    "network.links.end": Call("POST", _net("/links/{id}/end", "link-buys"), None, True, prepare=_network),
    "network.links.attach": Call("PUT", _net("/links/{id}/counterpart", "link-sells"), None, True, prepare=_network),
    "network.drafts.list": Call("GET", _net("/drafts"), prepare=_network),
    "network.drafts.read": Call("GET", _net("/drafts/{id}", "draft"), prepare=_network),
    # The body names a link of shop A (`_body`): sent to shop B it is refused as no link of that shop.
    "network.drafts.create": Call("POST", _net("/drafts"), None, True, 201, prepare=_network),
    "network.drafts.update": Call("PUT", _net("/drafts/{id}", "draft"), None, True, prepare=_network),
    "network.drafts.delete": Call("DELETE", _net("/drafts/{id}", "draft"), None, True, prepare=_network),
    "network.orders.send": Call("POST", _net("/drafts/{id}/send", "draft"), None, True, prepare=_network),
    "network.orders.list": Call("GET", _net("/orders"), prepare=_network),
    "network.orders.read": Call("GET", _net("/orders/{id}", "order-sent"), prepare=_network),
    "network.orders.cancel": Call(
        "POST", _net("/orders/{id}/cancel", "order-sent"), {"reason": "Suite uchun"}, True, prepare=_network
    ),
    "network.orders.accept": Call(
        "POST",
        _net("/orders/{id}/accept", "order-incoming"),
        {"lines": [{"line_no": 1, "qty": "1", "unit_price": 5000}]},
        True,
        prepare=_network,
    ),
    "network.orders.decline": Call(
        "POST", _net("/orders/{id}/decline", "order-incoming"), {"reason": "Suite uchun"}, True, prepare=_network
    ),
    "network.notes.issue": Call(
        "POST", _net("/orders/{id}/deliver", "order-accepted"), None, True, 201, prepare=_network
    ),
    "network.notes.list": Call("GET", _net("/notes"), prepare=_network),
    "network.notes.read": Call("GET", _net("/notes/{id}", "note-incoming"), prepare=_network),
    "network.notes.correct": Call(
        "POST", _net("/notes/{id}/correct", "note-issued"), {"reason": "Suite uchun"}, True, 201, prepare=_network
    ),
    # The one call that writes two shops' books: a receipt in shop A and a credit sale in shop C.
    "network.notes.confirm": Call("POST", _net("/notes/{id}/confirm", "note-incoming"), None, True, prepare=_network),
    "network.notes.reject": Call(
        "POST", _net("/notes/{id}/reject", "note-incoming"), {"reason": "Suite uchun"}, True, prepare=_network
    ),
    "network.payments.list": Call("GET", _net("/payments"), prepare=_network),
    "network.payments.read": Call("GET", _net("/payments/{id}", "payment-theirs"), prepare=_network),
    "network.payments.record": Call("POST", _net("/payments"), None, True, 201, prepare=_network),
    "network.payments.confirm": Call(
        "POST", _net("/payments/{id}/confirm", "payment-theirs"), None, True, prepare=_network
    ),
    "network.payments.decline": Call(
        "POST", _net("/payments/{id}/decline", "payment-theirs"), {"reason": "Suite uchun"}, True, prepare=_network
    ),
    "network.payments.withdraw": Call(
        "POST", _net("/payments/{id}/withdraw", "payment-own"), None, True, prepare=_network
    ),
    # The stock, its documents and the suppliers are behind the platform switch `stock_on`; the suite turns
    # it on, so that the roles are told apart. With the switch off: tests/api/test_stock.py.
    "stock.settings.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/stock/settings", prepare=_stock),
    "stock.settings.update": Call(
        "PUT", lambda w, shop: f"/api/v1/shops/{shop}/stock/settings", {"refuse_negative": True}, True, prepare=_stock
    ),
    "stock.items.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/stock/items", prepare=_stock),
    "stock.items.read": Call(
        "GET", lambda w, shop: f"/api/v1/shops/{shop}/stock/items/{w.catalog_item_a}", prepare=_stock
    ),
    "stock.items.update": Call(
        "PATCH",
        lambda w, shop: f"/api/v1/shops/{shop}/stock/items/{w.catalog_item_a}",
        {"low_stock": "5"},
        True,
        prepare=_stock,
    ),
    "stock.lookup": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/stock/lookup?code=SUITE-1", prepare=_stock),
    "stock.movements.list": Call(
        "GET", lambda w, shop: f"/api/v1/shops/{shop}/stock/items/{w.catalog_item_a}/movements", prepare=_stock
    ),
    "stock.report": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/stock/report", prepare=_stock),
    "stock.documents.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/stock/documents", prepare=_stock),
    "stock.documents.read": Call(
        "GET", lambda w, shop: f"/api/v1/shops/{shop}/stock/documents/{_stock_document_id(w)}", prepare=_stock
    ),
    # The body names "Non" of shop A (`_body`): sent to shop B it is refused as no item of that shop.
    "stock.documents.create": Call(
        "POST", lambda w, shop: f"/api/v1/shops/{shop}/stock/documents", None, True, 201, prepare=_stock
    ),
    "stock.documents.update": Call(
        "PUT",
        lambda w, shop: f"/api/v1/shops/{shop}/stock/documents/{_stock_document_id(w)}",
        None,
        True,
        prepare=_stock,
    ),
    "stock.documents.post": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/stock/documents/{_stock_document_id(w)}/post",
        None,
        True,
        prepare=_stock,
    ),
    "stock.documents.cancel": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/stock/documents/{_stock_document_id(w)}/cancel",
        {"reason": "Suite uchun"},
        True,
        prepare=_stock,
    ),
    "suppliers.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/suppliers", prepare=_stock),
    "suppliers.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/suppliers/{_supplier_id(w)}", prepare=_stock),
    "suppliers.create": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/suppliers",
        {"name": "Yangi ta'minotchi"},
        True,
        201,
        prepare=_stock,
    ),
    "suppliers.update": Call(
        "PUT",
        lambda w, shop: f"/api/v1/shops/{shop}/suppliers/{_supplier_id(w)}",
        {"name": "Ulgurji bozor", "phone": "+998901234567"},
        True,
        prepare=_stock,
    ),
    "suppliers.archive": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/suppliers/{_supplier_id(w)}/archive",
        None,
        True,
        prepare=_stock_settled_supplier,
    ),
    "suppliers.unarchive": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/suppliers/{_supplier_id(w)}/unarchive",
        None,
        True,
        prepare=_stock_archived_supplier,
    ),
    "suppliers.entries.create": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/suppliers/{_supplier_id(w)}/entries",
        {"kind": "payment", "amount": 5000},
        True,
        201,
        prepare=_stock,
    ),
    "suppliers.entries.cancel": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/suppliers/{_supplier_id(w)}/entries/{_supplier_entry_id(w)}/cancel",
        {"reason": "Suite uchun"},
        True,
        prepare=_stock,
    ),
    "permissions.catalogue": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/permissions", prepare=_permissions_on),
    "permissions.mine": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/permissions/mine", prepare=_permissions_on),
    "permissions.member.read": Call(
        "GET",
        lambda w, shop: f"/api/v1/shops/{shop}/staff/{w.seller_a_membership}/permissions",
        prepare=_permissions_on,
    ),
    "permissions.member.set": Call(
        "PUT",
        lambda w, shop: f"/api/v1/shops/{shop}/staff/{w.seller_a_membership}/permissions",
        {"granted": ["reports.view"], "denied": ["payments.record"]},
        True,
        prepare=_permissions_on,
    ),
    "shop.support_access.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/support-access"),
    "shop.support_access.end": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/support-access/{_support_id(w)}/end",
        None,
        True,
        prepare=_open_support,
    ),
    "shop.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}"),
    "shop.update": Call("PATCH", lambda w, shop: f"/api/v1/shops/{shop}", {"name": "Renamed"}, changes_data=True),
    "staff.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/staff"),
    "staff.invite": Call(
        "POST", lambda w, shop: f"/api/v1/shops/{shop}/staff/invitations", {"role": "seller"}, True, ok_status=201
    ),
    "staff.invitations.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/staff/invitations"),
    "staff.invitations.cancel": Call(
        "DELETE", lambda w, shop: f"/api/v1/shops/{shop}/staff/invitations/{w.invitation_a}", None, True
    ),
    "staff.update": Call(
        "PATCH", lambda w, shop: f"/api/v1/shops/{shop}/staff/{w.seller_a_membership}", {"role": "manager"}, True
    ),
    "staff.remove": Call("DELETE", lambda w, shop: f"/api/v1/shops/{shop}/staff/{w.seller_a_membership}", None, True),
    "activity.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/activity"),
    "ownership.transfer.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/ownership-transfer"),
    "ownership.transfer.start": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/ownership-transfer",
        None,  # the body names a member of shop A; filled in by _invoke
        True,
        ok_status=201,
    ),
    "ownership.transfer.cancel": Call(
        "DELETE", lambda w, shop: f"/api/v1/shops/{shop}/ownership-transfer", None, True, prepare=_pending_transfer
    ),
    "ownership.transfer.accept": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/ownership-transfer/accept",
        None,
        True,
        prepare=_pending_transfer,
        refused=(("owner_a", 409, "NOT_TRANSFER_TARGET"),),
    ),
    "ownership.transfer.decline": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/ownership-transfer/decline",
        None,
        True,
        prepare=_pending_transfer,
        refused=(("owner_a", 409, "NOT_TRANSFER_TARGET"),),
    ),
    "customers.create": Call(
        "POST", lambda w, shop: f"/api/v1/shops/{shop}/customers", {"display_name": "Yangi mijoz"}, True, 201
    ),
    "customers.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/customers"),
    "customers.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/customers/{w.customer_a}"),
    "customers.update": Call(
        "PATCH", lambda w, shop: f"/api/v1/shops/{shop}/customers/{w.customer_a}", {"display_name": "Ali aka"}, True
    ),
    "customers.archive": Call(
        "POST", lambda w, shop: f"/api/v1/shops/{shop}/customers/{w.settled_customer_a}/archive", None, True
    ),
    "customers.unarchive": Call(
        "POST", lambda w, shop: f"/api/v1/shops/{shop}/customers/{w.archived_customer_a}/unarchive", None, True
    ),
    "ledger.entry.create": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/customers/{w.customer_a}/entries",
        {"kind": "credit", "amount": 45000},
        True,
        201,
    ),
    "ledger.entry.reverse": Call(
        "POST", lambda w, shop: f"/api/v1/shops/{shop}/entries/{w.entry_a}/reversal", None, True, 201
    ),
    "ledger.entry.promise.choose": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/entries/{w.entry_a}/promise-choice",
        None,  # the body is a date a few days from now; filled in by _body
        True,
    ),
    "customers.link.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/customers/{w.customer_a}/link"),
    "customers.link.create": Call(
        "POST", lambda w, shop: f"/api/v1/shops/{shop}/customers/{w.settled_customer_a}/link", None, True, 201
    ),
    # A customer's read-only link is behind the platform switch `customer_links_on`; the suite turns it
    # on, so that the roles are told apart. With the switch off: tests/api/test_customer_shares.py.
    "customers.share.read": Call(
        "GET", lambda w, shop: f"/api/v1/shops/{shop}/customers/{w.customer_a}/share", prepare=_links_on
    ),
    "customers.share.create": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/customers/{w.settled_customer_a}/share",
        None,
        True,
        201,
        prepare=_links_on,
    ),
    "customers.share.revoke": Call(
        "DELETE",
        lambda w, shop: f"/api/v1/shops/{shop}/customers/{w.customer_a}/share",
        None,
        True,
        prepare=_live_share,
    ),
    "shop.share_contact.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/share-contact", prepare=_links_on),
    "shop.share_contact.update": Call(
        "PUT",
        lambda w, shop: f"/api/v1/shops/{shop}/share-contact",
        {"phone": "+998901234567"},
        True,
        prepare=_links_on,
    ),
    # The cash book is behind the platform switch `cash_book_on`; the suite turns it on, so that the roles
    # are told apart. With the switch off: tests/api/test_cash_book.py.
    "cash.day": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/cash/day", prepare=_cash_book),
    "cash.summary": Call("GET", lambda w, shop: _cash_today(shop), prepare=_cash_book),
    "cash.entry.create": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/cash/entries",
        None,  # the body names a category of shop A; filled in by _body
        True,
        201,
        prepare=_cash_book,
    ),
    "cash.entry.cancel": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/cash/entries/{_cash_entry_id(w)}/cancellation",
        {"reason": "Ikki marta yozilgan"},
        True,
        201,
        prepare=_cash_book,
    ),
    "cash.categories.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/cash/categories", prepare=_cash_book),
    "cash.categories.create": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/cash/categories",
        {"direction": "expense", "name": "Soliq"},
        True,
        201,
        prepare=_cash_book,
    ),
    "cash.categories.update": Call(
        "PATCH",
        lambda w, shop: f"/api/v1/shops/{shop}/cash/categories/{_cash_category_id(w)}",
        {"name": "Do'kon ijarasi", "archived": True},
        True,
        prepare=_cash_book,
    ),
    "cash.categories.delete": Call(
        "DELETE",
        lambda w, shop: f"/api/v1/shops/{shop}/cash/categories/{_cash_spare_category_id(w)}",
        None,
        True,
        prepare=_cash_book,
    ),
    "cash.backfill": Call("POST", lambda w, shop: f"/api/v1/shops/{shop}/cash/backfill", {}, True, prepare=_cash_book),
    "counter_code.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/counter-code"),
    "counter_code.rotate": Call("POST", lambda w, shop: f"/api/v1/shops/{shop}/counter-code", None, True, 201),
    "waiting.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/waiting"),
    "waiting.attach": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/waiting/{w.waiting_a}/attach",
        None,  # the body names a customer of shop A; filled in by _body
        True,
    ),
    "waiting.dismiss": Call("POST", lambda w, shop: f"/api/v1/shops/{shop}/waiting/{w.waiting_a}/dismiss", None, True),
    "disputes.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/disputes"),
    "disputes.decline": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/disputes/{_dispute_id(w)}/decline",
        {"reason": "Mahsulot berilgan"},
        True,
        prepare=_open_dispute,
    ),
    "exports.request": Call("POST", lambda w, shop: f"/api/v1/shops/{shop}/exports", None, True, 201),
    "exports.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/exports"),
    "exports.download": Call(
        "GET", lambda w, shop: f"/api/v1/shops/{shop}/exports/{_export_job_id(w)}/download", prepare=_finished_export
    ),
    "payment_notices.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/payment-notices"),
    "payment_notices.accept": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/payment-notices/{_notice_id(w)}/accept",
        None,
        True,
        prepare=_sent_notice,
    ),
    "payment_notices.decline": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/payment-notices/{_notice_id(w)}/decline",
        {"reason": "Pul kelib tushmagan"},
        True,
        prepare=_sent_notice,
    ),
    "payment_notices.receipt": Call(
        "GET", lambda w, shop: f"/api/v1/shops/{shop}/payment-notices/{_notice_id(w)}/receipt", prepare=_sent_notice
    ),
    "imports.template": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/imports/template"),
    "imports.upload": Call(
        "POST", lambda w, shop: f"/api/v1/shops/{shop}/imports", None, True, 201, content=SUITE_IMPORT
    ),
    "imports.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/imports", prepare=_validated_import),
    "imports.read": Call(
        "GET", lambda w, shop: f"/api/v1/shops/{shop}/imports/{_import_id(w)}", prepare=_validated_import
    ),
    "imports.apply": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/imports/{_import_id(w)}/apply",
        {"plan": SUITE_PLAN},  # the plan of the preview the batch was given
        True,
        202,  # the worker applies; the request only records that it is asked
        prepare=_validated_import,
    ),
    "imports.undo": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/imports/{_import_id(w)}/undo",
        None,
        True,
        202,
        prepare=_applied_import,
    ),
    "imports.discard": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/imports/{_import_id(w)}/discard",
        None,
        True,
        prepare=_validated_import,
    ),
    "date_requests.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/date-requests"),
    "date_requests.accept": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/date-requests/{_date_request_id(w)}/accept",
        None,
        True,
        prepare=_open_date_request,
    ),
    "date_requests.decline": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/date-requests/{_date_request_id(w)}/decline",
        {"reason": "Muddat allaqachon uzaytirilgan"},
        True,
        prepare=_open_date_request,
    ),
    "ledger.entry.promise.change": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/entries/{w.entry_a}/promise",
        None,  # the body is a date a few days from now; filled in by _body
        True,
    ),
    "ledger.entry.lines.add": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/entries/{w.entry_a}/lines",
        {"lines": [{"name": "Guruch", "qty": "2", "unit": "kg", "unit_price": 25000}]},
        True,
        201,
    ),
    "reminders.settings.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/reminders"),
    "reminders.settings.update": Call("PATCH", lambda w, shop: f"/api/v1/shops/{shop}/reminders", {"hour": 12}, True),
    "reminders.send": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/reminders/manual",
        None,  # the body names a customer of shop A; filled in by _body
        True,
        201,
        prepare=_reminders_due,
    ),
    "reminders.unreachable": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/reminders/unreachable"),
    "shop.credit.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/credit-settings"),
    "shop.credit.update": Call(
        "PATCH", lambda w, shop: f"/api/v1/shops/{shop}/credit-settings", {"sellers_may_exceed": False}, True
    ),
    "shop.subscription.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/subscription"),
    # Online payment is switched off (ADR-019): the one caller whose role allows it is told so, and the
    # suite checks that nothing was ordered. With the switch on: tests/api/test_online_payment.py.
    "shop.subscription.online_order.create": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/subscription/online-orders",
        {"months": 1},
        True,
        201,
        refused=(("owner_a", 409, "ONLINE_PAY_OFF"),),
    ),
    "shop.deletion.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/deletion"),
    "shop.deletion.request": Call(
        "POST", lambda w, shop: f"/api/v1/shops/{shop}/deletion", {"confirm_name": "Shop A"}, True, 201
    ),
    "shop.deletion.cancel": Call(
        "DELETE", lambda w, shop: f"/api/v1/shops/{shop}/deletion", None, True, prepare=_deletion_pending
    ),
    "shop.subscription.receipts.submit": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/subscription/receipts",
        None,
        True,
        201,
        form=({"amount": "100000", "months": "1"}, {"receipt": ("chek.jpg", JPEG, "image/jpeg")}),
    ),
    "shop.subscription.receipts.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/subscription/receipts"),
    "overview.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/overview"),
    "overview.debtors": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/overview/debtors"),
    "reports.period": Call("GET", lambda w, shop: _last_week(shop)),
    "reports.overdue": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/reports/overdue"),
    "catalog.list": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/catalog"),
    "catalog.create": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/catalog",
        {"name": "Shakar", "unit": "kg", "price": 14000},
        True,
        201,
    ),
    "catalog.update": Call(
        "PATCH", lambda w, shop: f"/api/v1/shops/{shop}/catalog/{w.catalog_item_a}", {"price": 4500}, True
    ),
    "catalog.hide": Call("POST", lambda w, shop: f"/api/v1/shops/{shop}/catalog/{w.catalog_item_a}/hide", None, True),
    "catalog.unhide": Call(
        "POST", lambda w, shop: f"/api/v1/shops/{shop}/catalog/{w.catalog_item_a}/unhide", None, True
    ),
    "catalog.learned.accept": Call(
        "POST", lambda w, shop: f"/api/v1/shops/{shop}/catalog/{w.learned_item_a}/accept", None, True
    ),
    "catalog.learned.dismiss": Call(
        "POST", lambda w, shop: f"/api/v1/shops/{shop}/catalog/{w.learned_item_a}/dismiss", None, True
    ),
    "catalog.learned.merge": Call(
        "POST",
        lambda w, shop: f"/api/v1/shops/{shop}/catalog/{w.learned_item_a}/merge",
        None,  # the body names an item of shop A; filled in by _body
        True,
    ),
}

# Written by hand from REQ-033 and the specification's authorization table; deliberately not derived
# from the code under test.
ALLOWED_ROLES: dict[str, set[Role]] = {
    # The network between shops: connecting to another shop is the owner's; the rest is the managers'.
    **{
        name: {Role.OWNER}
        for name in (
            "network.invites.create",
            "network.invites.revoke",
            "network.links.request",
            "network.links.accept",
            "network.links.decline",
            "network.links.end",
            "network.links.attach",
        )
    },
    **{
        name: {Role.MANAGER, Role.OWNER}
        for name in (
            "network.overview",
            "network.links.read",
            "network.drafts.list",
            "network.drafts.read",
            "network.drafts.create",
            "network.drafts.update",
            "network.drafts.delete",
            "network.orders.send",
            "network.orders.list",
            "network.orders.read",
            "network.orders.cancel",
            "network.orders.accept",
            "network.orders.decline",
            "network.notes.issue",
            "network.notes.list",
            "network.notes.read",
            "network.notes.correct",
            "network.notes.confirm",
            "network.notes.reject",
            "network.payments.list",
            "network.payments.read",
            "network.payments.record",
            "network.payments.confirm",
            "network.payments.decline",
            "network.payments.withdraw",
        )
    },
    # Who may look at the shop's data is the owner's to see and to stop (REQ-059).
    "shop.support_access.list": {Role.OWNER},
    "shop.support_access.end": {Role.OWNER},
    "shop.read": {Role.MANAGER, Role.OWNER},
    "shop.update": {Role.OWNER},
    "staff.list": {Role.OWNER},
    "staff.invite": {Role.OWNER},
    "staff.invitations.list": {Role.OWNER},
    "staff.invitations.cancel": {Role.OWNER},
    "staff.update": {Role.OWNER},
    "staff.remove": {Role.OWNER},
    "activity.list": {Role.OWNER},
    "ownership.transfer.read": {Role.MANAGER, Role.OWNER},
    "ownership.transfer.start": {Role.OWNER},
    "ownership.transfer.cancel": {Role.OWNER},
    "ownership.transfer.accept": {Role.MANAGER, Role.OWNER},
    "ownership.transfer.decline": {Role.MANAGER, Role.OWNER},
    "customers.create": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "customers.list": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "customers.read": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "customers.update": {Role.MANAGER, Role.OWNER},
    "customers.archive": {Role.MANAGER, Role.OWNER},
    "customers.unarchive": {Role.MANAGER, Role.OWNER},
    "ledger.entry.create": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "ledger.entry.reverse": {Role.MANAGER, Role.OWNER},
    # Any staff member by role; within the operation only the entry's author or a manager (REQ-008).
    "ledger.entry.promise.choose": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "customers.link.read": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "customers.link.create": {Role.SELLER, Role.MANAGER, Role.OWNER},
    # A read-only link keeps showing a customer's balance to whoever holds it: not for a seller to make,
    # to end, or to look up.
    "customers.share.read": {Role.MANAGER, Role.OWNER},
    "customers.share.create": {Role.MANAGER, Role.OWNER},
    "customers.share.revoke": {Role.MANAGER, Role.OWNER},
    "shop.share_contact.read": {Role.MANAGER, Role.OWNER},
    "shop.share_contact.update": {Role.OWNER},
    # The cash book is the shop's money: read, written and arranged by managers and the owner
    # (qarz.application.cash_book). Copying the ledger's past into it is the owner's decision alone.
    "cash.day": {Role.MANAGER, Role.OWNER},
    "cash.summary": {Role.MANAGER, Role.OWNER},
    "cash.entry.create": {Role.MANAGER, Role.OWNER},
    "cash.entry.cancel": {Role.MANAGER, Role.OWNER},
    "cash.categories.list": {Role.MANAGER, Role.OWNER},
    "cash.categories.create": {Role.MANAGER, Role.OWNER},
    "cash.categories.update": {Role.MANAGER, Role.OWNER},
    "cash.categories.delete": {Role.MANAGER, Role.OWNER},
    "cash.backfill": {Role.OWNER},
    "counter_code.read": {Role.SELLER, Role.MANAGER, Role.OWNER},
    # The code is printed and hangs at the counter; replacing it invalidates the print.
    "counter_code.rotate": {Role.MANAGER, Role.OWNER},
    "waiting.list": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "waiting.attach": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "waiting.dismiss": {Role.SELLER, Role.MANAGER, Role.OWNER},
    # Flagged to the owner and managers (REQ-017); a seller sees only that an entry is disputed.
    "disputes.list": {Role.MANAGER, Role.OWNER},
    "disputes.decline": {Role.MANAGER, Role.OWNER},
    # Specification, resources table: "payment notices: all staff"; role matrix, "Accept or decline a
    # payment notice": seller, manager, owner. The receipt is what they decide on.
    # REQ-028 "an owner or manager can export"; specification, resources table: "reports and exports:
    # manager, owner"; role matrix: exports in the manager's row.
    "exports.request": {Role.MANAGER, Role.OWNER},
    "exports.list": {Role.MANAGER, Role.OWNER},
    "exports.download": {Role.MANAGER, Role.OWNER},
    "payment_notices.list": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "payment_notices.accept": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "payment_notices.decline": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "payment_notices.receipt": {Role.SELLER, Role.MANAGER, Role.OWNER},
    # Specification, resources table: date requests are listed and decided by "manager, owner", and
    # `/shops/{id}/entries/{eid}/promise` is "Manager, owner" (REQ-067).
    "date_requests.list": {Role.MANAGER, Role.OWNER},
    "date_requests.accept": {Role.MANAGER, Role.OWNER},
    "date_requests.decline": {Role.MANAGER, Role.OWNER},
    # REQ-062: "An owner or manager can import"; specification, resources table: `/shops/{id}/imports`,
    # "Template, upload, preview, apply, undo", "Manager, owner".
    "imports.template": {Role.MANAGER, Role.OWNER},
    "imports.upload": {Role.MANAGER, Role.OWNER},
    "imports.list": {Role.MANAGER, Role.OWNER},
    "imports.read": {Role.MANAGER, Role.OWNER},
    "imports.apply": {Role.MANAGER, Role.OWNER},
    "imports.undo": {Role.MANAGER, Role.OWNER},
    "imports.discard": {Role.MANAGER, Role.OWNER},
    "ledger.entry.promise.change": {Role.MANAGER, Role.OWNER},
    # Specification, resources table: "Author, manager, owner". Any staff member by role; within the
    # operation only the entry's author or a manager (REQ-038).
    "ledger.entry.lines.add": {Role.SELLER, Role.MANAGER, Role.OWNER},
    # Specification, resources table: reminders are for managers and owners.
    "reminders.settings.read": {Role.MANAGER, Role.OWNER},
    "reminders.settings.update": {Role.MANAGER, Role.OWNER},
    "reminders.send": {Role.MANAGER, Role.OWNER},
    "reminders.unreachable": {Role.MANAGER, Role.OWNER},
    # A seller must know the rule they sell under; an owner or manager sets it (REQ-044).
    "shop.credit.read": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "shop.credit.update": {Role.MANAGER, Role.OWNER},
    # Specification, authorization table: the subscription is the owner's.
    "shop.subscription.read": {Role.OWNER},
    # Paying is the owner's, like the subscription it pays for.
    "shop.subscription.online_order.create": {Role.OWNER},
    # Specification, resources table: request deletion, cancel deletion: owner.
    "shop.deletion.read": {Role.OWNER},
    "shop.deletion.request": {Role.OWNER},
    "shop.deletion.cancel": {Role.OWNER},
    # Specification, resources table: the subscription, its receipts and their outcomes are the owner's.
    "shop.subscription.receipts.submit": {Role.OWNER},
    "shop.subscription.receipts.list": {Role.OWNER},
    # Expansion decision 10: the owner sets each member's permissions; nobody else reads or changes them.
    "permissions.catalogue": {Role.OWNER},
    "permissions.member.read": {Role.OWNER},
    "permissions.member.set": {Role.OWNER},
    # Every member learns what they themselves may do; a client uses it to decide what to offer.
    "permissions.mine": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "overview.read": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "overview.debtors": {Role.SELLER, Role.MANAGER, Role.OWNER},
    # Specification, resources table: "reports and exports: manager, owner" (REQ-046).
    "reports.period": {Role.MANAGER, Role.OWNER},
    "reports.overdue": {Role.MANAGER, Role.OWNER},
    # Specification, resources table: "Manager, owner; sellers read".
    "catalog.list": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "catalog.create": {Role.MANAGER, Role.OWNER},
    "catalog.update": {Role.MANAGER, Role.OWNER},
    "catalog.hide": {Role.MANAGER, Role.OWNER},
    "catalog.unhide": {Role.MANAGER, Role.OWNER},
    "catalog.learned.accept": {Role.MANAGER, Role.OWNER},
    "catalog.learned.dismiss": {Role.MANAGER, Role.OWNER},
    "catalog.learned.merge": {Role.MANAGER, Role.OWNER},
    # The stock (expansion module I): what is on hand is every member's to see; the rest is a manager's.
    "stock.settings.read": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "stock.items.list": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "stock.items.read": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "stock.lookup": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "stock.movements.list": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "stock.settings.update": {Role.MANAGER, Role.OWNER},
    "stock.items.update": {Role.MANAGER, Role.OWNER},
    "stock.report": {Role.MANAGER, Role.OWNER},
    "stock.documents.list": {Role.MANAGER, Role.OWNER},
    "stock.documents.read": {Role.MANAGER, Role.OWNER},
    "stock.documents.create": {Role.MANAGER, Role.OWNER},
    "stock.documents.update": {Role.MANAGER, Role.OWNER},
    "stock.documents.post": {Role.MANAGER, Role.OWNER},
    "stock.documents.cancel": {Role.MANAGER, Role.OWNER},
    "suppliers.list": {Role.MANAGER, Role.OWNER},
    "suppliers.read": {Role.MANAGER, Role.OWNER},
    "suppliers.create": {Role.MANAGER, Role.OWNER},
    "suppliers.update": {Role.MANAGER, Role.OWNER},
    "suppliers.archive": {Role.MANAGER, Role.OWNER},
    "suppliers.unarchive": {Role.MANAGER, Role.OWNER},
    "suppliers.entries.create": {Role.MANAGER, Role.OWNER},
    "suppliers.entries.cancel": {Role.MANAGER, Role.OWNER},
}

SELF_CALLS: dict[str, PlainCall] = {
    "me.read": PlainCall("GET", "/api/v1/me"),
    "me.update": PlainCall("PATCH", "/api/v1/me", {"lang": "ru"}),
    "auth.sign_out": PlainCall("POST", "/api/v1/auth/sign-out", ok_status=204),
    "auth.sign_out_everywhere": PlainCall("POST", "/api/v1/auth/sign-out-everywhere", ok_status=204),
    "shop.create": PlainCall(
        "POST", "/api/v1/shops", {"name": "My shop", "lang": "uz"}, 201, needs_key=True, returns_own_id=False
    ),
    # An unknown token: for any signed-in user the invitation simply does not exist.
    "staff.invitations.accept": PlainCall(
        "POST", "/api/v1/staff-invitations/accept", {"token": "unknown-token-0123456789abcdef"}, 404
    ),
    "me.shops.list": PlainCall("GET", "/api/v1/me/shops", returns_own_id=False),
    "me.accounts.list": PlainCall("GET", "/api/v1/me/accounts", returns_own_id=False),
    # A link nobody holds: for any signed-in user it does not exist.
    "me.accounts.read": PlainCall("GET", "/api/v1/me/accounts/00000000-0000-4000-8000-000000000000", ok_status=404),
    "me.accounts.disconnect": PlainCall(
        "POST", "/api/v1/me/accounts/00000000-0000-4000-8000-000000000000/disconnect", ok_status=404
    ),
    "me.accounts.removal": PlainCall(
        "POST", "/api/v1/me/accounts/00000000-0000-4000-8000-000000000000/removal", ok_status=404
    ),
    "me.owner_totals": PlainCall("GET", "/api/v1/me/owner-totals", returns_own_id=False),
    "me.accounts.disputes.open": PlainCall(
        "POST",
        "/api/v1/me/accounts/00000000-0000-4000-8000-000000000000/disputes",
        {"entry_id": "00000000-0000-4000-8000-000000000000", "reason": "Men olmaganman"},
        404,
    ),
    "me.accounts.disputes.withdraw": PlainCall(
        "POST",
        "/api/v1/me/accounts/00000000-0000-4000-8000-000000000000/disputes/00000000-0000-4000-8000-000000000000/withdraw",
        ok_status=404,
    ),
    "me.accounts.payment_notices.send": PlainCall(
        "POST",
        "/api/v1/me/accounts/00000000-0000-4000-8000-000000000000/payment-notices",
        {"amount": 20000},
        404,
    ),
    "me.accounts.date_requests.open": PlainCall(
        "POST",
        "/api/v1/me/accounts/00000000-0000-4000-8000-000000000000/date-requests",
        {"entry_id": "00000000-0000-4000-8000-000000000000", "requested_date": "2030-01-15"},
        404,
    ),
    # A shop nobody is a member of: for any signed-in user it does not exist.
    "me.active_shop.set": PlainCall(
        "PUT", "/api/v1/me/active-shop", {"shop_id": "00000000-0000-4000-8000-000000000000"}, 404
    ),
}

# Requests that are well formed but not signed by Telegram.
PUBLIC_CALLS: dict[str, PlainCall] = {
    "auth.telegram_webapp": PlainCall("POST", "/api/v1/auth/telegram-webapp", {"init_data": "user=%7B%7D&hash=00"}),
    "auth.telegram_login": PlainCall("POST", "/api/v1/auth/telegram-login", {"id": 1, "auth_date": 1, "hash": "00"}),
}

# Operations opened by a secret link: the route, and the header that carries the secret.
LINK_CALLS: dict[str, tuple[str, str, str]] = {
    "customer_share.view": ("GET", "/api/v1/customer-share", "X-Share-Token"),
}

BY_SCOPE = {
    scope: sorted(op.name for op in all_operations() if op.scope == scope) for scope in ("shop", "self", "public")
}
SHOP_OPS, SELF_OPS, PUBLIC_OPS = BY_SCOPE["shop"], BY_SCOPE["self"], BY_SCOPE["public"]
LINK_OPS = sorted(op.name for op in all_operations() if op.scope == "link")
STAFF = [("owner_a", Role.OWNER), ("manager_a", Role.MANAGER), ("seller_a", Role.SELLER)]
OUTSIDERS = ["suspended_a", "owner_b", "customer_of_a", "admin", "stranger"]
NO_CREDENTIALS = [{}, {"X-Test-User": "not-a-uuid"}]


def _key() -> dict[str, str]:
    return {"Idempotency-Key": f"suite-{uuid.uuid4().hex}"}


def _body(world: World, op_name: str, call: Call) -> dict[str, Any] | None:
    if op_name == "ownership.transfer.start":
        return {"membership_id": str(world.manager_a_membership)}
    if op_name == "reminders.send":
        return {"customer_id": str(world.customer_a)}
    if op_name == "waiting.attach":
        return {"customer_id": str(world.settled_customer_a)}
    if op_name == "ledger.entry.promise.change":
        return {"promised_date": (datetime.now(UTC).date() + timedelta(days=3)).isoformat(), "reason": "Kelishildi"}
    if op_name == "ledger.entry.promise.choose":
        return {"promised_date": (datetime.now(UTC).date() + timedelta(days=3)).isoformat()}
    if op_name == "catalog.learned.merge":
        return {"into": str(world.catalog_item_a)}
    if op_name in ("stock.documents.create", "stock.documents.update"):
        return _stocktake(world)
    if op_name == "network.links.request":
        return {"code": _net_code(world), "as": "buyer"}
    if op_name == "network.links.attach":
        return {"counterpart_id": str(world.settled_customer_a)}
    if op_name in ("network.drafts.create", "network.drafts.update"):
        line = {"name": "Un", "unit": "kg", "qty": "5"}
        return {"link_id": str(_net_id(world, "link-buys")), "lines": [line]}
    if op_name == "network.payments.record":
        return {"link_id": str(_net_id(world, "link-buys")), "amount": 10000}
    if op_name == "cash.entry.create":
        return {"direction": "expense", "method": "cash", "amount": 50000, "category_id": str(_cash_category_id(world))}
    return call.json


def _invoke(client: TestClient, world: World, call: Call, shop: uuid.UUID, headers: dict[str, str]) -> Any:
    if call.changes_data:
        headers = {**headers, **_key()}
    op_name = next(name for name, candidate in CALLS.items() if candidate is call)
    if call.content is not None:
        return client.request(call.method, call.path(world, shop), content=call.content, headers=headers)
    if call.form is not None:
        return client.request(
            call.method, call.path(world, shop), data=call.form[0], files=call.form[1], headers=headers
        )
    return client.request(call.method, call.path(world, shop), json=_body(world, op_name, call), headers=headers)


def _prepare(owner: psycopg.Connection, world: World, call: Call) -> None:
    if call.prepare is not None:
        call.prepare(owner, world)


def _snapshot(owner: psycopg.Connection, shop: uuid.UUID, *, shared: bool = True) -> tuple[Any, ...]:
    """Everything a refused call could have changed in a shop.

    Two of the things looked at belong to no shop: the total of measurement rows and the objects of the
    file store. `shared=False` leaves them out, for a call that rightly went through in another shop.
    """
    return (
        owner.execute(
            "SELECT name, lang, default_promise_days, status, deletion_due FROM shop WHERE id = %s", (shop,)
        ).fetchone(),
        owner.execute(
            "SELECT reminders_on, reminder_hour, reminder_tpl, sms_on, default_credit_limit, sellers_may_exceed "
            "FROM shop WHERE id = %s",
            (shop,),
        ).fetchone(),
        owner.execute("SELECT count(*) FROM reminder WHERE shop_id = %s", (shop,)).fetchone(),
        owner.execute(
            "SELECT id, user_id, role, status, permissions_granted, permissions_denied FROM membership "
            "WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT token_hash, status, role FROM invitation WHERE shop_id = %s ORDER BY token_hash", (shop,)
        ).fetchall(),
        owner.execute("SELECT count(*) FROM activity WHERE shop_id = %s", (shop,)).fetchone(),
        owner.execute("SELECT count(*) FROM request_key WHERE shop_id = %s", (shop,)).fetchone(),
        owner.execute(
            "SELECT id, status, from_membership, to_membership FROM ownership_transfer WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT id, display_name, name_norm, phone, status, reminders_off, credit_limit FROM customer "
            "WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT id, customer_id, seq, kind, amount, reverses_id FROM ledger_entry WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute("SELECT count(*) FROM promise WHERE shop_id = %s", (shop,)).fetchone(),
        owner.execute("SELECT count(*) FROM chat_pending").fetchone(),
        owner.execute(
            "SELECT id, customer_id, user_id, status, waiting_name FROM customer_link WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT id, customer_id, token_hash, created_by, expires_at, revoked_at, last_opened_at, opened_on "
            "FROM customer_share WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute("SELECT share_phone FROM shop WHERE id = %s", (shop,)).fetchone(),
        owner.execute(
            "SELECT id, direction, name, name_norm, system_key, archived_at FROM cash_category "
            "WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT id, direction, method, currency, amount, category_id, note, day, author_id, ledger_entry_id, "
            "cancelled_at, cancelled_by, cancel_reason FROM cash_entry WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute("SELECT count(*) FROM outbox_message WHERE shop_id = %s", (shop,)).fetchone(),
        owner.execute("SELECT id, status FROM removal_request WHERE shop_id = %s ORDER BY id", (shop,)).fetchall(),
        owner.execute(
            "SELECT id, status, decline_reason FROM dispute WHERE shop_id = %s ORDER BY id", (shop,)
        ).fetchall(),
        owner.execute(
            "SELECT id, entry_id, requested_date, reason, status, decline_reason, decided_by, closed_at "
            "FROM date_change_request WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT id, name, name_norm, unit, price, learned, status, merged_into FROM catalog_item "
            "WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT entry_id, line_no, catalog_item_id, name, qty, unit, unit_price, line_total FROM goods_line "
            "WHERE shop_id = %s ORDER BY entry_id, line_no",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT id, state, provider, provider_txn, months, amount FROM online_payment "
            "WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT state, trial_ends, paid_through FROM subscription WHERE shop_id = %s", (shop,)
        ).fetchone(),
        # Measurement rows carry no shop identifier; tests run one at a time, so a total is enough.
        owner.execute("SELECT count(*) FROM measure.event").fetchone() if shared else None,
        owner.execute(
            "SELECT id, admin_id, reason, starts_at, ends_at, closed_at, closed_by FROM support_access "
            "WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT id, customer_id, amount, file_id, status, payment_entry, decline_reason, decided_by, closed_at "
            "FROM payment_notice WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT id, purpose, object_key, sha256, size_bytes, mime, delete_after FROM stored_file "
            "WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT id, status, file_id, summary::text, author_id, applied_at, plan, preview::text, step_by, "
            "queued_at, started_at, attempts FROM import_batch WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT id, requested_by, status, file_id, error, attempts FROM export_job WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT id, file_id, stated_amount, stated_months, status, months, reject_reason, decided_by "
            "FROM subscription_receipt WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        # The stock, its documents and the suppliers (migration 0043).
        owner.execute(
            "SELECT id, tracked, low_stock, unit FROM catalog_item WHERE shop_id = %s ORDER BY id", (shop,)
        ).fetchall(),
        owner.execute("SELECT stock_refuse_negative FROM shop WHERE id = %s", (shop,)).fetchone(),
        owner.execute("SELECT code, item_id FROM catalog_barcode WHERE shop_id = %s ORDER BY code", (shop,)).fetchall(),
        owner.execute(
            "SELECT id, name, phone, note, status FROM supplier WHERE shop_id = %s ORDER BY id", (shop,)
        ).fetchall(),
        owner.execute(
            "SELECT id, supplier_id, seq, kind, amount, reverses_id FROM supplier_entry WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute(
            "SELECT id, kind, number, status, total, paid, draft::text FROM stock_document "
            "WHERE shop_id = %s ORDER BY id",
            (shop,),
        ).fetchall(),
        owner.execute("SELECT count(*) FROM stock_document_line WHERE shop_id = %s", (shop,)).fetchone(),
        owner.execute("SELECT count(*) FROM stock_movement WHERE shop_id = %s", (shop,)).fetchone(),
        owner.execute(
            "SELECT item_id, on_hand, cost_value FROM stock_level WHERE shop_id = %s ORDER BY item_id", (shop,)
        ).fetchall(),
        # The objects of the file store itself: a refused call writes and removes none. A file that another
        # shop has recorded as its own is that shop's; one that nobody recorded is counted here.
        _objects_not_of_other_shops(owner, shop) if shared else None,
    )


def _objects_not_of_other_shops(owner: psycopg.Connection, shop: uuid.UUID) -> list[str]:
    root = current_file_root()
    elsewhere = {row[0] for row in owner.execute("SELECT object_key FROM stored_file WHERE shop_id <> %s", (shop,))}
    found = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
    return [key for key in found if key not in elsewhere]


# --- the suite covers everything --------------------------------------------------------------------


def test_every_operation_is_described_in_the_suite() -> None:
    assert set(CALLS) == set(SHOP_OPS), "add the new shop operation to CALLS"
    assert set(ALLOWED_ROLES) == set(SHOP_OPS), "add the new shop operation to ALLOWED_ROLES"
    assert set(SELF_CALLS) == set(SELF_OPS), "add the new self operation to SELF_CALLS"
    assert set(PUBLIC_CALLS) == set(PUBLIC_OPS), "add the new public operation to PUBLIC_CALLS"
    assert set(LINK_CALLS) == set(LINK_OPS), "add the new link operation to LINK_CALLS"
    assert SHOP_OPS and SELF_OPS and PUBLIC_OPS and LINK_OPS
    scopes = {op.scope for op in all_operations()}
    assert scopes == {"shop", "self", "public", "link", "admin", "admin_entry"}, "a new scope needs its own checks"


def test_every_api_route_is_a_registered_operation(client: TestClient) -> None:
    api_routes = [r for r in client.app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/")]  # type: ignore[attr-defined]
    names = sorted(route.name for route in api_routes)
    assert names == sorted(op.name for op in all_operations()), (
        "every /api/ route must be bound to exactly one registered operation"
    )


def test_every_write_route_is_marked_as_changing_data(client: TestClient) -> None:
    """So that the "a refused call must change nothing" checks are not skipped for a new write."""
    for route in client.app.routes:  # type: ignore[attr-defined]
        if isinstance(route, APIRoute) and route.name in CALLS:
            writes = bool(route.methods - {"GET", "HEAD"})
            assert CALLS[route.name].changes_data is writes, route.name


def test_the_code_agrees_with_the_hand_written_table() -> None:
    for op in all_operations():
        if op.scope != "shop":
            assert op.capability is None
            continue
        assert op.capability is not None
        assert lowest_role_with(op.capability) == min(ALLOWED_ROLES[op.name], key=ROLE_ORDER.index)


# --- shop operations: staff are allowed or refused by role ------------------------------------------


@pytest.mark.parametrize("op_name", SHOP_OPS)
@pytest.mark.parametrize(("caller", "role"), STAFF)
def test_staff_are_allowed_or_refused_by_role(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, caller: str, role: Role
) -> None:
    call = CALLS[op_name]
    _prepare(owner, world, call)
    before = _snapshot(owner, world.shop_a)
    response = _invoke(client, world, call, world.shop_a, as_user(getattr(world, caller)))

    stated = {who: (status, code) for who, status, code in call.refused}
    if caller in stated:
        assert (response.status_code, response.json()["error"]["code"]) == stated[caller], response.text
        assert _snapshot(owner, world.shop_a) == before, "a refused call must change nothing"
    elif role in ALLOWED_ROLES[op_name]:
        assert response.status_code == call.ok_status, response.text
    else:
        assert response.status_code == 403, response.text
        error = response.json()["error"]
        if call.prepare is _permissions_on:
            # The switch is on for these three: the refusal names the permission, not a role.
            assert (error["code"], error["fields"]) == ("FORBIDDEN_PERMISSION", {"permission": "permissions.manage"})
        else:
            assert error["code"] == "FORBIDDEN_ROLE"
            assert error["fields"] == {"needed_role": min(ALLOWED_ROLES[op_name], key=ROLE_ORDER.index).value}
        assert _snapshot(owner, world.shop_a) == before, "a refused call must change nothing"


# --- shop operations: for everyone else the shop does not exist ---------------------------------------


@pytest.mark.parametrize("op_name", SHOP_OPS)
@pytest.mark.parametrize("caller", OUTSIDERS)
def test_outsiders_get_not_found_and_change_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, caller: str
) -> None:
    call = CALLS[op_name]
    _prepare(owner, world, call)
    before = _snapshot(owner, world.shop_a)
    response = _invoke(client, world, call, world.shop_a, as_user(getattr(world, caller)))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.json()["error"]["fields"] == {}
    assert _snapshot(owner, world.shop_a) == before


@pytest.mark.parametrize("op_name", SHOP_OPS)
def test_a_member_of_one_shop_cannot_reach_another(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str
) -> None:
    """owner_b, the most privileged caller in shop B, addresses shop A; then shop B using A's resources."""
    call = CALLS[op_name]
    _prepare(owner, world, call)
    before_a, before_b = _snapshot(owner, world.shop_a), _snapshot(owner, world.shop_b)
    rows_of_a = _snapshot(owner, world.shop_a, shared=False)

    into_a = _invoke(client, world, call, world.shop_a, as_user(world.owner_b))
    assert into_a.status_code == 404, into_a.text
    assert _snapshot(owner, world.shop_a) == before_a, "the call into shop A changed nothing anywhere"

    # Through their own shop, naming a member, invitation, customer or entry that belongs to shop A.
    through_b = _invoke(client, world, call, world.shop_b, as_user(world.owner_b))
    if call.path(world, world.shop_b) != call.path(world, world.shop_a).replace(str(world.shop_a), str(world.shop_b)):
        raise AssertionError("the path must differ only by the shop identifier")
    foreign = (
        world.seller_a_membership,
        world.invitation_a,
        world.customer_a,
        world.settled_customer_a,
        world.archived_customer_a,
        world.entry_a,
        world.catalog_item_a,
        world.learned_item_a,
        world.waiting_a,
        _dispute_id(world),
        _notice_id(world),
        _export_job_id(world),
        _date_request_id(world),
        _import_id(world),
        _supplier_id(world),
        _stock_document_id(world),
        _supplier_entry_id(world),
        _cash_category_id(world),
        _cash_spare_category_id(world),
        _cash_entry_id(world),
        *(_net_id(world, thing) for thing in NETWORK_THINGS),
    )
    uses_foreign_resource = any(str(resource) in call.path(world, world.shop_b) for resource in foreign)
    if uses_foreign_resource:
        assert through_b.status_code == 404, through_b.text
        assert _snapshot(owner, world.shop_b)[:3] == before_b[:3], "shop B's own data must be untouched"
    # A call that needs nothing of shop A goes through in shop B, as it should. What it adds to the things
    # no shop owns (a measurement row, a stored object) is not a change to shop A.
    own_shop_only = not uses_foreign_resource and through_b.status_code < 300
    assert _snapshot(owner, world.shop_a, shared=not own_shop_only) == (rows_of_a if own_shop_only else before_a), (
        "shop A must be untouched either way"
    )


@pytest.mark.parametrize("op_name", SHOP_OPS)
def test_refusals_are_indistinguishable_from_a_missing_shop(client: TestClient, world: World, op_name: str) -> None:
    call = CALLS[op_name]
    outsider = _invoke(client, world, call, world.shop_a, as_user(world.owner_b))
    missing = _invoke(client, world, call, uuid.uuid4(), as_user(world.owner_b))
    malformed = client.request(
        call.method,
        call.path(world, world.shop_a).replace(str(world.shop_a), "not-a-uuid"),
        json=call.json,
        headers={**as_user(world.owner_b), **_key()},
    )
    assert outsider.status_code == missing.status_code == malformed.status_code == 404
    assert outsider.json() == missing.json() == malformed.json()


@pytest.mark.parametrize("op_name", SHOP_OPS)
@pytest.mark.parametrize("headers", NO_CREDENTIALS, ids=["no credentials", "bad credentials"])
def test_unauthenticated_shop_calls_are_refused(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, headers: dict[str, str]
) -> None:
    call = CALLS[op_name]
    _prepare(owner, world, call)
    before = _snapshot(owner, world.shop_a)
    response = _invoke(client, world, call, world.shop_a, headers)
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert _snapshot(owner, world.shop_a) == before


@pytest.mark.parametrize("op_name", [name for name in SHOP_OPS if CALLS[name].changes_data])
def test_writes_need_an_idempotency_key_but_outsiders_still_see_not_found(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str
) -> None:
    call = CALLS[op_name]
    _prepare(owner, world, call)
    before = _snapshot(owner, world.shop_a)
    path = call.path(world, world.shop_a)
    body = _body(world, op_name, call)
    member = client.request(call.method, path, json=body, headers=as_user(world.owner_a))
    assert member.status_code == 422, member.text
    assert "Idempotency-Key" in member.json()["error"]["fields"]
    outsider = client.request(call.method, path, json=body, headers=as_user(world.owner_b))
    assert outsider.status_code == 404
    assert _snapshot(owner, world.shop_a) == before


# --- self operations: need a signed-in user, and act only on that user --------------------------------


def _users(owner: psycopg.Connection, except_for: uuid.UUID | None = None) -> list[Any]:
    return owner.execute(
        "SELECT id, lang FROM app_user WHERE id IS DISTINCT FROM %s ORDER BY id", (except_for,)
    ).fetchall()


@pytest.mark.parametrize("op_name", SELF_OPS)
@pytest.mark.parametrize("headers", NO_CREDENTIALS, ids=["no credentials", "bad credentials"])
def test_unauthenticated_self_calls_are_refused(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, headers: dict[str, str]
) -> None:
    call = SELF_CALLS[op_name]
    users, shops = _users(owner), owner.execute("SELECT count(*) FROM shop").fetchone()
    extra = _key() if call.needs_key else {}
    response = client.request(call.method, call.path, json=call.json, headers={**headers, **extra})
    assert response.status_code == 401, response.text
    assert _users(owner) == users
    assert owner.execute("SELECT count(*) FROM shop").fetchone() == shops


@pytest.mark.parametrize("op_name", SELF_OPS)
@pytest.mark.parametrize("caller", ["owner_a", "seller_a", "customer_of_a", "stranger"])
def test_any_signed_in_user_may_act_on_their_own_account_only(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, caller: str
) -> None:
    call = SELF_CALLS[op_name]
    me = getattr(world, caller)
    others = _users(owner, except_for=me)
    shops = (_snapshot(owner, world.shop_a), _snapshot(owner, world.shop_b))
    extra = _key() if call.needs_key else {}
    response = client.request(call.method, call.path, json=call.json, headers={**as_user(me), **extra})
    assert response.status_code == call.ok_status, response.text
    if response.content and call.returns_own_id and response.status_code < 300:
        assert response.json()["id"] == str(me)
    assert _users(owner, except_for=me) == others, "nobody else's account may change"
    assert (_snapshot(owner, world.shop_a), _snapshot(owner, world.shop_b)) == shops, "no existing shop may change"


# --- public operations: callable without a session, but only Telegram's signature signs anyone in -----


@pytest.mark.parametrize("op_name", PUBLIC_OPS)
def test_unsigned_data_signs_nobody_in(client: TestClient, owner: psycopg.Connection, op_name: str) -> None:
    call = PUBLIC_CALLS[op_name]
    sessions = owner.execute("SELECT count(*) FROM user_session").fetchone()
    users = owner.execute("SELECT count(*) FROM app_user").fetchone()
    response = client.request(call.method, call.path, json=call.json)
    assert response.status_code == 401, response.text
    assert response.json()["error"] == {"code": "UNAUTHENTICATED", "message": "Avval tizimga kiring.", "fields": {}}
    assert "set-cookie" not in response.headers
    assert owner.execute("SELECT count(*) FROM user_session").fetchone() == sessions
    assert owner.execute("SELECT count(*) FROM app_user").fetchone() == users


# --- link operations: without the secret of a live link there is no such route ------------------------


NOT_A_LIVE_LINK: dict[str, Callable[[str], str | None]] = {
    "no secret": lambda live: None,
    "an empty secret": lambda live: "",
    "not the shape of a secret": lambda live: "share",
    "a well-formed secret nobody was given": lambda live: "A" * len(live),
    "a live secret with one character changed": lambda live: live[:-1] + ("B" if live[-1] == "A" else "A"),
    "a live secret with something after it": lambda live: live + "0",
    "a live secret cut short": lambda live: live[:-1],
}


@pytest.mark.parametrize("op_name", LINK_OPS)
@pytest.mark.parametrize("secret", list(NOT_A_LIVE_LINK))
@pytest.mark.parametrize("caller", [None, "owner_a", "manager_a", "customer_of_a", "admin", "stranger"])
def test_without_a_live_secret_a_link_operation_does_not_exist(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str, secret: str, caller: str | None
) -> None:
    """The switch is on and a live link exists, so "not found" here is the caller's doing. Being signed
    in, even as the owner of the shop the link belongs to, opens nothing: only the secret does."""
    method, path, header = LINK_CALLS[op_name]
    _live_share(owner, world)
    before = (_snapshot(owner, world.shop_a), _snapshot(owner, world.shop_b))
    headers = {} if caller is None else as_user(getattr(world, caller))
    value = NOT_A_LIVE_LINK[secret](_share_token(world))
    if value is not None:
        headers = {**headers, header: value}
    response = client.request(method, path, headers=headers)
    unknown = client.request(method, "/api/v1/no-such-route", headers=headers)
    assert response.status_code == unknown.status_code == 404, response.text
    assert response.json() == unknown.json()
    assert "set-cookie" not in response.headers
    assert (_snapshot(owner, world.shop_a), _snapshot(owner, world.shop_b)) == before


@pytest.mark.parametrize("op_name", LINK_OPS)
def test_the_secret_of_a_live_link_opens_it_so_the_refusals_above_are_of_the_secret(
    client: TestClient, world: World, owner: psycopg.Connection, op_name: str
) -> None:
    """The counterpart: with the secret the same request is answered, so what was refused above was the
    secret and not the route."""
    method, path, header = LINK_CALLS[op_name]
    _live_share(owner, world)
    response = client.request(method, path, headers={header: _share_token(world)})
    assert response.status_code == 200, response.text


# --- the administrator's side: closed to everyone but an administrator who passed the second factor ----


@dataclass(frozen=True)
class AdminCall:
    """A valid request for an administrator operation, aimed at shop A where it names a shop."""

    method: str
    path: Callable[[World], str]
    json: Callable[[World], dict[str, Any]] | None = None
    changes_data: bool = False  # takes an Idempotency-Key
    ok_status: int = 200
    prepare: Callable[[psycopg.Connection, World], None] | None = None


def _tashkent_today() -> date:
    return datetime.now(ZoneInfo("Asia/Tashkent")).date()


def _suspended(owner: psycopg.Connection, world: World) -> None:
    owner.execute(
        "UPDATE subscription SET state = 'suspended', prior_state = 'trial' WHERE shop_id = %s", (world.shop_a,)
    )


def _receipt_id(world: World) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"suite-subscription-receipt:{world.entry_a}")


def _waiting_receipt(owner: psycopg.Connection, world: World) -> None:
    """Shop A's owner sent a receipt for one month, and it awaits a decision."""
    file_id = uuid.uuid5(uuid.NAMESPACE_URL, f"suite-subscription-file:{world.entry_a}")
    owner.execute(
        "INSERT INTO stored_file (id, shop_id, purpose, object_key, sha256, size_bytes, mime, delete_after) "
        "VALUES (%s, %s, 'subscription_receipt', %s, %s, 20, 'application/pdf', now() + interval '3 years') "
        "ON CONFLICT (id) DO NOTHING",
        (file_id, world.shop_a, f"{file_id.hex[:2]}/{file_id.hex * 2}", hashlib.sha256(file_id.bytes).digest()),
    )
    owner.execute(
        "INSERT INTO subscription_receipt (id, shop_id, file_id, stated_amount, stated_months) "
        "VALUES (%s, %s, %s, 100000, 1) "
        # Waiting again when a test runs several decisions on the same world.
        "ON CONFLICT (id) DO UPDATE SET status = 'submitted', months = NULL, reject_reason = NULL, "
        "decided_by = NULL, decided_at = NULL",
        (_receipt_id(world), world.shop_a, file_id),
    )


ADMIN_CALLS: dict[str, AdminCall] = {
    "admin.support.open": AdminCall(
        "POST",
        lambda w: f"{ADMIN_API}/shops/{w.shop_a}/support-access",
        lambda w: {"reason": "Egasi yordam so'radi", "hours": 2},
        True,
        201,
    ),
    "admin.support.close": AdminCall(
        "POST", lambda w: f"{ADMIN_API}/shops/{w.shop_a}/support-access/close", None, True, prepare=_open_support
    ),
    "admin.support.list": AdminCall("GET", lambda w: f"{ADMIN_API}/support-access"),
    # The two reads of a shop's data. With the access open, so that only the caller decides the answer.
    "admin.support.customers.list": AdminCall(
        "GET", lambda w: f"{ADMIN_API}/shops/{w.shop_a}/customers", prepare=_open_support
    ),
    "admin.support.customers.read": AdminCall(
        "GET", lambda w: f"{ADMIN_API}/shops/{w.shop_a}/customers/{w.customer_a}", prepare=_open_support
    ),
    "admin.receipts.list": AdminCall("GET", lambda w: f"{ADMIN_API}/receipts?status=submitted"),
    "admin.receipts.read": AdminCall(
        "GET", lambda w: f"{ADMIN_API}/receipts/{_receipt_id(w)}", prepare=_waiting_receipt
    ),
    "admin.receipts.approve": AdminCall(
        "POST",
        lambda w: f"{ADMIN_API}/receipts/{_receipt_id(w)}/approve",
        lambda w: {"months": 1},
        True,
        prepare=_waiting_receipt,
    ),
    "admin.receipts.reject": AdminCall(
        "POST",
        lambda w: f"{ADMIN_API}/receipts/{_receipt_id(w)}/reject",
        lambda w: {"reason": "Pul kelib tushmagan"},
        True,
        prepare=_waiting_receipt,
    ),
    "admin.session.close": AdminCall("DELETE", lambda w: f"{ADMIN_API}/auth/session", ok_status=204),
    "admin.shops.list": AdminCall("GET", lambda w: f"{ADMIN_API}/shops?q=Shop"),
    "admin.shops.read": AdminCall("GET", lambda w: f"{ADMIN_API}/shops/{w.shop_a}"),
    "admin.shops.trial.set": AdminCall(
        "POST",
        lambda w: f"{ADMIN_API}/shops/{w.shop_a}/trial",
        lambda w: {"trial_ends": (_tashkent_today() + timedelta(days=10)).isoformat(), "reason": "Sinovni uzaytirish"},
        True,
    ),
    "admin.shops.trial.end": AdminCall(
        "POST", lambda w: f"{ADMIN_API}/shops/{w.shop_a}/trial/end", lambda w: {"reason": "Sinov tugatildi"}, True
    ),
    "admin.shops.paid_through.set": AdminCall(
        "POST",
        lambda w: f"{ADMIN_API}/shops/{w.shop_a}/paid-through",
        lambda w: {"paid_through": (_tashkent_today() + timedelta(days=30)).isoformat(), "reason": "Naqd to'lov"},
        True,
    ),
    "admin.shops.suspend": AdminCall(
        "POST", lambda w: f"{ADMIN_API}/shops/{w.shop_a}/suspend", lambda w: {"reason": "Qoidabuzarlik"}, True
    ),
    "admin.shops.unsuspend": AdminCall(
        "POST",
        lambda w: f"{ADMIN_API}/shops/{w.shop_a}/unsuspend",
        lambda w: {"reason": "Masala hal bo'ldi"},
        True,
        prepare=_suspended,
    ),
    # This one asks for the second factor again on every call, and the suite cannot know a right code:
    # the code here is well formed and wrong. An administrator is therefore let in as far as the code and
    # refused there (403, nothing written), which still tells them apart from everyone else, who get the
    # 404 of an unknown route. What it does with a right code is in test_admin_owner.py.
    "admin.shops.owner.reassign": AdminCall(
        "POST",
        lambda w: f"{ADMIN_API}/shops/{w.shop_a}/owner",
        lambda w: {"new_owner_tg_id": 987654321, "reason": "Egasi hisobini yo'qotdi", "code": "000000"},
        True,
        ok_status=403,
    ),
    "admin.settings.read": AdminCall("GET", lambda w: f"{ADMIN_API}/settings"),
    # A change that needs no second code, so that it would go through for anyone let in.
    "admin.settings.update": AdminCall(
        "PATCH", lambda w: f"{ADMIN_API}/settings", lambda w: {"changes": {"trial_days": 14}}, True
    ),
    "admin.audit.list": AdminCall("GET", lambda w: f"{ADMIN_API}/audit"),
}

# The door: enrolling and passing the second factor. The code is well formed and wrong.
ADMIN_ENTRY_CALLS: dict[str, AdminCall] = {
    "admin.auth.read": AdminCall("GET", lambda w: f"{ADMIN_API}/auth"),
    "admin.auth.enrol": AdminCall("POST", lambda w: f"{ADMIN_API}/auth/enrolment", None, True, 201),
    "admin.session.open": AdminCall("POST", lambda w: f"{ADMIN_API}/auth/session", lambda w: {"code": "000000"}),
}

ADMIN_OPS = sorted(op.name for op in all_operations() if op.scope == "admin")
ADMIN_ENTRY_OPS = sorted(op.name for op in all_operations() if op.scope == "admin_entry")

# People who are simply not administrators, whatever else they are.
NOT_LISTED = ["owner_a", "manager_a", "seller_a", "suspended_a", "customer_of_a", "stranger"]
# People with some, but not all, of what makes an administrator. Each lacks exactly one thing.
ALMOST_ADMINS = [
    "listed_never_enrolled",  # on the allow-list, no administrator account
    "listed_without_factor",  # on the allow-list, active account, never passed the second factor
    "account_not_listed",  # active account and a live admin session, taken off the allow-list
    "disabled",  # on the allow-list, live admin session, account disabled
    "expired",  # the admin session ran out
    "signed_out",  # the admin session was closed
    "anothers_session",  # an administrator presenting another administrator's admin session
]
# Of those, the ones the door itself must turn away: the rest are exactly who the door is for.
REFUSED_AT_THE_DOOR = ["account_not_listed", "disabled"]


def _admin_caller(
    kind: str, client: TestClient, world: World, owner: psycopg.Connection, env: AdminEnv
) -> dict[str, str]:
    """Headers of the named kind of caller. Runs before the snapshot: it is the state the call meets."""
    if kind in NOT_LISTED:
        return as_user(getattr(world, kind))
    if kind == "listed_never_enrolled":
        allow_list(owner, env, world.stranger)
        return as_user(world.stranger)
    secret = make_admin(owner, env, world.admin)
    if kind == "listed_without_factor":
        return as_user(world.admin)
    headers = elevate(client, env, world.admin, secret)
    if kind == "account_not_listed":
        env.allowed.clear()
    elif kind == "disabled":
        owner.execute("UPDATE admin_account SET status = 'disabled' WHERE user_id = %s", (world.admin,))
    elif kind == "expired":
        env.clock.offset += timedelta(hours=8)
    elif kind == "signed_out":
        assert client.delete(f"{ADMIN_API}/auth/session", headers=headers).status_code == 204
    elif kind == "anothers_session":
        make_admin(owner, env, world.owner_b)
        return {**as_user(world.owner_b), "Cookie": headers["Cookie"]}
    else:
        assert kind == "administrator", kind
    return headers


def _admin_request(client: TestClient, world: World, call: AdminCall, headers: dict[str, str]) -> Any:
    if call.changes_data:
        headers = {**headers, **_key()}
    body = None if call.json is None else call.json(world)
    return client.request(call.method, call.path(world), json=body, headers=headers)


def _admin_snapshot(owner: psycopg.Connection, world: World) -> tuple[Any, ...]:
    """Everything a refused administrator call could have changed."""
    return (
        owner.execute("SELECT key, value, updated_by FROM platform_setting ORDER BY key").fetchall(),
        owner.execute(
            "SELECT shop_id, state, trial_ends, paid_through, prior_state FROM subscription ORDER BY shop_id"
        ).fetchall(),
        owner.execute(
            "SELECT user_id, status, totp_secret, confirmed_at, failed_codes, locked_until, last_step "
            "FROM admin_account ORDER BY user_id"
        ).fetchall(),
        owner.execute("SELECT id, user_id, expires_at, revoked_at FROM admin_session ORDER BY id").fetchall(),
        owner.execute("SELECT count(*) FROM admin_audit").fetchone(),
        owner.execute("SELECT count(*) FROM admin_request_key").fetchone(),
        owner.execute("SELECT count(*) FROM outbox_message").fetchone(),
        owner.execute("SELECT id, lang, active_shop FROM app_user ORDER BY id").fetchall(),
        _snapshot(owner, world.shop_a),
        _snapshot(owner, world.shop_b),
    )


def _unknown_route(client: TestClient, call: AdminCall, world: World, headers: dict[str, str]) -> Any:
    body = None if call.json is None else call.json(world)
    return client.request(call.method, f"{ADMIN_API}/no-such-thing", json=body, headers={**headers, **_key()})


def _assert_reads_like_an_unknown_route(response: Any, unknown: Any) -> None:
    assert unknown.status_code == 404
    assert response.status_code == 404, response.text
    assert response.content == unknown.content
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": "Topilmadi.", "fields": {}}}

    # Every answer has its own request identifier; nothing else in the headers may differ.
    def headers(answer: Any) -> dict[str, str]:
        return {name: value for name, value in answer.headers.items() if name.lower() != "x-request-id"}

    assert headers(response) == headers(unknown)
    assert set(response.headers) == set(unknown.headers)
    assert "set-cookie" not in response.headers


def test_every_admin_operation_is_described_in_the_suite(client: TestClient) -> None:
    assert set(ADMIN_CALLS) == set(ADMIN_OPS), "add the new administrator operation to ADMIN_CALLS"
    assert set(ADMIN_ENTRY_CALLS) == set(ADMIN_ENTRY_OPS), "add the new door operation to ADMIN_ENTRY_CALLS"
    assert ADMIN_OPS and ADMIN_ENTRY_OPS
    described = {**ADMIN_CALLS, **ADMIN_ENTRY_CALLS}
    for route in client.app.routes:  # type: ignore[attr-defined]
        if isinstance(route, APIRoute) and route.path.startswith(ADMIN_API):
            assert route.name in described, "every administrator route is an administrator operation"
            # Every write takes an Idempotency-Key, except passing and dropping the second factor,
            # which like the ordinary sign-in and sign-out are not repeatable requests.
            writes = bool(route.methods - {"GET", "HEAD"})
            keyless = route.name in ("admin.session.open", "admin.session.close")
            assert described[route.name].changes_data is (writes and not keyless), route.name
    for op in all_operations():
        assert op.name.startswith("admin.") is (op.scope in ("admin", "admin_entry")), op.name


@pytest.mark.parametrize("op_name", ADMIN_OPS)
def test_an_administrator_who_passed_the_second_factor_is_let_in(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, op_name: str
) -> None:
    """The control for everything below: the same calls do succeed for the one caller they are for."""
    call = ADMIN_CALLS[op_name]
    if call.prepare is not None:
        call.prepare(owner, world)
    headers = _admin_caller("administrator", client, world, owner, admin_env)
    response = _admin_request(client, world, call, headers)
    assert response.status_code == call.ok_status, response.text


@pytest.mark.parametrize("op_name", ADMIN_OPS)
@pytest.mark.parametrize("caller", NOT_LISTED + ALMOST_ADMINS)
def test_for_anyone_else_the_administrators_side_does_not_exist(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, op_name: str, caller: str
) -> None:
    call = ADMIN_CALLS[op_name]
    if call.prepare is not None:
        call.prepare(owner, world)
    # In Russian, so that an answer in the caller's own language would give the route away.
    owner.execute("UPDATE app_user SET lang = 'ru'")
    headers = _admin_caller(caller, client, world, owner, admin_env)
    before = _admin_snapshot(owner, world)
    response = _admin_request(client, world, call, headers)
    _assert_reads_like_an_unknown_route(response, _unknown_route(client, call, world, headers))
    assert _admin_snapshot(owner, world) == before, "a refused call must change nothing"


@pytest.mark.parametrize("op_name", ADMIN_OPS + ADMIN_ENTRY_OPS)
@pytest.mark.parametrize("headers", NO_CREDENTIALS, ids=["no credentials", "bad credentials"])
def test_unauthenticated_admin_calls_are_refused(
    client: TestClient,
    world: World,
    owner: psycopg.Connection,
    admin_env: AdminEnv,
    op_name: str,
    headers: dict[str, str],
) -> None:
    call = {**ADMIN_CALLS, **ADMIN_ENTRY_CALLS}[op_name]
    if call.prepare is not None:
        call.prepare(owner, world)
    # Even with a live admin session of a real administrator in hand: without a sign-in it is nothing.
    cookie = _admin_caller("administrator", client, world, owner, admin_env)["Cookie"]
    before = _admin_snapshot(owner, world)
    response = _admin_request(client, world, call, {**headers, "Cookie": cookie})
    assert response.status_code == 401, response.text
    assert response.json()["error"] == {"code": "UNAUTHENTICATED", "message": "Avval tizimga kiring.", "fields": {}}
    assert _admin_snapshot(owner, world) == before


@pytest.mark.parametrize("op_name", [name for name in ADMIN_OPS if ADMIN_CALLS[name].changes_data])
def test_admin_writes_need_an_idempotency_key_but_outsiders_still_see_not_found(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, op_name: str
) -> None:
    call = ADMIN_CALLS[op_name]
    if call.prepare is not None:
        call.prepare(owner, world)
    headers = _admin_caller("administrator", client, world, owner, admin_env)
    before = _admin_snapshot(owner, world)
    body = None if call.json is None else call.json(world)
    inside = client.request(call.method, call.path(world), json=body, headers=headers)
    assert inside.status_code == 422, inside.text
    assert "Idempotency-Key" in inside.json()["error"]["fields"]
    outside = client.request(call.method, call.path(world), json=body, headers=as_user(world.owner_a))
    assert outside.status_code == 404
    assert _admin_snapshot(owner, world) == before


@pytest.mark.parametrize("op_name", ADMIN_OPS)
@pytest.mark.parametrize("caller", ["owner_a", "listed_without_factor", "expired"])
def test_a_malformed_admin_request_tells_an_outsider_nothing(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, op_name: str, caller: str
) -> None:
    """A body that is not JSON, a shop identifier that is not one, a missing key: still "not found"."""
    call = ADMIN_CALLS[op_name]
    headers = _admin_caller(caller, client, world, owner, admin_env)
    before = _admin_snapshot(owner, world)
    path = call.path(world).replace(str(world.shop_a), "not-a-uuid") + ("&limit=x" if "?" in call.path(world) else "")
    response = client.request(
        call.method, path, content=b"{not json", headers={**headers, "Content-Type": "application/json"}
    )
    unknown = client.request(
        call.method,
        f"{ADMIN_API}/no-such-thing",
        content=b"{not json",
        headers={**headers, "Content-Type": "application/json"},
    )
    _assert_reads_like_an_unknown_route(response, unknown)
    assert _admin_snapshot(owner, world) == before


# --- the door -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("op_name", ADMIN_ENTRY_OPS)
@pytest.mark.parametrize("caller", NOT_LISTED + REFUSED_AT_THE_DOOR)
def test_the_door_does_not_exist_for_someone_who_is_not_on_the_allow_list_or_is_disabled(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, op_name: str, caller: str
) -> None:
    call = ADMIN_ENTRY_CALLS[op_name]
    owner.execute("UPDATE app_user SET lang = 'ru'")
    headers = _admin_caller(caller, client, world, owner, admin_env)
    before = _admin_snapshot(owner, world)
    response = _admin_request(client, world, call, headers)
    _assert_reads_like_an_unknown_route(response, _unknown_route(client, call, world, headers))
    assert _admin_snapshot(owner, world) == before, "a refused call must change nothing"


@pytest.mark.parametrize("caller", ["listed_never_enrolled", "listed_without_factor", "expired", "signed_out"])
def test_the_door_is_there_for_an_allow_listed_person_but_opens_nothing_without_the_code(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, caller: str
) -> None:
    headers = _admin_caller(caller, client, world, owner, admin_env)
    status = _admin_request(client, world, ADMIN_ENTRY_CALLS["admin.auth.read"], headers)
    assert status.status_code == 200, status.text
    assert status.json()["elevated"] is False
    assert status.json()["enrolled"] is (caller != "listed_never_enrolled")

    sessions = owner.execute("SELECT count(*) FROM admin_session WHERE revoked_at IS NULL").fetchone()
    wrong = _admin_request(client, world, ADMIN_ENTRY_CALLS["admin.session.open"], headers)
    expected = (409, "ADMIN_NOT_ENROLLED") if caller == "listed_never_enrolled" else (403, "SECOND_FACTOR_INVALID")
    assert (wrong.status_code, wrong.json()["error"]["code"]) == expected
    assert "set-cookie" not in wrong.headers
    assert owner.execute("SELECT count(*) FROM admin_session WHERE revoked_at IS NULL").fetchone() == sessions
    # And having knocked, they are still outside.
    inside = _admin_request(client, world, ADMIN_CALLS["admin.shops.list"], headers)
    assert inside.status_code == 404


# --- support access: the only way an administrator sees a shop's data, and it changes nothing ---------

SUPPORT_READS = ["admin.support.customers.list", "admin.support.customers.read"]


def test_the_reads_under_support_access_are_exactly_these(client: TestClient) -> None:
    """Every administrator route that answers with a shop's customers or entries is named here, and each
    is a read. A route added later under a shop's customers must be added too, or this fails."""
    assert set(SUPPORT_READS) <= set(ADMIN_OPS)
    under_customers = {
        route.name: route.methods
        for route in client.app.routes  # type: ignore[attr-defined]
        if isinstance(route, APIRoute) and route.path.startswith(ADMIN_API) and "/customers" in route.path
    }
    assert set(under_customers) == set(SUPPORT_READS)
    assert all(methods == {"GET"} for methods in under_customers.values())


@pytest.mark.parametrize("op_name", SUPPORT_READS)
def test_an_administrator_without_support_access_is_refused_a_shops_data_and_the_attempt_is_audited(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, op_name: str
) -> None:
    call = ADMIN_CALLS[op_name]
    headers = _admin_caller("administrator", client, world, owner, admin_env)
    before = _admin_snapshot(owner, world)
    response = _admin_request(client, world, call, headers)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "SUPPORT_ACCESS_REQUIRED"
    for hidden in ("Ali", "50000", str(world.customer_a), str(world.entry_a)):
        assert hidden not in response.text
    after = _admin_snapshot(owner, world)
    audit = owner.execute(
        "SELECT action, target_shop FROM admin_audit WHERE admin_id = %s AND action = 'support.refused'",
        (world.admin,),
    ).fetchall()
    assert audit == [("support.refused", world.shop_a)]
    # Nothing but that audit row: the count of audit rows is the fifth item of the snapshot.
    assert after[:4] + after[5:] == before[:4] + before[5:]


@pytest.mark.parametrize("op_name", SUPPORT_READS)
@pytest.mark.parametrize(
    "state", ["of another shop", "of another administrator", "expired", "not started", "closed by the owner"]
)
def test_only_the_administrators_own_current_access_to_that_shop_opens_its_data(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, op_name: str, state: str
) -> None:
    call = ADMIN_CALLS[op_name]
    headers = _admin_caller("administrator", client, world, owner, admin_env)
    _open_support(owner, world)
    change = {
        "of another shop": ("shop_id = %s", world.shop_b),
        "of another administrator": ("admin_id = %s", world.owner_b),
        "expired": ("starts_at = now() - interval '2 hours', ends_at = %s", datetime.now(UTC) - timedelta(seconds=1)),
        "not started": ("starts_at = %s", datetime.now(UTC) + timedelta(minutes=5)),
        "closed by the owner": ("closed_by = 'owner', closed_at = %s", datetime.now(UTC)),
    }[state]
    owner.execute(f"UPDATE support_access SET {change[0]} WHERE id = %s", (change[1], _support_id(world)))
    response = _admin_request(client, world, call, headers)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "SUPPORT_ACCESS_REQUIRED")


@pytest.mark.parametrize("op_name", SHOP_OPS)
def test_support_access_opens_none_of_the_shops_own_operations(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv, op_name: str
) -> None:
    """With an open support access and the second factor passed, the administrator is still an outsider
    to every operation of the shop itself: nothing a member can do, read or write, is theirs."""
    call = CALLS[op_name]
    _prepare(owner, world, call)
    if call.prepare is not _open_support:
        _open_support(owner, world)
    headers = _admin_caller("administrator", client, world, owner, admin_env)
    before = _snapshot(owner, world.shop_a)
    response = _invoke(client, world, call, world.shop_a, headers)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert _snapshot(owner, world.shop_a) == before


def test_no_administrator_route_under_a_shop_writes_to_its_ledger_or_customers(
    client: TestClient, world: World, owner: psycopg.Connection, admin_env: AdminEnv
) -> None:
    """Every administrator operation is run with support access open; the shop's ledger, goods, customers,
    catalog and staff must be byte for byte what they were."""

    def shop_data() -> tuple[Any, ...]:
        tables = ("ledger_entry", "goods_line", "promise", "customer", "catalog_item", "membership", "dispute")
        return tuple(
            owner.execute(
                f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) FROM {table} t "
                "WHERE shop_id = %s",
                (world.shop_a,),
            ).fetchone()
            for table in tables
        )

    headers = _admin_caller("administrator", client, world, owner, admin_env)
    before = shop_data()
    for name in ADMIN_OPS:
        if name == "admin.session.close":
            continue  # it would end the session the other calls need
        call = ADMIN_CALLS[name]
        # Each call meets the shop as the world made it: in its trial, not suspended.
        owner.execute(
            "UPDATE subscription SET state = 'trial', trial_ends = %s, paid_through = NULL, prior_state = NULL "
            "WHERE shop_id = %s",
            (_tashkent_today() + timedelta(days=30), world.shop_a),
        )
        if call.prepare is not None and call.prepare is not _open_support:
            call.prepare(owner, world)
        owner.execute("DELETE FROM support_access WHERE shop_id = %s", (world.shop_a,))
        if call.prepare is _open_support or name in SUPPORT_READS:
            _open_support(owner, world)
        response = _admin_request(client, world, call, headers)
        assert response.status_code == call.ok_status, (name, response.text)
    assert shop_data() == before
