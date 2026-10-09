"""Orders and delivery notes between two linked shops (module J of the expansion).

The buyer writes an order (a draft is its own and never leaves it), sends it, and the supplier accepts it
line by line with quantities and prices, or declines. For an accepted order the supplier issues a
delivery note; the buyer confirms it or rejects it with a reason, and the supplier may answer a rejection
with a corrected note. Nothing of this touches either shop's books until the buyer confirms.

CONFIRMING A NOTE is the one step that writes two shops' books, and it does so in one transaction
(`NoteService.confirm`): the buyer's stock receipt and supplier account through the stock's own
`write_in` and `post_in`, with the note as the receipt's `origin_ref`; the supplier's credit sale to the
buyer's account and the goods out of its stock through the ledger's own `append_entry_in` and the
stock's own `move`. The shop with the lower identifier is written first, whichever role it has, so two
confirmations between the same two shops in opposite directions take their locks in one order. Inside
each shop the order is the documented one: customer, document, items (sorted), supplier, cash. If either
side refuses, nothing is written anywhere; a refusal of the supplier's books is told to the buyer
without its reason, which is the supplier's own business.

On the supplier's side the sale is written with the authority of the note itself: it was issued by a
member who held `network.fulfil` and `credits.record` at that moment, the entry is in that member's name,
and no member of the supplier is present when the buyer confirms. That is the only place an entry is
written without its author's live permission check, and the database refuses to mark the note received
unless the entry is that customer's, that amount and that member's (`network_receipt_finish`).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency, stock_cash
from qarz.application.authorization import require_permission
from qarz.application.customers import (
    MAX_PAGE,
    CustomerArchived,
    decode_cursor,
    encode_cursor,
    require_viewable,
    require_writable,
)
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.ledger_service import append_entry_in
from qarz.application.network import (
    BooksMismatch,
    NetworkState,
    PartnerRefused,
    ensure_counterpart_in,
    events_body,
    iso,
    locked_link,
    own_link,
    refusals,
    require_on,
    tell_partner,
)
from qarz.application.network_ports import DraftRecord, NoteLine, NoteRecord, OrderLine, OrderRecord
from qarz.application.operations import operation
from qarz.application.ports import Membership, Storage, TenantSession
from qarz.application.shops import refuse_suspended, require_member
from qarz.application.stock_currency import require_currency
from qarz.application.stock_documents import (
    DocumentRequest,
    LineRequest,
    NewItemRequest,
    clean_document,
    post_in,
    write_in,
)
from qarz.application.stock_moves import StockInsufficient, lock_items, move
from qarz.application.stock_moves import switched_on as stock_switched_on
from qarz.domain import network, permissions, stock
from qarz.domain.access import Capability, Role
from qarz.domain.goods import format_qty
from qarz.domain.ledger import EntryKind
from qarz.domain.money import Currency
from qarz.domain.promise import tashkent_date

LIST_ORDERS = operation("network.orders.list", Capability.MANAGE)
READ_ORDER = operation("network.orders.read", Capability.MANAGE)
LIST_DRAFTS = operation("network.drafts.list", Capability.MANAGE)
READ_DRAFT = operation("network.drafts.read", Capability.MANAGE)
CREATE_DRAFT = operation("network.drafts.create", Capability.MANAGE)
UPDATE_DRAFT = operation("network.drafts.update", Capability.MANAGE)
DELETE_DRAFT = operation("network.drafts.delete", Capability.MANAGE)
SEND_ORDER = operation("network.orders.send", Capability.MANAGE)
CANCEL_ORDER = operation("network.orders.cancel", Capability.MANAGE)
ACCEPT_ORDER = operation("network.orders.accept", Capability.MANAGE)
DECLINE_ORDER = operation("network.orders.decline", Capability.MANAGE)
LIST_NOTES = operation("network.notes.list", Capability.MANAGE)
READ_NOTE = operation("network.notes.read", Capability.MANAGE)
ISSUE_NOTE = operation("network.notes.issue", Capability.MANAGE)
CORRECT_NOTE = operation("network.notes.correct", Capability.MANAGE)
CONFIRM_NOTE = operation("network.notes.confirm", Capability.MANAGE)
REJECT_NOTE = operation("network.notes.reject", Capability.MANAGE)

MAX_DRAFTS = 50
# The note of the entry a confirmed delivery writes on the supplier's side, by the supplier's language.
_SALE_NOTE = {"uz": "Yuk xati № {number}", "ru": "Накладная № {number}"}
_PAID_NOTE = {"uz": "Yuk xati № {number}: to'lov", "ru": "Накладная № {number}: оплата"}


@dataclass(frozen=True)
class OrderLineRequest:
    name: str
    unit: str
    qty: str
    item_id: UUID | None = None


@dataclass(frozen=True)
class OrderRequest:
    link_id: UUID
    note: str | None
    wanted_date: date | None
    lines: Sequence[OrderLineRequest]


@dataclass(frozen=True)
class OfferLine:
    """What the supplier answers for one line of an order, or delivers of it."""

    line_no: int
    qty: str
    unit_price: int
    item_id: UUID | None = None


@dataclass(frozen=True)
class ReceiptLine:
    """How the buyer takes one line of a note into its own stock: one of its items, or a new one."""

    line_no: int
    item_id: UUID | None = None
    new_price: int | None = None
    new_barcode: str | None = None


@dataclass(frozen=True)
class CountedLine:
    line_no: int
    received_qty: str


def clean_order(request: OrderRequest, today: date) -> tuple[str | None, date | None, list[dict[str, Any]]]:
    """Check what the buyer wrote, without touching storage. Field names carry the line's position."""
    fields: dict[str, str] = {}
    note: str | None = None
    wanted: date | None = None
    try:
        note = network.text(request.note, limit=network.MAX_NOTE)
    except ValueError as error:
        fields["note"] = str(error)
    try:
        wanted = network.wanted_date(request.wanted_date, today)
    except ValueError as error:
        fields["wanted_date"] = str(error)
    if not 1 <= len(request.lines) <= network.MAX_LINES:
        fields["lines"] = f"between 1 and {network.MAX_LINES} lines"
    lines: list[dict[str, Any]] = []
    for index, raw in enumerate(request.lines[: network.MAX_LINES]):
        line: dict[str, Any] = {"item_id": None if raw.item_id is None else str(raw.item_id)}
        for name, check, value in (
            ("name", network.line_name, raw.name),
            ("unit", network.unit, raw.unit),
            ("qty", lambda text: format_qty(stock.quantity(text)), raw.qty),
        ):
            try:
                line[name] = check(value)
            except ValueError as error:
                fields[f"lines.{index}.{name}"] = str(error)
        lines.append(line)
    if fields:
        raise ValidationFailed(fields)
    return note, wanted, lines


def clean_offer(
    offered: Sequence[OfferLine], known: Sequence[int], *, may_be_nothing: bool
) -> list[network.PricedLine]:
    """Check quantities and prices against the order's lines: each line once, none that is not there."""
    fields: dict[str, str] = {}
    seen: set[int] = set()
    lines: list[network.PricedLine] = []
    for index, raw in enumerate(offered):
        if raw.line_no not in known or raw.line_no in seen:
            fields[f"lines.{index}.line_no"] = "a line of the order, once"
            continue
        seen.add(raw.line_no)
        qty, price = stock.ZERO, 0
        try:
            qty = network.offered_qty(raw.qty) if may_be_nothing else stock.quantity(raw.qty)
        except ValueError as error:
            fields[f"lines.{index}.qty"] = str(error)
        try:
            price = stock.unit_cost(raw.unit_price)
        except ValueError as error:
            fields[f"lines.{index}.unit_price"] = str(error)
        lines.append(network.PricedLine(raw.line_no, qty, price))
    if fields:
        raise ValidationFailed(fields)
    return lines


def draft_body(draft: DraftRecord) -> dict[str, Any]:
    return {
        "id": str(draft.draft_id),
        "status": network.DRAFT,
        "link_id": str(draft.link_id),
        "partner": {"name": draft.peer_name},
        "note": draft.note,
        "wanted_date": None if draft.wanted_date is None else draft.wanted_date.isoformat(),
        "lines": [
            {
                "line_no": number,
                "name": line["name"],
                "unit": line["unit"],
                "qty": line["qty"],
                "item_id": line.get("item_id"),
            }
            for number, line in enumerate(draft.lines, start=1)
        ],
        "updated_at": draft.updated_at.isoformat(),
    }


def order_summary(order: OrderRecord) -> dict[str, Any]:
    return {
        "id": str(order.order_id),
        "link_id": str(order.link_id),
        "role": order.role,
        "partner": {"name": order.peer_name},
        "number": order.number,
        "status": order.status,
        "note": order.note,
        "wanted_date": None if order.wanted_date is None else order.wanted_date.isoformat(),
        "currency": order.currency,
        "total": order.total,
        "sent_at": order.sent_at.isoformat(),
        "updated_at": order.updated_at.isoformat(),
        "closed_reason": order.closed_reason,
    }


def _order_line(line: OrderLine) -> dict[str, Any]:
    accepted = line.accepted_qty
    return {
        "line_no": line.line_no,
        "name": line.name,
        "unit": line.unit,
        "qty": format_qty(line.qty),
        # This shop's own catalogue item for the line, if it chose one. The partner's is never here.
        "item_id": None if line.item_id is None else str(line.item_id),
        "accepted_qty": None if accepted is None else format_qty(accepted),
        "unit_price": line.unit_price,
        "line_total": None
        if accepted is None or line.unit_price is None
        else stock.line_cost(accepted, line.unit_price),
        # What the supplier changed is said, so the buyer does not have to compare.
        "changed": accepted is not None and accepted != line.qty,
    }


def note_summary(note: NoteRecord) -> dict[str, Any]:
    return {
        "id": str(note.note_id),
        "link_id": str(note.link_id),
        "order_id": str(note.order_id),
        "order_number": note.order_number,
        "role": note.role,
        "partner": {"name": note.peer_name},
        "number": note.number,
        "status": note.status,
        "currency": note.currency,
        "total": note.total,
        "paid": note.paid,
        "terms": network.payment_terms(note.total, note.paid),
        "issued_at": note.issued_at.isoformat(),
        "decided_at": iso(note.decided_at),
        "reject_reason": note.reject_reason,
        "supersedes_id": None if note.supersedes_id is None else str(note.supersedes_id),
        "supersede_reason": note.supersede_reason,
    }


def _note_line(line: NoteLine) -> dict[str, Any]:
    return {
        "line_no": line.line_no,
        "name": line.name,
        "unit": line.unit,
        "qty": format_qty(line.qty),
        "unit_price": line.unit_price,
        "line_total": line.line_total,
        "item_id": None if line.item_id is None else str(line.item_id),
        "received_qty": None if line.received_qty is None else format_qty(line.received_qty),
    }


async def order_body_in(session: TenantSession, order: OrderRecord) -> dict[str, Any]:
    current = await session.network_current_note(order.order_id)
    return {
        **order_summary(order),
        "lines": [_order_line(line) for line in await session.network_order_lines(order.order_id)],
        "delivery_note": None if current is None else note_summary(current),
        "events": await events_body(session, order.order_id),
    }


async def note_body_in(session: TenantSession, note: NoteRecord) -> dict[str, Any]:
    body = {
        **note_summary(note),
        "lines": [_note_line(line) for line in await session.network_note_lines(note.note_id)],
        "events": await events_body(session, note.note_id),
    }
    # What confirming it wrote into this shop's own books, for a link from the note to them.
    if note.document_id is not None:
        body["stock_document_id"] = str(note.document_id)
    if note.ledger_entry_id is not None:
        body["ledger_entry_id"] = str(note.ledger_entry_id)
        body["customer_id"] = None if note.customer_id is None else str(note.customer_id)
    return body


def _page(cursor: str | None, limit: int) -> tuple[datetime, UUID] | None:
    if not 1 <= limit <= MAX_PAGE:
        raise ValidationFailed({"limit": f"must be between 1 and {MAX_PAGE}"})
    if not cursor:
        return None
    at, last = decode_cursor(cursor, 2)
    try:
        return datetime.fromisoformat(at), UUID(last)
    except ValueError as error:
        raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error


def _filters(role: str | None, status: str | None, statuses: Sequence[str]) -> None:
    fields: dict[str, str] = {}
    if role is not None and role not in network.ROLES:
        fields["role"] = "must be buyer or supplier"
    if status is not None and status not in statuses:
        fields["status"] = "not a status"
    if fields:
        raise ValidationFailed(fields)


async def _own_items(session: TenantSession, item_ids: Sequence[UUID | None], units: Sequence[str]) -> None:
    """Every item a line names is an item of this shop, and a counted one is counted in the line's unit."""
    wanted = sorted({item_id for item_id in item_ids if item_id is not None})
    known = await session.stock_items_by_ids(wanted) if wanted else {}
    fields: dict[str, str] = {}
    for index, (item_id, unit) in enumerate(zip(item_ids, units, strict=True)):
        if item_id is None:
            continue
        item = known.get(item_id)
        if item is None or item.merged_into is not None:
            fields[f"lines.{index}.item_id"] = "not an item of this shop"
        elif item.tracked and item.unit != unit:
            fields[f"lines.{index}.item_id"] = f"counted in {item.unit}, the line is in {unit}"
    if fields:
        raise ValidationFailed(fields)


class OrderService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def list(
        self,
        user_id: UUID,
        shop_id: UUID,
        *,
        role: str | None,
        status: str | None,
        link_id: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, LIST_ORDERS)
            await require_viewable(session, actor, self._today())
            before = _page(cursor, limit)
            _filters(role, status, network.ORDER_STATES)
            rows = await session.network_orders(
                role=role, status=status, link_id=link_id, before=before, limit=limit + 1
            )
            page, more = rows[:limit], len(rows) > limit
            return {
                "orders": [order_summary(row) for row in page],
                "next_cursor": encode_cursor(page[-1].sent_at.isoformat(), page[-1].order_id) if more else None,
            }

    async def read(self, user_id: UUID, shop_id: UUID, order_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, READ_ORDER)
            await require_viewable(session, actor, self._today())
            order = await session.network_order(order_id)
            if order is None:
                raise NotFound()
            return await order_body_in(session, order)

    # --- the buyer's drafts ------------------------------------------------------------------------

    async def drafts(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, LIST_DRAFTS)
            await require_viewable(session, actor, self._today())
            return {"drafts": [draft_body(draft) for draft in await session.network_drafts(MAX_DRAFTS)]}

    async def draft(self, user_id: UUID, shop_id: UUID, draft_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, READ_DRAFT)
            await require_viewable(session, actor, self._today())
            draft = await session.network_draft(draft_id, for_update=False)
            if draft is None:
                raise NotFound()
            return draft_body(draft)

    async def save_draft(
        self, user_id: UUID, shop_id: UUID, draft_id: UUID | None, request: OrderRequest, *, request_key: str | None
    ) -> dict[str, Any]:
        """Write a new draft, or replace what one says as a whole. A draft is the buyer's own."""
        op = CREATE_DRAFT if draft_id is None else UPDATE_DRAFT
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, op)
            key = idempotency.validate_key(request_key)
            note, wanted, lines = clean_order(request, self._today())
            await require_writable(session, self._today(), new_credit=True)

            async def apply() -> dict[str, Any]:
                now = self._now()
                link = await session.network_link(request.link_id)
                if link is None:
                    raise ValidationFailed({"link_id": "not a link of this shop"})
                if not network.may_send_order(link.state, link.role):
                    raise NetworkState()
                await _own_items(session, [raw.item_id for raw in request.lines], [line["unit"] for line in lines])
                if draft_id is None:
                    if len(await session.network_drafts(MAX_DRAFTS)) >= MAX_DRAFTS:
                        raise ValidationFailed({"lines": f"at most {MAX_DRAFTS} unsent orders"})
                    written = uuid4()
                    await session.insert_network_draft(
                        draft_id=written,
                        link_id=link.link_id,
                        note=note,
                        wanted_date=wanted,
                        lines=lines,
                        created_by=actor.membership_id,
                        now=now,
                    )
                else:
                    existing = await session.network_draft(draft_id, for_update=True)
                    if existing is None:
                        raise NotFound()
                    if existing.link_id != link.link_id:
                        raise ValidationFailed({"link_id": "the partner of a draft does not change"})
                    written = draft_id
                    await session.update_network_draft(draft_id, note=note, wanted_date=wanted, lines=lines, now=now)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="network.order_drafted",
                    subject_type="network_order",
                    subject_id=written,
                )
                saved = await session.network_draft(written, for_update=False)
                assert saved is not None
                return draft_body(saved)

            return await idempotency.run_once(
                session,
                key=key,
                operation=op.name,
                user_id=user_id,
                request={
                    "draft": None if draft_id is None else str(draft_id),
                    "link": str(request.link_id),
                    "note": request.note,
                    "wanted": request.wanted_date,
                    "lines": [[raw.name, raw.unit, raw.qty, raw.item_id] for raw in request.lines],
                },
                action=apply,
            )

    async def delete_draft(
        self, user_id: UUID, shop_id: UUID, draft_id: UUID, *, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, DELETE_DRAFT)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                if await session.network_draft(draft_id, for_update=True) is None:
                    raise NotFound()
                await session.delete_network_draft(draft_id)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="network.order_draft_dropped",
                    subject_type="network_order",
                    subject_id=draft_id,
                )
                return {"id": str(draft_id), "deleted": True}

            return await idempotency.run_once(
                session,
                key=key,
                operation=DELETE_DRAFT.name,
                user_id=user_id,
                request={"draft": str(draft_id)},
                action=apply,
            )

    async def send(self, user_id: UUID, shop_id: UUID, draft_id: UUID, *, request_key: str | None) -> dict[str, Any]:
        """Send a draft to the supplier. It becomes an order on both sides under the same identifier."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, SEND_ORDER)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=True)

            async def apply() -> dict[str, Any]:
                draft = await session.network_draft(draft_id, for_update=False)
                if draft is None:
                    raise NotFound()
                link = await own_link(session, draft.link_id)
                if not network.may_send_order(link.state, link.role):
                    raise NetworkState()
                await locked_link(session, link)
                await _own_items(
                    session,
                    [None if line.get("item_id") is None else UUID(line["item_id"]) for line in draft.lines],
                    [line["unit"] for line in draft.lines],
                )
                with refusals():
                    number = await session.network_send_order(
                        link.peer_shop_id,
                        link.link_id,
                        draft_id,
                        member_id=actor.membership_id,
                        note=draft.note,
                        wanted_date=draft.wanted_date,
                        lines=draft.lines,
                        now=self._now(),
                    )
                await tell_partner(session, link, permissions.NETWORK_FULFIL, "net_order_sent", draft_id, number=number)
                order = await session.network_order(draft_id)
                assert order is not None
                return await order_body_in(session, order)

            return await idempotency.run_once(
                session,
                key=key,
                operation=SEND_ORDER.name,
                user_id=user_id,
                request={"draft": str(draft_id)},
                action=apply,
            )

    # --- the supplier's answer, and either side closing --------------------------------------------

    async def accept(
        self,
        user_id: UUID,
        shop_id: UUID,
        order_id: UUID,
        *,
        currency: str | None,
        lines: Sequence[OfferLine],
        request_key: str | None,
    ) -> dict[str, Any]:
        """Accept an order: the currency, and for every line the quantity that will be delivered (which
        may be less, more or nothing) and its price. The supplier may tie a line to one of its own items."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, ACCEPT_ORDER)
            key = idempotency.validate_key(request_key)
            money = await require_currency(session, currency)
            await require_writable(session, self._today(), new_credit=True)

            async def apply() -> dict[str, Any]:
                order = await session.network_order(order_id)
                if order is None:
                    raise NotFound()
                if not network.may_accept_order(order.status, order.role):
                    raise NetworkState()
                link = await own_link(session, order.link_id)
                asked = await session.network_order_lines(order_id)
                offer = clean_offer(lines, [line.line_no for line in asked], may_be_nothing=True)
                if len(offer) != len(asked):
                    raise ValidationFailed({"lines": "answer every line of the order"})
                problem = network.total_problem(Currency(money), network.total_of(offer))
                if problem is not None:
                    raise ValidationFailed({"lines": problem})
                units = {line.line_no: line.unit for line in asked}
                await _own_items(session, [raw.item_id for raw in lines], [units[raw.line_no] for raw in lines])
                await locked_link(session, link)
                with refusals():
                    total = await session.network_accept_order(
                        link.peer_shop_id,
                        order_id,
                        member_id=actor.membership_id,
                        currency=money,
                        lines=[
                            {
                                "line_no": raw.line_no,
                                "qty": format_qty(priced.qty),
                                "unit_price": priced.unit_price,
                                "item_id": None if raw.item_id is None else str(raw.item_id),
                            }
                            for raw, priced in zip(lines, offer, strict=True)
                        ],
                        now=self._now(),
                    )
                await tell_partner(
                    session,
                    link,
                    permissions.NETWORK_ORDER,
                    "net_order_accepted",
                    order_id,
                    number=order.number,
                    amount=(total, money),
                )
                accepted = await session.network_order(order_id)
                assert accepted is not None
                return await order_body_in(session, accepted)

            return await idempotency.run_once(
                session,
                key=key,
                operation=ACCEPT_ORDER.name,
                user_id=user_id,
                request={
                    "order": str(order_id),
                    "currency": money,
                    "lines": [[raw.line_no, raw.qty, raw.unit_price, raw.item_id] for raw in lines],
                },
                action=apply,
            )

    async def close(
        self, user_id: UUID, shop_id: UUID, order_id: UUID, *, as_buyer: bool, reason: str, request_key: str | None
    ) -> dict[str, Any]:
        """The buyer cancels (`as_buyer`) or the supplier declines, with a reason the other side reads."""
        op = CANCEL_ORDER if as_buyer else DECLINE_ORDER
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, op)
            key = idempotency.validate_key(request_key)
            try:
                why = network.reason(reason)
            except ValueError as error:
                raise ValidationFailed({"reason": str(error)}) from error
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                order = await session.network_order(order_id)
                # The other side's step on one's own order is "no such order of yours to cancel".
                if order is None or (order.role == network.BUYER) != as_buyer:
                    raise NotFound()
                current = await session.network_current_note(order_id)
                if not network.may_close_order(order.status, None if current is None else current.status):
                    raise NetworkState()
                link = await own_link(session, order.link_id)
                with refusals():
                    outcome = await session.network_close_order(
                        link.peer_shop_id, order_id, member_id=actor.membership_id, reason=why, now=self._now()
                    )
                await tell_partner(
                    session,
                    link,
                    permissions.NETWORK_FULFIL if as_buyer else permissions.NETWORK_ORDER,
                    f"net_order_{outcome}",
                    order_id,
                    number=order.number,
                    reason=why,
                )
                closed = await session.network_order(order_id)
                assert closed is not None
                return await order_body_in(session, closed)

            return await idempotency.run_once(
                session,
                key=key,
                operation=op.name,
                user_id=user_id,
                request={"order": str(order_id), "reason": why},
                action=apply,
            )


async def sell_in(there: TenantSession, note_id: UUID, *, now: datetime) -> tuple[UUID, UUID | None]:
    """The supplier's side of a confirmed delivery, written as the supplier (see the module docstring):
    a credit sale to the buyer's account for the note's total, the goods of its counted items out of its
    stock, and a payment for what was handed over on delivery. Returns the two entries.

    The goods leave whatever the stock holds: they were carried out when they were delivered, and
    "refuse sales beyond stock" was asked when the note was issued.
    """
    await refuse_suspended(there)
    note = await there.network_note(note_id)
    if note is None or note.role != network.SUPPLIER or note.issued_by is None or note.customer_id is None:
        raise NotFound()
    lines = await there.network_note_lines(note_id)
    author = Membership(note.issued_by, Role.OWNER)
    settings = await there.shop_settings()
    lang = "uz" if settings is None else settings.lang
    currency = Currency(note.currency)
    sale = await append_entry_in(
        there,
        author,
        note.customer_id,
        kind=EntryKind.CREDIT,
        amount=note.total,
        note=_SALE_NOTE.get(lang, _SALE_NOTE["uz"]).format(number=note.number),
        promised_date=None,
        now=now,
        currency=currency,
    )
    entry_id = UUID(sale["entry"]["id"])
    item_ids = [line.item_id for line in lines if line.item_id is not None]
    if item_ids and await stock_switched_on(there):
        known = await lock_items(there, item_ids)
        for line in lines:
            item = None if line.item_id is None else known.get(line.item_id)
            if item is None or not item.tracked or item.unit != line.unit:
                continue
            qty = line.qty
            await move(
                there,
                author,
                item.item_id,
                kind=stock.SALE,
                compute=lambda level, qty=qty: stock.go_out(level, qty, may_go_negative=True),  # type: ignore[misc]
                now=now,
                # A selling price in so'm, as on any sale; a dollar note states none.
                sale_total=line.line_total if currency is Currency.UZS and line.line_total > 0 else None,
                ledger_entry_id=entry_id,
                line_no=line.line_no,
            )
    paid_entry: UUID | None = None
    if note.paid:
        paid = await append_entry_in(
            there,
            author,
            note.customer_id,
            kind=EntryKind.PAYMENT,
            amount=note.paid,
            note=_PAID_NOTE.get(lang, _PAID_NOTE["uz"]).format(number=note.number),
            promised_date=None,
            now=now,
            currency=currency,
        )
        paid_entry = UUID(paid["entry"]["id"])
    return entry_id, paid_entry


class NoteService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def list(
        self,
        user_id: UUID,
        shop_id: UUID,
        *,
        role: str | None,
        status: str | None,
        link_id: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, LIST_NOTES)
            await require_viewable(session, actor, self._today())
            before = _page(cursor, limit)
            _filters(role, status, network.NOTE_STATES)
            rows = await session.network_notes(
                role=role, status=status, link_id=link_id, before=before, limit=limit + 1
            )
            page, more = rows[:limit], len(rows) > limit
            return {
                "notes": [note_summary(row) for row in page],
                "next_cursor": encode_cursor(page[-1].issued_at.isoformat(), page[-1].note_id) if more else None,
            }

    async def read(self, user_id: UUID, shop_id: UUID, note_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, READ_NOTE)
            await require_viewable(session, actor, self._today())
            note = await session.network_note(note_id)
            if note is None:
                raise NotFound()
            return await note_body_in(session, note)

    async def issue(
        self,
        user_id: UUID,
        shop_id: UUID,
        *,
        order_id: UUID | None = None,
        corrects: UUID | None = None,
        paid: int,
        reason: str | None = None,
        lines: Sequence[OfferLine] | None = None,
        request_key: str | None,
    ) -> dict[str, Any]:
        """Issue the delivery note of an accepted order (`order_id`): its accepted lines as they stand.
        Or correct a note that is not received (`corrects`), with a reason and, if they differ, the lines
        as they should be. The note before it is superseded; neither is ever edited.

        Issuing a note is agreeing to a credit sale that the buyer's confirmation will write, so it asks
        for `credits.record` as well, and is refused to a shop that may not sell on credit now."""
        op = ISSUE_NOTE if corrects is None else CORRECT_NOTE
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, op)
            key = idempotency.validate_key(request_key)
            require_permission(actor, permissions.CREDITS_RECORD)
            why: str | None = None
            if corrects is not None:
                try:
                    why = network.reason(reason or "")
                except ValueError as error:
                    raise ValidationFailed({"reason": str(error)}) from error
            if isinstance(paid, bool) or not isinstance(paid, int) or paid < 0:
                raise ValidationFailed({"paid": "a whole amount, zero or more"})
            await require_writable(session, self._today(), new_credit=True)

            async def apply() -> dict[str, Any]:
                target = order_id
                if corrects is not None:
                    before = await session.network_note(corrects)
                    if before is None:
                        raise NotFound()
                    target = before.order_id
                assert target is not None
                order = await session.network_order(target)
                if order is None:
                    raise NotFound()
                current = await session.network_current_note(order.order_id)
                if corrects is not None and (current is None or current.note_id != corrects):
                    raise NetworkState()
                if (corrects is None and current is not None) or not network.may_issue_note(
                    order.status, order.role, None if current is None else current.status
                ):
                    raise NetworkState()
                assert order.currency is not None
                link = await own_link(session, order.link_id)
                await locked_link(session, link)
                customer_id = await ensure_counterpart_in(session, actor, link, today=self._today())
                customer = await session.get_customer(customer_id, for_update=False)
                if customer is None or customer.status == "anonymized":
                    raise NotFound()
                if customer.status == "archived":
                    raise CustomerArchived()
                asked = await session.network_order_lines(order.order_id)
                if lines is None:
                    priced = [
                        network.PricedLine(line.line_no, line.accepted_qty, line.unit_price or 0)
                        for line in asked
                        if line.accepted_qty
                    ]
                else:
                    priced = clean_offer(lines, [line.line_no for line in asked], may_be_nothing=False)
                total = network.total_of(priced)
                problem = None if priced else "nothing is delivered"
                problem = problem or network.total_problem(Currency(order.currency), total)
                if problem is not None:
                    raise ValidationFailed({"lines": problem})
                if paid > total:
                    raise ValidationFailed({"paid": "between 0 and the note's total"})
                await self._require_on_hand(session, asked, priced)
                note_id = uuid4()
                with refusals():
                    number = await session.network_issue_note(
                        link.peer_shop_id,
                        order.order_id,
                        note_id,
                        member_id=actor.membership_id,
                        customer_id=customer_id,
                        paid=paid,
                        reason=why,
                        lines=None
                        if lines is None
                        else [
                            {"line_no": line.line_no, "qty": format_qty(line.qty), "unit_price": line.unit_price}
                            for line in priced
                        ],
                        now=self._now(),
                    )
                await tell_partner(
                    session,
                    link,
                    permissions.NETWORK_CONFIRM,
                    "net_note_issued" if corrects is None else "net_note_corrected",
                    note_id,
                    number=number,
                    amount=(total, order.currency),
                    **({} if why is None else {"reason": why}),
                )
                note = await session.network_note(note_id)
                assert note is not None
                return await note_body_in(session, note)

            return await idempotency.run_once(
                session,
                key=key,
                operation=op.name,
                user_id=user_id,
                request={
                    "order": None if order_id is None else str(order_id),
                    "corrects": None if corrects is None else str(corrects),
                    "paid": paid,
                    "reason": why,
                    "lines": None if lines is None else [[raw.line_no, raw.qty, raw.unit_price] for raw in lines],
                },
                action=apply,
            )

    @staticmethod
    async def _require_on_hand(
        session: TenantSession, asked: Sequence[OrderLine], priced: Sequence[network.PricedLine]
    ) -> None:
        """A shop that refuses sales beyond its stock is refused a note for more than it holds, now: when
        the buyer confirms, nobody of this shop is there to be told."""
        if not await stock_switched_on(session) or not await session.stock_refuse_negative():
            return
        own = {line.line_no: line for line in asked}
        wanted: dict[UUID, Decimal] = {}
        units: dict[UUID, str] = {}
        for line in priced:
            source = own[line.line_no]
            if source.item_id is not None:
                wanted[source.item_id] = wanted.get(source.item_id, stock.ZERO) + line.qty
                units[source.item_id] = source.unit
        items = await session.stock_items_by_ids(sorted(wanted))
        for item_id, qty in wanted.items():
            item = items.get(item_id)
            if item is not None and item.tracked and item.unit == units[item_id] and item.level.on_hand < qty:
                raise StockInsufficient(
                    {
                        "item": str(item_id),
                        "name": item.name,
                        "on_hand": format_qty(item.level.on_hand),
                        "wanted": format_qty(qty),
                    }
                )

    async def reject(
        self,
        user_id: UUID,
        shop_id: UUID,
        note_id: UUID,
        *,
        reason: str,
        lines: Sequence[CountedLine] | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        """Reject a note with a reason and, if it helps, what did arrive line by line. Nothing is posted
        on either side; the supplier sees it and may issue a corrected note."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, REJECT_NOTE)
            key = idempotency.validate_key(request_key)
            try:
                why = network.reason(reason)
            except ValueError as error:
                raise ValidationFailed({"reason": str(error)}) from error
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                note = await session.network_note(note_id)
                if note is None:
                    raise NotFound()
                if not network.may_answer_note(note.status, note.role):
                    raise NetworkState()
                link = await own_link(session, note.link_id)
                counted: list[dict[str, Any]] | None = None
                if lines is not None:
                    known = {line.line_no for line in await session.network_note_lines(note_id)}
                    fields: dict[str, str] = {}
                    counted = []
                    for index, raw in enumerate(lines):
                        if raw.line_no not in known:
                            fields[f"lines.{index}.line_no"] = "a line of the note"
                            continue
                        try:
                            counted.append(
                                {"line_no": raw.line_no, "received_qty": format_qty(stock.counted(raw.received_qty))}
                            )
                        except ValueError as error:
                            fields[f"lines.{index}.received_qty"] = str(error)
                    if fields:
                        raise ValidationFailed(fields)
                with refusals():
                    await session.network_reject_note(
                        link.peer_shop_id,
                        note_id,
                        member_id=actor.membership_id,
                        reason=why,
                        lines=counted,
                        now=self._now(),
                    )
                await tell_partner(
                    session,
                    link,
                    permissions.NETWORK_FULFIL,
                    "net_note_rejected",
                    note_id,
                    number=note.number,
                    reason=why,
                )
                rejected = await session.network_note(note_id)
                assert rejected is not None
                return await note_body_in(session, rejected)

            return await idempotency.run_once(
                session,
                key=key,
                operation=REJECT_NOTE.name,
                user_id=user_id,
                request={
                    "note": str(note_id),
                    "reason": why,
                    "lines": None if lines is None else [[raw.line_no, raw.received_qty] for raw in lines],
                },
                action=apply,
            )

    async def confirm(
        self,
        user_id: UUID,
        shop_id: UUID,
        note_id: UUID,
        *,
        lines: Sequence[ReceiptLine],
        method: str | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        """Confirm that the goods of a note arrived, as the note says. In ONE transaction the buyer's
        books take a stock receipt and the supplier's a credit sale; if either refuses, neither is written
        and the note still waits. `lines` says which of the buyer's own items each line is (or that it
        is a new one, with its selling price); a line left out keeps the item chosen on the order.

        The receipt is the stock's own: the member needs `stock.receive`, and `suppliers.pay` when
        something was paid on delivery, exactly as for a receipt written by hand."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, CONFIRM_NOTE)
            key = idempotency.validate_key(request_key)
            paid_by = stock_cash.clean_method(method)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                now = self._now()
                note = await session.network_note(note_id)
                if note is None:
                    raise NotFound()
                if not network.may_answer_note(note.status, note.role):
                    raise NetworkState()
                if paid_by is not None and not note.paid:
                    raise ValidationFailed({"method": "only money paid on delivery has a method"})
                link = await own_link(session, note.link_id)
                # The link first, then each shop's own rows, the lower shop first.
                await locked_link(session, link)
                supplier_id = await ensure_counterpart_in(session, actor, link, today=self._today())
                request = await self._receipt(session, note, supplier_id, lines, method)
                clean = clean_document(request, self._today())
                if clean.total != note.total or clean.paid != note.paid:
                    raise BooksMismatch()
                currency = await require_currency(session, note.currency)

                async def buyers_side() -> UUID:
                    document_id = await write_in(session, actor, clean, currency, now=now, origin_ref=note_id)
                    await post_in(session, actor, document_id, now=now)
                    return document_id

                async def suppliers_side() -> tuple[UUID, UUID | None]:
                    with refusals():
                        async with session.network_peer(link.peer_shop_id, note_id) as there:
                            try:
                                return await sell_in(there, note_id, now=now)
                            except AppError as error:
                                # Why the supplier's books refused is the supplier's own business.
                                raise PartnerRefused() from error

                if shop_id < link.peer_shop_id:
                    document_id = await buyers_side()
                    entry_id, paid_entry_id = await suppliers_side()
                else:
                    entry_id, paid_entry_id = await suppliers_side()
                    document_id = await buyers_side()
                with refusals():
                    await session.network_finish_receipt(
                        link.peer_shop_id,
                        note_id,
                        member_id=actor.membership_id,
                        document_id=document_id,
                        entry_id=entry_id,
                        paid_entry_id=paid_entry_id,
                        now=now,
                    )
                await tell_partner(
                    session,
                    link,
                    permissions.NETWORK_FULFIL,
                    "net_note_received",
                    note_id,
                    number=note.number,
                    amount=(note.total, note.currency),
                )
                received = await session.network_note(note_id)
                assert received is not None
                return await note_body_in(session, received)

            return await idempotency.run_once(
                session,
                key=key,
                operation=CONFIRM_NOTE.name,
                user_id=user_id,
                request={
                    "note": str(note_id),
                    "method": method,
                    "lines": [[raw.line_no, raw.item_id, raw.new_price, raw.new_barcode] for raw in lines],
                },
                action=apply,
            )

    @staticmethod
    async def _receipt(
        session: TenantSession,
        note: NoteRecord,
        supplier_id: UUID,
        chosen: Sequence[ReceiptLine],
        method: str | None,
    ) -> DocumentRequest:
        """The purchase receipt a note becomes on the buyer's side: its lines, quantities and prices as
        the note states them, each on one of the buyer's own items."""
        note_lines = await session.network_note_lines(note.note_id)
        by_number = {line.line_no: line for line in note_lines}
        fields: dict[str, str] = {}
        choice: dict[int, ReceiptLine] = {}
        for index, raw in enumerate(chosen):
            if raw.line_no not in by_number or raw.line_no in choice:
                fields[f"lines.{index}.line_no"] = "a line of the note, once"
            elif (raw.item_id is None) == (raw.new_price is None):
                fields[f"lines.{index}.item_id"] = "give one of your items, or the price of a new one"
            else:
                choice[raw.line_no] = raw
        if fields:
            raise ValidationFailed(fields)
        item_ids = [choice[line.line_no].item_id if line.line_no in choice else line.item_id for line in note_lines]
        known = await session.stock_items_by_ids(sorted({item_id for item_id in item_ids if item_id is not None}))
        requested: list[LineRequest] = []
        for index, (line, item_id) in enumerate(zip(note_lines, item_ids, strict=True)):
            picked = choice.get(line.line_no)
            new_item: NewItemRequest | None = None
            if item_id is None:
                if picked is None or picked.new_price is None:
                    fields[f"lines.{index}.item_id"] = "choose one of your items, or add this one with its price"
                    continue
                new_item = NewItemRequest(line.name, line.unit, picked.new_price, picked.new_barcode)
            else:
                item = known.get(item_id)
                if item is None:
                    fields[f"lines.{index}.item_id"] = "not an item of this shop"
                    continue
                if item.unit != line.unit:
                    fields[f"lines.{index}.item_id"] = f"counted in {item.unit}, the line is in {line.unit}"
                    continue
            requested.append(LineRequest(item_id, new_item, format_qty(line.qty), line.unit_price))
        if fields:
            raise ValidationFailed(fields)
        return DocumentRequest(
            kind=stock.DOC_RECEIPT,
            doc_date=None,
            supplier_id=supplier_id,
            customer_id=None,
            currency=note.currency,
            paid=note.paid,
            reason=None,
            note=None,
            lines=requested,
            method=method,
        )
