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
from typing import Any
from uuid import UUID

from qarz.application.ports import Storage
from qarz.application.xlsx import Workbook
from qarz.domain.goods import format_qty
from qarz.domain.money import Currency, parse_code, plain
from qarz.domain.promise import TASHKENT

PAGE = 100

# The words of these sheets. Uzbek and Russian; a reader of another language is shown the Uzbek ones.
_TEXTS: dict[str, dict[str, Any]] = {
    "uz": {
        "links": "Hamkorlar",
        "orders": "Hamkor buyurtmalari",
        "notes": "Yuk xatlari",
        "payments": "Hamkor to'lovlari",
        "links_header": ("Hamkor", "Telefon", "Biz", "Holati", "So'ralgan", "Tugagan", "Aloqa ID"),
        "orders_header": (
            "Hamkor", "Biz", "Buyurtma №", "Holati", "Yuborilgan", "Kerakli sana", "Izoh", "Valyuta", "Jami",
            "Qator", "Tovar", "Birlik", "So'ralgan", "Qabul qilingan", "Narx", "Sabab", "Buyurtma ID",
        ),
        "notes_header": (
            "Hamkor", "Biz", "Yuk xati №", "Buyurtma №", "Holati", "Berilgan", "Valyuta", "Jami", "To'langan",
            "Qator", "Tovar", "Birlik", "Miqdor", "Narx", "Summa", "Rad etish sababi", "Tuzatish sababi", "Yuk xati ID",
        ),
        "payments_header": (
            "Hamkor", "Biz", "Kim yozgan", "Holati", "Yozilgan", "Valyuta", "Summa", "Izoh", "Rad etish sababi",
            "Daftarimizda", "To'lov ID",
        ),
        "buyer": "Xaridor",
        "supplier": "Ta'minotchi",
        "own": "Biz",
        "partner": "Hamkor",
        "removed": "(o'chirilgan)",
        "yes": "Ha",
        "no": "Yo'q",
    },
    "ru": {
        "links": "Партнёры",
        "orders": "Заказы партнёров",
        "notes": "Накладные",
        "payments": "Оплаты партнёров",
        "links_header": ("Партнёр", "Телефон", "Мы", "Состояние", "Запрошено", "Завершено", "ID связи"),
        "orders_header": (
            "Партнёр", "Мы", "Заказ №", "Состояние", "Отправлен", "Нужная дата", "Заметка", "Валюта", "Итого",
            "Строка", "Товар", "Единица", "Запрошено", "Принято", "Цена", "Причина", "ID заказа",
        ),
        "notes_header": (
            "Партнёр", "Мы", "Накладная №", "Заказ №", "Состояние", "Оформлена", "Валюта", "Итого", "Оплачено",
            "Строка", "Товар", "Единица", "Количество", "Цена", "Сумма", "Причина отклонения",
            "Причина исправления", "ID накладной",
        ),
        "payments_header": (
            "Партнёр", "Мы", "Кто записал", "Состояние", "Записано", "Валюта", "Сумма", "Заметка",
            "Причина отклонения", "В нашем учёте", "ID оплаты",
        ),
        "buyer": "Покупатель",
        "supplier": "Поставщик",
        "own": "Мы",
        "partner": "Партнёр",
        "removed": "(удалён)",
        "yes": "Да",
        "no": "Нет",
    },
}  # fmt: skip


def _local(at: datetime | None) -> str | None:
    return None if at is None else at.astimezone(TASHKENT).strftime("%Y-%m-%d %H:%M")


def _amount(currency: str | None, amount: int | None) -> int | Decimal | None:
    """An amount as a cell: whole so'm as the number it is, cents as dollars with two decimals."""
    code = parse_code(currency)
    if amount is None:
        return None
    return amount if code is None or code is Currency.UZS else Decimal(plain(code, amount))


async def write_network(book: Workbook, storage: Storage, shop_id: UUID, lang: str) -> None:
    words = _TEXTS.get(lang, _TEXTS["uz"])
    async with storage.tenant(shop_id) as session:
        links = await session.network_links()
    if not links:
        return

    def partner(name: str | None) -> str:
        return name or str(words["removed"])

    sheet = book.sheet(words["links"], words["links_header"], (28, 16, 14, 12, 18, 18, 38))
    for link in links:
        sheet.append(
            (
                partner(link.peer_name),
                link.peer_phone,
                words[link.role],
                link.state,
                _local(link.requested_at),
                _local(link.ended_at),
                str(link.link_id),
            )
        )

    sheet = book.sheet(
        words["orders"], words["orders_header"], (24, 12, 10, 12, 18, 12, 28, 8, 14, 6, 28, 8, 10, 10, 12, 28, 38)
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
                        words[order.role],
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
        words["notes"], words["notes_header"], (24, 12, 10, 10, 12, 18, 8, 14, 14, 6, 28, 8, 10, 12, 14, 28, 28, 38)
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
                        words[note.role],
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

    sheet = book.sheet(words["payments"], words["payments_header"], (24, 12, 12, 12, 18, 8, 14, 28, 28, 12, 38))
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
                    words[payment.role],
                    words["own" if payment.recorded_by_own else "partner"],
                    payment.status,
                    _local(payment.recorded_at),
                    payment.currency,
                    _amount(payment.currency, payment.amount),
                    payment.note,
                    payment.decline_reason,
                    words["yes" if payment.own_entry_stands else "no"],
                    str(payment.payment_id),
                )
            )
