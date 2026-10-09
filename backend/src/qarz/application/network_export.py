"""The network between shops in the owner's export of the shop's own data (REQ-028; module J).

Four more sheets after the stock's: the shop's links, its orders, its delivery notes and its payments
with partners, each as the shop's OWN side holds it. They are written only for a shop that has a link,
so a shop that never used the network gets exactly the workbook it always got. An export is the owner's
copy of what is recorded, so nothing here depends on the platform switch.

What is exported is what the shop may read in the application and nothing more: a partner is a name and
a contact phone; nothing of the partner's own books, staff or catalogue is here, because none of it is
in this shop's rows.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from qarz.application.export_texts import header, word
from qarz.application.ports import Storage
from qarz.application.xlsx import Workbook
from qarz.domain.goods import format_qty
from qarz.domain.money import Currency, parse_code, plain
from qarz.domain.promise import TASHKENT

PAGE = 100


def _local(at: datetime | None) -> str | None:
    return None if at is None else at.astimezone(TASHKENT).strftime("%Y-%m-%d %H:%M")


def _amount(currency: str | None, amount: int | None) -> int | Decimal | None:
    """An amount as a cell: whole so'm as the number it is, cents as dollars with two decimals."""
    code = parse_code(currency)
    if amount is None:
        return None
    return amount if code is None or code is Currency.UZS else Decimal(plain(code, amount))


async def write_network(book: Workbook, storage: Storage, shop_id: UUID, lang: str) -> None:
    # The words of these sheets are the export's own catalog (`export_texts`), in the shop's language.
    role = {"buyer": word(lang, "net_buyer"), "supplier": word(lang, "net_supplier")}
    async with storage.tenant(shop_id) as session:
        links = await session.network_links()
    if not links:
        return

    def partner(name: str | None) -> str:
        return name or word(lang, "net_removed")

    sheet = book.sheet(word(lang, "sheet_net_links"), header(lang, "net_links"), (28, 16, 14, 12, 18, 18, 38))
    for link in links:
        sheet.append(
            (
                partner(link.peer_name),
                link.peer_phone,
                role[link.role],
                link.state,
                _local(link.requested_at),
                _local(link.ended_at),
                str(link.link_id),
            )
        )

    sheet = book.sheet(
        word(lang, "sheet_net_orders"),
        header(lang, "net_orders"),
        (24, 12, 10, 12, 18, 12, 28, 8, 14, 6, 28, 8, 10, 10, 12, 28, 38),
    )
    before: tuple[datetime, UUID] | None = None
    while True:
        async with storage.tenant(shop_id) as session:
            orders = await session.network_orders(role=None, status=None, link_id=None, before=before, limit=PAGE)
            lines = {order.order_id: await session.network_order_lines(order.order_id) for order in orders}
        if not orders:
            break
        before = (orders[-1].sent_at, orders[-1].order_id)
        for order in orders:
            for line in lines[order.order_id]:
                sheet.append(
                    (
                        partner(order.peer_name),
                        role[order.role],
                        order.number,
                        order.status,
                        _local(order.sent_at),
                        None if order.wanted_date is None else order.wanted_date.isoformat(),
                        order.note,
                        order.currency,
                        _amount(order.currency, order.total),
                        line.line_no,
                        line.name,
                        line.unit,
                        Decimal(format_qty(line.qty)),
                        None if line.accepted_qty is None else Decimal(format_qty(line.accepted_qty)),
                        _amount(order.currency, line.unit_price),
                        order.closed_reason,
                        str(order.order_id),
                    )
                )

    sheet = book.sheet(
        word(lang, "sheet_net_notes"),
        header(lang, "net_notes"),
        (24, 12, 10, 10, 12, 18, 8, 14, 14, 6, 28, 8, 10, 12, 14, 28, 28, 38),
    )
    before = None
    while True:
        async with storage.tenant(shop_id) as session:
            notes = await session.network_notes(role=None, status=None, link_id=None, before=before, limit=PAGE)
            note_lines = {note.note_id: await session.network_note_lines(note.note_id) for note in notes}
        if not notes:
            break
        before = (notes[-1].issued_at, notes[-1].note_id)
        for note in notes:
            for row in note_lines[note.note_id]:
                sheet.append(
                    (
                        partner(note.peer_name),
                        role[note.role],
                        note.number,
                        note.order_number,
                        note.status,
                        _local(note.issued_at),
                        note.currency,
                        _amount(note.currency, note.total),
                        _amount(note.currency, note.paid),
                        row.line_no,
                        row.name,
                        row.unit,
                        Decimal(format_qty(row.qty)),
                        _amount(note.currency, row.unit_price),
                        _amount(note.currency, row.line_total),
                        note.reject_reason,
                        note.supersede_reason,
                        str(note.note_id),
                    )
                )

    sheet = book.sheet(
        word(lang, "sheet_net_payments"), header(lang, "net_payments"), (24, 12, 12, 12, 18, 8, 14, 28, 28, 12, 38)
    )
    before = None
    while True:
        async with storage.tenant(shop_id) as session:
            payments = await session.network_payments(status=None, link_id=None, before=before, limit=PAGE)
        if not payments:
            break
        before = (payments[-1].recorded_at, payments[-1].payment_id)
        for payment in payments:
            sheet.append(
                (
                    partner(payment.peer_name),
                    role[payment.role],
                    word(lang, "net_own" if payment.recorded_by_own else "net_partner"),
                    payment.status,
                    _local(payment.recorded_at),
                    payment.currency,
                    _amount(payment.currency, payment.amount),
                    payment.note,
                    payment.decline_reason,
                    word(lang, "yes" if payment.own_entry_stands else "no"),
                    str(payment.payment_id),
                )
            )
