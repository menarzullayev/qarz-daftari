"""The stock and the suppliers in the owner's export of the shop's own data (REQ-028; module I).

Five more sheets after the ones every workbook has: what is on hand, every movement, the documents, the
suppliers with what they are owed, and their accounts. They are written only for a shop that has any of
it, so a shop that never used the stock gets exactly the workbook it always got. A sixth, the sales for
cash a line at a time, is written for a shop that made one. An export is the
owner's copy of everything recorded, so nothing here depends on the platform switch, and the cost
figures are in it: only managers and owners may ask for an export.

Amounts of the two currencies are in cells of their own, each with its currency beside it; no cell is a
sum of so'm and dollars.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from qarz.application.export_texts import header, word
from qarz.application.ports import Storage
from qarz.application.xlsx import Workbook
from qarz.domain import stock, suppliers
from qarz.domain.money import Currency, parse_code, plain
from qarz.domain.promise import TASHKENT

# Rows read by one statement: found by key, so each costs what the page holds.
PAGE = 1000


def _local(at: datetime) -> str:
    return at.astimezone(TASHKENT).strftime("%Y-%m-%d %H:%M")


def _amount(currency: str | None, amount: int | None) -> int | Decimal | None:
    """An amount as a cell: whole so'm as the number it is, cents as dollars with two decimals."""
    code = parse_code(currency)
    if amount is None:
        return None
    return amount if code is None or code is Currency.UZS else Decimal(plain(code, amount))


def _document(lang: str, kind: str | None, number: int | None) -> str | None:
    return None if kind is None else f"{word(lang, f'document_{kind}', kind)} № {number}"


async def write_stock(book: Workbook, storage: Storage, shop_id: UUID, lang: str, until: datetime) -> None:
    async with storage.tenant(shop_id) as session:
        if not await session.stock_recorded():
            return
    yes, no = word(lang, "yes"), word(lang, "no")

    on_hand = book.sheet(word(lang, "sheet_stock"), header(lang, "stock"), (28, 9, 12, 12, 14, 10, 14, 18, 30, 38))
    after_item: tuple[str, UUID] | None = None
    while True:
        async with storage.tenant(shop_id) as session:
            items = await session.stock_items(name_part=None, only="tracked", after=after_item, limit=PAGE)
            codes = await session.barcodes_of([item.item_id for item in items])
        if not items:
            break
        after_item = (items[-1].name_norm, items[-1].item_id)
        for item in items:
            average = item.level.average
            on_hand.append(
                (
                    item.name,
                    item.unit,
                    item.price,
                    item.level.on_hand,
                    item.low_stock,
                    item.level.currency,
                    None if average is None else _amount(item.level.currency, stock.money(average)),
                    _amount(item.level.currency, item.level.value),
                    ", ".join(codes.get(item.item_id, [])) or None,
                    str(item.item_id),
                )
            )

    moves = book.sheet(
        word(lang, "sheet_stock_movements"),
        header(lang, "stock_movements"),
        (17, 28, 22, 10, 9, 14, 14, 9, 14, 18, 22, 10, 38, 38),
    )
    after: tuple[datetime, UUID, int] | None = None
    while True:
        async with storage.tenant(shop_id) as session:
            page = await session.export_movements(until=until, after=after, limit=PAGE)
        if not page:
            break
        last = page[-1].movement
        after = (last.created_at, last.item_id, last.item_seq)
        for row in page:
            movement = row.movement
            moves.append(
                (
                    _local(movement.created_at),
                    row.item_name,
                    word(lang, f"movement_{movement.kind}", movement.kind),
                    movement.qty,
                    row.unit,
                    movement.on_hand_after,
                    _amount(movement.currency, movement.cost_total),
                    movement.currency if movement.cost_total is not None else None,
                    movement.sale_total,
                    None if movement.reason is None else word(lang, f"reason_{movement.reason}", movement.reason),
                    _document(lang, movement.document_kind, movement.document_number),
                    yes if movement.is_reversed else no,
                    str(movement.movement_id),
                    str(movement.item_id),
                )
            )

    papers = book.sheet(
        word(lang, "sheet_stock_documents"),
        header(lang, "stock_documents"),
        (8, 24, 14, 12, 28, 28, 9, 14, 14, 18, 30, 30, 38),
    )
    before: tuple[datetime, UUID] | None = None
    while True:
        async with storage.tenant(shop_id) as session:
            documents = await session.list_documents(
                kind=None, status=None, supplier_id=None, before=before, limit=PAGE
            )
        if not documents:
            break
        before = (documents[-1].created_at, documents[-1].document_id)
        for document in documents:
            if document.created_at > until:
                continue
            priced = document.kind in stock.PRICED_KINDS
            papers.append(
                (
                    document.number,
                    word(lang, f"document_{document.kind}", document.kind),
                    word(lang, f"document_{document.status}", document.status),
                    document.doc_date.isoformat(),
                    document.supplier_name,
                    document.customer_name,
                    document.currency if priced else None,
                    _amount(document.currency, document.total) if priced else None,
                    _amount(document.currency, document.paid) if priced else None,
                    None if document.reason is None else word(lang, f"reason_{document.reason}", document.reason),
                    document.note,
                    document.cancel_reason,
                    str(document.document_id),
                )
            )

    await _write_cash_sales(book, storage, shop_id, lang, until)

    people = book.sheet(word(lang, "sheet_suppliers"), header(lang, "suppliers"), (28, 16, 30, 12, 16, 14, 38))
    for status in (suppliers.ACTIVE, suppliers.ARCHIVED):
        after_name: tuple[str, UUID] | None = None
        while True:
            async with storage.tenant(shop_id) as session:
                rows = await session.search_suppliers(name_part=None, status=status, after=after_name, limit=PAGE)
                owed = await session.supplier_balances([row.supplier_id for row in rows])
            if not rows:
                break
            after_name = (rows[-1].name_norm, rows[-1].supplier_id)
            for supplier in rows:
                theirs = owed.get(supplier.supplier_id, {})
                people.append(
                    (
                        supplier.name,
                        supplier.phone,
                        supplier.note,
                        word(lang, f"status_{supplier.status}", supplier.status),
                        theirs.get("UZS", 0),
                        _amount("USD", theirs.get("USD", 0)),
                        str(supplier.supplier_id),
                    )
                )

    account = book.sheet(
        word(lang, "sheet_supplier_entries"),
        header(lang, "supplier_entries"),
        (17, 28, 18, 14, 9, 14, 30, 10, 22, 38, 38),
    )
    after = None
    while True:
        async with storage.tenant(shop_id) as session:
            entries = await session.export_supplier_entries(until=until, after=after, limit=PAGE)
        if not entries:
            break
        after = (entries[-1].entry.created_at, entries[-1].entry.supplier_id, entries[-1].entry.seq)
        for line in entries:
            entry = line.entry
            if entry.kind == suppliers.REVERSAL:
                effect = -suppliers.effect(line.reversed_kind or suppliers.PAYMENT, entry.amount)
            else:
                effect = suppliers.effect(entry.kind, entry.amount)
            account.append(
                (
                    _local(entry.created_at),
                    line.supplier_name,
                    word(lang, f"supplier_{entry.kind}", entry.kind),
                    _amount(entry.currency, entry.amount),
                    entry.currency,
                    _amount(entry.currency, effect),
                    entry.note,
                    yes if entry.is_reversed else no,
                    _document(lang, entry.document_kind, entry.document_number),
                    str(entry.entry_id),
                    str(entry.supplier_id),
                )
            )


async def _write_cash_sales(book: Workbook, storage: Storage, shop_id: UUID, lang: str, until: datetime) -> None:
    """The sales for cash, a row for each line sold, cancelled sales included and marked. The sheet is
    written only for a shop that made such a sale, so every other workbook is the one it was."""
    sheet = None
    after: tuple[datetime, UUID, int] | None = None
    while True:
        async with storage.tenant(shop_id) as session:
            rows = await session.export_sale_lines(until=until, after=after, limit=PAGE)
        if not rows:
            break
        if sheet is None:
            sheet = book.sheet(
                word(lang, "sheet_cash_sales"),
                header(lang, "cash_sales"),
                (8, 17, 14, 14, 28, 9, 12, 14, 14, 14, 10, 14, 30, 30, 38),
            )
        after = (rows[-1].document.created_at, rows[-1].document.document_id, rows[-1].line.line_no)
        for row in rows:
            sale, line = row.document, row.line
            sheet.append(
                (
                    sale.number,
                    _local(sale.created_at),
                    word(lang, f"document_{sale.status}", sale.status),
                    None if sale.method is None else word(lang, f"cash_{sale.method}", sale.method),
                    row.item_name,
                    row.unit,
                    line.qty,
                    line.unit_cost,
                    line.line_total,
                    _amount(row.cost_currency, row.cost_total),
                    row.cost_currency if row.cost_total is not None else None,
                    sale.total,
                    sale.note,
                    sale.cancel_reason,
                    str(sale.document_id),
                )
            )
