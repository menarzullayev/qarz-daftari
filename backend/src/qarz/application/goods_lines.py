"""Goods lines of a credit sale: given with the sale, or added to it once afterwards (REQ-037, REQ-038, REQ-040).

A line chosen from the catalog takes the item's name and unit; a typed line teaches the catalog its good
(BR-6). Either way the line keeps its own name, unit and price, so the catalog can change later without
altering a saved sale (INV-17). The database repeats the three hard rules as triggers: one batch per
entry, the sum equal to the entry total, and the time limit.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from qarz.application.authorization import require_permission
from qarz.application.catalog import register_learned
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.operations import operation
from qarz.application.ports import CatalogItemRecord, GoodsLineRecord, Membership, TenantSession
from qarz.application.stock_moves import draw_for_sale
from qarz.domain import permissions
from qarz.domain.access import Capability
from qarz.domain.catalog import check_price, item_name, normalize_unit
from qarz.domain.goods import MAX_LINES, MIN_LINES, format_qty, line_count_allowed, lines_window_open, parse_qty
from qarz.domain.ledger import EntryKind
from qarz.domain.names import normalize_name
from qarz.domain.rounding import line_total

ADD_LINES = operation("ledger.entry.lines.add", Capability.RECORD)


class LinesAlreadyAdded(AppError):
    """Goods lines are added to an entry once (INV-8)."""

    code = "LINES_ALREADY_ADDED"


class LinesSumMismatch(AppError):
    """The line totals do not add up to the entry total (INV-7)."""

    code = "LINES_SUM_MISMATCH"

    def __init__(self, *, lines_sum: int, amount: int) -> None:
        super().__init__({"lines_sum": str(lines_sum), "amount": str(amount)})


class LinesWindowClosed(AppError):
    """The end of the day after the sale has passed (INV-8)."""

    code = "LINES_WINDOW_CLOSED"


class EntryReversed(AppError):
    code = "ALREADY_REVERSED"


@dataclass(frozen=True)
class LineRequest:
    """One line as the caller sent it."""

    catalog_item_id: UUID | None
    name: str | None
    qty: str
    unit: str | None
    unit_price: int


@dataclass(frozen=True)
class CleanLine:
    """A line whose shape has been checked. `name` and `unit` are set on a typed line only."""

    catalog_item_id: UUID | None
    name: str | None
    unit: str | None
    qty: Decimal
    unit_price: int
    line_total: int


def _clean_line(line: LineRequest) -> tuple[CleanLine | None, dict[str, str]]:
    fields: dict[str, str] = {}
    name: str | None = None
    unit: str | None = None
    if line.catalog_item_id is not None:
        # The catalog item supplies both; a second name beside it could only disagree with it.
        if line.name is not None:
            fields["name"] = "leave out when catalog_item_id is given"
        if line.unit is not None:
            fields["unit"] = "leave out when catalog_item_id is given"
    elif line.name is None:
        fields["name"] = "required when catalog_item_id is absent"
    else:
        try:
            name = item_name(line.name)
        except ValueError as error:
            fields["name"] = str(error)
        try:
            unit = normalize_unit(line.unit)
        except ValueError as error:
            fields["unit"] = str(error)

    qty: Decimal | None = None
    try:
        qty = parse_qty(line.qty)
    except ValueError as error:
        fields["qty"] = str(error)
    price: int | None = None
    try:
        price = check_price(line.unit_price)
    except ValueError as error:
        fields["unit_price"] = str(error)
    if fields or qty is None or price is None:
        return None, fields
    try:
        total = line_total(qty, price)
    except ValueError:
        return None, {"qty": "quantity times unit price must come to at least 1 UZS"}
    return CleanLine(line.catalog_item_id, name, unit, qty, price, total), {}


def clean_lines(raw: Sequence[LineRequest]) -> list[CleanLine]:
    """Check the shape of the lines without touching storage. Field names carry the line's position."""
    if not line_count_allowed(len(raw)):
        raise ValidationFailed({"lines": f"between {MIN_LINES} and {MAX_LINES} lines"})
    cleaned: list[CleanLine] = []
    fields: dict[str, str] = {}
    for index, line in enumerate(raw):
        clean, problems = _clean_line(line)
        fields.update({f"lines.{index}.{field}": problem for field, problem in problems.items()})
        if clean is not None:
            cleaned.append(clean)
    if fields:
        raise ValidationFailed(fields)
    return cleaned


def lines_sum(lines: Sequence[CleanLine]) -> int:
    return sum(line.line_total for line in lines)


def require_sum(lines: Sequence[CleanLine], amount: int) -> None:
    """INV-7. Checked here so that the deferred database trigger never has to refuse at commit."""
    total = lines_sum(lines)
    if total != amount:
        raise LinesSumMismatch(lines_sum=total, amount=amount)


def lines_request(lines: Sequence[CleanLine]) -> list[dict[str, Any]]:
    """The lines as part of an idempotent request: the same lines always give the same fingerprint."""
    return [
        {
            "catalog_item_id": line.catalog_item_id,
            "name": line.name,
            "qty": format_qty(line.qty),
            "unit": line.unit,
            "unit_price": line.unit_price,
        }
        for line in lines
    ]


def line_body(line: GoodsLineRecord) -> dict[str, Any]:
    return {
        "line_no": line.line_no,
        "catalog_item_id": None if line.catalog_item_id is None else str(line.catalog_item_id),
        "name": line.name,
        "qty": format_qty(line.qty),
        "unit": line.unit,
        "unit_price": line.unit_price,
        "line_total": line.line_total,
    }


async def store_lines_in(
    session: TenantSession, actor: Membership, entry_id: UUID, lines: Sequence[CleanLine]
) -> list[GoodsLineRecord]:
    """Resolve each line against the catalog and store them all as the entry's one batch.

    The caller has authorized the actor, locked the customer's account and checked the sum. A refusal
    here is raised before anything of the lines is stored; the caller's transaction then rolls back.
    """
    chosen: dict[UUID, CatalogItemRecord] = {}
    fields: dict[str, str] = {}
    for index, line in enumerate(lines):
        if line.catalog_item_id is None or line.catalog_item_id in chosen:
            continue
        # Another shop's item is invisible through the tenant session, so it reads as unknown.
        item = await session.get_catalog_item(line.catalog_item_id, for_update=False)
        if item is None or item.status != "active":
            fields[f"lines.{index}.catalog_item_id"] = "not an item shown in this shop's catalog"
        else:
            chosen[item.item_id] = item
    if fields:
        raise ValidationFailed(fields)

    typed: dict[str, CleanLine] = {}
    for line in lines:
        if line.name is not None:
            typed.setdefault(normalize_name(line.name), line)
    learned: dict[str, UUID] = {}
    # In name order: two sales that type the same new goods then take the catalog's unique-name locks in
    # the same order and cannot deadlock each other.
    for name_norm in sorted(typed):
        first = typed[name_norm]
        learned[name_norm] = await register_learned(
            session, actor, name=first.name or "", unit=first.unit, price=first.unit_price
        )

    records: list[GoodsLineRecord] = []
    for line_no, line in enumerate(lines, start=1):
        if line.catalog_item_id is not None:
            item = chosen[line.catalog_item_id]
            item_id, name, unit = item.item_id, item.name, item.unit
        else:
            name, unit = line.name or "", line.unit or ""
            item_id = learned[normalize_name(name)]
        records.append(
            GoodsLineRecord(
                line_no=line_no,
                catalog_item_id=item_id,
                name=name,
                qty=line.qty,
                unit=unit,
                unit_price=line.unit_price,
                line_total=line.line_total,
            )
        )
    await session.add_goods_lines(entry_id, records)
    return records


async def add_lines_in(
    session: TenantSession, actor: Membership, entry_id: UUID, lines: Sequence[CleanLine], *, now: datetime
) -> dict[str, Any]:
    """Add goods lines to an amount-only credit sale, once (REQ-038, INV-8).

    Open to the entry's author, and to managers and owners, until the end of the day after the sale.
    """
    customer_id = await session.customer_of_entry(entry_id)
    if customer_id is None:
        raise NotFound()
    # The account lock also makes two additions to one entry take turns: the second finds the first's lines.
    customer = await session.get_customer(customer_id, for_update=True)
    if customer is None:
        raise NotFound()
    account = await session.entries_of(customer_id)
    row = next((candidate for candidate in account if candidate.entry.id == entry_id), None)
    if row is None:
        raise NotFound()
    if row.author_id != actor.membership_id:
        require_permission(actor, permissions.ENTRIES_OTHERS)

    entry = row.entry
    if entry.kind is not EntryKind.CREDIT:
        raise ValidationFailed({"entry": "only a credit sale has goods lines"})
    if any(other.entry.reverses_id == entry_id for other in account):
        raise EntryReversed()
    if await session.goods_lines_of([entry_id]):
        raise LinesAlreadyAdded()
    if not lines_window_open(entry.created_at, now):
        raise LinesWindowClosed()
    require_sum(lines, entry.amount)

    stored = await store_lines_in(session, actor, entry_id, lines)
    stock_warnings = await draw_for_sale(session, actor, entry_id, stored, now=now)
    await session.record_activity(
        membership_id=actor.membership_id,
        action="ledger.lines_added",
        subject_type="customer",
        subject_id=customer_id,
    )
    await session.record_measure(kind="lines_added", entry_ref=entry_id, amount=entry.amount, promised=None)
    body: dict[str, Any] = {
        "entry": {"id": str(entry_id), "amount": entry.amount, "lines": [line_body(line) for line in stored]}
    }
    if stock_warnings:
        body["stock_warnings"] = stock_warnings
    return body
