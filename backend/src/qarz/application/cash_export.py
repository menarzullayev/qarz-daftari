"""One period of the cash book as a workbook (expansion module H).

Two sheets: what the period came to (the balances by method and what stands by category, each currency
by itself), and every entry dated in it, cancelled ones included and marked with their reason. It is the
period report of the cash screen and the pages of its day book, as a file.

Who may: whoever may read the book (`cash.view`). The workbook holds nothing that member does not
already read on the screen, so it asks for no permission of its own: whose payment an entry of the ledger
is, is written only for a reader who holds `ledger.view`, as there. The export of the whole shop
(`qarz.application.exports`) stays the managers' and owners' `reports.export`.

Written at once, not as a job of the worker. The worker's jobs (`export_job`) are exports of a whole
shop and have nowhere to keep which period was asked for; a period is bounded instead: at most
`MAX_PERIOD_DAYS` days and `MAX_EXPORT_ENTRIES` entries, read a page at a time by key. The workbook is
kept in the file store like any export and handed over the same way, through a link that works for five
minutes; the file itself is deleted an hour later by the hourly cleanup.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from qarz.application import currencies, idempotency
from qarz.application.authorization import may
from qarz.application.cash_book import period_of
from qarz.application.cash_ports import CashEntryRecord
from qarz.application.customers import require_viewable
from qarz.application.errors import NotFound, ValidationFailed
from qarz.application.export_texts import header, word
from qarz.application.files import CheckedFile, FileService
from qarz.application.operations import operation
from qarz.application.ports import Storage
from qarz.application.shops import require_member
from qarz.application.xlsx import MIME, Workbook
from qarz.domain import cash, permissions
from qarz.domain.access import Capability
from qarz.domain.cash import Direction, Line, Method, Sum
from qarz.domain.money import Currency, parse_code, plain
from qarz.domain.promise import TASHKENT, tashkent_date

EXPORT_CASH = operation("cash.export", Capability.MANAGE)

FILE_PURPOSE = "export"
FILE_LINK_PATH = "/files"
# How long the workbook stays in the file store. Its link works for five minutes; the rest is slack for
# a download that has begun.
KEPT_FOR = timedelta(hours=1)
# Rows read by one statement: found by key, so each costs what the page holds.
PAGE = 1000
# What the period's last day is refused with when the period holds more entries than one workbook takes.
TOO_MANY_ENTRIES = "TOO_MANY_ENTRIES"


def _local(at: datetime) -> str:
    return at.astimezone(TASHKENT).strftime("%Y-%m-%d %H:%M")


def _amount(currency: Currency, amount: int) -> int | Decimal:
    """An amount as a cell: whole so'm as the number it is, cents as dollars with two decimals."""
    return amount if currency is Currency.UZS else Decimal(plain(currency, amount))


def _source(record: CashEntryRecord) -> str:
    if record.ledger_entry_id is not None:
        return "ledger"
    if record.supplier_entry_id is not None or record.stock_document_id is not None:
        return "stock"
    return "manual"


class CashExportService:
    def __init__(self, storage: Storage, files: FileService, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._files = files
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def export(
        self, user_id: UUID, shop_id: UUID, *, raw_first: str | None, raw_last: str | None, request_key: str | None
    ) -> dict[str, Any]:
        """Write the period's workbook and answer with a link to it, valid five minutes."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, EXPORT_CASH)
            key = idempotency.validate_key(request_key)
            today = self._today()
            # As the book itself is read: in a suspended shop only by the owner (BR-30).
            await require_viewable(session, actor, today)
            first, last = period_of(raw_first, raw_last, today)
            before = last + timedelta(days=1)
            if await session.count_cash_entries(first=first, before=before) > cash.MAX_EXPORT_ENTRIES:
                raise ValidationFailed({"to": TOO_MANY_ENTRIES})
            settings = await session.shop_settings()
            if settings is None:
                raise NotFound()
            opening = await session.cash_sums(first=None, before=first)
            shown = await currencies.shop_currencies(session)
            names = may(actor, permissions.LEDGER_VIEW)
        until = self._now()
        book = Workbook()
        try:
            entries = await self._write(
                book,
                shop_id,
                lang=settings.lang,
                shop_name=settings.name,
                first=first,
                last=last,
                until=until,
                opening=opening,
                shown=shown,
                names=names,
            )
            # Packing compresses everything written so far; done off the event loop so that other
            # requests keep being answered meanwhile.
            content = await asyncio.to_thread(book.finish)
        finally:
            book.close()
        # Stored before the writing transaction opens, and removed again unless that transaction records it.
        staged = await self._files.stage(CheckedFile(MIME, content))
        recorded = False

        async def apply() -> dict[str, Any]:
            nonlocal recorded
            now = self._now()
            file_id = await self._files.record_in(
                session, staged, purpose=FILE_PURPOSE, now=now, delete_after=now + KEPT_FOR
            )
            token, expires_at = self._files.link(shop_id, await self._files.record_of(session, file_id), now)
            await session.record_activity(
                membership_id=actor.membership_id,
                action="cash.exported",
                subject_type="shop",
                subject_id=shop_id,
                detail={"from": first.isoformat(), "to": last.isoformat(), "entries": entries},
            )
            recorded = True
            return {
                "from": first.isoformat(),
                "to": last.isoformat(),
                "entries": entries,
                "url": f"{FILE_LINK_PATH}/{token}",
                "expires_at": expires_at.isoformat(),
            }

        try:
            async with self._storage.tenant(shop_id) as session:
                body = await idempotency.run_once(
                    session,
                    key=key,
                    operation=EXPORT_CASH.name,
                    user_id=user_id,
                    request={"from": first.isoformat(), "to": last.isoformat()},
                    action=apply,
                )
        except BaseException:
            await self._files.discard(staged)
            raise
        if not recorded:
            # A repeat of a request already carried out: the first workbook is the one that was kept.
            await self._files.discard(staged)
        return body

    async def _write(
        self,
        book: Workbook,
        shop_id: UUID,
        *,
        lang: str,
        shop_name: str,
        first: date,
        last: date,
        until: datetime,
        opening: list[Sum],
        shown: tuple[Currency, ...],
        names: bool,
    ) -> int:
        """Fill the workbook. Returns the number of entries written, cancelled ones included.

        The summary is added up from the very rows the second sheet lists, so the two agree whatever is
        written or cancelled meanwhile. No cell is a sum of so'm and dollars.
        """
        summary = book.sheet(word(lang, "sheet_summary"), widths=(34, 22, 22, 18, 18, 22, 12))
        titles = header(lang, "cash_period")
        widths: tuple[int, ...] = (12, 17, 10, 14, 9, 14, 26, 30, 18, 10, 30, 17, 38, 38)
        if names:
            # Whose payment an entry of the ledger is: a column only a reader of the customers has.
            titles = (*titles[:8], word(lang, "cash_customer"), *titles[8:])
            widths = (*widths[:8], 28, *widths[8:])
        entries = book.sheet(word(lang, "sheet_cash"), titles, widths)
        yes, no = word(lang, "yes"), word(lang, "no")

        during: dict[tuple[Method, Currency, Direction], list[int]] = {}
        # (currency, direction, category) -> the category's name, the amount, the number of entries
        by_category: dict[tuple[Currency, Direction, UUID], tuple[str, list[int]]] = {}
        after: tuple[date, datetime, UUID] | None = None
        while True:
            async with self._storage.tenant(shop_id) as session:
                page = await session.cash_entries_of_period(
                    first=first, before=last + timedelta(days=1), until=until, after=after, limit=PAGE
                )
            if not page:
                break
            after = (page[-1].day, page[-1].created_at, page[-1].entry_id)
            for row in page:
                currency = parse_code(row.currency) or Currency.UZS
                source = _source(row)
                cancelled = row.cancelled_at is not None
                # The ledger cancels its entry without a reason: the payment was reversed.
                reason = row.cancel_reason or (word(lang, "cash_reversed") if source == "ledger" else None)
                cells = [
                    row.day.isoformat(),
                    _local(row.created_at),
                    word(lang, f"cash_{row.direction}", row.direction),
                    word(lang, f"cash_{row.method}", row.method),
                    currency.value,
                    _amount(currency, row.amount),
                    row.category_name,
                    row.note,
                    word(lang, f"cash_source_{source}"),
                    yes if cancelled else no,
                    reason if cancelled else None,
                    None if row.cancelled_at is None else _local(row.cancelled_at),
                    str(row.author_id),
                    str(row.entry_id),
                ]
                if names:
                    cells.insert(8, row.customer_name)
                entries.append(cells)
                if cancelled:
                    continue  # a cancelled entry is in the list and in no figure
                method, direction = Method(row.method), Direction(row.direction)
                figures = during.setdefault((method, currency, direction), [0, 0])
                figures[0] += row.amount
                figures[1] += 1
                _, of_category = by_category.setdefault(
                    (currency, direction, row.category_id), (row.category_name, [0, 0])
                )
                of_category[0] += row.amount
                of_category[1] += 1

        for label, value in (
            ("summary_shop", shop_name),
            ("cash_period_from", first.isoformat()),
            ("cash_period_to", last.isoformat()),
            ("summary_made", _local(until)),
            ("cash_entries_count", entries.data_rows),
        ):
            summary.append((word(lang, label), value))

        lines = cash.book(
            opening,
            [
                Sum(method, currency, direction, amount, count)
                for (method, currency, direction), (amount, count) in during.items()
            ],
            shown,
        )
        summary.append(())
        summary.append((word(lang, "cash_balances_title"),), bold=True)
        summary.append(header(lang, "cash_balances"), bold=True)
        totals = cash.totals_by_currency(lines)

        def balance(line: Line, method: str) -> tuple[str | int | Decimal, ...]:
            return (
                line.currency.value,
                method,
                _amount(line.currency, line.opening),
                _amount(line.currency, line.income),
                _amount(line.currency, line.expense),
                _amount(line.currency, line.closing),
                line.count,
            )

        for currency, total in totals.items():
            for line in lines:
                if line.currency is currency:
                    summary.append(balance(line, word(lang, f"cash_{line.method.value}")))
            # One currency over its methods: never a figure of two currencies.
            summary.append(balance(total, word(lang, "cash_all_methods")), bold=True)

        summary.append(())
        summary.append((word(lang, "cash_categories_title"),), bold=True)
        summary.append(header(lang, "cash_categories"), bold=True)
        ordered = sorted(
            by_category.items(),
            key=lambda item: (
                item[0][0] is not Currency.UZS,
                item[0][1] is not Direction.INCOME,
                -item[1][1][0],
                item[1][0],
                str(item[0][2]),
            ),
        )
        for (currency, direction, _), (name, (amount, count)) in ordered:
            summary.append(
                (currency.value, word(lang, f"cash_{direction.value}"), name, _amount(currency, amount), count)
            )
        summary.append(())
        summary.append((word(lang, "cash_summary_note"),))
        return entries.data_rows
