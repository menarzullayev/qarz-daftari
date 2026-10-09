"""PostgreSQL storage of the stock, its documents and the suppliers (module I of the expansion).

`PgTenantSession` inherits `StockQueries`, so these statements run in the tenant transaction of the
request, under the same row-level security as everything else. SQL here is composed only from the
module-level constants below, with every value bound as a parameter (tests/test_sql_composition.py).
"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from qarz.application.stock_ports import (
    BelowCostSale,
    DocumentLine,
    DocumentRecord,
    MovementRecord,
    NewMovement,
    StockItem,
    StockTotals,
    SupplierEntryRecord,
    SupplierRecord,
)
from qarz.domain.stock import ZERO, Level

_ITEM_COLUMNS = (
    "i.id, i.name, i.name_norm, i.unit, i.price, i.status, i.learned, i.merged_into, i.tracked, i.low_stock, "
    "coalesce(l.on_hand, 0) AS on_hand, coalesce(l.cost_value, 0) AS cost_value, l.cost_currency, l.last_cost, "
    "coalesce(l.last_seq, 0) AS last_seq, l.last_sale_at"
)
_ITEM_FROM = "FROM catalog_item i LEFT JOIN stock_level l ON l.item_id = i.id"
_ITEM_BY_ID = f"SELECT {_ITEM_COLUMNS} {_ITEM_FROM} WHERE i.id = :id"
# The lock is on the catalogue row: it exists before the first movement, when there is no level yet.
_ITEM_LOCKED = f"{_ITEM_BY_ID} FOR UPDATE OF i"
_PAGE_AFTER = (
    "AND (CAST(:name AS text) IS NULL OR i.name_norm LIKE CAST(:name AS text)) "
    "AND (CAST(:after_name AS text) IS NULL "
    "     OR (i.name_norm, i.id) > (CAST(:after_name AS text), CAST(:after_id AS uuid))) "
    "ORDER BY i.name_norm, i.id LIMIT :limit"
)
_ITEMS_ALL = f"SELECT {_ITEM_COLUMNS} {_ITEM_FROM} WHERE i.status = 'active' {_PAGE_AFTER}"
_ITEMS_TRACKED = f"SELECT {_ITEM_COLUMNS} {_ITEM_FROM} WHERE i.tracked {_PAGE_AFTER}"
_LOW = "i.tracked AND i.low_stock IS NOT NULL AND coalesce(l.on_hand, 0) <= i.low_stock"
_ITEMS_LOW = f"SELECT {_ITEM_COLUMNS} {_ITEM_FROM} WHERE {_LOW} {_PAGE_AFTER}"
_ITEM_LISTS = {"all": _ITEMS_ALL, "tracked": _ITEMS_TRACKED, "low": _ITEMS_LOW}

_MOVEMENT_COLUMNS = (
    "m.id, m.item_id, m.item_seq, m.kind, m.qty, m.unit_cost, m.cost_total, m.sale_total, m.value_delta, "
    "m.currency, m.on_hand_after, m.value_after, m.reason, m.document_id, m.line_no, m.ledger_entry_id, "
    "m.reverses_id, m.author_id, m.created_at, "
    "EXISTS (SELECT 1 FROM stock_movement r WHERE r.reverses_id = m.id) AS is_reversed"
)
_STANDING = "m.kind <> 'reversal' AND NOT EXISTS (SELECT 1 FROM stock_movement r WHERE r.reverses_id = m.id)"

_DOCUMENT_COLUMNS = (
    "d.id, d.kind, d.number, d.status, d.doc_date, d.supplier_id, d.customer_id, d.currency, d.total, d.paid, "
    "d.reason, d.note, d.draft, d.ledger_entry_id, d.cash_entry_id, d.created_by, d.created_at, "
    "d.posted_at, d.cancelled_at, d.cancel_reason, "
    "(SELECT s.name FROM supplier s WHERE s.id = d.supplier_id) AS supplier_name, "
    "(SELECT c.display_name FROM customer c WHERE c.id = d.customer_id) AS customer_name"
)
_DOCUMENT_BY_ID = f"SELECT {_DOCUMENT_COLUMNS} FROM stock_document d WHERE d.id = :id"
_DOCUMENT_LOCKED = f"{_DOCUMENT_BY_ID} FOR UPDATE OF d"

_SUPPLIER_COLUMNS = "s.id, s.name, s.name_norm, s.phone, s.note, s.status, s.created_at"
_SUPPLIER_BY_ID = f"SELECT {_SUPPLIER_COLUMNS} FROM supplier s WHERE s.id = :id"
_SUPPLIER_LOCKED = f"{_SUPPLIER_BY_ID} FOR UPDATE"

_ENTRY_COLUMNS = (
    "e.id, e.supplier_id, e.seq, e.kind, e.amount, e.currency, e.note, e.reverses_id, e.document_id, "
    "e.cash_entry_id, e.author_id, e.created_at, "
    "EXISTS (SELECT 1 FROM supplier_entry r WHERE r.reverses_id = e.id) AS is_reversed, "
    "d.kind AS document_kind, d.number AS document_number"
)
_ENTRY_FROM = "FROM supplier_entry e LEFT JOIN stock_document d ON d.id = e.document_id"


def like_pattern(part: str) -> str:
    """The text as a LIKE pattern that matches it anywhere, with its own wildcards made literal."""
    escaped = part.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + escaped + "%"


def _item(row: Any) -> StockItem:
    return StockItem(
        item_id=row.id,
        name=row.name,
        name_norm=row.name_norm,
        unit=row.unit,
        price=int(row.price),
        status=row.status,
        learned=row.learned,
        merged_into=row.merged_into,
        tracked=row.tracked,
        low_stock=row.low_stock,
        level=Level(
            on_hand=Decimal(row.on_hand) if row.on_hand is not None else ZERO,
            value=int(row.cost_value),
            currency=row.cost_currency,
            last_cost=row.last_cost,
        ),
        last_seq=int(row.last_seq),
        last_sale_at=row.last_sale_at,
    )


def _movement(row: Any, *, with_document: bool = False) -> MovementRecord:
    return MovementRecord(
        movement_id=row.id,
        item_id=row.item_id,
        item_seq=int(row.item_seq),
        kind=row.kind,
        qty=row.qty,
        unit_cost=None if row.unit_cost is None else int(row.unit_cost),
        cost_total=None if row.cost_total is None else int(row.cost_total),
        sale_total=None if row.sale_total is None else int(row.sale_total),
        value_delta=int(row.value_delta),
        currency=row.currency,
        on_hand_after=row.on_hand_after,
        value_after=int(row.value_after),
        reason=row.reason,
        document_id=row.document_id,
        line_no=None if row.line_no is None else int(row.line_no),
        ledger_entry_id=row.ledger_entry_id,
        reverses_id=row.reverses_id,
        author_id=row.author_id,
        created_at=row.created_at,
        is_reversed=bool(row.is_reversed),
        document_kind=row.document_kind if with_document else None,
        document_number=(None if row.document_number is None else int(row.document_number)) if with_document else None,
    )


def _document(row: Any) -> DocumentRecord:
    draft = row.draft
    return DocumentRecord(
        document_id=row.id,
        kind=row.kind,
        number=int(row.number),
        status=row.status,
        doc_date=row.doc_date,
        supplier_id=row.supplier_id,
        customer_id=row.customer_id,
        currency=row.currency,
        total=int(row.total),
        paid=int(row.paid),
        reason=row.reason,
        note=row.note,
        draft=json.loads(draft) if isinstance(draft, str) else draft,
        ledger_entry_id=row.ledger_entry_id,
        cash_entry_id=row.cash_entry_id,
        created_by=row.created_by,
        created_at=row.created_at,
        posted_at=row.posted_at,
        cancelled_at=row.cancelled_at,
        cancel_reason=row.cancel_reason,
        supplier_name=row.supplier_name,
        customer_name=row.customer_name,
    )


def _supplier(row: Any) -> SupplierRecord:
    return SupplierRecord(row.id, row.name, row.name_norm, row.phone, row.note, row.status, row.created_at)


def _entry(row: Any) -> SupplierEntryRecord:
    return SupplierEntryRecord(
        entry_id=row.id,
        supplier_id=row.supplier_id,
        seq=int(row.seq),
        kind=row.kind,
        amount=int(row.amount),
        currency=row.currency,
        note=row.note,
        reverses_id=row.reverses_id,
        document_id=row.document_id,
        cash_entry_id=row.cash_entry_id,
        author_id=row.author_id,
        created_at=row.created_at,
        is_reversed=bool(row.is_reversed),
        document_kind=row.document_kind,
        document_number=None if row.document_number is None else int(row.document_number),
    )


class StockQueries:
    _conn: AsyncConnection
    _shop_id: UUID

    # --- items ------------------------------------------------------------------------------------

    async def stock_item(self, item_id: UUID, *, for_update: bool) -> StockItem | None:
        row = (await self._conn.execute(text(_ITEM_LOCKED if for_update else _ITEM_BY_ID), {"id": item_id})).first()
        return None if row is None else _item(row)

    async def stock_items(
        self, *, name_part: str | None, only: str, after: tuple[str, UUID] | None, limit: int
    ) -> list[StockItem]:
        rows = (
            await self._conn.execute(
                text(_ITEM_LISTS[only]),
                {
                    "name": like_pattern(name_part) if name_part else None,
                    "after_name": after[0] if after else None,
                    "after_id": after[1] if after else None,
                    "limit": limit,
                },
            )
        ).all()
        return [_item(row) for row in rows]

    async def stock_items_by_ids(self, item_ids: list[UUID]) -> dict[UUID, StockItem]:
        if not item_ids:
            return {}
        rows = (
            await self._conn.execute(
                text(f"SELECT {_ITEM_COLUMNS} {_ITEM_FROM} WHERE i.id = ANY(CAST(:ids AS uuid[]))"), {"ids": item_ids}
            )
        ).all()
        return {row.id: _item(row) for row in rows}

    async def set_item_stock(self, item_id: UUID, *, tracked: bool, low_stock: Decimal | None, unit: str) -> None:
        await self._conn.execute(
            text(
                "UPDATE catalog_item SET tracked = :tracked, low_stock = CAST(:low AS numeric), unit = :unit "
                "WHERE id = :id"
            ),
            {"id": item_id, "tracked": tracked, "low": low_stock, "unit": unit},
        )

    async def barcodes_of(self, item_ids: list[UUID]) -> dict[UUID, list[str]]:
        if not item_ids:
            return {}
        rows = (
            await self._conn.execute(
                text(
                    "SELECT b.item_id, b.code FROM catalog_barcode b "
                    "WHERE b.item_id = ANY(CAST(:ids AS uuid[])) ORDER BY b.item_id, b.created_at, b.code"
                ),
                {"ids": item_ids},
            )
        ).all()
        found: dict[UUID, list[str]] = {}
        for row in rows:
            found.setdefault(row.item_id, []).append(row.code)
        return found

    async def replace_barcodes(self, item_id: UUID, codes: list[str]) -> str | None:
        taken = (
            await self._conn.execute(
                text(
                    "SELECT b.code FROM catalog_barcode b "
                    "WHERE b.code = ANY(CAST(:codes AS text[])) AND b.item_id <> :item ORDER BY b.code LIMIT 1"
                ),
                {"codes": codes, "item": item_id},
            )
        ).first()
        if taken is not None:
            return str(taken.code)
        await self._conn.execute(
            text("DELETE FROM catalog_barcode WHERE item_id = :item AND code <> ALL(CAST(:codes AS text[]))"),
            {"item": item_id, "codes": codes},
        )
        if codes:
            # In the order given: `created_at` then tells the order they are shown in.
            await self._conn.execute(
                text(
                    "INSERT INTO catalog_barcode (shop_id, code, item_id, created_at) "
                    "SELECT :shop_id, c.code, :item, now() + c.place * interval '1 microsecond' "
                    "FROM unnest(CAST(:codes AS text[])) WITH ORDINALITY AS c(code, place) "
                    "ON CONFLICT (shop_id, code) DO NOTHING"
                ),
                {"shop_id": self._shop_id, "item": item_id, "codes": codes},
            )
        return None

    async def item_by_barcode(self, code: str) -> UUID | None:
        row = (
            await self._conn.execute(
                text("SELECT b.item_id FROM catalog_barcode b WHERE b.code = :code"), {"code": code}
            )
        ).first()
        return None if row is None else row.item_id

    async def stock_refuse_negative(self) -> bool:
        row = (
            await self._conn.execute(
                text("SELECT stock_refuse_negative FROM shop WHERE id = :shop_id"), {"shop_id": self._shop_id}
            )
        ).first()
        return bool(row is not None and row.stock_refuse_negative)

    async def set_stock_refuse_negative(self, refuse: bool) -> None:
        await self._conn.execute(
            text("UPDATE shop SET stock_refuse_negative = :refuse WHERE id = :shop_id"),
            {"refuse": refuse, "shop_id": self._shop_id},
        )

    # --- movements --------------------------------------------------------------------------------

    async def add_movement(self, movement: NewMovement) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO stock_movement (id, shop_id, item_id, item_seq, kind, qty, unit_cost, cost_total, "
                "  sale_total, value_delta, currency, on_hand_after, value_after, reason, document_id, line_no, "
                "  ledger_entry_id, reverses_id, author_id, created_at) "
                "VALUES (:id, :shop_id, :item_id, :item_seq, :kind, :qty, :unit_cost, :cost_total, :sale_total, "
                "  :value_delta, :currency, :on_hand_after, :value_after, :reason, :document_id, :line_no, "
                "  :ledger_entry_id, :reverses_id, :author_id, :created_at)"
            ),
            {
                "id": movement.movement_id,
                "shop_id": self._shop_id,
                "item_id": movement.item_id,
                "item_seq": movement.item_seq,
                "kind": movement.kind,
                "qty": movement.qty,
                "unit_cost": movement.unit_cost,
                "cost_total": movement.cost_total,
                "sale_total": movement.sale_total,
                "value_delta": movement.value_delta,
                "currency": movement.currency,
                "on_hand_after": movement.on_hand_after,
                "value_after": movement.value_after,
                "reason": movement.reason,
                "document_id": movement.document_id,
                "line_no": movement.line_no,
                "ledger_entry_id": movement.ledger_entry_id,
                "reverses_id": movement.reverses_id,
                "author_id": movement.author_id,
                "created_at": movement.created_at,
            },
        )

    async def movements_of_item(self, item_id: UUID, *, before_seq: int | None, limit: int) -> list[MovementRecord]:
        rows = (
            await self._conn.execute(
                text(
                    f"SELECT {_MOVEMENT_COLUMNS}, d.kind AS document_kind, d.number AS document_number "
                    "FROM stock_movement m LEFT JOIN stock_document d ON d.id = m.document_id "
                    "WHERE m.item_id = :item "
                    "  AND (CAST(:before AS integer) IS NULL OR m.item_seq < CAST(:before AS integer)) "
                    "ORDER BY m.item_seq DESC LIMIT :limit"
                ),
                {"item": item_id, "before": before_seq, "limit": limit},
            )
        ).all()
        return [_movement(row, with_document=True) for row in rows]

    async def standing_movements_of_entry(self, entry_id: UUID) -> list[MovementRecord]:
        rows = (
            await self._conn.execute(
                text(
                    f"SELECT {_MOVEMENT_COLUMNS} FROM stock_movement m "
                    f"WHERE m.ledger_entry_id = :entry AND {_STANDING} ORDER BY m.created_at DESC, m.id"
                ),
                {"entry": entry_id},
            )
        ).all()
        return [_movement(row) for row in rows]

    async def standing_movements_of_document(self, document_id: UUID) -> list[MovementRecord]:
        rows = (
            await self._conn.execute(
                text(
                    f"SELECT {_MOVEMENT_COLUMNS} FROM stock_movement m "
                    f"WHERE m.document_id = :document AND {_STANDING} ORDER BY m.line_no DESC, m.id"
                ),
                {"document": document_id},
            )
        ).all()
        return [_movement(row) for row in rows]

    # --- documents --------------------------------------------------------------------------------

    async def next_document_number(self, kind: str) -> int:
        await self._conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": "stock_document:" + str(self._shop_id) + ":" + kind},
        )
        row = (
            await self._conn.execute(
                text("SELECT coalesce(max(d.number), 0) + 1 AS next FROM stock_document d WHERE d.kind = :kind"),
                {"kind": kind},
            )
        ).one()
        return int(row.next)

    async def insert_document(
        self,
        *,
        document_id: UUID,
        kind: str,
        number: int,
        doc_date: date,
        supplier_id: UUID | None,
        customer_id: UUID | None,
        currency: str,
        total: int,
        paid: int,
        reason: str | None,
        note: str | None,
        draft: dict[str, Any],
        created_by: UUID,
        created_at: datetime,
    ) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO stock_document (id, shop_id, kind, number, doc_date, supplier_id, customer_id, currency, "
                "  total, paid, reason, note, draft, created_by, created_at) "
                "VALUES (:id, :shop_id, :kind, :number, :doc_date, :supplier_id, :customer_id, :currency, :total, "
                "  :paid, :reason, :note, CAST(:draft AS jsonb), :created_by, :created_at)"
            ),
            {
                "id": document_id,
                "shop_id": self._shop_id,
                "kind": kind,
                "number": number,
                "doc_date": doc_date,
                "supplier_id": supplier_id,
                "customer_id": customer_id,
                "currency": currency,
                "total": total,
                "paid": paid,
                "reason": reason,
                "note": note,
                "draft": json.dumps(draft, sort_keys=True),
                "created_by": created_by,
                "created_at": created_at,
            },
        )

    async def get_document(self, document_id: UUID, *, for_update: bool) -> DocumentRecord | None:
        row = (
            await self._conn.execute(text(_DOCUMENT_LOCKED if for_update else _DOCUMENT_BY_ID), {"id": document_id})
        ).first()
        return None if row is None else _document(row)

    async def update_draft(
        self,
        document_id: UUID,
        *,
        doc_date: date,
        supplier_id: UUID | None,
        customer_id: UUID | None,
        currency: str,
        total: int,
        paid: int,
        reason: str | None,
        note: str | None,
        draft: dict[str, Any],
    ) -> None:
        await self._conn.execute(
            text(
                "UPDATE stock_document SET doc_date = :doc_date, supplier_id = :supplier_id, "
                "  customer_id = :customer_id, currency = :currency, total = :total, paid = :paid, "
                "  reason = :reason, note = :note, draft = CAST(:draft AS jsonb) "
                "WHERE id = :id AND status = 'draft'"
            ),
            {
                "id": document_id,
                "doc_date": doc_date,
                "supplier_id": supplier_id,
                "customer_id": customer_id,
                "currency": currency,
                "total": total,
                "paid": paid,
                "reason": reason,
                "note": note,
                "draft": json.dumps(draft, sort_keys=True),
            },
        )

    async def add_document_lines(self, document_id: UUID, lines: list[DocumentLine]) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO stock_document_line "
                "(shop_id, document_id, line_no, item_id, qty, unit_cost, line_total, expected) "
                "VALUES (:shop_id, :document_id, :line_no, :item_id, :qty, :unit_cost, :line_total, :expected)"
            ),
            [
                {
                    "shop_id": self._shop_id,
                    "document_id": document_id,
                    "line_no": line.line_no,
                    "item_id": line.item_id,
                    "qty": line.qty,
                    "unit_cost": line.unit_cost,
                    "line_total": line.line_total,
                    "expected": line.expected,
                }
                for line in lines
            ],
        )

    async def mark_document_posted(
        self,
        document_id: UUID,
        *,
        posted_by: UUID,
        posted_at: datetime,
        ledger_entry_id: UUID | None,
        cash_entry_id: UUID | None,
    ) -> None:
        await self._conn.execute(
            text(
                "UPDATE stock_document SET status = 'posted', draft = NULL, posted_by = :by, posted_at = :at, "
                "  ledger_entry_id = :entry, cash_entry_id = :cash WHERE id = :id AND status = 'draft'"
            ),
            {"id": document_id, "by": posted_by, "at": posted_at, "entry": ledger_entry_id, "cash": cash_entry_id},
        )

    async def mark_document_cancelled(
        self, document_id: UUID, *, cancelled_by: UUID, cancelled_at: datetime, reason: str
    ) -> None:
        await self._conn.execute(
            text(
                "UPDATE stock_document SET status = 'cancelled', cancelled_by = :by, cancelled_at = :at, "
                "  cancel_reason = :reason WHERE id = :id AND status <> 'cancelled'"
            ),
            {"id": document_id, "by": cancelled_by, "at": cancelled_at, "reason": reason},
        )

    async def document_lines(self, document_id: UUID) -> list[DocumentLine]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT n.line_no, n.item_id, n.qty, n.unit_cost, n.line_total, n.expected "
                    "FROM stock_document_line n WHERE n.document_id = :id ORDER BY n.line_no"
                ),
                {"id": document_id},
            )
        ).all()
        return [
            DocumentLine(
                line_no=int(row.line_no),
                item_id=row.item_id,
                qty=row.qty,
                unit_cost=None if row.unit_cost is None else int(row.unit_cost),
                line_total=None if row.line_total is None else int(row.line_total),
                expected=row.expected,
            )
            for row in rows
        ]

    async def list_documents(
        self,
        *,
        kind: str | None,
        status: str | None,
        supplier_id: UUID | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[DocumentRecord]:
        rows = (
            await self._conn.execute(
                text(
                    f"SELECT {_DOCUMENT_COLUMNS} FROM stock_document d "
                    "WHERE (CAST(:kind AS text) IS NULL OR d.kind = CAST(:kind AS text)) "
                    "  AND (CAST(:status AS text) IS NULL OR d.status = CAST(:status AS text)) "
                    "  AND (CAST(:supplier AS uuid) IS NULL OR d.supplier_id = CAST(:supplier AS uuid)) "
                    "  AND (CAST(:before_at AS timestamptz) IS NULL "
                    "       OR (d.created_at, d.id) < (CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
                    "ORDER BY d.created_at DESC, d.id DESC LIMIT :limit"
                ),
                {
                    "kind": kind,
                    "status": status,
                    "supplier": supplier_id,
                    "before_at": before[0] if before else None,
                    "before_id": before[1] if before else None,
                    "limit": limit,
                },
            )
        ).all()
        return [_document(row) for row in rows]

    # --- suppliers --------------------------------------------------------------------------------

    async def insert_supplier(
        self, *, supplier_id: UUID, name: str, name_norm: str, phone: str | None, note: str | None
    ) -> SupplierRecord | None:
        row = (
            await self._conn.execute(
                text(
                    "INSERT INTO supplier AS s (id, shop_id, name, name_norm, phone, note) "
                    "VALUES (:id, :shop_id, :name, :norm, :phone, :note) "
                    f"ON CONFLICT (shop_id, name_norm) DO NOTHING RETURNING {_SUPPLIER_COLUMNS}"
                ),
                {
                    "id": supplier_id,
                    "shop_id": self._shop_id,
                    "name": name,
                    "norm": name_norm,
                    "phone": phone,
                    "note": note,
                },
            )
        ).first()
        return None if row is None else _supplier(row)

    async def get_supplier(self, supplier_id: UUID, *, for_update: bool) -> SupplierRecord | None:
        row = (
            await self._conn.execute(text(_SUPPLIER_LOCKED if for_update else _SUPPLIER_BY_ID), {"id": supplier_id})
        ).first()
        return None if row is None else _supplier(row)

    async def update_supplier(
        self, supplier_id: UUID, *, name: str, name_norm: str, phone: str | None, note: str | None
    ) -> SupplierRecord | None:
        try:
            # A savepoint, so that a name already taken leaves the transaction usable.
            async with self._conn.begin_nested():
                row = (
                    await self._conn.execute(
                        text(
                            "UPDATE supplier AS s SET name = :name, name_norm = :norm, phone = :phone, note = :note "
                            f"WHERE s.id = :id RETURNING {_SUPPLIER_COLUMNS}"
                        ),
                        {"id": supplier_id, "name": name, "norm": name_norm, "phone": phone, "note": note},
                    )
                ).one()
        except IntegrityError as error:
            if "supplier_shop_id_name_norm_key" in str(error.orig):
                return None
            raise
        return _supplier(row)

    async def set_supplier_status(self, supplier_id: UUID, status: str) -> SupplierRecord:
        row = (
            await self._conn.execute(
                text(f"UPDATE supplier AS s SET status = :status WHERE s.id = :id RETURNING {_SUPPLIER_COLUMNS}"),
                {"id": supplier_id, "status": status},
            )
        ).one()
        return _supplier(row)

    async def search_suppliers(
        self, *, name_part: str | None, status: str, after: tuple[str, UUID] | None, limit: int
    ) -> list[SupplierRecord]:
        rows = (
            await self._conn.execute(
                text(
                    f"SELECT {_SUPPLIER_COLUMNS} FROM supplier s WHERE s.status = :status "
                    "  AND (CAST(:name AS text) IS NULL OR s.name_norm LIKE CAST(:name AS text)) "
                    "  AND (CAST(:after_name AS text) IS NULL "
                    "       OR (s.name_norm, s.id) > (CAST(:after_name AS text), CAST(:after_id AS uuid))) "
                    "ORDER BY s.name_norm, s.id LIMIT :limit"
                ),
                {
                    "status": status,
                    "name": like_pattern(name_part) if name_part else None,
                    "after_name": after[0] if after else None,
                    "after_id": after[1] if after else None,
                    "limit": limit,
                },
            )
        ).all()
        return [_supplier(row) for row in rows]

    async def supplier_balances(self, supplier_ids: list[UUID]) -> dict[UUID, dict[str, int]]:
        if not supplier_ids:
            return {}
        rows = (
            await self._conn.execute(
                text(
                    "SELECT b.supplier_id, b.currency, b.balance FROM supplier_balance b "
                    "WHERE b.supplier_id = ANY(CAST(:ids AS uuid[])) AND b.balance <> 0 ORDER BY b.currency DESC"
                ),
                {"ids": supplier_ids},
            )
        ).all()
        found: dict[UUID, dict[str, int]] = {}
        for row in rows:
            found.setdefault(row.supplier_id, {})[row.currency] = int(row.balance)
        return found

    async def supplier_totals(self) -> dict[str, int]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT b.currency, sum(b.balance)::bigint AS owed FROM supplier_balance b "
                    "WHERE b.shop_id = :shop_id AND b.balance > 0 GROUP BY b.currency ORDER BY b.currency DESC"
                ),
                {"shop_id": self._shop_id},
            )
        ).all()
        return {row.currency: int(row.owed) for row in rows}

    async def last_supplier_seq(self, supplier_id: UUID) -> int:
        row = (
            await self._conn.execute(
                text("SELECT coalesce(max(e.seq), 0) AS seq FROM supplier_entry e WHERE e.supplier_id = :id"),
                {"id": supplier_id},
            )
        ).one()
        return int(row.seq)

    async def append_supplier_entry(
        self,
        *,
        entry_id: UUID,
        supplier_id: UUID,
        seq: int,
        kind: str,
        amount: int,
        currency: str,
        note: str | None,
        reverses_id: UUID | None,
        document_id: UUID | None,
        cash_entry_id: UUID | None,
        author_id: UUID,
        created_at: datetime,
    ) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO supplier_entry (id, shop_id, supplier_id, seq, kind, amount, currency, note, "
                "  reverses_id, document_id, cash_entry_id, author_id, created_at) "
                "VALUES (:id, :shop_id, :supplier_id, :seq, :kind, :amount, :currency, :note, :reverses_id, "
                "  :document_id, :cash_entry_id, :author_id, :created_at)"
            ),
            {
                "id": entry_id,
                "shop_id": self._shop_id,
                "supplier_id": supplier_id,
                "seq": seq,
                "kind": kind,
                "amount": amount,
                "currency": currency,
                "note": note,
                "reverses_id": reverses_id,
                "document_id": document_id,
                "cash_entry_id": cash_entry_id,
                "author_id": author_id,
                "created_at": created_at,
            },
        )

    async def get_supplier_entry(self, entry_id: UUID) -> SupplierEntryRecord | None:
        row = (
            await self._conn.execute(text(f"SELECT {_ENTRY_COLUMNS} {_ENTRY_FROM} WHERE e.id = :id"), {"id": entry_id})
        ).first()
        return None if row is None else _entry(row)

    async def supplier_entries(
        self, supplier_id: UUID, *, before_seq: int | None, limit: int
    ) -> list[SupplierEntryRecord]:
        rows = (
            await self._conn.execute(
                text(
                    f"SELECT {_ENTRY_COLUMNS} {_ENTRY_FROM} WHERE e.supplier_id = :supplier "
                    "  AND (CAST(:before AS integer) IS NULL OR e.seq < CAST(:before AS integer)) "
                    "ORDER BY e.seq DESC LIMIT :limit"
                ),
                {"supplier": supplier_id, "before": before_seq, "limit": limit},
            )
        ).all()
        return [_entry(row) for row in rows]

    async def standing_supplier_entries_of_document(self, document_id: UUID) -> list[SupplierEntryRecord]:
        rows = (
            await self._conn.execute(
                text(
                    f"SELECT {_ENTRY_COLUMNS} {_ENTRY_FROM} WHERE e.document_id = :document "
                    "  AND e.kind <> 'reversal' "
                    "  AND NOT EXISTS (SELECT 1 FROM supplier_entry r WHERE r.reverses_id = e.id) "
                    "ORDER BY e.seq DESC"
                ),
                {"document": document_id},
            )
        ).all()
        return [_entry(row) for row in rows]

    # --- the report -------------------------------------------------------------------------------

    async def stock_totals(self) -> StockTotals:
        # Through stock_level_idle: the rows of this shop with something on hand, each joined to its item
        # by primary key. The cost is added up per currency and never across.
        rows = (
            await self._conn.execute(
                text(
                    "SELECT l.cost_currency, count(*) AS items, coalesce(sum(l.cost_value), 0)::bigint AS cost, "
                    "       coalesce(sum(round(l.on_hand * i.price)), 0)::bigint AS selling "
                    "FROM stock_level l JOIN catalog_item i ON i.id = l.item_id "
                    "WHERE l.shop_id = :shop_id AND l.on_hand > 0 AND i.tracked GROUP BY l.cost_currency"
                ),
                {"shop_id": self._shop_id},
            )
        ).all()
        low = (
            await self._conn.execute(
                text(f"SELECT count(*) AS n {_ITEM_FROM} WHERE i.shop_id = :shop_id AND {_LOW}"),
                {"shop_id": self._shop_id},
            )
        ).one()
        return StockTotals(
            items=sum(int(row.items) for row in rows),
            cost={row.cost_currency: int(row.cost) for row in rows if row.cost_currency is not None},
            selling=sum(int(row.selling) for row in rows),
            selling_of_costed=sum(int(row.selling) for row in rows if row.cost_currency == "UZS"),
            low=int(low.n),
        )

    async def stock_idle(self, *, unsold_since: datetime, limit: int) -> list[StockItem]:
        rows = (
            await self._conn.execute(
                text(
                    f"SELECT {_ITEM_COLUMNS} FROM stock_level l JOIN catalog_item i ON i.id = l.item_id "
                    "WHERE l.shop_id = :shop_id AND l.on_hand > 0 AND i.tracked "
                    "  AND (l.last_sale_at IS NULL OR l.last_sale_at < :since) "
                    "ORDER BY l.last_sale_at NULLS FIRST, l.item_id LIMIT :limit"
                ),
                {"shop_id": self._shop_id, "since": unsold_since, "limit": limit},
            )
        ).all()
        return [_item(row) for row in rows]

    async def stock_sold_below_cost(self, *, since: datetime, limit: int) -> list[BelowCostSale]:
        rows = (
            await self._conn.execute(
                text(
                    "SELECT m.item_id, i.name, i.unit, m.qty, m.sale_total, m.cost_total, m.created_at "
                    "FROM stock_movement m JOIN catalog_item i ON i.id = m.item_id "
                    "WHERE m.shop_id = :shop_id AND m.kind = 'sale' AND m.currency = 'UZS' "
                    "  AND m.sale_total < m.cost_total AND m.created_at >= :since "
                    "  AND NOT EXISTS (SELECT 1 FROM stock_movement r WHERE r.reverses_id = m.id) "
                    "ORDER BY m.created_at DESC, m.id DESC LIMIT :limit"
                ),
                {"shop_id": self._shop_id, "since": since, "limit": limit},
            )
        ).all()
        return [
            BelowCostSale(
                item_id=row.item_id,
                name=row.name,
                unit=row.unit,
                qty=-row.qty,
                sale_total=int(row.sale_total),
                cost_total=int(row.cost_total),
                created_at=row.created_at,
            )
            for row in rows
        ]
