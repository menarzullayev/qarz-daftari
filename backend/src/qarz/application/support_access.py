"""Support access (REQ-059; BR-31; INV-10).

An administrator sees nothing of a shop's customers and entries. When a shop needs help they open a
support access for that one shop, with a reason, for at most 24 hours. From then until it ends they may
look at the shop's customers and entries, and nothing more: there is no operation here that changes a
shop's data. The owner is told when it is opened and when the administrator closes it, sees it and its
history, and can end it at any moment. Everything the administrator opens, reads or is refused goes to
the admin audit, and what they did inside the shop goes to the shop's own activity log as well.

The administrator's methods assume the caller already passed `AdminAccess.require_admin`.
"""

import base64
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency
from qarz.application.admin_access import AdminRequestKeys
from qarz.application.chat_texts import say
from qarz.application.customers import list_customers_in
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.ledger_service import customer_detail_in
from qarz.application.operations import admin_operation, operation
from qarz.application.ports import PlatformSession, Storage, SupportAccessRow, SupportChange
from qarz.application.shops import require_member
from qarz.domain import support_access as rules
from qarz.domain.access import Capability
from qarz.domain.promise import TASHKENT, tashkent_date

OPEN_SUPPORT = admin_operation("admin.support.open")
CLOSE_SUPPORT = admin_operation("admin.support.close")
LIST_SUPPORT = admin_operation("admin.support.list")
SUPPORT_LIST_CUSTOMERS = admin_operation("admin.support.customers.list")
SUPPORT_READ_CUSTOMER = admin_operation("admin.support.customers.read")
# The owner's side: the subscription, the staff and this are the owner's (authorization table).
LIST_SHOP_SUPPORT = operation("shop.support_access.list", Capability.ADMINISTER_SHOP)
END_SHOP_SUPPORT = operation("shop.support_access.end", Capability.ADMINISTER_SHOP)

MAX_PAGE = 100


class SupportAccessRequired(AppError):
    """The administrator asked for a shop's data without an open support access for that shop."""

    code = "SUPPORT_ACCESS_REQUIRED"


class SupportAccessAlreadyOpen(AppError):
    code = "SUPPORT_ACCESS_ALREADY_OPEN"


class SupportAccessNotOpen(AppError):
    """There is no open support access to close: it ran out, was closed already, or never was."""

    code = "SUPPORT_ACCESS_NOT_OPEN"


def _encode_cursor(at: datetime, row_id: UUID) -> str:
    raw = json.dumps([at.isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        at, row_id = json.loads(raw)
        return datetime.fromisoformat(at), UUID(row_id)
    except (ValueError, TypeError) as error:
        raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error


def access_body(row: SupportAccessRow, now: datetime) -> dict[str, Any]:
    body = {
        "id": str(row.access_id),
        "shop_id": str(row.shop_id),
        "admin_id": str(row.admin_id),
        "reason": row.reason,
        "state": rules.state(row.starts_at, row.ends_at, row.closed_at, now),
        "starts_at": row.starts_at.isoformat(),
        "ends_at": row.ends_at.isoformat(),
        "closed_at": None if row.closed_at is None else row.closed_at.isoformat(),
        "closed_by": row.closed_by,
    }
    if row.shop_name is not None:
        body["shop_name"] = row.shop_name
    return body


def clean_reason(raw: str | None) -> str:
    reason = " ".join((raw or "").split())
    if not rules.MIN_REASON <= len(reason) <= rules.MAX_REASON:
        raise ValidationFailed({"reason": f"length must be between {rules.MIN_REASON} and {rules.MAX_REASON}"})
    return reason


class SupportAccessService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    # --- the administrator opens and closes ---------------------------------------------------------------

    @staticmethod
    async def _tell_owner(
        session: PlatformSession, change: SupportChange, shop_id: UUID, key: str, text_key: str, **values: Any
    ) -> None:
        if change.owner_tg is None:
            return
        text = say(change.owner_lang or "uz", text_key, shop=change.shop_name, **values)
        await session.enqueue(
            channel="telegram",
            recipient=str(change.owner_tg),
            payload={"text": text},
            dedupe_key=f"support:{change.access_id}:{key}",
            shop_id=shop_id,
        )

    async def open(
        self, admin_id: UUID, shop_id: UUID, reason: str | None, hours: int, request_key: str | None
    ) -> dict[str, Any]:
        """Open a support access for one shop. Active at once; the owner is told why and until when."""
        key = idempotency.validate_key(request_key)
        why = clean_reason(reason)
        if not rules.valid_hours(hours):
            raise ValidationFailed({"hours": f"must be between 1 and {rules.MAX_HOURS}"})
        now = self._now()
        ends = rules.ends_at(now, hours)
        async with self._storage.platform() as session:

            async def apply() -> dict[str, Any]:
                access_id = uuid4()
                change = await session.admin_support_open(
                    admin_id, shop_id, access_id=access_id, reason=why, now=now, ends_at=ends
                )
                if change is None:
                    raise NotFound()
                if change.already_open:
                    raise SupportAccessAlreadyOpen()
                await session.add_admin_audit(
                    admin_id=admin_id,
                    action="support.opened",
                    target_type="shop",
                    target_id=str(shop_id),
                    shop_id=shop_id,
                    reason=why,
                    detail={"access_id": str(access_id), "ends_at": ends.isoformat()},
                    now=now,
                )
                until = ends.astimezone(TASHKENT).strftime("%d.%m.%Y %H:%M")
                await self._tell_owner(session, change, shop_id, "opened", "support_opened", reason=why, until=until)
                return access_body(SupportAccessRow(access_id, shop_id, admin_id, why, now, ends, None, None), now)

            return await idempotency.run_once(
                AdminRequestKeys(session, admin_id, about_shop=shop_id),
                key=key,
                operation=OPEN_SUPPORT.name,
                user_id=admin_id,
                request={"shop": str(shop_id), "reason": why, "hours": hours},
                action=apply,
            )

    async def close(self, admin_id: UUID, shop_id: UUID, request_key: str | None) -> dict[str, Any]:
        """End the administrator's own open support access for the shop before its time."""
        key = idempotency.validate_key(request_key)
        now = self._now()
        async with self._storage.platform() as session:

            async def apply() -> dict[str, Any]:
                change = await session.admin_support_close(admin_id, shop_id, now)
                if change is None:
                    raise SupportAccessNotOpen()
                await session.add_admin_audit(
                    admin_id=admin_id,
                    action="support.closed",
                    target_type="shop",
                    target_id=str(shop_id),
                    shop_id=shop_id,
                    reason=None,
                    detail={"access_id": str(change.access_id)},
                    now=now,
                )
                await self._tell_owner(session, change, shop_id, "closed", "support_closed")
                return {"id": str(change.access_id), "shop_id": str(shop_id), "state": rules.CLOSED}

            return await idempotency.run_once(
                AdminRequestKeys(session, admin_id, about_shop=shop_id),
                key=key,
                operation=CLOSE_SUPPORT.name,
                user_id=admin_id,
                request={"shop": str(shop_id)},
                action=apply,
            )

    async def list_all(
        self, admin_id: UUID, *, shop_id: UUID | None, open_only: bool, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        """Support accesses across shops, whoever opened them: administrators see each other's."""
        if not 1 <= limit <= MAX_PAGE:
            raise ValidationFailed({"limit": f"must be between 1 and {MAX_PAGE}"})
        after = _decode_cursor(cursor) if cursor else None
        now = self._now()
        async with self._storage.platform() as session:
            rows = await session.admin_support_list(
                admin_id, shop_id=shop_id, open_only=open_only, now=now, after=after, limit=limit + 1
            )
        page, more = rows[:limit], len(rows) > limit
        return {
            "items": [access_body(row, now) for row in page],
            "next_cursor": _encode_cursor(page[-1].starts_at, page[-1].access_id) if more else None,
        }

    # --- what the administrator may read while it is open -------------------------------------------------

    async def _enter(self, admin_id: UUID, shop_id: UUID, action: str, detail: dict[str, Any]) -> UUID:
        """Check the support access and write the audit row, before anything of the shop is read.

        Without an open access the attempt itself is audited and refused (PRD, acceptance of FEAT-021:
        "refused and logged"). The refusal is raised after the transaction, so that its audit row stays.
        """
        now = self._now()
        async with self._storage.platform() as session:
            access = await session.admin_open_shop(admin_id, shop_id, now)
            await session.add_admin_audit(
                admin_id=admin_id,
                action=action if access is not None else "support.refused",
                target_type="shop",
                target_id=str(shop_id),
                shop_id=shop_id,
                reason=None,
                detail={**detail, "access_id": str(access[0])} if access is not None else {"attempted": action},
                now=now,
            )
        if access is None:
            raise SupportAccessRequired()
        return access[0]

    async def list_customers(
        self, admin_id: UUID, shop_id: UUID, *, query: str | None, status: str, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        access_id = await self._enter(admin_id, shop_id, "support.customers_listed", {})
        async with self._storage.tenant(shop_id) as session:
            body = await list_customers_in(session, query=query, status=status, cursor=cursor, limit=limit)
            await session.record_admin_activity(
                admin_id=admin_id,
                action="support_access.customers_listed",
                subject_type="support_access",
                subject_id=access_id,
            )
            return body

    async def read_customer(self, admin_id: UUID, shop_id: UUID, customer_id: UUID) -> dict[str, Any]:
        await self._enter(admin_id, shop_id, "support.customer_viewed", {"customer_id": str(customer_id)})
        async with self._storage.tenant(shop_id) as session:
            body = await customer_detail_in(session, customer_id, self._today(), self._now())
            await session.record_admin_activity(
                admin_id=admin_id,
                action="support_access.customer_viewed",
                subject_type="customer",
                subject_id=customer_id,
            )
            return body

    # --- the owner sees it and can end it -----------------------------------------------------------------

    async def list_for_shop(self, user_id: UUID, shop_id: UUID, *, cursor: str | None, limit: int) -> dict[str, Any]:
        """This shop's support accesses, newest first: the open one, if any, and the history."""
        async with self._storage.tenant(shop_id) as session:
            # In every mode, a suspended shop included: an owner may always see who can look at their data.
            await require_member(session, user_id, LIST_SHOP_SUPPORT)
            if not 1 <= limit <= MAX_PAGE:
                raise ValidationFailed({"limit": f"must be between 1 and {MAX_PAGE}"})
            before = _decode_cursor(cursor) if cursor else None
            rows = await session.support_accesses(before=before, limit=limit + 1)
            now = self._now()
            page, more = rows[:limit], len(rows) > limit
            return {
                "items": [access_body(row, now) for row in page],
                "next_cursor": _encode_cursor(page[-1].starts_at, page[-1].access_id) if more else None,
            }

    async def end_by_owner(
        self, user_id: UUID, shop_id: UUID, access_id: UUID, request_key: str | None
    ) -> dict[str, Any]:
        """The owner ends a support access at once. Allowed in every mode, a suspended shop included."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, END_SHOP_SUPPORT)
            key = idempotency.validate_key(request_key)
            now = self._now()

            async def apply() -> dict[str, Any]:
                row = await session.support_access(access_id, for_update=True)
                if row is None:
                    raise NotFound()
                if rules.state(row.starts_at, row.ends_at, row.closed_at, now) != rules.ACTIVE:
                    raise SupportAccessNotOpen()
                await session.end_support_access(access_id, now)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="support_access.ended",
                    subject_type="support_access",
                    subject_id=access_id,
                )
                ended = SupportAccessRow(
                    row.access_id, row.shop_id, row.admin_id, row.reason, row.starts_at, row.ends_at, now, "owner"
                )
                return access_body(ended, now)

            return await idempotency.run_once(
                session,
                key=key,
                operation=END_SHOP_SUPPORT.name,
                user_id=user_id,
                request={"access": str(access_id)},
                action=apply,
            )
