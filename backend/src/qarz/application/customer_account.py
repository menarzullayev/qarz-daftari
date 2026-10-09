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
from qarz.application.currencies import USD, UZS, dollars_on, platform_dollars, tag
from qarz.application.errors import NotFound
from qarz.application.goods_lines import line_body
from qarz.application.ledger_service import (
    HISTORY_PAGE,
    balances_of,
    date_request_body,
    latest_date_requests,
    payment_history_body,
    promise_body,
)
from qarz.application.notice_view import NOTICES_SHOWN, notice_body
from qarz.application.operations import self_operation
from qarz.application.ports import DisputeRecord, ShopTotals, Storage
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
            dollars = await platform_dollars(session)
        return {
            "items": [
                {
                    "link_id": str(row.link_id),
                    "shop_name": row.shop_name,
                    "display_name": row.display_name,
                    "balance": row.balance,
                    # Beside the so'm balance, never added to it; only for a shop that works in dollars.
                    **({"usd": {"balance": row.balance_usd}} if dollars and row.usd_on else {}),
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
            dollars = await dollars_on(session)
            account = [row for row in await session.entries_of(customer_id) if dollars or row.entry.currency is UZS]
            entries = ledger.in_currency([row.entry for row in account], UZS)
            in_dollars = ledger.in_currency([row.entry for row in account], USD)
            today = self._today()
            status = ledger.overdue(entries, today)
            usd_status = ledger.overdue(in_dollars, today)
            disputes = await session.disputes_of_customer(customer_id)
            reversed_ids = {row.entry.reverses_id for row in account if row.entry.reverses_id is not None}
            newest_first = sorted(account, key=lambda row: row.entry.seq, reverse=True)
            shown = newest_first[:HISTORY_PAGE]
            lines = await session.goods_lines_of([row.entry.id for row in shown])
            promises = await session.promises_of([row.entry.id for row in shown])
            requests = latest_date_requests(await session.date_requests_of_customer(customer_id))
            # No note and no author: those are the shop's own (REQ-045). The customer does see their own
            # payment history indicator, the same figures the shop's staff see, calculated from this
            # shop's records only (BR-9; the founder's decision of 2026-10-08, DEC-066, changing DEC-033).
            return {
                "link_id": str(link_id),
                "shop_name": settings.name,
                "display_name": customer.display_name,
                "balance": ledger.balance(entries),
                "overdue": {"amount": status.overdue_amount, "due_today": status.due_today_amount},
                "payment_history": payment_history_body(ledger.payment_history(entries, today)),
                # The dollar book's own figures, beside the so'm ones and never added to them.
                **(
                    {
                        "usd": {
                            "balance": ledger.balance(in_dollars),
                            "overdue": {"amount": usd_status.overdue_amount, "due_today": usd_status.due_today_amount},
                            "payment_history": payment_history_body(ledger.payment_history(in_dollars, today)),
                        }
                    }
                    if dollars
                    else {}
                ),
                "removal_requested": await session.removal_waiting(customer_id),
                "entries": [
                    {
                        "id": str(row.entry.id),
                        "kind": row.entry.kind.value,
                        **tag({}, row.entry.currency),
                        "amount": row.entry.amount,
                        "created_at": row.entry.created_at.isoformat(),
                        "promised_date": None
                        if row.entry.promised_date is None
                        else row.entry.promised_date.isoformat(),
                        "reverses_id": None if row.entry.reverses_id is None else str(row.entry.reverses_id),
                        "reversed": row.entry.id in reversed_ids,
                        "disputed": row.entry.disputed,
                        "dispute": _dispute(disputes.get(row.entry.id)),
                        "lines": [line_body(line) for line in lines.get(row.entry.id, [])],
                        # Every promised date the entry has carried, oldest first (INV-9).
                        "promises": [promise_body(promise) for promise in promises.get(row.entry.id, ())],
                        "date_request": None
                        if row.entry.id not in requests
                        else date_request_body(requests[row.entry.id]),
                    }
                    for row in shown
                ],
                "entries_total": len(account),
                # The customer's own payment notices with their outcome, newest first (REQ-061).
                "payment_notices": [
                    notice_body(record, self._now())
                    for record in await session.notices_of_customer(customer_id, NOTICES_SHOWN)
                ],
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
            # Every currency counts, whether or not the shop shows dollars now (BR-32).
            balances = balances_of([row.entry for row in await session.entries_of(customer_id)])
            outcome = await removal.request_removal(session, customer_id, any(balances.values()), self._now())
            waiting = outcome == "waiting"
            body: dict[str, Any] = {"removed": not waiting, "waiting_for_balance": balances[UZS] if waiting else None}
            if waiting and await dollars_on(session):
                body["usd"] = {"waiting_for_balance": balances[USD]}
        return body

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
                in_dollars = await tenant.shop_totals(today, USD) if await dollars_on(tenant) else None
            item = _owner_figures(totals)
            if in_dollars is not None:
                item["usd"] = _owner_figures(in_dollars)
            items.append({"shop_id": str(shop.shop_id), "name": shop.name, **item})
        total: dict[str, Any] = {key: sum(int(item[key]) for item in items) for key in _OWNER_KEYS}
        with_dollars = [item["usd"] for item in items if "usd" in item]
        if with_dollars:
            # The dollars of the shops that work in them, added to each other and to nothing else.
            total["usd"] = {key: sum(int(item[key]) for item in with_dollars) for key in _OWNER_KEYS}
        return {"items": items, "total": total}


_OWNER_KEYS = ("outstanding", "debtors", "overdue", "due_today")


def _owner_figures(totals: ShopTotals) -> dict[str, Any]:
    return {
        "outstanding": totals.outstanding,
        "debtors": totals.debtors,
        "overdue": totals.overdue_amount,
        "due_today": totals.due_today_amount,
    }
