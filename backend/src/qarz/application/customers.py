"""Customer book (REQ-003 to REQ-005) and the checks every ledger write shares."""

import base64
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency
from qarz.application.currencies import USD, dollars_on, limit_hint
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.operations import operation
from qarz.application.ports import CustomerRecord, Membership, Storage, TenantSession
from qarz.application.shops import ShopSuspended, require_member
from qarz.domain import platform_settings
from qarz.domain.access import Capability, Role
from qarz.domain.credit import MAX_LIMIT, MIN_LIMIT, valid_limit
from qarz.domain.money import Currency
from qarz.domain.names import normalize_name
from qarz.domain.phones import normalize_phone
from qarz.domain.promise import tashkent_date
from qarz.domain.subscription import LIMITED, effective_state, may_add_customers, with_free_plan

CREATE_CUSTOMER = operation("customers.create", Capability.RECORD)
LIST_CUSTOMERS = operation("customers.list", Capability.RECORD)
UPDATE_CUSTOMER = operation("customers.update", Capability.MANAGE)
ARCHIVE_CUSTOMER = operation("customers.archive", Capability.MANAGE)
UNARCHIVE_CUSTOMER = operation("customers.unarchive", Capability.MANAGE)

MAX_PAGE = 100
_UNSET: Any = object()


class SubscriptionLimited(AppError):
    """The trial or paid period has ended: new credit sales are refused, everything else still works."""

    code = "SUBSCRIPTION_LIMITED"


class FreePlanFull(AppError):
    """BR-34: the free plan holds no more customers, and the shop has no trial or paid period that would.
    `limit` is how many it holds."""

    code = "FREE_PLAN_FULL"

    def __init__(self, limit: int) -> None:
        super().__init__({"limit": str(limit)})
        self.limit = limit


class CustomerArchived(AppError):
    code = "CUSTOMER_ARCHIVED"


class CustomerHasBalance(AppError):
    """A customer who still owes something cannot be archived (INV-13)."""

    code = "CUSTOMER_HAS_BALANCE"


FREE_PLAN_ON = "free_plan_on"
FREE_PLAN_CUSTOMERS = "free_plan_customers"


async def free_plan_customers(session: TenantSession) -> int | None:
    """How many customers the free plan holds; None while the platform switch is off (BR-33)."""
    if await session.platform_setting(FREE_PLAN_ON) is not True:
        return None
    return platform_settings.free_plan_customers(True, await session.platform_setting(FREE_PLAN_CUSTOMERS))


async def stored_subscription(session: TenantSession, today: date) -> str:
    """trial, active, limited or suspended by the stored row and its end dates alone (BR-29, BR-30)."""
    row = await session.subscription()
    if row is None:
        return LIMITED
    state, trial_ends, paid_through = row
    return effective_state(state, trial_ends, paid_through, today)


async def effective_subscription(session: TenantSession, today: date) -> str:
    """trial, active, free, limited or suspended: the end dates and the free plan taken into account
    (BR-29, BR-30, BR-33). The plan is looked at only for a shop that would otherwise be limited."""
    state = await stored_subscription(session, today)
    if state != LIMITED:
        return state
    held = await free_plan_customers(session)
    if held is None:
        return LIMITED
    return with_free_plan(state, held, await session.active_customers())


async def require_room(session: TenantSession, today: date, adding: int = 1) -> None:
    """BR-34: refuse `adding` more active customers when the free plan would not hold them and no trial
    or paid period is running. Called inside the transaction that adds them; the count is locked, so two
    requests at once cannot both take the last place. With the switch off nothing is read but the switch."""
    held = await free_plan_customers(session)
    if held is None or adding <= 0:
        return
    state = await stored_subscription(session, today)
    if not may_add_customers(state, held, await session.active_customers(lock=True), adding):
        raise FreePlanFull(held)


async def require_writable(session: TenantSession, today: date, *, new_credit: bool) -> None:
    state = await effective_subscription(session, today)
    if state == "suspended":
        raise ShopSuspended()
    if state == "limited" and new_credit:
        raise SubscriptionLimited()


async def require_viewable(session: TenantSession, actor: Membership, today: date) -> None:
    """BR-30: in a suspended shop only the owner may still look at the data."""
    if actor.role is not Role.OWNER and await effective_subscription(session, today) == "suspended":
        raise ShopSuspended()


def clean_name(raw: str) -> str:
    name = " ".join(raw.split())
    if not 1 <= len(name) <= 80:
        raise ValidationFailed({"display_name": "length must be between 1 and 80"})
    return name


def clean_phone(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    phone = normalize_phone(raw)
    if phone is None:
        raise ValidationFailed({"phone": "not a phone number"})
    return phone


def customer_body(customer: CustomerRecord, balance: int, usd: int | None = None) -> dict[str, Any]:
    """A customer as the API gives them. `balance` and `credit_limit` are so'm.

    `usd` is the dollar balance in cents, given only for a shop that works in dollars: the body then has
    a `usd` object with the same two figures in dollars. Otherwise the body is what it always was.
    """
    body: dict[str, Any] = {
        "id": str(customer.customer_id),
        "display_name": customer.display_name,
        "phone": customer.phone,
        "status": customer.status,
        "reminders_off": customer.reminders_off,
        "credit_limit": customer.credit_limit,
        "balance": balance,
    }
    if usd is not None:
        body["usd"] = {"balance": usd, "credit_limit": customer.credit_limit_usd}
    return body


async def dollar_balances(session: TenantSession, customer_ids: list[UUID]) -> dict[UUID, int] | None:
    """What each of the customers owes in dollars, or None when the shop does not work in dollars."""
    if not await dollars_on(session):
        return None
    return await session.balances(customer_ids, USD) if customer_ids else {}


async def owes_anything(session: TenantSession, customer_id: UUID) -> bool:
    """INV-13: whether the customer owes in any currency, whether or not the shop shows dollars now."""
    for currency in Currency:
        if (await session.balances([customer_id], currency)).get(customer_id, 0) != 0:
            return True
    return False


async def create_customer_in(
    session: TenantSession, actor: Membership, name: str, phone: str | None, today: date
) -> CustomerRecord:
    """Add a customer inside a tenant transaction the caller has opened and authorized."""
    await require_room(session, today)
    customer = await session.create_customer(
        customer_id=uuid4(), display_name=name, name_norm=normalize_name(name), phone=phone
    )
    await session.record_activity(
        membership_id=actor.membership_id,
        action="customer.created",
        subject_type="customer",
        subject_id=customer.customer_id,
    )
    return customer


def encode_cursor(*parts: Any) -> str:
    return base64.urlsafe_b64encode(json.dumps([str(p) for p in parts]).encode()).decode().rstrip("=")


def decode_cursor(cursor: str, count: int) -> list[str]:
    try:
        parts = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except ValueError as error:
        raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error
    if not isinstance(parts, list) or len(parts) != count or not all(isinstance(p, str) for p in parts):
        raise ValidationFailed({"cursor": "not a cursor returned by this API"})
    return parts


async def list_customers_in(
    session: TenantSession, *, query: str | None, status: str, cursor: str | None, limit: int
) -> dict[str, Any]:
    """One page of customers with their balances. The caller has already decided who may see them."""
    fields: dict[str, str] = {}
    if not 1 <= limit <= MAX_PAGE:
        fields["limit"] = f"must be between 1 and {MAX_PAGE}"
    if status not in ("active", "archived"):
        fields["status"] = "must be active or archived"
    if query is not None and len(query) > 80:
        fields["q"] = "at most 80 characters"
    if fields:
        raise ValidationFailed(fields)
    after: tuple[str, UUID] | None = None
    if cursor:
        name_norm, customer_id = decode_cursor(cursor, 2)
        try:
            after = (name_norm, UUID(customer_id))
        except ValueError as error:
            raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error

    text = (query or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    rows = await session.search_customers(
        name_part=normalize_name(text) if text else None,
        # A search that looks like a phone number also matches by phone digits.
        phone_digits=digits if len(digits) >= 4 and len(digits) >= len(text.replace(" ", "")) - 3 else None,
        status=status,
        after=after,
        limit=limit + 1,
    )
    page, more = rows[:limit], len(rows) > limit
    dollars = await dollar_balances(session, [customer.customer_id for customer, _, _ in page])
    return {
        "items": [
            customer_body(customer, balance, None if dollars is None else dollars.get(customer.customer_id, 0))
            for customer, balance, _ in page
        ],
        "next_cursor": encode_cursor(page[-1][2], page[-1][0].customer_id) if more else None,
    }


class CustomerService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def create(
        self, user_id: UUID, shop_id: UUID, display_name: str, phone: str | None, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, CREATE_CUSTOMER)
            key = idempotency.validate_key(request_key)
            name, number = clean_name(display_name), clean_phone(phone)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                created = await create_customer_in(session, actor, clean_name(name), number, self._today())
                return customer_body(created, 0, 0 if await dollars_on(session) else None)

            return await idempotency.run_once(
                session,
                key=key,
                operation=CREATE_CUSTOMER.name,
                user_id=user_id,
                request={"display_name": name, "phone": number},
                action=apply,
            )

    async def list(
        self, user_id: UUID, shop_id: UUID, *, query: str | None, status: str, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_CUSTOMERS)
            await require_viewable(session, actor, self._today())
            return await list_customers_in(session, query=query, status=status, cursor=cursor, limit=limit)

    async def update(
        self,
        user_id: UUID,
        shop_id: UUID,
        customer_id: UUID,
        *,
        display_name: str | None,
        phone: Any,
        reminders_off: bool | None,
        request_key: str | None,
        credit_limit: Any = _UNSET,
        credit_limit_usd: Any = _UNSET,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, UPDATE_CUSTOMER)
            key = idempotency.validate_key(request_key)
            if credit_limit_usd is not _UNSET and not await dollars_on(session):
                # As any field the request model does not know: the shop has no dollar limit to set.
                raise ValidationFailed({"credit_limit_usd": "unknown field"})
            if (
                display_name is None
                and phone is _UNSET
                and reminders_off is None
                and credit_limit is _UNSET
                and credit_limit_usd is _UNSET
            ):
                raise ValidationFailed({"_": "nothing to change"})
            if credit_limit is not _UNSET and credit_limit is not None and not valid_limit(credit_limit):
                raise ValidationFailed(
                    {"credit_limit": f"a whole amount between {MIN_LIMIT} and {MAX_LIMIT} UZS, or null"}
                )
            if (
                credit_limit_usd is not _UNSET
                and credit_limit_usd is not None
                and not valid_limit(credit_limit_usd, USD)
            ):
                raise ValidationFailed({"credit_limit_usd": limit_hint(USD)})
            name = clean_name(display_name) if display_name is not None else None
            number = _UNSET if phone is _UNSET else clean_phone(phone)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                customer = await session.get_customer(customer_id, for_update=True)
                if customer is None or customer.status == "anonymized":
                    raise NotFound()
                updated = await session.update_customer(
                    customer_id,
                    display_name=name,
                    name_norm=normalize_name(name) if name is not None else None,
                    set_phone=number is not _UNSET,
                    phone=None if number is _UNSET else number,
                    reminders_off=reminders_off,
                    set_limit=credit_limit is not _UNSET,
                    credit_limit=None if credit_limit is _UNSET else credit_limit,
                    set_limit_usd=credit_limit_usd is not _UNSET,
                    credit_limit_usd=None if credit_limit_usd is _UNSET else credit_limit_usd,
                )
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="customer.updated",
                    subject_type="customer",
                    subject_id=customer_id,
                )
                balances = await session.balances([customer_id])
                dollars = await dollar_balances(session, [customer_id])
                return customer_body(
                    updated, balances.get(customer_id, 0), None if dollars is None else dollars.get(customer_id, 0)
                )

            return await idempotency.run_once(
                session,
                key=key,
                operation=UPDATE_CUSTOMER.name,
                user_id=user_id,
                request={
                    "customer": str(customer_id),
                    "display_name": name,
                    "phone": None if number is _UNSET else number,
                    "phone_set": number is not _UNSET,
                    "reminders_off": reminders_off,
                    "limit_set": credit_limit is not _UNSET,
                    "credit_limit": None if credit_limit is _UNSET else credit_limit,
                    # Only a request that names the dollar limit carries the key, so every other request
                    # keeps the fingerprint it had before dollars existed.
                    **({} if credit_limit_usd is _UNSET else {"credit_limit_usd": credit_limit_usd}),
                },
                action=apply,
            )

    async def set_archived(
        self, user_id: UUID, shop_id: UUID, customer_id: UUID, *, archived: bool, request_key: str | None
    ) -> dict[str, Any]:
        op = ARCHIVE_CUSTOMER if archived else UNARCHIVE_CUSTOMER
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, op)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                customer = await session.get_customer(customer_id, for_update=True)
                if customer is None or customer.status == "anonymized":
                    raise NotFound()
                balance = (await session.balances([customer_id])).get(customer_id, 0)
                if archived and await owes_anything(session, customer_id):
                    raise CustomerHasBalance()
                dollars = await dollar_balances(session, [customer_id])
                if not archived and customer.status != "active":
                    # A customer taken out of the archive counts again (BR-33), so needs a place (BR-34).
                    await require_room(session, self._today())
                updated = await session.set_customer_status(customer_id, "archived" if archived else "active")
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="customer.archived" if archived else "customer.unarchived",
                    subject_type="customer",
                    subject_id=customer_id,
                )
                return customer_body(updated, balance, None if dollars is None else dollars.get(customer_id, 0))

            return await idempotency.run_once(
                session,
                key=key,
                operation=op.name,
                user_id=user_id,
                request={"customer": str(customer_id)},
                action=apply,
            )


UNSET = _UNSET
