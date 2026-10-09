"""Exports of a shop's own data as worker jobs with signed downloads (REQ-028, ADR-020).

A manager or owner asks; the request only records a job. The worker writes the workbook, a page of the
ledger at a time, keeps it in the file store and tells the requester. The file is fetched through a
five-minute signed link from an authorized operation and deleted after seven days.

Who may: managers and owners. In a suspended shop only the owner (BR-30); a shop waiting to be deleted
exports as before (BR-25). The workbook holds the shop's own rows only: every read goes through a
transaction of that shop.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency
from qarz.application.chat_texts import say
from qarz.application.currencies import USD, UZS, dollars_on
from qarz.application.customers import require_viewable
from qarz.application.errors import AppError, NotFound, StorageTimeout
from qarz.application.export_texts import header, word
from qarz.application.files import CheckedFile, FileService, FileStoreUnavailable, StagedFile
from qarz.application.network_export import write_network
from qarz.application.operations import operation
from qarz.application.ports import ExportJobRecord, Storage
from qarz.application.shops import require_member
from qarz.application.stock_export import write_stock
from qarz.application.xlsx import MIME, Cell, Workbook
from qarz.domain.access import Capability
from qarz.domain.exports import (
    DONE,
    FAILED,
    MAX_EXPORT_BYTES,
    ExportError,
    counts,
    delete_after,
    gives_up,
    may_request,
    signed_effect,
    stale_before,
)
from qarz.domain.money import Currency, parse_code, plain
from qarz.domain.promise import TASHKENT, tashkent_date
from qarz.domain.reports import day_start

REQUEST_EXPORT = operation("exports.request", Capability.MANAGE)
LIST_EXPORTS = operation("exports.list", Capability.MANAGE)
DOWNLOAD_EXPORT = operation("exports.download", Capability.MANAGE)

FILE_PURPOSE = "export"
FILE_LINK_PATH = "/files"
JOBS_SHOWN = 20
# Rows read by one statement. Each is far below the worker's statement timeout at any shop size, because
# the page is found by key and costs what the page holds.
PAGE = 1000
# Jobs one pass of the worker writes before it goes back to its other work.
JOBS_PER_PASS = 3

log = logging.getLogger("qarz.exports")


class ExportNotAllowed(AppError):
    """The shop may not start another export now. The field says why."""

    code = "EXPORT_NOT_ALLOWED"


class ExportNotReady(AppError):
    """The job has no file to download: it is not finished, it failed, or its file has been deleted."""

    code = "EXPORT_NOT_READY"


def job_body(record: ExportJobRecord, now: datetime) -> dict[str, Any]:
    available = record.file_id is not None and record.file_delete_after is not None and record.file_delete_after > now
    return {
        "id": str(record.job_id),
        "status": record.status,
        "error": record.error,
        "requested_by": str(record.requested_by),
        "created_at": record.created_at.isoformat(),
        "finished_at": None if record.finished_at is None else record.finished_at.isoformat(),
        "rows": record.row_count,
        # Whether the workbook can be downloaded now, and until when.
        "available": available,
        "available_until": record.file_delete_after.isoformat()
        if available and record.file_delete_after is not None
        else None,
    }


def _local(at: datetime) -> str:
    return at.astimezone(TASHKENT).strftime("%Y-%m-%d %H:%M")


class ExportService:
    def __init__(self, storage: Storage, files: FileService, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._files = files
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    # --- the API --------------------------------------------------------------------------------------

    async def request(self, user_id: UUID, shop_id: UUID, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, REQUEST_EXPORT)
            key = idempotency.validate_key(request_key)
            # Not `refuse_suspended`: exporting is what a suspended shop's owner may still do (BR-30).
            await require_viewable(session, actor, self._today())

            async def apply() -> dict[str, Any]:
                now = self._now()
                await session.lock_exports()
                refusal = may_request(
                    in_progress=await session.export_in_progress(),
                    today_count=await session.exports_since(day_start(self._today())),
                )
                if refusal is not None:
                    raise ExportNotAllowed({"reason": refusal.value})
                record = await session.add_export_job(job_id=uuid4(), requested_by=actor.membership_id, now=now)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="export.requested",
                    subject_type="shop",
                    subject_id=shop_id,
                )
                return job_body(record, now)

            return await idempotency.run_once(
                session, key=key, operation=REQUEST_EXPORT.name, user_id=user_id, request={}, action=apply
            )

    async def list(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        now = self._now()
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_EXPORTS)
            await require_viewable(session, actor, self._today())
            return {"items": [job_body(record, now) for record in await session.export_jobs(JOBS_SHOWN)]}

    async def download(self, user_id: UUID, shop_id: UUID, job_id: UUID) -> dict[str, Any]:
        """A link to the workbook, valid five minutes. Nothing is read from the file store here."""
        now = self._now()
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, DOWNLOAD_EXPORT)
            await require_viewable(session, actor, self._today())
            record = await session.get_export_job(job_id)
            if record is None:
                raise NotFound()
            if record.file_id is None:
                # A finished job without a file is one whose workbook has been deleted.
                raise ExportNotReady({"status": "expired" if record.status == DONE else record.status})
            stored = await session.get_stored_file(record.file_id)
        if stored is None or stored.delete_after is None or stored.delete_after <= now:
            raise ExportNotReady({"status": "expired"})
        token, expires_at = self._files.link(shop_id, stored, now)
        return {"url": f"{FILE_LINK_PATH}/{token}", "expires_at": expires_at.isoformat()}

    # --- the worker -----------------------------------------------------------------------------------

    async def run_pending(self, limit: int = JOBS_PER_PASS) -> int:
        """Write the exports that are waiting. Returns how many jobs were taken. Safe with two workers."""
        taken = 0
        while taken < limit:
            now = self._now()
            async with self._storage.platform() as platform:
                claimed = await platform.claim_export_job(now, stale_before(now))
            if claimed is None:
                break
            taken += 1
            job_id, shop_id, attempts = claimed
            if gives_up(attempts):
                await self._fail(shop_id, job_id, ExportError.INTERRUPTED)
                continue
            try:
                await self._produce(shop_id, job_id, now)
            except _Superseded:
                continue
            except StorageTimeout:
                await self._fail(shop_id, job_id, ExportError.TIMEOUT)
            except FileStoreUnavailable:
                await self._fail(shop_id, job_id, ExportError.FILE_STORE)
            except Exception as error:
                # Only the kind of failure: a message could repeat a name or an amount.
                log.error("export_failed: %s", type(error).__name__, extra={"job_id": str(job_id)})
                await self._fail(shop_id, job_id, ExportError.INTERNAL)
        return taken

    async def _fail(self, shop_id: UUID, job_id: UUID, error: ExportError) -> None:
        try:
            await self._close(shop_id, job_id, error=error)
        except _Superseded:
            return

    async def _produce(self, shop_id: UUID, job_id: UUID, until: datetime) -> None:
        async with self._storage.tenant(shop_id) as session:
            settings = await session.shop_settings()
            # An export is the owner's copy of everything recorded: dollar entries are in it whenever
            # the shop has any, even while it does not show dollars. A shop that never had one gets the
            # workbook it always got.
            with_dollars = await dollars_on(session) or await session.dollars_recorded()
        if settings is None:
            raise NotFound()
        book = Workbook()
        try:
            rows = await self._write(book, shop_id, settings.name, settings.lang, until, with_dollars)
            # The stock and the suppliers follow, as sheets of their own after the ones every workbook
            # has: only for a shop that has any, so a shop without them gets the workbook it always got.
            await write_stock(book, self._storage, shop_id, settings.lang, until)
            # And the shop's own side of the network between shops, for a shop that has a link.
            await write_network(book, self._storage, shop_id, settings.lang)
            # Packing compresses everything written so far; done off the event loop so that messages
            # keep being delivered meanwhile.
            content = await asyncio.to_thread(book.finish)
        finally:
            book.close()
        if len(content) > MAX_EXPORT_BYTES:
            # Larger than anything the file store is asked to hand back: not kept at all.
            raise FileStoreUnavailable()
        staged = await self._files.stage(CheckedFile(MIME, content))
        try:
            await self._close(shop_id, job_id, staged=staged, rows=rows)
        except BaseException:
            await self._files.discard(staged)
            raise

    async def _close(
        self,
        shop_id: UUID,
        job_id: UUID,
        *,
        error: ExportError | None = None,
        staged: StagedFile | None = None,
        rows: int | None = None,
    ) -> None:
        """Record how the job ended and tell whoever asked for it."""
        now = self._now()
        async with self._storage.tenant(shop_id) as session:
            job = await session.get_export_job(job_id)
            if job is not None:
                file_id = (
                    None
                    if staged is None
                    else await self._files.record_in(
                        session, staged, purpose=FILE_PURPOSE, now=now, delete_after=delete_after(now)
                    )
                )
                closed = await session.finish_export_job(
                    job_id,
                    status=FAILED if error is not None else DONE,
                    file_id=file_id,
                    error=None if error is None else error.value,
                    row_count=rows,
                    now=now,
                )
                if not closed:
                    # Another worker took the job over after this one was silent too long and has closed
                    # it. Raised inside the transaction, so the file row written above is not kept.
                    raise _Superseded()
                # Measured when the work is done, with its size: how many ledger rows an export carries.
                await session.record_measure(
                    kind="export_failed" if error is not None else "export_done",
                    entry_ref=job_id,
                    amount=rows or 0,
                    promised=None,
                )
                settings = await session.shop_settings()
                recipient = await session.member_recipient(job.requested_by)
                if recipient is not None and settings is not None:
                    tg_id, lang = recipient
                    key = "export_failed" if error is not None else "export_ready"
                    await session.enqueue(
                        recipient=str(tg_id),
                        payload={"text": say(lang, key, shop=settings.name)},
                        dedupe_key=f"export:{job_id}:{key}",
                    )

    async def _write(
        self, book: Workbook, shop_id: UUID, shop_name: str, lang: str, until: datetime, with_dollars: bool = False
    ) -> int:
        """Fill the workbook from the shop's own rows. Returns the number of ledger rows written.

        With dollars, the ledger sheet has the currency of every entry in a last column and writes a
        dollar amount as dollars and cents; the customers sheet has the dollar limit and the dollar
        debt in two last columns; and the summary has the dollar totals in rows of their own. No cell
        is a sum of so'm and dollars.
        """
        dollar_columns = (word(lang, "limit_usd"), word(lang, "owed_usd")) if with_dollars else ()
        summary = book.sheet(word(lang, "sheet_summary"), widths=(46, 22, 14, 26, 20, 16, 30))
        customers = book.sheet(
            word(lang, "sheet_customers"),
            (*header(lang, "customers"), *dollar_columns),
            (28, 16, 22, 14, 14, 16, 38, *((16, 14) if with_dollars else ())),
        )
        ledger = book.sheet(
            word(lang, "sheet_ledger"),
            (*header(lang, "ledger"), *((word(lang, "currency"),) if with_dollars else ())),
            (17, 28, 18, 12, 14, 30, 14, 10, 38, 14, 38, 10, 38, 38, *((9,) if with_dollars else ())),
        )
        promises = book.sheet(word(lang, "sheet_promises"), header(lang, "promises"), (38, 28, 14, 17, 28, 30))
        goods = book.sheet(word(lang, "sheet_goods"), header(lang, "goods"), (38, 17, 28, 7, 28, 10, 10, 12, 14))

        yes, no = word(lang, "yes"), word(lang, "no")
        # Each currency has balances and months of its own.
        owed: dict[Currency, dict[UUID, int]] = {currency: defaultdict(int) for currency in Currency}
        # month -> credit amount, credit count, opening amount, payment amount, payment count, reversed count
        by_month: dict[Currency, dict[str, list[int]]] = {
            currency: defaultdict(lambda: [0, 0, 0, 0, 0, 0]) for currency in Currency
        }
        after: tuple[datetime, UUID] | None = None
        while True:
            async with self._storage.tenant(shop_id) as session:
                page = await session.export_entries(until=until, after=after, limit=PAGE)
                ids = [entry.entry_id for entry in page]
                set_dates = await session.export_promises(ids) if ids else []
                lines = await session.goods_lines_of(ids) if ids else {}
            if not page:
                break
            after = (page[-1].created_at, page[-1].entry_id)
            names = {entry.entry_id: entry for entry in page}
            for entry in page:
                if entry.currency is not UZS and not with_dollars:
                    continue  # cannot happen: `with_dollars` is true whenever a dollar entry exists
                effect = signed_effect(entry.kind, entry.amount, entry.reversed_kind)
                owed[entry.currency][entry.customer_id] += effect
                month = by_month[entry.currency][entry.created_at.astimezone(TASHKENT).strftime("%Y-%m")]
                if counts(entry.kind, entry.is_reversed):
                    if entry.kind == "credit":
                        month[0] += entry.amount
                        month[1] += 1
                    elif entry.kind == "opening":
                        month[2] += entry.amount
                    else:
                        month[3] += entry.amount
                        month[4] += 1
                elif entry.is_reversed:
                    month[5] += 1
                ledger.append(
                    (
                        _local(entry.created_at),
                        entry.customer_name,
                        word(lang, f"kind_{entry.kind}", entry.kind),
                        _amount(entry.currency, entry.amount),
                        _amount(entry.currency, effect),
                        entry.note,
                        None if entry.promised_date is None else entry.promised_date.isoformat(),
                        yes if entry.is_reversed else no,
                        None if entry.reverses_id is None else str(entry.reverses_id),
                        word(lang, f"role_{entry.author_role}", entry.author_role),
                        str(entry.author_id),
                        entry.seq,
                        str(entry.entry_id),
                        str(entry.customer_id),
                        *((entry.currency.value,) if with_dollars else ()),
                    )
                )
                for line in lines.get(entry.entry_id, ()):
                    goods.append(
                        (
                            str(entry.entry_id),
                            _local(entry.created_at),
                            entry.customer_name,
                            line.line_no,
                            line.name,
                            line.qty,
                            line.unit,
                            line.unit_price,
                            line.line_total,
                        )
                    )
            for promise in set_dates:
                promises.append(
                    (
                        str(promise.entry_id),
                        names[promise.entry_id].customer_name,
                        promise.promised_date.isoformat(),
                        _local(promise.created_at),
                        word(lang, f"actor_{promise.actor}", promise.actor),
                        promise.reason,
                    )
                )

        people = []
        last: UUID | None = None
        while True:
            async with self._storage.tenant(shop_id) as session:
                batch = await session.export_customers(after=last, limit=PAGE)
            if not batch:
                break
            last = batch[-1].customer_id
            people.extend(batch)
        people.sort(key=lambda person: (person.name_norm, str(person.customer_id)))
        for person in people:
            # An anonymized customer has only a label for a name and no phone: nothing else is known here.
            customers.append(
                (
                    person.display_name,
                    person.phone,
                    word(lang, f"status_{person.status}", person.status),
                    person.credit_limit,
                    owed[UZS].get(person.customer_id, 0),
                    person.created_at.astimezone(TASHKENT).date().isoformat(),
                    str(person.customer_id),
                    *(
                        (
                            None if person.credit_limit_usd is None else _amount(USD, person.credit_limit_usd),
                            _amount(USD, owed[USD].get(person.customer_id, 0)),
                        )
                        if with_dollars
                        else ()
                    ),
                )
            )

        figures: list[tuple[str, Cell]] = [
            ("summary_shop", shop_name),
            ("summary_made", _local(until)),
            ("summary_customers", len(people)),
            ("summary_debtors", sum(1 for balance in owed[UZS].values() if balance > 0)),
            ("summary_outstanding", sum(owed[UZS].values())),
        ]
        if with_dollars:
            figures += [
                ("summary_debtors_usd", sum(1 for balance in owed[USD].values() if balance > 0)),
                ("summary_outstanding_usd", _amount(USD, sum(owed[USD].values()))),
            ]
        figures.append(("summary_entries", ledger.data_rows))
        for label, value in figures:
            summary.append((word(lang, label), value))
        for currency, title in ((UZS, "summary_months"), (USD, "summary_months_usd")):
            if currency is USD and not with_dollars:
                continue
            summary.append(())
            summary.append((word(lang, title),), bold=True)
            summary.append(header(lang, "months"), bold=True)
            for month_name in sorted(by_month[currency]):
                credit, credits, opening, paid, payments, reversed_count = by_month[currency][month_name]
                summary.append(
                    (
                        month_name,
                        _amount(currency, credit),
                        credits,
                        _amount(currency, opening),
                        _amount(currency, paid),
                        payments,
                        reversed_count,
                    )
                )
        await self._write_cash(book, shop_id, lang, until)
        return ledger.data_rows

    async def _write_cash(self, book: Workbook, shop_id: UUID, lang: str, until: datetime) -> None:
        """The shop's cash book as one more sheet: every entry, cancelled ones included and marked.

        Only in the workbook of a shop that has a cash book: one that never used it gets the workbook it
        always got. Each row says its currency; nothing on the sheet is a sum.
        """
        async with self._storage.tenant(shop_id) as session:
            if not await session.cash_entries_exist():
                return
        sheet = book.sheet(
            word(lang, "sheet_cash"), header(lang, "cash"), (12, 17, 10, 12, 9, 14, 26, 30, 10, 30, 14, 38, 38, 38)
        )
        yes, no = word(lang, "yes"), word(lang, "no")
        after: tuple[date, datetime, UUID] | None = None
        while True:
            async with self._storage.tenant(shop_id) as session:
                page = await session.export_cash_entries(until=until, after=after, limit=PAGE)
            if not page:
                break
            after = (page[-1].day, page[-1].created_at, page[-1].entry_id)
            for row in page:
                currency = parse_code(row.currency) or UZS
                sheet.append(
                    (
                        row.day.isoformat(),
                        _local(row.created_at),
                        word(lang, f"cash_{row.direction}", row.direction),
                        word(lang, f"cash_{row.method}", row.method),
                        row.currency,
                        _amount(currency, row.amount),
                        row.category_name,
                        row.note,
                        no if row.cancelled_at is None else yes,
                        row.cancel_reason,
                        word(lang, f"role_{row.author_role}", row.author_role),
                        str(row.author_id),
                        None if row.ledger_entry_id is None else str(row.ledger_entry_id),
                        str(row.entry_id),
                    )
                )


def _amount(currency: Currency, amount: int) -> int | Decimal:
    """An amount as a cell: whole so'm as the number it is, cents as dollars with two decimals."""
    return amount if currency is UZS else Decimal(plain(currency, amount))


class _Superseded(Exception):
    """This worker's job was finished by another; what it staged is discarded and nothing is told twice."""
