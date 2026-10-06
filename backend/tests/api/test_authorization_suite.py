"""The authorization and tenant suite (NFR-013, launch criterion 5). Blocking.

Every registered operation is exercised according to its scope:

- shop operations: as each role, as a suspended member, as the owner of another shop, as a customer, as a
  platform administrator without support access, as a stranger, and without signing in;
- self operations: without signing in, and as any signed-in user;
- public operations: without signing in, with data that is not validly signed.

An operation that is registered but not described here fails the suite, and so does an API route that is
not bound to a registered operation, so nothing can be added without being checked.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from qarz.application.operations import all_operations
from qarz.domain.access import Role, lowest_role_with

from .conftest import World, as_user

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
    # Puts shop A into the state the call needs (for example a pending transfer). Runs as the owner role.
    prepare: Callable[[psycopg.Connection, World], None] | None = None
    # Callers whose role is allowed but who are refused for another stated reason: caller -> (status, code).
    refused: tuple[tuple[str, int, str], ...] = ()


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


CALLS: dict[str, Call] = {
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
    "overview.read": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/overview"),
    "overview.debtors": Call("GET", lambda w, shop: f"/api/v1/shops/{shop}/overview/debtors"),
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
    "counter_code.read": {Role.SELLER, Role.MANAGER, Role.OWNER},
    # The code is printed and hangs at the counter; replacing it invalidates the print.
    "counter_code.rotate": {Role.MANAGER, Role.OWNER},
    "waiting.list": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "waiting.attach": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "waiting.dismiss": {Role.SELLER, Role.MANAGER, Role.OWNER},
    # Flagged to the owner and managers (REQ-017); a seller sees only that an entry is disputed.
    "disputes.list": {Role.MANAGER, Role.OWNER},
    "disputes.decline": {Role.MANAGER, Role.OWNER},
    # Specification, resources table: date requests are listed and decided by "manager, owner", and
    # `/shops/{id}/entries/{eid}/promise` is "Manager, owner" (REQ-067).
    "date_requests.list": {Role.MANAGER, Role.OWNER},
    "date_requests.accept": {Role.MANAGER, Role.OWNER},
    "date_requests.decline": {Role.MANAGER, Role.OWNER},
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
    "overview.read": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "overview.debtors": {Role.SELLER, Role.MANAGER, Role.OWNER},
    # Specification, resources table: "Manager, owner; sellers read".
    "catalog.list": {Role.SELLER, Role.MANAGER, Role.OWNER},
    "catalog.create": {Role.MANAGER, Role.OWNER},
    "catalog.update": {Role.MANAGER, Role.OWNER},
    "catalog.hide": {Role.MANAGER, Role.OWNER},
    "catalog.unhide": {Role.MANAGER, Role.OWNER},
    "catalog.learned.accept": {Role.MANAGER, Role.OWNER},
    "catalog.learned.dismiss": {Role.MANAGER, Role.OWNER},
    "catalog.learned.merge": {Role.MANAGER, Role.OWNER},
}

SELF_CALLS: dict[str, PlainCall] = {
    "me.read": PlainCall("GET", "/api/v1/me"),
    "me.update": PlainCall("PATCH", "/api/v1/me", {"lang": "ru"}),
    "auth.sign_out": PlainCall("POST", "/api/v1/auth/sign-out", ok_status=204),
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

BY_SCOPE = {
    scope: sorted(op.name for op in all_operations() if op.scope == scope) for scope in ("shop", "self", "public")
}
SHOP_OPS, SELF_OPS, PUBLIC_OPS = BY_SCOPE["shop"], BY_SCOPE["self"], BY_SCOPE["public"]
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
    return call.json


def _invoke(client: TestClient, world: World, call: Call, shop: uuid.UUID, headers: dict[str, str]) -> Any:
    if call.changes_data:
        headers = {**headers, **_key()}
    op_name = next(name for name, candidate in CALLS.items() if candidate is call)
    return client.request(call.method, call.path(world, shop), json=_body(world, op_name, call), headers=headers)


def _prepare(owner: psycopg.Connection, world: World, call: Call) -> None:
    if call.prepare is not None:
        call.prepare(owner, world)


def _snapshot(owner: psycopg.Connection, shop: uuid.UUID) -> tuple[Any, ...]:
    """Everything a refused call could have changed in a shop."""
    return (
        owner.execute("SELECT name, lang, default_promise_days, status FROM shop WHERE id = %s", (shop,)).fetchone(),
        owner.execute(
            "SELECT reminders_on, reminder_hour, reminder_tpl, sms_on, default_credit_limit, sellers_may_exceed "
            "FROM shop WHERE id = %s",
            (shop,),
        ).fetchone(),
        owner.execute("SELECT count(*) FROM reminder WHERE shop_id = %s", (shop,)).fetchone(),
        owner.execute(
            "SELECT id, user_id, role, status FROM membership WHERE shop_id = %s ORDER BY id", (shop,)
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
        # Measurement rows carry no shop identifier; tests run one at a time, so a total is enough.
        owner.execute("SELECT count(*) FROM measure.event").fetchone(),
    )


# --- the suite covers everything --------------------------------------------------------------------


def test_every_operation_is_described_in_the_suite() -> None:
    assert set(CALLS) == set(SHOP_OPS), "add the new shop operation to CALLS"
    assert set(ALLOWED_ROLES) == set(SHOP_OPS), "add the new shop operation to ALLOWED_ROLES"
    assert set(SELF_CALLS) == set(SELF_OPS), "add the new self operation to SELF_CALLS"
    assert set(PUBLIC_CALLS) == set(PUBLIC_OPS), "add the new public operation to PUBLIC_CALLS"
    assert SHOP_OPS and SELF_OPS and PUBLIC_OPS


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

    into_a = _invoke(client, world, call, world.shop_a, as_user(world.owner_b))
    assert into_a.status_code == 404, into_a.text

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
        _date_request_id(world),
    )
    uses_foreign_resource = any(str(resource) in call.path(world, world.shop_b) for resource in foreign)
    if uses_foreign_resource:
        assert through_b.status_code == 404, through_b.text
        assert _snapshot(owner, world.shop_b)[:3] == before_b[:3], "shop B's own data must be untouched"
    assert _snapshot(owner, world.shop_a) == before_a, "shop A must be untouched either way"


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
