"""What the stock, the documents and the suppliers ask of storage (module I of the expansion).

`qarz.application.ports.TenantSession` includes `StockSession`, so every tenant transaction can do all
of this; it is kept in a file of its own because it is one self-contained area.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from qarz.domain.stock import Level


@dataclass(frozen=True)
class StockItem:
    """A catalogue item with what the stock knows about it."""

    item_id: UUID
    name: str
    name_norm: str
    unit: str
    price: int
    status: str
    learned: bool
    merged_into: UUID | None
    tracked: bool
    low_stock: Decimal | None
    level: Level
    last_seq: int
    last_sale_at: datetime | None


@dataclass(frozen=True)
class MovementRecord:
    movement_id: UUID
    item_id: UUID
    item_seq: int
    kind: str
    qty: Decimal
    unit_cost: int | None
    cost_total: int | None
    sale_total: int | None
    value_delta: int
    currency: str | None
    on_hand_after: Decimal
    value_after: int
    reason: str | None
    document_id: UUID | None
    line_no: int | None
    ledger_entry_id: UUID | None
    reverses_id: UUID | None
    author_id: UUID
    created_at: datetime
    is_reversed: bool = False
    # Read with the movement where a list shows it.
    document_kind: str | None = None
    document_number: int | None = None


@dataclass(frozen=True)
class NewMovement:
    movement_id: UUID
    item_id: UUID
    item_seq: int
    kind: str
    qty: Decimal
    value_delta: int
    on_hand_after: Decimal
    value_after: int
    currency: str | None
    author_id: UUID
    created_at: datetime
    unit_cost: int | None = None
    cost_total: int | None = None
    sale_total: int | None = None
    reason: str | None = None
    document_id: UUID | None = None
    line_no: int | None = None
    ledger_entry_id: UUID | None = None
    reverses_id: UUID | None = None


@dataclass(frozen=True)
class DocumentRecord:
    document_id: UUID
    kind: str
    number: int
    status: str
    doc_date: date
    supplier_id: UUID | None
    customer_id: UUID | None
    currency: str
    total: int
    paid: int
    reason: str | None
    note: str | None
    draft: dict[str, Any] | None
    ledger_entry_id: UUID | None
    created_by: UUID
    created_at: datetime
    posted_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    supplier_name: str | None = None
    customer_name: str | None = None
    # A cash sale: how the buyer paid. None on every other kind.
    method: str | None = None


@dataclass(frozen=True)
class DocumentLine:
    line_no: int
    item_id: UUID
    qty: Decimal
    unit_cost: int | None
    line_total: int | None
    expected: Decimal | None = None


@dataclass(frozen=True)
class SupplierRecord:
    supplier_id: UUID
    name: str
    name_norm: str
    phone: str | None
    note: str | None
    status: str
    created_at: datetime


@dataclass(frozen=True)
class SupplierEntryRecord:
    entry_id: UUID
    supplier_id: UUID
    seq: int
    kind: str
    amount: int
    currency: str
    note: str | None
    reverses_id: UUID | None
    document_id: UUID | None
    author_id: UUID
    created_at: datetime
    is_reversed: bool = False
    in_cash_book: bool = False
    document_kind: str | None = None
    document_number: int | None = None


@dataclass(frozen=True)
class StockTotals:
    """The whole stock on hand. One figure per currency: nothing here is a sum of two."""

    items: int  # counted items with something on hand
    cost: dict[str, int]  # value at cost, by the currency the cost is kept in
    selling: int  # value at the selling price, in so'm
    # Of the items whose cost is kept in so'm: their selling value and their cost, so the margin is of one currency.
    selling_of_costed: int
    low: int  # counted items at or below their threshold


@dataclass(frozen=True)
class BelowCostSale:
    item_id: UUID
    name: str
    unit: str
    qty: Decimal
    sale_total: int
    cost_total: int
    created_at: datetime


@dataclass(frozen=True)
class ExportMovement:
    movement: MovementRecord
    item_name: str
    unit: str


@dataclass(frozen=True)
class ExportSupplierEntry:
    entry: SupplierEntryRecord
    supplier_name: str
    reversed_kind: str | None  # of a reversal: the kind of the entry it reverses


@dataclass(frozen=True)
class SaleCost:
    """What the goods of one line of a cash sale cost, as its movement keeps it."""

    line_no: int
    cost_total: int | None
    currency: str | None


@dataclass(frozen=True)
class SaleTotals:
    """The cash sales that stand in a stretch of time: how many, and their money by how it was paid."""

    count: int
    total: int
    by_method: dict[str, int]


@dataclass(frozen=True)
class SoldItem:
    """What was sold of one counted item since a moment, credit and cash sales together."""

    item_id: UUID
    name: str
    unit: str
    qty: Decimal
    revenue: int
    # Of the sales whose cost is known in so'm: what they brought and what they cost. The margin is of
    # these alone, so it is never a difference of two currencies.
    costed_revenue: int
    cost: int
    cash_qty: Decimal  # of it, sold for cash without a customer
    cash_revenue: int


@dataclass(frozen=True)
class ExportSaleLine:
    document: DocumentRecord
    line: DocumentLine
    item_name: str
    unit: str
    cost_total: int | None
    cost_currency: str | None


class StockSession(Protocol):
    # --- items ------------------------------------------------------------------------------------

    async def stock_item(self, item_id: UUID, *, for_update: bool) -> StockItem | None:
        """The item with its level. `for_update` locks the item: every movement of it takes that lock."""
        ...

    async def stock_items(
        self, *, name_part: str | None, only: str, after: tuple[str, UUID] | None, limit: int
    ) -> list[StockItem]:
        """A page of shown items by name: `all`, the `tracked` ones, or those running `low`."""
        ...

    async def stock_items_by_ids(self, item_ids: list[UUID]) -> dict[UUID, StockItem]: ...

    async def set_item_stock(self, item_id: UUID, *, tracked: bool, low_stock: Decimal | None, unit: str) -> None: ...

    async def barcodes_of(self, item_ids: list[UUID]) -> dict[UUID, list[str]]: ...

    async def replace_barcodes(self, item_id: UUID, codes: list[str]) -> str | None:
        """Make these the item's barcodes. Returns a code another item of the shop already has, with
        nothing changed, or None when done."""
        ...

    async def item_by_barcode(self, code: str) -> UUID | None: ...

    async def stock_refuse_negative(self) -> bool: ...

    async def set_stock_refuse_negative(self, refuse: bool) -> None: ...

    # --- movements --------------------------------------------------------------------------------

    async def add_movement(self, movement: NewMovement) -> None: ...

    async def movements_of_item(self, item_id: UUID, *, before_seq: int | None, limit: int) -> list[MovementRecord]: ...

    async def standing_movements_of_entry(self, entry_id: UUID) -> list[MovementRecord]:
        """The movements a ledger entry caused that are not reversed yet, newest first."""
        ...

    async def standing_movements_of_document(self, document_id: UUID) -> list[MovementRecord]: ...

    # --- documents --------------------------------------------------------------------------------

    async def next_document_number(self, kind: str) -> int:
        """The next number of this kind in the shop. Holds a lock until the transaction ends."""
        ...

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
        origin_ref: UUID | None = None,
        method: str | None = None,
    ) -> None:
        """`origin_ref` is the delivery note of another shop that a receipt answers (module J): written
        with the document and never changed. `method` is how a cash sale was paid; only a sale has one."""
        ...

    async def get_document(self, document_id: UUID, *, for_update: bool) -> DocumentRecord | None: ...

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
    ) -> None: ...

    async def add_document_lines(self, document_id: UUID, lines: list[DocumentLine]) -> None: ...

    async def mark_document_posted(
        self,
        document_id: UUID,
        *,
        posted_by: UUID,
        posted_at: datetime,
        ledger_entry_id: UUID | None,
    ) -> None: ...

    async def mark_document_cancelled(
        self, document_id: UUID, *, cancelled_by: UUID, cancelled_at: datetime, reason: str
    ) -> None: ...

    async def document_lines(self, document_id: UUID) -> list[DocumentLine]: ...

    async def list_documents(
        self,
        *,
        kind: str | None,
        status: str | None,
        supplier_id: UUID | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[DocumentRecord]: ...

    # --- cash sales (a document of the kind `sale`) --------------------------------------------------

    async def list_sales(
        self,
        *,
        since: datetime,
        until: datetime,
        item_id: UUID | None,
        seller_id: UUID | None,
        status: str | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[DocumentRecord]:
        """A page of the cash sales made in `[since, until)`, newest first, cancelled ones included."""
        ...

    async def sale_totals(
        self, *, since: datetime, until: datetime, item_id: UUID | None, seller_id: UUID | None
    ) -> SaleTotals:
        """The sales of that stretch that stand (posted, not cancelled), under the same narrowing."""
        ...

    async def sale_costs(self, document_id: UUID) -> dict[int, SaleCost]:
        """By line: what the goods of a sale cost. A line of an item that is not counted has none."""
        ...

    async def add_sale_cash_entry(
        self,
        *,
        entry_id: UUID,
        method: str,
        amount: int,
        category_id: UUID,
        note: str | None,
        day: date,
        author_id: UUID,
        stock_document_id: UUID,
        now: datetime,
    ) -> None:
        """Write the income of the cash book that a cash sale is, in so'm."""
        ...

    async def stock_sold(self, *, since: datetime, limit: int) -> list[SoldItem]:
        """What sold since then, per counted item, the most money first. Cancelled sales are left out."""
        ...

    async def export_sale_lines(
        self, *, until: datetime, after: tuple[datetime, UUID, int] | None, limit: int
    ) -> list[ExportSaleLine]:
        """A page of the lines of the shop's cash sales up to a moment, oldest first."""
        ...

    # --- suppliers --------------------------------------------------------------------------------

    async def insert_supplier(
        self, *, supplier_id: UUID, name: str, name_norm: str, phone: str | None, note: str | None
    ) -> SupplierRecord | None:
        """None when another supplier of the shop has the same name after normalization."""
        ...

    async def get_supplier(self, supplier_id: UUID, *, for_update: bool) -> SupplierRecord | None: ...

    async def update_supplier(
        self, supplier_id: UUID, *, name: str, name_norm: str, phone: str | None, note: str | None
    ) -> SupplierRecord | None: ...

    async def set_supplier_status(self, supplier_id: UUID, status: str) -> SupplierRecord: ...

    async def search_suppliers(
        self, *, name_part: str | None, status: str, after: tuple[str, UUID] | None, limit: int
    ) -> list[SupplierRecord]: ...

    async def supplier_balances(self, supplier_ids: list[UUID]) -> dict[UUID, dict[str, int]]:
        """What the shop owes each of them, per currency; a currency with nothing is left out."""
        ...

    async def supplier_totals(self) -> dict[str, int]:
        """What the shop owes all its suppliers together, per currency: debts only, advances aside."""
        ...

    async def last_supplier_seq(self, supplier_id: UUID) -> int: ...

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
        author_id: UUID,
        created_at: datetime,
    ) -> None: ...

    async def get_supplier_entry(self, entry_id: UUID) -> SupplierEntryRecord | None: ...

    async def supplier_entries(
        self, supplier_id: UUID, *, before_seq: int | None, limit: int
    ) -> list[SupplierEntryRecord]: ...

    async def standing_supplier_entries_of_document(self, document_id: UUID) -> list[SupplierEntryRecord]: ...

    # --- the cash book (module H) ------------------------------------------------------------------

    async def add_stock_cash_entry(
        self,
        *,
        entry_id: UUID,
        method: str,
        currency: str,
        amount: int,
        category_id: UUID,
        note: str | None,
        day: date,
        author_id: UUID,
        supplier_entry_id: UUID | None,
        stock_document_id: UUID | None,
        now: datetime,
    ) -> None:
        """Write the expense of the cash book that a supplier's payment, or a document's money, is."""
        ...

    async def cancel_stock_cash_entries(
        self, *, supplier_entry_id: UUID | None, stock_document_id: UUID | None, by: UUID, reason: str, now: datetime
    ) -> int:
        """Cancel the standing cash entry written for a payment or a document. Returns how many."""
        ...

    # --- the owner's export -----------------------------------------------------------------------

    async def stock_recorded(self) -> bool:
        """Whether the shop has anything of the stock or the suppliers to export."""
        ...

    # --- turning the shop's dollars off (qarz.application.shops.set_dollars_in) ---------------------

    async def supplier_dollars_open(self) -> bool:
        """Whether the shop's account with any supplier is other than zero in dollars: what the shop
        owes, or what it paid ahead."""
        ...

    async def stock_dollars_on_hand(self) -> bool:
        """Whether anything is on hand whose cost is kept in dollars."""
        ...

    async def export_movements(
        self, *, until: datetime, after: tuple[datetime, UUID, int] | None, limit: int
    ) -> list[ExportMovement]:
        """A page of the shop's movements up to a moment, oldest first; those of one moment in the order
        they were written for their item. `after` is the moment, the item and the number of the last row."""
        ...

    async def export_supplier_entries(
        self, *, until: datetime, after: tuple[datetime, UUID, int] | None, limit: int
    ) -> list[ExportSupplierEntry]: ...

    # --- the report -------------------------------------------------------------------------------

    async def stock_totals(self) -> StockTotals: ...

    async def stock_idle(self, *, unsold_since: datetime, limit: int) -> list[StockItem]:
        """Items on hand that were not sold since then (or never), longest unsold first."""
        ...

    async def stock_sold_below_cost(self, *, since: datetime, limit: int) -> list[BelowCostSale]: ...
