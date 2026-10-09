"""A sale for cash, without a customer (the founder's decision of 2026-10-09; BR-98 to BR-104).

Until this existed the stock was drawn only by a credit sale with goods lines and by documents, so goods
sold for cash stayed on the books. A cash sale is goods lines sold at the counter: the counted items leave
the stock, the money is income of the cash book while that is on, and nobody's account is touched: it
names no customer and no supplier.

It is stored as a stock document of the kind `sale`, with the lines and the movements every document
has, but it has no life as a draft: it is written and posted in one transaction, so a sale that is
refused leaves nothing behind, not a number either. It is taken back like a document: cancelled with a
reason, which brings the goods back at the cost they left with and cancels the cash entry.

- A line names an item of the catalogue, a quantity and a price. The price is the item's unless the
  seller gives another; anyone who may sell may do that. An item that is not counted is sold without a
  movement: the sale still records it and its money.
- Counted goods leave at the weighted average like any other issue (`qarz.domain.stock.go_out`), and
  below zero by the shop's own rule: a warning, or a refusal when the shop turned that on.
- Money: so'm only. Selling prices are so'm everywhere; a shop that also works in dollars still sells
  from the shelf in so'm.
- Cost and margin are in the answer of a member who holds `stock.costs.view` and absent for anyone
  else, as is the warning that a line was sold below what it cost.

The locks, in the order every writer of the stock takes them: the request key, the number of the kind,
the items in the order of their identifiers (`lock_items`), then the cash book's categories.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency, stock_cash
from qarz.application.authorization import may
from qarz.application.chat_texts import say
from qarz.application.customers import MAX_PAGE, decode_cursor, encode_cursor, require_viewable, require_writable
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.operations import operation
from qarz.application.ports import Membership, Storage, TenantSession
from qarz.application.shops import require_member
from qarz.application.stock import sees_costs
from qarz.application.stock_moves import lock_items, move, require_on, reverse_movements
from qarz.application.stock_ports import DocumentLine, DocumentRecord, SaleCost, StockItem
from qarz.domain import cash, permissions, stock
from qarz.domain import suppliers as supplier_rules
from qarz.domain.access import Capability
from qarz.domain.cash import Method
from qarz.domain.goods import format_qty
from qarz.domain.promise import TASHKENT, tashkent_date

RECORD_SALE = operation("stock.sales.create", Capability.RECORD)
LIST_SALES = operation("stock.sales.list", Capability.RECORD)
READ_SALE = operation("stock.sales.read", Capability.RECORD)
CANCEL_SALE = operation("stock.sales.cancel", Capability.MANAGE)

UZS = "UZS"
# The longest stretch of days one list covers, like a report's.
MAX_DAYS = 366
# The note of the cash entry, in the shop's language (a text of `chat_texts`).
_CASH_NOTE = "stock_sale_note"


class SaleCancelled(AppError):
    """The sale was already taken back."""

    code = "SALE_CANCELLED"


@dataclass(frozen=True)
class SaleLineRequest:
    item_id: UUID
    qty: str
    price: int | None


@dataclass(frozen=True)
class SaleRequest:
    lines: Sequence[SaleLineRequest]
    method: str | None
    currency: str | None
    note: str | None


@dataclass(frozen=True)
class CleanSaleLine:
    item_id: UUID
    qty: Decimal
    price: int | None  # None: the item's own price


@dataclass(frozen=True)
class CleanSale:
    lines: list[CleanSaleLine]
    method: Method
    note: str | None


def clean_sale(request: SaleRequest) -> CleanSale:
    """Check the shape of a sale without touching storage. Field names carry the line's position."""
    fields: dict[str, str] = {}
    if request.currency not in (None, UZS):
        fields["currency"] = "a cash sale is in UZS: selling prices are kept in so'm"
    method = cash.DEFAULT_METHOD
    if request.method is not None:
        parsed = cash.parse_method(request.method)
        if parsed is None:
            fields["method"] = "must be cash, card or transfer"
        else:
            method = parsed
    note: str | None = None
    try:
        note = supplier_rules.note(request.note, limit=stock.MAX_NOTE)
    except ValueError as error:
        fields["note"] = str(error)
    if not 1 <= len(request.lines) <= stock.MAX_SALE_LINES:
        fields["lines"] = f"between 1 and {stock.MAX_SALE_LINES} lines"
    lines: list[CleanSaleLine] = []
    seen: set[UUID] = set()
    for index, raw in enumerate(request.lines[: stock.MAX_SALE_LINES]):
        prefix = f"lines.{index}"
        if raw.item_id in seen:
            fields[f"{prefix}.item_id"] = "listed twice: add the quantities together"
        seen.add(raw.item_id)
        qty = stock.ZERO
        try:
            qty = stock.quantity(raw.qty)
        except ValueError as error:
            fields[f"{prefix}.qty"] = str(error)
        price: int | None = None
        if raw.price is not None:
            try:
                price = stock.sale_price(raw.price)
            except ValueError as error:
                fields[f"{prefix}.price"] = str(error)
        lines.append(CleanSaleLine(raw.item_id, qty, price))
    if fields:
        raise ValidationFailed(fields)
    return CleanSale(lines, method, note)


def _fingerprint(request: SaleRequest) -> dict[str, Any]:
    return {
        "method": request.method,
        "currency": request.currency,
        "note": request.note,
        "lines": [{"item": line.item_id, "qty": line.qty, "price": line.price} for line in request.lines],
    }


def _line_body(line: DocumentLine, item: StockItem | None, cost: SaleCost | None, *, costs: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "line_no": line.line_no,
        "item": {
            "id": str(line.item_id),
            "name": "" if item is None else item.name,
            "unit": "" if item is None else item.unit,
        },
        "qty": format_qty(line.qty),
        "price": line.unit_cost,
        "line_total": line.line_total,
        # Whether the line took anything out of the stock: an item that is not counted has no movement.
        "counted": cost is not None,
    }
    if costs:
        known = cost is not None and cost.cost_total is not None
        body["cost"] = {
            "currency": None if cost is None else cost.currency,
            "total": cost.cost_total if cost is not None and known else None,
            "margin": None
            if cost is None or line.line_total is None
            else stock.sale_margin(line.line_total, cost.cost_total, cost.currency),
        }
    return body


def sale_summary(
    document: DocumentRecord,
    lines: Sequence[DocumentLine],
    items: dict[UUID, StockItem],
    actor: Membership,
    roles: dict[UUID, str],
) -> dict[str, Any]:
    """A sale as a row of a list: what was sold and for how much. Nothing of cost is in it."""
    return {
        "id": str(document.document_id),
        "number": document.number,
        "status": document.status,
        "day": document.doc_date.isoformat(),
        "created_at": document.created_at.isoformat(),
        "created_by": str(document.created_by),
        "seller_role": roles.get(document.created_by),
        "mine": document.created_by == actor.membership_id,
        "method": document.method,
        "currency": document.currency,
        "total": document.total,
        "note": document.note,
        "cancelled_at": None if document.cancelled_at is None else document.cancelled_at.isoformat(),
        "cancel_reason": document.cancel_reason,
        "lines": [_line_body(line, items.get(line.item_id), None, costs=False) | {"counted": None} for line in lines],
    }


async def _roles(session: TenantSession) -> dict[UUID, str]:
    return {member.membership_id: member.role.value for member in await session.list_members()}


async def sale_body_in(session: TenantSession, actor: Membership, document: DocumentRecord) -> dict[str, Any]:
    """A sale in full: its lines, and for a member who may see cost what the goods cost and earned."""
    costs = sees_costs(actor)
    lines = await session.document_lines(document.document_id)
    items = await session.stock_items_by_ids(sorted({line.item_id for line in lines}))
    moved = await session.sale_costs(document.document_id)
    body = sale_summary(document, lines, items, actor, await _roles(session))
    body["lines"] = [_line_body(line, items.get(line.item_id), moved.get(line.line_no), costs=costs) for line in lines]
    body["in_cash_book"] = await session.cash_entry_of_document(document.document_id)
    if costs:
        margins = [row["cost"]["margin"] for row in body["lines"] if row["counted"]]
        known = [margin for margin in margins if margin is not None]
        body["cost"] = {
            "total": sum(row["cost"]["total"] for row in body["lines"] if row["cost"]["margin"] is not None),
            "margin": sum(known) if known else None,
            # False when a counted line has no cost in so'm: the margin is then of the others alone.
            "complete": len(known) == len(margins),
        }
    return body


async def sell_in(
    session: TenantSession, actor: Membership, clean: CleanSale, *, now: datetime
) -> tuple[DocumentRecord, list[dict[str, str]]]:
    """Record a checked sale inside a transaction the caller has opened and authorized.

    Returns the posted document and what the seller should be told. Anything that refuses it raises
    before the caller's transaction ends, so nothing of it stays.
    """
    document_id = uuid4()
    number = await session.next_document_number(stock.DOC_SALE)
    items = await lock_items(session, [line.item_id for line in clean.lines])
    fields: dict[str, str] = {}
    priced: list[DocumentLine] = []
    for index, line in enumerate(clean.lines):
        item = items.get(line.item_id)
        if item is None or item.merged_into is not None:
            # An alias stands for another item: the client sells the one it was merged into.
            fields[f"lines.{index}.item_id"] = "not an item of this shop"
            continue
        price = item.price if line.price is None else line.price
        try:
            total = stock.sale_line_total(line.qty, stock.sale_price(price))
        except ValueError as error:
            fields[f"lines.{index}.price" if line.price is not None else f"lines.{index}.qty"] = str(error)
            continue
        priced.append(DocumentLine(index + 1, line.item_id, line.qty, price, total))
    total = sum(line.line_total or 0 for line in priced)
    if not fields and total > stock.MAX_DOCUMENT_TOTAL:
        fields["lines"] = "the total is too large for one sale"
    if fields:
        raise ValidationFailed(fields)

    await session.insert_document(
        document_id=document_id,
        kind=stock.DOC_SALE,
        number=number,
        doc_date=tashkent_date(now),
        supplier_id=None,
        customer_id=None,
        currency=UZS,
        total=total,
        paid=total,
        reason=None,
        note=clean.note,
        # Never read: the database asks a document to hold a draft until it is posted, a moment later.
        draft={"lines": len(priced)},
        created_by=actor.membership_id,
        created_at=now,
        method=clean.method.value,
    )
    await session.add_document_lines(document_id, priced)

    refuse = await session.stock_refuse_negative()
    show_costs = sees_costs(actor)
    warnings: list[dict[str, str]] = []
    for sold in priced:
        item = items[sold.item_id]
        if not item.tracked:
            continue
        qty = sold.qty
        moved = await move(
            session,
            actor,
            item.item_id,
            kind=stock.SALE,
            compute=lambda level, qty=qty: stock.go_out(level, qty, may_go_negative=not refuse),  # type: ignore[misc]
            now=now,
            sale_total=sold.line_total,
            document_id=document_id,
            line_no=sold.line_no,
        )
        assert moved is not None and sold.line_total is not None
        effect = moved[1]
        named = {"item": str(item.item_id), "name": item.name}
        if effect.after.on_hand < 0:
            warnings.append({"kind": "negative", **named, "on_hand": format_qty(effect.after.on_hand)})
        margin = stock.sale_margin(sold.line_total, effect.cost_total, effect.after.currency)
        if show_costs and margin is not None and margin < 0:
            # Said only to someone who may see cost: to anyone else it would tell what the goods cost.
            warnings.append({"kind": "below_cost", **named})

    settings = await session.shop_settings()
    lang = "uz" if settings is None else settings.lang
    await stock_cash.record_sale_income(
        session,
        actor,
        amount=total,
        method=clean.method,
        note=say(lang, _CASH_NOTE, number=number),
        now=now,
        stock_document_id=document_id,
    )
    await session.mark_document_posted(document_id, posted_by=actor.membership_id, posted_at=now, ledger_entry_id=None)
    await session.record_activity(
        membership_id=actor.membership_id,
        action="stock.sale_recorded",
        subject_type="stock_document",
        subject_id=document_id,
        detail={"number": number, "total": total, "method": clean.method.value, "lines": len(priced)},
    )
    posted = await session.get_document(document_id, for_update=False)
    assert posted is not None
    return posted, warnings


async def cancel_sale_in(
    session: TenantSession, actor: Membership, document_id: UUID, *, reason: str, now: datetime
) -> DocumentRecord:
    """Take a sale back: the goods return at the cost they left with, and the cash entry is cancelled.

    The goods first, like every document. Nothing of a sale's history can forbid it: goods that left
    always fit back. Whatever the cash book's switch says by now, an entry written then is cancelled.
    """
    document = await session.get_document(document_id, for_update=True)
    if document is None or document.kind != stock.DOC_SALE:
        raise NotFound()
    if document.status == stock.CANCELLED:
        raise SaleCancelled()
    await reverse_movements(session, actor, await session.standing_movements_of_document(document_id), now=now)
    await stock_cash.cancel_expense(session, actor, reason=reason, now=now, stock_document_id=document_id)
    await session.mark_document_cancelled(
        document_id, cancelled_by=actor.membership_id, cancelled_at=now, reason=reason
    )
    await session.record_activity(
        membership_id=actor.membership_id,
        action="stock.sale_cancelled",
        subject_type="stock_document",
        subject_id=document_id,
        detail={"number": document.number, "total": document.total},
    )
    cancelled = await session.get_document(document_id, for_update=False)
    assert cancelled is not None
    return cancelled


def _day_bounds(first: date, last: date) -> tuple[datetime, datetime]:
    """The moments a stretch of Tashkent days starts and ends at."""
    start = datetime.combine(first, time.min, tzinfo=TASHKENT)
    return start.astimezone(UTC), datetime.combine(last + timedelta(days=1), time.min, tzinfo=TASHKENT).astimezone(UTC)


class SaleService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def sell(
        self, user_id: UUID, shop_id: UUID, request: SaleRequest, *, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, RECORD_SALE)
            key = idempotency.validate_key(request_key)
            clean = clean_sale(request)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                document, warnings = await sell_in(session, actor, clean, now=self._now())
                return {**await sale_body_in(session, actor, document), "warnings": warnings}

            return await idempotency.run_once(
                session,
                key=key,
                operation=RECORD_SALE.name,
                user_id=user_id,
                request=_fingerprint(request),
                action=apply,
            )

    async def list(
        self,
        user_id: UUID,
        shop_id: UUID,
        *,
        day_from: date | None,
        day_to: date | None,
        item_id: UUID | None,
        seller_id: UUID | None,
        mine: bool,
        status: str | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        """The cash sales of a day, or of a stretch of days, newest first, with what they came to.

        With no day it is today's. `mine` narrows to the caller's own; `seller_id` to one member's.
        """
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, LIST_SALES)
            await require_viewable(session, actor, self._today())
            today = self._today()
            first = day_from or day_to or today
            last = day_to or day_from or today
            fields: dict[str, str] = {}
            if not 1 <= limit <= MAX_PAGE:
                fields["limit"] = f"must be between 1 and {MAX_PAGE}"
            if last < first:
                fields["day_to"] = "must not be before day_from"
            elif (last - first).days >= MAX_DAYS:
                fields["day_to"] = f"at most {MAX_DAYS} days at once"
            if status is not None and status not in (stock.POSTED, stock.CANCELLED):
                fields["status"] = "must be posted or cancelled"
            if mine and seller_id is not None:
                fields["seller_id"] = "give a seller or ask for your own, not both"
            if fields:
                raise ValidationFailed(fields)
            before: tuple[datetime, UUID] | None = None
            if cursor:
                at, last_id = decode_cursor(cursor, 2)
                try:
                    before = (datetime.fromisoformat(at), UUID(last_id))
                except ValueError as error:
                    raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error
            since, until = _day_bounds(first, last)
            seller = actor.membership_id if mine else seller_id
            rows = await session.list_sales(
                since=since,
                until=until,
                item_id=item_id,
                seller_id=seller,
                status=status,
                before=before,
                limit=limit + 1,
            )
            page, more = rows[:limit], len(rows) > limit
            lines = await session.document_lines_of([row.document_id for row in page])
            items = await session.stock_items_by_ids(
                sorted({line.item_id for found in lines.values() for line in found})
            )
            totals = await session.sale_totals(since=since, until=until, item_id=item_id, seller_id=seller)
            roles = await _roles(session)
            return {
                "day_from": first.isoformat(),
                "day_to": last.isoformat(),
                "sales": [sale_summary(row, lines.get(row.document_id, []), items, actor, roles) for row in page],
                "totals": {
                    "count": totals.count,
                    "total": totals.total,
                    "by_method": [
                        {"method": method.value, "total": totals.by_method[method.value]}
                        for method in Method
                        if method.value in totals.by_method
                    ],
                },
                # Whether this member may take a sale back: a client offers the action by this alone.
                "may_cancel": may(actor, permissions.STOCK_SELL_CANCEL),
                "next_cursor": encode_cursor(page[-1].created_at.isoformat(), page[-1].document_id) if more else None,
            }

    async def read(self, user_id: UUID, shop_id: UUID, sale_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, READ_SALE)
            await require_viewable(session, actor, self._today())
            document = await session.get_document(sale_id, for_update=False)
            if document is None or document.kind != stock.DOC_SALE:
                raise NotFound()
            return await sale_body_in(session, actor, document)

    async def cancel(
        self, user_id: UUID, shop_id: UUID, sale_id: UUID, *, reason: str, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, CANCEL_SALE)
            key = idempotency.validate_key(request_key)
            try:
                why = supplier_rules.reason(reason)
            except ValueError as error:
                raise ValidationFailed({"reason": str(error)}) from error
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                document = await cancel_sale_in(session, actor, sale_id, reason=why, now=self._now())
                return await sale_body_in(session, actor, document)

            return await idempotency.run_once(
                session,
                key=key,
                operation=CANCEL_SALE.name,
                user_id=user_id,
                request={"sale": str(sale_id), "reason": why},
                action=apply,
            )
