"""Stock documents: what a person fills in to change the stock (module I of the expansion).

Five kinds, one life: a document is a *draft* while it is being written, *posted* when it takes effect,
and *cancelled* when it is taken back. Posting writes its lines and one movement for each; cancelling
reverses those movements and the money that went with them. Nothing posted is ever edited.

- `receipt` (kirim): goods bought, at a cost, from a supplier or for cash. With a supplier, the total is
  owed to them and what was paid at once is a payment on their account; without one it was all paid.
  A line may name an item the catalogue does not have yet, and counting is turned on for every item
  received.
- `supplier_return`: goods sent back. They leave at the average cost like anything else; what the
  supplier credits for them is the document's own price and lowers what the shop owes.
- `customer_return`: goods a customer brought back. They come in at the average cost, which therefore
  does not move. Their price lowers the customer's debt through an ordinary payment entry of the
  customers' ledger, under that ledger's own rules (it cannot exceed what is owed); what is handed back
  in money instead is `paid`. Cancelling the document reverses that entry the ordinary way.
- `write_off`: goods that are gone, with the reason.
- `stocktake`: what was counted, item by item. Reading the draft shows each count beside what the books
  hold; posting writes a correction for every difference, against the books as they are at that moment.

Cancelling is refused when it would take back goods that have since left (`STOCK_ALREADY_USED`): the
receipt's goods were sold, or returned goods were sold again. Return them or write them off instead.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency, stock_cash
from qarz.application import suppliers as supplier_account
from qarz.application.authorization import require_permission
from qarz.application.catalog import CatalogNameTaken
from qarz.application.customers import (
    MAX_PAGE,
    CustomerArchived,
    decode_cursor,
    encode_cursor,
    require_viewable,
    require_writable,
)
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.ledger_service import MAX_AMOUNT, MIN_AMOUNT, append_entry_in, reverse_entry_in
from qarz.application.operations import operation
from qarz.application.ports import Membership, Storage, TenantSession
from qarz.application.shops import require_member
from qarz.application.stock import BarcodeTaken, check_countable, sees_costs
from qarz.application.stock_currency import UZS, require_currency
from qarz.application.stock_moves import lock_items, move, require_on, reverse_movements
from qarz.application.stock_ports import DocumentLine, DocumentRecord, StockItem
from qarz.application.suppliers import SupplierArchived
from qarz.domain import permissions, stock
from qarz.domain import suppliers as supplier_rules
from qarz.domain.access import Capability
from qarz.domain.catalog import check_price, item_name
from qarz.domain.goods import format_qty
from qarz.domain.ledger import EntryKind
from qarz.domain.names import normalize_name
from qarz.domain.promise import tashkent_date

LIST_DOCUMENTS = operation("stock.documents.list", Capability.MANAGE)
READ_DOCUMENT = operation("stock.documents.read", Capability.MANAGE)
CREATE_DOCUMENT = operation("stock.documents.create", Capability.MANAGE)
UPDATE_DOCUMENT = operation("stock.documents.update", Capability.MANAGE)
POST_DOCUMENT = operation("stock.documents.post", Capability.MANAGE)
CANCEL_DOCUMENT = operation("stock.documents.cancel", Capability.MANAGE)

# The permission each kind needs: receiving goods is one job, correcting the books another.
PERMISSION_OF_KIND = {
    stock.DOC_RECEIPT: permissions.STOCK_RECEIVE,
    stock.DOC_SUPPLIER_RETURN: permissions.STOCK_RECEIVE,
    stock.DOC_CUSTOMER_RETURN: permissions.STOCK_ADJUST,
    stock.DOC_WRITE_OFF: permissions.STOCK_ADJUST,
    stock.DOC_STOCKTAKE: permissions.STOCK_ADJUST,
}
# How far back a document may be dated. Its movements always carry the moment it was posted.
OLDEST_DAYS = 365
# The note of the ledger entry a customer's return writes, by the shop's language.
_RETURN_NOTE = {"uz": "Tovar qaytarildi, hujjat № {number}", "ru": "Возврат товара, документ № {number}"}
_PURCHASE_NOTE = {"uz": "Kirim № {number}", "ru": "Приход № {number}"}


class DocumentNotDraft(AppError):
    """Only a draft is edited or posted."""

    code = "DOCUMENT_NOT_DRAFT"


class DocumentCancelled(AppError):
    code = "DOCUMENT_CANCELLED"


@dataclass(frozen=True)
class NewItemRequest:
    name: str
    unit: str | None
    price: int
    barcode: str | None


@dataclass(frozen=True)
class LineRequest:
    item_id: UUID | None
    new_item: NewItemRequest | None
    qty: str
    unit_cost: int | None


@dataclass(frozen=True)
class DocumentRequest:
    kind: str
    doc_date: date | None
    supplier_id: UUID | None
    customer_id: UUID | None
    currency: str | None
    paid: int | None
    reason: str | None
    note: str | None
    lines: Sequence[LineRequest]


@dataclass(frozen=True)
class CleanNewItem:
    name: str
    unit: str
    price: int
    barcode: str | None


@dataclass(frozen=True)
class CleanLine:
    item_id: UUID | None
    new_item: CleanNewItem | None
    qty: Decimal
    unit_cost: int | None
    line_total: int | None


@dataclass(frozen=True)
class CleanDocument:
    kind: str
    doc_date: date
    supplier_id: UUID | None
    customer_id: UUID | None
    paid: int
    total: int
    reason: str | None
    note: str | None
    lines: list[CleanLine]


def _clean_new_item(raw: NewItemRequest, prefix: str, fields: dict[str, str]) -> CleanNewItem | None:
    name, price, code = "", 0, None
    try:
        name = item_name(raw.name)
    except ValueError as error:
        fields[f"{prefix}.name"] = str(error)
    unit = raw.unit or "dona"
    if unit not in stock.UNIT_KEYS:
        fields[f"{prefix}.unit"] = "must be one of the stock units"
    try:
        price = check_price(raw.price)
    except ValueError as error:
        fields[f"{prefix}.price"] = str(error)
    if raw.barcode is not None:
        try:
            code = stock.barcode(raw.barcode)
        except ValueError as error:
            fields[f"{prefix}.barcode"] = str(error)
    return CleanNewItem(name, unit, price, code)


def clean_document(request: DocumentRequest, today: date) -> CleanDocument:
    """Check the shape of a document without touching storage. Field names carry the line's position."""
    kind = request.kind
    if kind not in stock.DOCUMENT_KINDS:
        raise ValidationFailed({"kind": "must be receipt, customer_return, supplier_return, write_off or stocktake"})
    fields: dict[str, str] = {}
    priced = kind in stock.PRICED_KINDS

    doc_date = request.doc_date or today
    if not today - timedelta(days=OLDEST_DAYS) <= doc_date <= today:
        fields["doc_date"] = f"today or up to {OLDEST_DAYS} days back"
    if kind == stock.DOC_SUPPLIER_RETURN and request.supplier_id is None:
        fields["supplier_id"] = "required for a return to a supplier"
    if kind not in (stock.DOC_RECEIPT, stock.DOC_SUPPLIER_RETURN) and request.supplier_id is not None:
        fields["supplier_id"] = "only a receipt and a return to a supplier have one"
    if (kind == stock.DOC_CUSTOMER_RETURN) != (request.customer_id is not None):
        fields["customer_id"] = "required for a customer's return, and only there"
    if (kind == stock.DOC_WRITE_OFF) != (request.reason is not None):
        fields["reason"] = "required for a write-off, and only there"
    elif request.reason is not None and request.reason not in stock.WRITE_OFF_KEYS:
        fields["reason"] = "must be damaged, expired, lost or own_use"
    note: str | None = None
    try:
        note = supplier_rules.note(request.note, limit=stock.MAX_NOTE)
    except ValueError as error:
        fields["note"] = str(error)

    if not 1 <= len(request.lines) <= stock.MAX_DOCUMENT_LINES:
        fields["lines"] = f"between 1 and {stock.MAX_DOCUMENT_LINES} lines"
    lines: list[CleanLine] = []
    for index, raw in enumerate(request.lines[: stock.MAX_DOCUMENT_LINES]):
        prefix = f"lines.{index}"
        new_item: CleanNewItem | None = None
        if (raw.item_id is None) == (raw.new_item is None):
            fields[f"{prefix}.item_id"] = "give an item or a new item, one of the two"
        elif raw.new_item is not None:
            if kind != stock.DOC_RECEIPT:
                fields[f"{prefix}.new_item"] = "a new item is added on a receipt only"
            else:
                new_item = _clean_new_item(raw.new_item, f"{prefix}.new_item", fields)
        qty = stock.ZERO
        try:
            qty = stock.counted(raw.qty) if kind == stock.DOC_STOCKTAKE else stock.quantity(raw.qty)
        except ValueError as error:
            fields[f"{prefix}.qty"] = str(error)
        cost: int | None = None
        if priced:
            try:
                cost = stock.unit_cost(raw.unit_cost)
            except ValueError as error:
                fields[f"{prefix}.unit_cost"] = "required: " + str(error) if raw.unit_cost is None else str(error)
        elif raw.unit_cost is not None:
            fields[f"{prefix}.unit_cost"] = "leave out: this kind of document has no prices"
        lines.append(CleanLine(raw.item_id, new_item, qty, cost, None if cost is None else stock.line_cost(qty, cost)))
    if kind == stock.DOC_STOCKTAKE:
        seen: set[UUID] = set()
        for index, line in enumerate(lines):
            if line.item_id is not None and line.item_id in seen:
                fields[f"lines.{index}.item_id"] = "counted twice in this stocktake"
            if line.item_id is not None:
                seen.add(line.item_id)

    total = sum(line.line_total or 0 for line in lines)
    if total > stock.MAX_DOCUMENT_TOTAL:
        fields["lines"] = "the total is too large for one document"
    paid = request.paid
    if kind == stock.DOC_RECEIPT:
        if request.supplier_id is None:
            # Nobody to owe it to: goods bought without a supplier were paid for.
            if paid is not None and paid != total:
                fields["paid"] = "a receipt without a supplier is paid in full"
            paid = total
        elif paid is None:
            paid = 0
    elif kind == stock.DOC_CUSTOMER_RETURN:
        paid = 0 if paid is None else paid
    elif paid:
        fields["paid"] = "leave out: nothing is paid on this kind of document"
    else:
        paid = 0
    if isinstance(paid, bool) or not isinstance(paid, int) or not 0 <= paid <= total:
        fields.setdefault("paid", "between 0 and the document's total")
    if kind == stock.DOC_CUSTOMER_RETURN and "paid" not in fields:
        owed_less = total - (paid or 0)
        if owed_less and not MIN_AMOUNT <= owed_less <= MAX_AMOUNT:
            fields["paid"] = f"what lowers the debt must be between {MIN_AMOUNT} and {MAX_AMOUNT}, or nothing"
    if fields:
        raise ValidationFailed(fields)
    assert isinstance(paid, int)
    return CleanDocument(
        kind, doc_date, request.supplier_id, request.customer_id, paid, total, request.reason, note, lines
    )


def _draft(lines: Sequence[tuple[UUID, CleanLine]]) -> dict[str, Any]:
    return {
        "lines": [
            {"item_id": str(item_id), "qty": format_qty(line.qty), "unit_cost": line.unit_cost}
            for item_id, line in lines
        ]
    }


def _draft_lines(document: DocumentRecord) -> list[DocumentLine]:
    """The lines a draft holds, in the shape posted lines have."""
    rows: list[DocumentLine] = []
    for number, raw in enumerate((document.draft or {}).get("lines", []), start=1):
        qty = Decimal(raw["qty"])
        cost = raw.get("unit_cost")
        rows.append(
            DocumentLine(number, UUID(raw["item_id"]), qty, cost, None if cost is None else stock.line_cost(qty, cost))
        )
    return rows


def _request_fingerprint(request: DocumentRequest) -> dict[str, Any]:
    return {
        "kind": request.kind,
        "doc_date": request.doc_date,
        "supplier": request.supplier_id,
        "customer": request.customer_id,
        "currency": request.currency,
        "paid": request.paid,
        "reason": request.reason,
        "note": request.note,
        "lines": [
            {
                "item": line.item_id,
                "new": None
                if line.new_item is None
                else [line.new_item.name, line.new_item.unit, line.new_item.price, line.new_item.barcode],
                "qty": line.qty,
                "unit_cost": line.unit_cost,
            }
            for line in request.lines
        ],
    }


def shows_money(document: DocumentRecord, actor: Membership) -> bool:
    """Whether this member reads the document's prices. What a customer is given back is a selling price
    and no secret; what goods were bought for is shown to those who may see cost, and to the author of a
    draft, who typed it."""
    if document.kind == stock.DOC_CUSTOMER_RETURN or sees_costs(actor):
        return True
    return document.status == stock.DRAFT and document.created_by == actor.membership_id


def document_summary(document: DocumentRecord, *, money: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(document.document_id),
        "kind": document.kind,
        "number": document.number,
        "status": document.status,
        "doc_date": document.doc_date.isoformat(),
        "supplier": None
        if document.supplier_id is None
        else {"id": str(document.supplier_id), "name": document.supplier_name},
        "customer": None
        if document.customer_id is None
        else {"id": str(document.customer_id), "name": document.customer_name},
        "reason": document.reason,
        "note": document.note,
        "created_by": str(document.created_by),
        "created_at": document.created_at.isoformat(),
        "posted_at": None if document.posted_at is None else document.posted_at.isoformat(),
        "cancelled_at": None if document.cancelled_at is None else document.cancelled_at.isoformat(),
        "cancel_reason": document.cancel_reason,
    }
    if money and document.kind in stock.PRICED_KINDS:
        body["currency"] = document.currency
        body["total"] = document.total
        body["paid"] = document.paid
    return body


async def document_body_in(session: TenantSession, actor: Membership, document: DocumentRecord) -> dict[str, Any]:
    money = shows_money(document, actor)
    draft = document.status == stock.DRAFT or (document.status == stock.CANCELLED and document.posted_at is None)
    lines = _draft_lines(document) if draft else await session.document_lines(document.document_id)
    items = await session.stock_items_by_ids(sorted({line.item_id for line in lines}))
    rows: list[dict[str, Any]] = []
    for line in lines:
        item = items.get(line.item_id)
        row: dict[str, Any] = {
            "line_no": line.line_no,
            "item": {
                "id": str(line.item_id),
                "name": "" if item is None else item.name,
                "unit": "" if item is None else item.unit,
            },
            "qty": format_qty(line.qty),
        }
        if document.kind == stock.DOC_STOCKTAKE:
            # A draft is compared with the books as they are now: the preview. A posted one keeps what
            # the books held when it was posted.
            expected = line.expected
            if expected is None and item is not None and document.status == stock.DRAFT:
                expected = item.level.on_hand
            row["expected"] = None if expected is None else format_qty(expected)
            row["difference"] = None if expected is None else format_qty(line.qty - expected)
        if money and line.unit_cost is not None:
            row["unit_cost"] = line.unit_cost
            row["line_total"] = line.line_total
        rows.append(row)
    return {
        **document_summary(document, money=money),
        "ledger_entry_id": _text(document.ledger_entry_id),
        "lines": rows,
    }


def _text(value: UUID | None) -> str | None:
    return None if value is None else str(value)


async def _resolve(
    session: TenantSession, actor: Membership, clean: CleanDocument, *, now: datetime
) -> list[tuple[UUID, CleanLine]]:
    """Check the document against the shop and give every line its item, adding the new ones.

    Raises before anything of the document is stored; items it added are rolled back with the caller's
    transaction when a later line is refused.
    """
    if clean.supplier_id is not None:
        supplier = await session.get_supplier(clean.supplier_id, for_update=False)
        if supplier is None:
            raise ValidationFailed({"supplier_id": "not a supplier of this shop"})
        if supplier.status == supplier_rules.ARCHIVED:
            raise SupplierArchived()
    if clean.customer_id is not None:
        customer = await session.get_customer(clean.customer_id, for_update=False)
        if customer is None or customer.status == "anonymized":
            raise ValidationFailed({"customer_id": "not a customer of this shop"})
        if customer.status == "archived":
            raise CustomerArchived()

    known = await session.stock_items_by_ids(sorted({line.item_id for line in clean.lines if line.item_id is not None}))
    fields: dict[str, str] = {}
    for index, line in enumerate(clean.lines):
        if line.item_id is None:
            continue
        item = known.get(line.item_id)
        if item is None:
            fields[f"lines.{index}.item_id"] = "not an item of this shop"
        elif clean.kind == stock.DOC_RECEIPT:
            if not item.tracked:
                _require_countable(item, index)
        elif not item.tracked:
            fields[f"lines.{index}.item_id"] = "not counted in stock"
    if fields:
        raise ValidationFailed(fields)

    resolved: list[tuple[UUID, CleanLine]] = []
    for index, line in enumerate(clean.lines):
        if line.item_id is not None:
            resolved.append((line.item_id, line))
            continue
        assert line.new_item is not None
        resolved.append((await _add_item(session, actor, line.new_item, index), line))
    return resolved


def _require_countable(item: StockItem, index: int) -> None:
    try:
        check_countable(item, item.unit)
    except AppError as error:
        error.fields["line"] = str(index)
        raise


async def _add_item(session: TenantSession, actor: Membership, new: CleanNewItem, index: int) -> UUID:
    """A good met for the first time on a receipt: it joins the catalogue, counted from the start."""
    name_norm = normalize_name(new.name)
    created = await session.insert_catalog_item(
        item_id=uuid4(), name=new.name, name_norm=name_norm, unit=new.unit, price=new.price, learned=False
    )
    if created is None:
        existing = await session.catalog_item_by_norm(name_norm)
        fields = {"line": str(index)}
        if existing is not None:
            fields["existing_id"] = str(existing.merged_into or existing.item_id)
        raise CatalogNameTaken(fields)
    await session.set_item_stock(created.item_id, tracked=True, low_stock=None, unit=new.unit)
    if new.barcode is not None:
        taken = await session.replace_barcodes(created.item_id, [new.barcode])
        if taken is not None:
            raise BarcodeTaken({"code": taken, "line": str(index)})
    await session.record_activity(
        membership_id=actor.membership_id,
        action="catalog.item.created",
        subject_type="catalog_item",
        subject_id=created.item_id,
    )
    return created.item_id


async def _lock_for(session: TenantSession, document_id: UUID) -> DocumentRecord:
    """The document under its lock. The customer of a customer's return is locked first, in the order
    every write to a customer's account takes its locks, so the two can never wait for each other."""
    seen = await session.get_document(document_id, for_update=False)
    if seen is None:
        raise NotFound()
    if seen.customer_id is not None:
        await session.get_customer(seen.customer_id, for_update=True)
    document = await session.get_document(document_id, for_update=True)
    if document is None:
        raise NotFound()
    return document


async def post_in(session: TenantSession, actor: Membership, document_id: UUID, *, now: datetime) -> DocumentRecord:
    """Make a draft take effect: its lines, a movement for each, and the money that goes with them."""
    document = await _lock_for(session, document_id)
    require_permission(actor, PERMISSION_OF_KIND[document.kind])
    if document.status != stock.DRAFT:
        raise DocumentNotDraft()
    kind, lines = document.kind, _draft_lines(document)
    lang = "uz"
    settings = await session.shop_settings()
    if settings is not None:
        lang = settings.lang

    if document.supplier_id is not None:
        supplier = await session.get_supplier(document.supplier_id, for_update=True)
        if supplier is None:
            raise NotFound()
        if supplier.status == supplier_rules.ARCHIVED:
            raise SupplierArchived()

    # The customer's ledger first: its entry is what the movements of a return point at, and a refusal
    # there (more than is owed, an archived customer) stops the document before anything moves.
    ledger_entry_id: UUID | None = None
    cash_entry_id: UUID | None = None
    if kind == stock.DOC_CUSTOMER_RETURN:
        assert document.customer_id is not None
        owed_less = document.total - document.paid
        if owed_less:
            written = await append_entry_in(
                session,
                actor,
                document.customer_id,
                kind=EntryKind.PAYMENT,
                amount=owed_less,
                note=_RETURN_NOTE.get(lang, _RETURN_NOTE["uz"]).format(number=document.number),
                promised_date=None,
                now=now,
            )
            ledger_entry_id = UUID(written["entry"]["id"])

    items = await lock_items(session, [line.item_id for line in lines])
    stored: list[DocumentLine] = []
    for line in lines:
        item = items.get(line.item_id)
        if item is None:
            raise ValidationFailed({f"lines.{line.line_no - 1}.item_id": "not an item of this shop"})
        if kind == stock.DOC_RECEIPT and not item.tracked:
            _require_countable(item, line.line_no - 1)
            await session.set_item_stock(item.item_id, tracked=True, low_stock=item.low_stock, unit=item.unit)
        elif kind != stock.DOC_RECEIPT and not item.tracked:
            raise ValidationFailed({f"lines.{line.line_no - 1}.item_id": "not counted in stock"})
        expected = item.level.on_hand if kind == stock.DOC_STOCKTAKE else None
        stored.append(DocumentLine(line.line_no, line.item_id, line.qty, line.unit_cost, line.line_total, expected))
    await session.add_document_lines(document_id, stored)

    for line in stored:
        await _move_line(session, actor, document, line, ledger_entry_id=ledger_entry_id, now=now)

    if document.supplier_id is not None:
        note = _PURCHASE_NOTE.get(lang, _PURCHASE_NOTE["uz"]).format(number=document.number)
        if kind == stock.DOC_RECEIPT:
            if document.total:
                await supplier_account.append_entry_in(
                    session,
                    actor,
                    document.supplier_id,
                    kind=supplier_rules.PURCHASE,
                    amount=document.total,
                    currency=document.currency,
                    note=None,
                    now=now,
                    document_id=document_id,
                )
            if document.paid:
                # Paying a supplier is a permission of its own, on a receipt as anywhere else.
                require_permission(actor, permissions.SUPPLIERS_PAY)
                await supplier_account.pay_in(
                    session,
                    actor,
                    document.supplier_id,
                    amount=document.paid,
                    currency=document.currency,
                    note=note,
                    now=now,
                    document_id=document_id,
                )
        elif document.total:
            await supplier_account.append_entry_in(
                session,
                actor,
                document.supplier_id,
                kind=supplier_rules.RETURN,
                amount=document.total,
                currency=document.currency,
                note=None,
                now=now,
                document_id=document_id,
            )
    elif kind == stock.DOC_RECEIPT and document.paid:
        cash_entry_id = await stock_cash.purchase_expense(
            session,
            actor,
            amount=document.paid,
            currency=document.currency,
            note=_PURCHASE_NOTE.get(lang, _PURCHASE_NOTE["uz"]).format(number=document.number),
            now=now,
        )

    await session.mark_document_posted(
        document_id,
        posted_by=actor.membership_id,
        posted_at=now,
        ledger_entry_id=ledger_entry_id,
        cash_entry_id=cash_entry_id,
    )
    await session.record_activity(
        membership_id=actor.membership_id,
        action="stock.document_posted",
        subject_type="stock_document",
        subject_id=document_id,
        detail={"kind": kind, "number": document.number},
    )
    posted = await session.get_document(document_id, for_update=False)
    assert posted is not None
    return posted


async def _move_line(
    session: TenantSession,
    actor: Membership,
    document: DocumentRecord,
    line: DocumentLine,
    *,
    ledger_entry_id: UUID | None,
    now: datetime,
) -> None:
    kind, qty = document.kind, line.qty
    links: dict[str, Any] = {"document_id": document.document_id, "line_no": line.line_no, "now": now}
    if kind == stock.DOC_RECEIPT:
        assert line.unit_cost is not None
        cost = line.unit_cost
        await move(
            session,
            actor,
            line.item_id,
            kind=stock.RECEIPT,
            compute=lambda level: stock.receive(level, qty, cost, document.currency),
            unit_cost=cost,
            **links,
        )
    elif kind == stock.DOC_SUPPLIER_RETURN:
        await move(
            session,
            actor,
            line.item_id,
            kind=stock.SUPPLIER_RETURN,
            compute=lambda level: stock.go_out(level, qty, may_go_negative=False),
            **links,
        )
    elif kind == stock.DOC_WRITE_OFF:
        await move(
            session,
            actor,
            line.item_id,
            kind=stock.WRITE_OFF,
            compute=lambda level: stock.go_out(level, qty, may_go_negative=False),
            reason=document.reason,
            **links,
        )
    elif kind == stock.DOC_CUSTOMER_RETURN:
        await move(
            session,
            actor,
            line.item_id,
            kind=stock.CUSTOMER_RETURN,
            compute=lambda level: stock.find_more(level, qty),
            ledger_entry_id=ledger_entry_id,
            **links,
        )
    else:
        await move(
            session,
            actor,
            line.item_id,
            kind=stock.CORRECTION,
            compute=lambda level: stock.correct_to(level, qty),
            **links,
        )


async def cancel_in(
    session: TenantSession, actor: Membership, document_id: UUID, *, reason: str, now: datetime
) -> DocumentRecord:
    """Take a document back. A draft is simply dropped; a posted one has everything it did reversed."""
    document = await _lock_for(session, document_id)
    require_permission(actor, PERMISSION_OF_KIND[document.kind])
    if document.status == stock.CANCELLED:
        raise DocumentCancelled()
    if document.status == stock.POSTED:
        # The goods first: if they cannot come back, nothing of the money is touched.
        await reverse_movements(session, actor, await session.standing_movements_of_document(document_id), now=now)
        if document.supplier_id is not None:
            await session.get_supplier(document.supplier_id, for_update=True)
            for entry in await session.standing_supplier_entries_of_document(document_id):
                await supplier_account.reverse_entry_in(session, actor, entry, reason=reason, now=now)
        await stock_cash.cancel_expense(session, actor, document.cash_entry_id, reason=reason, now=now)
        if document.ledger_entry_id is not None:
            # By the customers' ledger's own rule: a reversal entry, the original untouched.
            await reverse_entry_in(session, actor, document.ledger_entry_id, now=now)
    await session.mark_document_cancelled(
        document_id, cancelled_by=actor.membership_id, cancelled_at=now, reason=reason
    )
    await session.record_activity(
        membership_id=actor.membership_id,
        action="stock.document_cancelled",
        subject_type="stock_document",
        subject_id=document_id,
        detail={"kind": document.kind, "number": document.number, "was": document.status},
    )
    cancelled = await session.get_document(document_id, for_update=False)
    assert cancelled is not None
    return cancelled


class DocumentService:
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
        kind: str | None,
        status: str | None,
        supplier_id: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, LIST_DOCUMENTS)
            await require_viewable(session, actor, self._today())
            fields: dict[str, str] = {}
            if not 1 <= limit <= MAX_PAGE:
                fields["limit"] = f"must be between 1 and {MAX_PAGE}"
            if kind is not None and kind not in stock.DOCUMENT_KINDS:
                fields["kind"] = "not a kind of document"
            if status is not None and status not in (stock.DRAFT, stock.POSTED, stock.CANCELLED):
                fields["status"] = "must be draft, posted or cancelled"
            if fields:
                raise ValidationFailed(fields)
            before: tuple[datetime, UUID] | None = None
            if cursor:
                at, last = decode_cursor(cursor, 2)
                try:
                    before = (datetime.fromisoformat(at), UUID(last))
                except ValueError as error:
                    raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error
            rows = await session.list_documents(
                kind=kind, status=status, supplier_id=supplier_id, before=before, limit=limit + 1
            )
            page, more = rows[:limit], len(rows) > limit
            return {
                "documents": [document_summary(row, money=shows_money(row, actor)) for row in page],
                "next_cursor": encode_cursor(page[-1].created_at.isoformat(), page[-1].document_id) if more else None,
            }

    async def read(self, user_id: UUID, shop_id: UUID, document_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, READ_DOCUMENT)
            await require_viewable(session, actor, self._today())
            document = await session.get_document(document_id, for_update=False)
            if document is None:
                raise NotFound()
            return await document_body_in(session, actor, document)

    async def create(
        self, user_id: UUID, shop_id: UUID, request: DocumentRequest, *, post: bool, request_key: str | None
    ) -> dict[str, Any]:
        """Write a document as a draft, or write and post it in one step."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, CREATE_DOCUMENT)
            key = idempotency.validate_key(request_key)
            clean = clean_document(request, self._today())
            require_permission(actor, PERMISSION_OF_KIND[clean.kind])
            currency = await self._currency(session, clean.kind, request.currency)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                now = self._now()
                resolved = await _resolve(session, actor, clean, now=now)
                document_id = uuid4()
                await session.insert_document(
                    document_id=document_id,
                    kind=clean.kind,
                    number=await session.next_document_number(clean.kind),
                    doc_date=clean.doc_date,
                    supplier_id=clean.supplier_id,
                    customer_id=clean.customer_id,
                    currency=currency,
                    total=clean.total,
                    paid=clean.paid,
                    reason=clean.reason,
                    note=clean.note,
                    draft=_draft(resolved),
                    created_by=actor.membership_id,
                    created_at=now,
                )
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="stock.document_created",
                    subject_type="stock_document",
                    subject_id=document_id,
                    detail={"kind": clean.kind},
                )
                if post:
                    document = await post_in(session, actor, document_id, now=now)
                else:
                    found = await session.get_document(document_id, for_update=False)
                    assert found is not None
                    document = found
                return await document_body_in(session, actor, document)

            return await idempotency.run_once(
                session,
                key=key,
                operation=CREATE_DOCUMENT.name,
                user_id=user_id,
                request={**_request_fingerprint(request), "post": post},
                action=apply,
            )

    async def update(
        self, user_id: UUID, shop_id: UUID, document_id: UUID, request: DocumentRequest, *, request_key: str | None
    ) -> dict[str, Any]:
        """Replace what a draft says, as a whole. Its kind and its number stay."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, UPDATE_DOCUMENT)
            key = idempotency.validate_key(request_key)
            clean = clean_document(request, self._today())
            require_permission(actor, PERMISSION_OF_KIND[clean.kind])
            currency = await self._currency(session, clean.kind, request.currency)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                document = await session.get_document(document_id, for_update=True)
                if document is None:
                    raise NotFound()
                if document.kind != clean.kind:
                    raise ValidationFailed({"kind": "the kind of a document does not change"})
                if document.status != stock.DRAFT:
                    raise DocumentNotDraft()
                resolved = await _resolve(session, actor, clean, now=self._now())
                await session.update_draft(
                    document_id,
                    doc_date=clean.doc_date,
                    supplier_id=clean.supplier_id,
                    customer_id=clean.customer_id,
                    currency=currency,
                    total=clean.total,
                    paid=clean.paid,
                    reason=clean.reason,
                    note=clean.note,
                    draft=_draft(resolved),
                )
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="stock.document_changed",
                    subject_type="stock_document",
                    subject_id=document_id,
                    detail={"kind": clean.kind, "number": document.number},
                )
                updated = await session.get_document(document_id, for_update=False)
                assert updated is not None
                return await document_body_in(session, actor, updated)

            return await idempotency.run_once(
                session,
                key=key,
                operation=UPDATE_DOCUMENT.name,
                user_id=user_id,
                request={**_request_fingerprint(request), "document": document_id},
                action=apply,
            )

    async def post(self, user_id: UUID, shop_id: UUID, document_id: UUID, *, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, POST_DOCUMENT)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                document = await post_in(session, actor, document_id, now=self._now())
                return await document_body_in(session, actor, document)

            return await idempotency.run_once(
                session,
                key=key,
                operation=POST_DOCUMENT.name,
                user_id=user_id,
                request={"document": str(document_id)},
                action=apply,
            )

    async def cancel(
        self, user_id: UUID, shop_id: UUID, document_id: UUID, *, reason: str, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, CANCEL_DOCUMENT)
            key = idempotency.validate_key(request_key)
            try:
                why = supplier_rules.reason(reason)
            except ValueError as error:
                raise ValidationFailed({"reason": str(error)}) from error
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                document = await cancel_in(session, actor, document_id, reason=why, now=self._now())
                return await document_body_in(session, actor, document)

            return await idempotency.run_once(
                session,
                key=key,
                operation=CANCEL_DOCUMENT.name,
                user_id=user_id,
                request={"document": str(document_id), "reason": why},
                action=apply,
            )

    @staticmethod
    async def _currency(session: TenantSession, kind: str, code: str | None) -> str:
        """The document's currency. Only what is bought or sent back to a supplier has a choice: a
        customer's return is in so'm like the goods lines it undoes, and the rest carry no price."""
        if kind in (stock.DOC_RECEIPT, stock.DOC_SUPPLIER_RETURN):
            return await require_currency(session, code)
        if code not in (None, UZS):
            raise ValidationFailed({"currency": "must be UZS for this kind of document"})
        return UZS
