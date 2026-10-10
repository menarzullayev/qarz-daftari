"""The stock as staff read and set it up: items with what is on hand, barcodes, movements, the report.

Everything here is behind the platform switch `stock_on`: while it is off every operation answers as a
route that does not exist. What changes the stock is elsewhere: documents (`stock_documents`) and the
sale hooks (`stock_moves`).

Cost and margin are sensitive: a seller sees what is on hand, not what it was bought for. Every body
built here leaves the cost figures out unless the member holds `stock.costs.view`; they are absent, not
null, so nothing about them can be read from the answer.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from qarz.application import idempotency
from qarz.application.authorization import may
from qarz.application.customers import MAX_PAGE, decode_cursor, encode_cursor, require_viewable, require_writable
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.export_texts import in_every_language
from qarz.application.operations import operation
from qarz.application.ports import Membership, Storage, TenantSession
from qarz.application.shared_catalog_feed import propose_barcodes
from qarz.application.shops import require_member
from qarz.application.stock_cash import cash_book_on
from qarz.application.stock_currency import shop_currencies
from qarz.application.stock_moves import require_on
from qarz.application.stock_ports import MovementRecord, StockItem
from qarz.domain import permissions, stock
from qarz.domain.access import Capability
from qarz.domain.goods import format_qty
from qarz.domain.names import normalize_name
from qarz.domain.promise import tashkent_date

READ_SETTINGS = operation("stock.settings.read", Capability.RECORD)
UPDATE_SETTINGS = operation("stock.settings.update", Capability.MANAGE)
LIST_ITEMS = operation("stock.items.list", Capability.RECORD)
READ_ITEM = operation("stock.items.read", Capability.RECORD)
UPDATE_ITEM = operation("stock.items.update", Capability.MANAGE)
LOOKUP = operation("stock.lookup", Capability.RECORD)
LIST_MOVEMENTS = operation("stock.movements.list", Capability.RECORD)
READ_REPORT = operation("stock.report", Capability.MANAGE)

ITEM_FILTERS = ("all", "tracked", "low")
REPORT_ROWS = 50
MIN_IDLE_DAYS, MAX_IDLE_DAYS, DEFAULT_IDLE_DAYS = 1, 365, 30


class BarcodeTaken(AppError):
    """Another item of the shop already has this barcode."""

    code = "BARCODE_TAKEN"


class ItemNotCountable(AppError):
    """The item cannot be counted in stock as it is. The field says why."""

    code = "ITEM_NOT_COUNTABLE"


def sees_costs(actor: Membership) -> bool:
    return may(actor, permissions.STOCK_COSTS_VIEW)


def cost_body(item: StockItem) -> dict[str, Any]:
    level = item.level
    average = level.average
    return {
        "currency": level.currency,
        "average": None if average is None else stock.money(average),
        "value": level.value,
        "margin": stock.margin(item.price, level),
    }


def item_body(item: StockItem, barcodes: list[str], *, costs: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(item.item_id),
        "name": item.name,
        "unit": item.unit,
        "price": item.price,
        "status": item.status,
        "learned": item.learned,
        "tracked": item.tracked,
        "on_hand": format_qty(item.level.on_hand),
        "low_stock": None if item.low_stock is None else format_qty(item.low_stock),
        "low": item.tracked and stock.is_low(item.level.on_hand, item.low_stock),
        "barcodes": barcodes,
        "last_sale_at": None if item.last_sale_at is None else item.last_sale_at.isoformat(),
    }
    if costs:
        body["cost"] = cost_body(item)
    return body


def movement_body(movement: MovementRecord, *, costs: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(movement.movement_id),
        "seq": movement.item_seq,
        "kind": movement.kind,
        "qty": format_qty(movement.qty),
        "on_hand_after": format_qty(movement.on_hand_after),
        "reason": movement.reason,
        "document": None
        if movement.document_id is None
        else {
            "id": str(movement.document_id),
            "kind": movement.document_kind,
            "number": movement.document_number,
        },
        "ledger_entry_id": None if movement.ledger_entry_id is None else str(movement.ledger_entry_id),
        "reverses_id": None if movement.reverses_id is None else str(movement.reverses_id),
        "reversed": movement.is_reversed,
        "author_id": str(movement.author_id),
        "created_at": movement.created_at.isoformat(),
    }
    if movement.sale_total is not None:
        # What a line was sold for is on the sale itself; it is not a cost.
        body["sale_total"] = movement.sale_total
    if costs:
        body["cost"] = {
            "currency": movement.currency,
            "unit_cost": movement.unit_cost,
            "total": movement.cost_total,
            "value_after": movement.value_after,
        }
    return body


def settings_body(*, refuse_negative: bool, currencies: tuple[str, ...], cash_book: bool) -> dict[str, Any]:
    return {
        "refuse_negative": refuse_negative,
        # Whether money the stock pays out is also written to the cash book: a client then asks how it
        # was paid (cash, card, transfer).
        "cash_book": cash_book,
        "currencies": list(currencies),
        # Each name in all six languages, like a permission's (`GET .../permissions`): the reader's client
        # picks its own.
        "units": [
            {"key": unit.key, "label": in_every_language(f"unit_{unit.key}"), "weighed": unit.weighed}
            for unit in stock.UNITS
        ],
        "write_off_reasons": [
            {"key": reason, "label": in_every_language(f"reason_{reason}")} for reason in stock.WRITE_OFF_REASONS
        ],
        "document_kinds": list(stock.DOCUMENT_KINDS),
    }


def check_countable(item: StockItem, unit: str) -> None:
    """Refuse to count an item that is an alias, still unreviewed, or in a unit the stock does not use."""
    if item.merged_into is not None:
        raise ItemNotCountable({"item": str(item.item_id), "why": "merged"})
    if item.learned:
        raise ItemNotCountable({"item": str(item.item_id), "why": "learned"})
    if unit not in stock.UNIT_KEYS:
        raise ItemNotCountable({"item": str(item.item_id), "why": "unit", "unit": unit})


async def items_page_in(
    session: TenantSession, actor: Membership, *, query: str | None, only: str, cursor: str | None, limit: int
) -> dict[str, Any]:
    fields: dict[str, str] = {}
    if not 1 <= limit <= MAX_PAGE:
        fields["limit"] = f"must be between 1 and {MAX_PAGE}"
    if only not in ITEM_FILTERS:
        fields["filter"] = "must be all, tracked or low"
    if query is not None and len(query) > 80:
        fields["q"] = "at most 80 characters"
    if fields:
        raise ValidationFailed(fields)
    after: tuple[str, UUID] | None = None
    if cursor:
        name_norm, item_id = decode_cursor(cursor, 2)
        try:
            after = (name_norm, UUID(item_id))
        except ValueError as error:
            raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error
    rows = await session.stock_items(
        name_part=normalize_name(query or "") or None, only=only, after=after, limit=limit + 1
    )
    page, more = rows[:limit], len(rows) > limit
    codes = await session.barcodes_of([item.item_id for item in page])
    costs = sees_costs(actor)
    return {
        "items": [item_body(item, codes.get(item.item_id, []), costs=costs) for item in page],
        "next_cursor": encode_cursor(page[-1].name_norm, page[-1].item_id) if more else None,
    }


class StockService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def switched_on(self) -> bool:
        async with self._storage.platform() as session:
            return await session.platform_setting(stock.SWITCH) is True

    async def require_on(self) -> None:
        """Called for every route of the stock and of the suppliers before anything else."""
        if not await self.switched_on():
            raise NotFound()

    async def settings(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, READ_SETTINGS)
            await require_viewable(session, actor, self._today())
            return settings_body(
                refuse_negative=await session.stock_refuse_negative(),
                currencies=await shop_currencies(session),
                cash_book=await cash_book_on(session),
            )

    async def update_settings(
        self, user_id: UUID, shop_id: UUID, *, refuse_negative: bool, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, UPDATE_SETTINGS)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                if await session.stock_refuse_negative() != refuse_negative:
                    await session.set_stock_refuse_negative(refuse_negative)
                    await session.record_activity(
                        membership_id=actor.membership_id,
                        action="stock.settings_changed",
                        subject_type="shop",
                        subject_id=shop_id,
                        detail={"refuse_negative": refuse_negative},
                    )
                return settings_body(
                    refuse_negative=refuse_negative,
                    currencies=await shop_currencies(session),
                    cash_book=await cash_book_on(session),
                )

            return await idempotency.run_once(
                session,
                key=key,
                operation=UPDATE_SETTINGS.name,
                user_id=user_id,
                request={"refuse_negative": refuse_negative},
                action=apply,
            )

    async def items(
        self, user_id: UUID, shop_id: UUID, *, query: str | None, only: str, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, LIST_ITEMS)
            await require_viewable(session, actor, self._today())
            return await items_page_in(session, actor, query=query, only=only, cursor=cursor, limit=limit)

    async def item(self, user_id: UUID, shop_id: UUID, item_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, READ_ITEM)
            await require_viewable(session, actor, self._today())
            item = await session.stock_item(item_id, for_update=False)
            if item is None:
                raise NotFound()
            codes = await session.barcodes_of([item_id])
            return item_body(item, codes.get(item_id, []), costs=sees_costs(actor))

    async def lookup(self, user_id: UUID, shop_id: UUID, code: str) -> dict[str, Any]:
        """The item a scanned or typed barcode names. An unknown code is "not found", like an unknown item."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, LOOKUP)
            await require_viewable(session, actor, self._today())
            try:
                clean = stock.barcode(code)
            except ValueError as error:
                raise ValidationFailed({"code": str(error)}) from error
            item_id = await session.item_by_barcode(clean)
            item = None if item_id is None else await session.stock_item(item_id, for_update=False)
            if item is None:
                raise NotFound()
            codes = await session.barcodes_of([item.item_id])
            return item_body(item, codes.get(item.item_id, []), costs=sees_costs(actor))

    async def update_item(
        self,
        user_id: UUID,
        shop_id: UUID,
        item_id: UUID,
        *,
        tracked: bool | None,
        low_stock: str | None,
        clear_low_stock: bool,
        unit: str | None,
        barcodes: list[str] | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        """Set what the stock needs to know about an item: whether it is counted, in which unit, the
        quantity it runs low at, and its barcodes. Each part that is not given stays as it is."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, UPDATE_ITEM)
            key = idempotency.validate_key(request_key)
            fields: dict[str, str] = {}
            threshold: Decimal | None = None
            codes: list[str] | None = None
            if low_stock is not None and clear_low_stock:
                fields["low_stock"] = "give a quantity or clear it, not both"
            elif low_stock is not None:
                try:
                    threshold = stock.low_stock(low_stock)
                except ValueError as error:
                    fields["low_stock"] = str(error)
            if unit is not None and unit not in stock.UNIT_KEYS:
                fields["unit"] = "must be one of the stock units"
            if barcodes is not None:
                try:
                    codes = stock.barcodes(barcodes)
                except ValueError as error:
                    fields["barcodes"] = str(error)
            if tracked is None and low_stock is None and not clear_low_stock and unit is None and barcodes is None:
                fields["_"] = "nothing to change"
            if fields:
                raise ValidationFailed(fields)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                item = await session.stock_item(item_id, for_update=True)
                if item is None:
                    raise NotFound()
                now_tracked = item.tracked if tracked is None else tracked
                now_unit = item.unit if unit is None else unit
                now_low = None if clear_low_stock else (item.low_stock if threshold is None else threshold)
                if unit is not None and unit != item.unit and item.last_seq:
                    # Quantities already recorded are in the old unit; a new unit would silently re-read them.
                    raise ValidationFailed({"unit": "the unit cannot change once the item has stock movements"})
                if now_tracked:
                    check_countable(item, now_unit)
                elif item.tracked and item.level.on_hand != 0:
                    raise ValidationFailed({"tracked": "write off or count the stock to zero before turning it off"})
                if (now_tracked, now_unit, now_low) != (item.tracked, item.unit, item.low_stock):
                    await session.set_item_stock(item_id, tracked=now_tracked, low_stock=now_low, unit=now_unit)
                if codes is not None:
                    taken = await session.replace_barcodes(item_id, codes)
                    if taken is not None:
                        raise BarcodeTaken({"code": taken})
                    # A code attached to an item picked from the platform's catalogue is proposed for it.
                    await propose_barcodes(session, item_id, codes)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="stock.item_changed",
                    subject_type="catalog_item",
                    subject_id=item_id,
                    detail={"tracked": now_tracked},
                )
                updated = await session.stock_item(item_id, for_update=False)
                assert updated is not None
                stored = await session.barcodes_of([item_id])
                return item_body(updated, stored.get(item_id, []), costs=sees_costs(actor))

            return await idempotency.run_once(
                session,
                key=key,
                operation=UPDATE_ITEM.name,
                user_id=user_id,
                request={
                    "item": str(item_id),
                    "tracked": tracked,
                    "low_stock": None if threshold is None else format_qty(threshold),
                    "clear_low_stock": clear_low_stock,
                    "unit": unit,
                    "barcodes": codes,
                },
                action=apply,
            )

    async def movements(
        self, user_id: UUID, shop_id: UUID, item_id: UUID, *, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, LIST_MOVEMENTS)
            await require_viewable(session, actor, self._today())
            if not 1 <= limit <= MAX_PAGE:
                raise ValidationFailed({"limit": f"must be between 1 and {MAX_PAGE}"})
            before: int | None = None
            if cursor:
                (raw,) = decode_cursor(cursor, 1)
                if not raw.isascii() or not raw.isdigit():
                    raise ValidationFailed({"cursor": "not a cursor returned by this API"})
                before = int(raw)
            if await session.stock_item(item_id, for_update=False) is None:
                raise NotFound()
            rows = await session.movements_of_item(item_id, before_seq=before, limit=limit + 1)
            page, more = rows[:limit], len(rows) > limit
            costs = sees_costs(actor)
            return {
                "movements": [movement_body(row, costs=costs) for row in page],
                "next_cursor": encode_cursor(page[-1].item_seq) if more else None,
            }

    async def report(self, user_id: UUID, shop_id: UUID, *, days: int) -> dict[str, Any]:
        """What the stock is worth, and where money sits or leaks: what does not sell, what sold at a loss.

        Opened by `stock.costs.view` alone: the whole report is about cost. Amounts of different
        currencies are side by side, never added; the margin is of the items whose cost is kept in so'm,
        the currency they are sold in.
        """
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, READ_REPORT)
            await require_viewable(session, actor, self._today())
            if not MIN_IDLE_DAYS <= days <= MAX_IDLE_DAYS:
                raise ValidationFailed({"days": f"must be between {MIN_IDLE_DAYS} and {MAX_IDLE_DAYS}"})
            since = self._now() - timedelta(days=days)
            totals = await session.stock_totals()
            idle = await session.stock_idle(unsold_since=since, limit=REPORT_ROWS + 1)
            below = await session.stock_sold_below_cost(since=since, limit=REPORT_ROWS + 1)
            low = await session.stock_items(name_part=None, only="low", after=None, limit=REPORT_ROWS + 1)
            sold = await session.stock_sold(since=since, limit=REPORT_ROWS + 1)
            cash_sales = await session.sale_totals(since=since, until=self._now(), item_id=None, seller_id=None)
            cost_uzs = totals.cost.get("UZS", 0)
            return {
                "days": days,
                "totals": {
                    "items": totals.items,
                    "low": totals.low,
                    "cost": [{"currency": currency, "value": value} for currency, value in totals.cost.items()],
                    "selling": totals.selling,
                    "margin": {
                        "selling": totals.selling_of_costed,
                        "cost": cost_uzs,
                        "margin": totals.selling_of_costed - cost_uzs,
                    },
                },
                "not_sold": {
                    "items": [item_body(item, [], costs=True) for item in idle[:REPORT_ROWS]],
                    "more": len(idle) > REPORT_ROWS,
                },
                "sold_below_cost": {
                    "sales": [
                        {
                            "item_id": str(sale.item_id),
                            "name": sale.name,
                            "unit": sale.unit,
                            "qty": format_qty(sale.qty),
                            "sale_total": sale.sale_total,
                            "cost_total": sale.cost_total,
                            "loss": sale.cost_total - sale.sale_total,
                            "created_at": sale.created_at.isoformat(),
                        }
                        for sale in below[:REPORT_ROWS]
                    ],
                    "more": len(below) > REPORT_ROWS,
                },
                "low_stock": {
                    "items": [item_body(item, [], costs=True) for item in low[:REPORT_ROWS]],
                    "more": len(low) > REPORT_ROWS,
                },
                # What sold in those days, per counted item: credit sales and cash sales together, with
                # how much of it was for cash. The margin is of the sales whose cost is known in so'm.
                "sold": {
                    "items": [
                        {
                            "item_id": str(row.item_id),
                            "name": row.name,
                            "unit": row.unit,
                            "qty": format_qty(row.qty),
                            "revenue": row.revenue,
                            "cost": row.cost,
                            "margin": row.costed_revenue - row.cost,
                            "cash_qty": format_qty(row.cash_qty),
                            "cash_revenue": row.cash_revenue,
                        }
                        for row in sold[:REPORT_ROWS]
                    ],
                    "more": len(sold) > REPORT_ROWS,
                },
                # The cash sales of those days that stand, every line of them, counted item or not.
                "cash_sales": {
                    "count": cash_sales.count,
                    "total": cash_sales.total,
                    "by_method": [
                        {"method": method, "total": total} for method, total in sorted(cash_sales.by_method.items())
                    ],
                },
            }
