"""What a linked customer can see and do about their own account (REQ-019 to REQ-021, REQ-029).

A customer is not a member of the shop. Their link is resolved first, outside any shop (it must be theirs
and live); only then is the shop's transaction opened, and everything read or written in it is narrowed
to the one customer record behind the link (INV-10).
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from qarz.application import removal
from qarz.application.errors import NotFound
from qarz.application.ledger_service import HISTORY_PAGE
from qarz.application.operations import self_operation
from qarz.application.ports import DisputeRecord, Storage
from qarz.domain import ledger
from qarz.domain.access import Role
from qarz.domain.promise import tashkent_date

LIST_ACCOUNTS = self_operation("me.accounts.list")
READ_ACCOUNT = self_operation("me.accounts.read")
DISCONNECT = self_operation("me.accounts.disconnect")
REQUEST_REMOVAL = self_operation("me.accounts.removal")
OWNER_TOTALS = self_operation("me.owner_totals")


def _dispute(record: DisputeRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "id": str(record.dispute_id),
        "status": record.status,
        "reason": record.reason,
        "decline_reason": record.decline_reason,
    }


async def resolve_link(storage: Storage, user_id: UUID, link_id: UUID) -> tuple[UUID, UUID]:
    """Shop and customer behind a live link of this user. Anything else does not exist for them."""
    async with storage.platform() as session:
        found = await session.my_link(user_id, link_id)
    if found is None:
        # Not theirs, ended, or not a link at all: the same answer.
        raise NotFound()
    return found


class CustomerAccountService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def _resolve(self, user_id: UUID, link_id: UUID) -> tuple[UUID, UUID]:
        return await resolve_link(self._storage, user_id, link_id)

    async def accounts(self, user_id: UUID) -> dict[str, Any]:
        async with self._storage.platform() as session:
            rows = await session.my_accounts(user_id)
        return {
            "items": [
                {
                    "link_id": str(row.link_id),
                    "shop_name": row.shop_name,
                    "display_name": row.display_name,
                    "balance": row.balance,
                }
                for row in rows
            ]
        }

    async def account(self, user_id: UUID, link_id: UUID) -> dict[str, Any]:
        shop_id, customer_id = await self._resolve(user_id, link_id)
        async with self._storage.tenant(shop_id) as session:
            customer = await session.get_customer(customer_id, for_update=False)
            settings = await session.shop_settings()
            if customer is None or settings is None:
                raise NotFound()
            account = await session.entries_of(customer_id)
            entries = [row.entry for row in account]
            today = self._today()
            status = ledger.overdue(entries, today)
            disputes = await session.disputes_of_customer(customer_id)
            reversed_ids = {row.entry.reverses_id for row in account if row.entry.reverses_id is not None}
            newest_first = sorted(account, key=lambda row: row.entry.seq, reverse=True)
            # No note, no author and no payment indicator: those are the shop's own (REQ-045).
            return {
                "link_id": str(link_id),
                "shop_name": settings.name,
                "display_name": customer.display_name,
                "balance": ledger.balance(entries),
                "overdue": {"amount": status.overdue_amount, "due_today": status.due_today_amount},
                "removal_requested": await session.removal_waiting(customer_id),
                "entries": [
                    {
                        "id": str(row.entry.id),
                        "kind": row.entry.kind.value,
                        "amount": row.entry.amount,
                        "created_at": row.entry.created_at.isoformat(),
                        "promised_date": None
                        if row.entry.promised_date is None
                        else row.entry.promised_date.isoformat(),
                        "reverses_id": None if row.entry.reverses_id is None else str(row.entry.reverses_id),
                        "reversed": row.entry.id in reversed_ids,
                        "disputed": row.entry.disputed,
                        "dispute": _dispute(disputes.get(row.entry.id)),
                    }
                    for row in newest_first[:HISTORY_PAGE]
                ],
                "entries_total": len(account),
            }

    async def disconnect(self, user_id: UUID, link_id: UUID) -> dict[str, Any]:
        shop_id, _ = await self._resolve(user_id, link_id)
        async with self._storage.platform() as session:
            if not await session.end_my_link(user_id, shop_id):
                raise NotFound()
        return {"disconnected": True}

    async def request_removal(self, user_id: UUID, link_id: UUID) -> dict[str, Any]:
        shop_id, customer_id = await self._resolve(user_id, link_id)
        async with self._storage.tenant(shop_id) as session:
            customer = await session.get_customer(customer_id, for_update=True)
            if customer is None:
                raise NotFound()
            balance = ledger.balance([row.entry for row in await session.entries_of(customer_id)])
            outcome = await removal.request_removal(session, customer_id, balance, self._now())
        return {"removed": outcome == "done", "waiting_for_balance": balance if outcome == "waiting" else None}

    async def owner_totals(self, user_id: UUID) -> dict[str, Any]:
        """Totals of every shop the caller owns, and their sum (REQ-065)."""
        async with self._storage.platform() as session:
            shops = await session.my_memberships(user_id)
        today = self._today()
        items: list[dict[str, Any]] = []
        for shop in shops:
            async with self._storage.tenant(shop.shop_id) as tenant:
                # The list above only says where to look; ownership is decided inside each shop.
                membership = await tenant.active_membership(user_id)
                if membership is None or membership.role is not Role.OWNER:
                    continue
                totals = await tenant.shop_totals(today)
            items.append(
                {
                    "shop_id": str(shop.shop_id),
                    "name": shop.name,
                    "outstanding": totals.outstanding,
                    "debtors": totals.debtors,
                    "overdue": totals.overdue_amount,
                    "due_today": totals.due_today_amount,
                }
            )
        return {
            "items": items,
            "total": {
                key: sum(int(item[key]) for item in items) for key in ("outstanding", "debtors", "overdue", "due_today")
            },
        }
