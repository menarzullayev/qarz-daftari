"""Import of customers with opening balances from a spreadsheet (REQ-062, REQ-063; BR-24; DOM-017).

The API only records what is asked; the worker does the work (architecture, "Import"): it checks an
uploaded file and builds the preview, applies a confirmed batch in one transaction, and undoes one.

    uploaded ──worker──> validated ──apply──> applying ──worker──> applied ──undo──> undoing ──worker──> undone
                    └──> rejected (problems)        └──refused──> validated or rejected   └──refused──> applied
                    └──> failed (could not be checked)             validated, rejected, failed ──> discarded

A batch waits for the worker in `uploaded`, `applying` and `undoing`. A worker takes it with the claim
that exports use (one row at a time, skipping locked rows; taken again after a silent worker; given up
after three starts) and does the step in one transaction of the shop that first locks the batch and
looks at its state again: two workers cannot do one step twice, and a worker that dies leaves the batch
as it was.

What applying will do is decided again at that moment and compared with the plan the person confirmed,
so a customer added or renamed since the preview is never merged with a row unseen: the batch then goes
back with a fresh preview and the reason.
"""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency, notify, removal
from qarz.application.chat_texts import money, say
from qarz.application.customers import customer_body, owes_anything, require_viewable, require_writable
from qarz.application.errors import AppError, NotFound, StorageTimeout, ValidationFailed
from qarz.application.files import CheckedFile, FileService, FileStoreUnavailable, StagedFile
from qarz.application.ledger_service import DEFAULT_ACTOR, STAFF_ACTOR, expire_settled_date_requests
from qarz.application.operations import operation
from qarz.application.ports import (
    ImportBatchRecord,
    ImportCandidate,
    Membership,
    NewImportCustomer,
    NewImportEntry,
    NewReversal,
    Storage,
    StoredFileRecord,
    TenantSession,
)
from qarz.application.shops import refuse_suspended, require_member
from qarz.application.xlsx import Workbook
from qarz.domain import imports
from qarz.domain.access import Capability
from qarz.domain.imports import Candidate, FileProblem, ParsedFile, PlannedRow, RowError
from qarz.domain.money import Currency
from qarz.domain.promise import default_promise_date, tashkent_date

IMPORT_TEMPLATE = operation("imports.template", Capability.MANAGE)
UPLOAD_IMPORT = operation("imports.upload", Capability.MANAGE)
LIST_IMPORTS = operation("imports.list", Capability.MANAGE)
READ_IMPORT = operation("imports.read", Capability.MANAGE)
APPLY_IMPORT = operation("imports.apply", Capability.MANAGE)
UNDO_IMPORT = operation("imports.undo", Capability.MANAGE)
DISCARD_IMPORT = operation("imports.discard", Capability.MANAGE)

# Technical specification, "Retention": import files 30 days. One that is applied or discarded goes at once.
FILE_RETENTION = timedelta(days=30)
LIST_LIMIT = 20
# Steps one pass of the worker does before it goes back to its other work.
STEPS_PER_PASS = 3
UPLOADED, VALIDATED, REJECTED, FAILED = "uploaded", "validated", "rejected", "failed"
APPLYING, APPLIED, UNDOING, UNDONE, DISCARDED = "applying", "applied", "undoing", "undone", "discarded"
# Where a step that was not done leaves the batch.
_BACK = {UPLOADED: FAILED, APPLYING: VALIDATED, UNDOING: APPLIED}
_STEP = {UPLOADED: "check", APPLYING: "apply", UNDOING: "undo"}
OWNERS = ("owner",)
TEMPLATE_SHEET = "Import"
TEMPLATE_WIDTHS = (28, 20, 18, 18, 36)

log = logging.getLogger("qarz.imports")


class ImportNotApplicable(AppError):
    """The batch cannot be applied or discarded as it stands. `reason` says why."""

    code = "IMPORT_NOT_APPLICABLE"


class ImportUndoRefused(AppError):
    code = "IMPORT_UNDO_REFUSED"


@dataclass(frozen=True)
class Plan:
    rows: list[PlannedRow]
    errors: list[RowError]
    customers: dict[UUID, ImportCandidate]


def template(lang: str) -> bytes:
    """The published template (REQ-062): one sheet with the five column titles, in the caller's language."""
    book = Workbook()
    book.sheet(TEMPLATE_SHEET, imports.TEMPLATE_HEADERS.get(lang, imports.TEMPLATE_HEADERS["uz"]), TEMPLATE_WIDTHS)
    return book.finish()


def _errors(errors: list[RowError] | tuple[RowError, ...]) -> list[dict[str, Any]]:
    ordered = sorted(errors, key=lambda error: (error.row, error.column, error.code.value))
    return [{"row": error.row, "column": error.column, "code": error.code.value} for error in ordered]


def _counts(planned: list[PlannedRow]) -> dict[str, int]:
    return {
        "new_customers": sum(item.action == imports.CREATE for item in planned),
        "existing_customers": len({item.customer_id for item in planned if item.action == imports.EXISTING}),
        "entries": len(planned),
        "amount": sum(item.row.amount for item in planned),
    }


def batch_body(record: ImportBatchRecord) -> dict[str, Any]:
    summary = record.summary
    applied = record.applied_at
    return {
        "id": str(record.batch_id),
        "status": record.status,
        "format": summary.get("format"),
        "author_id": str(record.author_id),
        "created_at": record.created_at.isoformat(),
        "applied_at": None if applied is None else applied.isoformat(),
        # Until when an undo can still be asked for (BR-24); null when there is nothing to undo.
        "undo_until": (applied + imports.UNDO_WINDOW).isoformat()
        if applied is not None and record.status == APPLIED
        else None,
        "rows": summary.get("rows", 0),
        # Why the file as a whole could not be used, when it could not; else the problems of its rows.
        "file_problem": summary.get("file_problem"),
        "errors": summary.get("errors", []),
        "applied": summary.get("applied"),
        "undone": summary.get("undone"),
        # The last step that was asked for and not done, with the reason: {"step": ..., "reason": ...}.
        "refused": summary.get("refused"),
    }


async def _plan(session: TenantSession, parsed: ParsedFile) -> Plan:
    """Set the rows of the file against the shop's customers as they are now."""
    found = await session.import_candidates(
        sorted({row.name_norm for row in parsed.rows}), sorted({row.phone for row in parsed.rows if row.phone})
    )
    planned, errors = imports.plan(
        parsed.rows,
        [Candidate(c.customer_id, c.name_norm, c.phone, c.status == "archived") for c in found],
    )
    return Plan(planned, [*parsed.errors, *errors], {c.customer_id: c for c in found})


def _preview_row(item: PlannedRow, customers: dict[UUID, ImportCandidate], balances: dict[UUID, int]) -> dict[str, Any]:
    row = item.row
    customer = None if item.customer_id is None else customers[item.customer_id]
    return {
        "row": row.row,
        "name": row.name,
        "phone": row.phone,
        "amount": row.amount,
        # Null: the shop's default number of days will apply (BR-1).
        "promised_date": None if row.promised is None else row.promised.isoformat(),
        "note": row.note,
        "action": item.action,
        "matched_by": item.matched_by,
        "same_as_row": item.first_row,
        "customer": None
        if customer is None
        else {
            "id": str(customer.customer_id),
            "display_name": customer.display_name,
            "phone": customer.phone,
            "balance": balances.get(customer.customer_id, 0),
        },
    }


async def _batch(session: TenantSession, batch_id: UUID) -> ImportBatchRecord:
    record = await session.get_import_batch(batch_id, for_update=False)
    assert record is not None  # read back inside the transaction that has just written it
    return record


async def _tell(
    session: TenantSession, batch: ImportBatchRecord, key: str, *, owners: bool = False, **values: Any
) -> None:
    """Tell whoever asked for the step, and the owner too of what the specification's events name
    (ImportApplied, ImportUndone). Each person once, in their own language."""
    settings = await session.shop_settings()
    shop = "" if settings is None else settings.name
    amount = values.pop("amount", None)
    people: dict[int, str] = {}
    if owners:
        people.update(dict(await session.staff_recipients(list(OWNERS))))
    asked = None if batch.step_by is None else await session.member_recipient(batch.step_by)
    if asked is not None:
        people[asked[0]] = asked[1]
    for tg_id, lang in sorted(people.items()):
        said = dict(values) if amount is None else {**values, "amount": money(lang, int(amount))}
        await session.enqueue(
            recipient=str(tg_id),
            payload={"text": say(lang, key, shop=shop, **said)},
            dedupe_key=f"import:{batch.batch_id}:{key}:{batch.queued_at}:{tg_id}",
        )


class ImportService:
    def __init__(self, storage: Storage, files: FileService, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._files = files
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    # --- the template, the list and the batch with its preview: reads of the database only --------------

    async def template(self, user_id: UUID, shop_id: UUID) -> bytes:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, IMPORT_TEMPLATE)
            await require_viewable(session, actor, self._today())
        return template(await self._storage.user_language(user_id) or "uz")

    async def list(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_IMPORTS)
            await require_viewable(session, actor, self._today())
            return {"items": [batch_body(record) for record in await session.list_import_batches(LIST_LIMIT)]}

    async def read(self, user_id: UUID, shop_id: UUID, batch_id: UUID) -> dict[str, Any]:
        """The batch and, once the worker has checked the file, what applying it would do."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_IMPORT)
            await require_viewable(session, actor, self._today())
            record = await session.get_import_batch(batch_id, for_update=False)
            if record is None:
                raise NotFound()
            return {**batch_body(record), "preview": await session.import_preview(batch_id)}

    # --- upload: the file is kept and the batch waits to be checked -------------------------------------

    async def _authorize_upload(
        self, session: TenantSession, user_id: UUID, request_key: str | None
    ) -> tuple[Membership, str]:
        actor = await require_member(session, user_id, UPLOAD_IMPORT)
        key = idempotency.validate_key(request_key)
        # BR-29: an import is refused in limited mode, like a new credit sale; BR-30: and in a suspended shop.
        await require_writable(session, self._today(), new_credit=True)
        return actor, key

    async def upload(self, user_id: UUID, shop_id: UUID, data: bytes, request_key: str | None) -> dict[str, Any]:
        # Everything that can refuse an upload whatever the file is, before the file is looked at.
        async with self._storage.tenant(shop_id) as session:
            await self._authorize_upload(session, user_id, request_key)
        # Only what the bytes say without the sheet being read; the worker reads the sheet.
        kind = imports.sniff(data)
        if isinstance(kind, FileProblem):
            raise ValidationFailed({"file": kind.value})
        staged = await self._files.stage(CheckedFile(imports.MIMES[kind], data))
        recorded = False
        try:
            async with self._storage.tenant(shop_id) as session:
                actor, key = await self._authorize_upload(session, user_id, request_key)

                async def apply() -> dict[str, Any]:
                    nonlocal recorded
                    body = await self._record(session, actor, kind, staged)
                    recorded = True
                    return body

                return await idempotency.run_once(
                    session,
                    key=key,
                    operation=UPLOAD_IMPORT.name,
                    user_id=user_id,
                    request={"sha256": hashlib.sha256(data).hexdigest()},
                    action=apply,
                )
        except BaseException:
            recorded = False
            raise
        finally:
            if not recorded:
                # A repeated request, or a transaction that did not commit: the staged copy belongs to nobody.
                await self._files.discard(staged)

    async def _record(self, session: TenantSession, actor: Membership, kind: str, staged: StagedFile) -> dict[str, Any]:
        now = self._now()
        batch_id = uuid4()
        file_id = await FileService.record_in(
            session, staged, purpose="import", now=now, delete_after=now + FILE_RETENTION
        )
        await session.create_import_batch(
            batch_id=batch_id, file_id=file_id, summary={"format": kind}, author_id=actor.membership_id, now=now
        )
        await session.record_activity(
            membership_id=actor.membership_id, action="import.uploaded", subject_type="import", subject_id=batch_id
        )
        return batch_body(await _batch(session, batch_id))

    # --- apply and undo: the request is checked and the batch waits for the worker ----------------------

    async def apply(
        self, user_id: UUID, shop_id: UUID, batch_id: UUID, plan_token: str, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, APPLY_IMPORT)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=True)

            async def apply() -> dict[str, Any]:
                record = await session.get_import_batch(batch_id, for_update=True)
                if record is None:
                    raise NotFound()
                if record.status != VALIDATED:
                    raise ImportNotApplicable({"reason": record.status})
                if record.plan != plan_token:
                    # Not the preview this batch has now: nothing is applied on what nobody was shown.
                    raise ImportNotApplicable({"reason": "stale"})
                await self._queue(session, actor, record, APPLYING, "import.apply_requested")
                return batch_body(await _batch(session, batch_id))

            return await idempotency.run_once(
                session,
                key=key,
                operation=APPLY_IMPORT.name,
                user_id=user_id,
                request={"import": str(batch_id), "plan": plan_token},
                action=apply,
            )

    async def undo(self, user_id: UUID, shop_id: UUID, batch_id: UUID, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, UNDO_IMPORT)
            key = idempotency.validate_key(request_key)
            # Undoing is a set of reversals, which limited mode allows (BR-29); a suspended shop changes nothing.
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                record = await session.get_import_batch(batch_id, for_update=True)
                if record is None:
                    raise NotFound()
                # The 24 hours are counted to the moment the undo is asked for, not to when the worker gets to it.
                refusal = imports.may_undo(record.status, record.applied_at, self._now())
                if refusal is not None:
                    raise ImportUndoRefused({"reason": refusal.value})
                await self._queue(session, actor, record, UNDOING, "import.undo_requested")
                return batch_body(await _batch(session, batch_id))

            return await idempotency.run_once(
                session,
                key=key,
                operation=UNDO_IMPORT.name,
                user_id=user_id,
                request={"import": str(batch_id)},
                action=apply,
            )

    async def _queue(
        self, session: TenantSession, actor: Membership, record: ImportBatchRecord, status: str, action: str
    ) -> None:
        summary = {name: value for name, value in record.summary.items() if name != "refused"}
        await session.set_import_batch(
            record.batch_id,
            status=status,
            summary=summary,
            plan=record.plan,
            queued=(actor.membership_id, self._now()),
        )
        await session.record_activity(
            membership_id=actor.membership_id, action=action, subject_type="import", subject_id=record.batch_id
        )

    async def discard(self, user_id: UUID, shop_id: UUID, batch_id: UUID, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, DISCARD_IMPORT)
            await refuse_suspended(session)
            key = idempotency.validate_key(request_key)

            async def apply() -> dict[str, Any]:
                record = await session.get_import_batch(batch_id, for_update=True)
                if record is None:
                    raise NotFound()
                if record.status not in (UPLOADED, VALIDATED, REJECTED, FAILED):
                    raise ImportNotApplicable({"reason": record.status})
                if record.file_id is not None:
                    await session.shorten_file_retention(record.file_id, self._now())
                await session.set_import_preview(batch_id, None)
                await session.set_import_batch(batch_id, status=DISCARDED, summary=record.summary, plan=None)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="import.discarded",
                    subject_type="import",
                    subject_id=batch_id,
                )
                return batch_body(await _batch(session, batch_id))

            return await idempotency.run_once(
                session,
                key=key,
                operation=DISCARD_IMPORT.name,
                user_id=user_id,
                request={"import": str(batch_id)},
                action=apply,
            )

    # --- the worker -------------------------------------------------------------------------------------

    async def run_pending(self, limit: int = STEPS_PER_PASS) -> int:
        """Do the steps that are waiting. Returns how many were taken. Safe with two workers."""
        taken = 0
        while taken < limit:
            now = self._now()
            async with self._storage.platform() as platform:
                claimed = await platform.claim_import_batch(now, imports.stale_before(now))
            if claimed is None:
                break
            taken += 1
            batch_id, shop_id, step, attempts = claimed
            if imports.gives_up(attempts):
                await self._give_up(shop_id, batch_id, step, "interrupted")
                continue
            try:
                await self.do_step(shop_id, batch_id, step)
            except StorageTimeout:
                await self._give_up(shop_id, batch_id, step, "timeout")
            except FileStoreUnavailable:
                await self._give_up(shop_id, batch_id, step, "file_store")
            except Exception as error:
                # Only the kind of failure: a message could repeat a name or an amount.
                log.error("import_step_failed: %s", type(error).__name__, extra={"batch_id": str(batch_id)})
                await self._give_up(shop_id, batch_id, step, "internal")
        return taken

    async def do_step(self, shop_id: UUID, batch_id: UUID, step: str) -> None:
        """One step of one batch, whole or not at all. `step` is the state the batch was claimed in."""
        if step == UNDOING:
            async with self._storage.tenant(shop_id) as session:
                await undo_in(session, batch_id, self._now())
            return
        async with self._storage.tenant(shop_id) as session:
            record = await session.get_import_batch(batch_id, for_update=False)
            if record is None or record.status != step:
                return
            stored = None if record.file_id is None else await session.get_stored_file(record.file_id)
        parsed = await self._content(stored)
        async with self._storage.tenant(shop_id) as session:
            if step == UPLOADED:
                await check_in(session, batch_id, parsed, self._now())
            else:
                await apply_in(session, batch_id, parsed, self._now())

    async def _content(self, record: StoredFileRecord | None) -> ParsedFile | str:
        """The rows of a batch, read from its file, or the code of why the file cannot be used.

        Called outside any transaction: the store is a network call, and reading a workbook is work for
        a thread of its own, so that messages keep being delivered meanwhile.
        """
        if record is None:
            return imports.FILE_GONE
        try:
            data = await self._files.content(record)
        except NotFound:
            return imports.FILE_GONE
        parsed = await asyncio.to_thread(imports.parse, data, self._today())
        return parsed.value if isinstance(parsed, FileProblem) else parsed

    async def _give_up(self, shop_id: UUID, batch_id: UUID, step: str, reason: str) -> None:
        """A step that could not be done leaves the batch where it was before it was asked for."""
        async with self._storage.tenant(shop_id) as session:
            record = await session.get_import_batch(batch_id, for_update=True)
            if record is None or record.status != step:
                return  # another worker has finished the step meanwhile
            await _refuse(session, record, _BACK[step], reason, "import_failed")


async def _refuse(session: TenantSession, record: ImportBatchRecord, status: str, reason: str, text: str) -> None:
    """Put the batch back into a state the person can act on, with the reason, and tell them."""
    await session.set_import_batch(
        record.batch_id,
        status=status,
        summary={**record.summary, "refused": {"step": _STEP[record.status], "reason": reason}},
        plan=record.plan if status == VALIDATED else None,
    )
    await _tell(session, record, text)


async def _write_preview(session: TenantSession, record: ImportBatchRecord, plan: Plan, total: int) -> str:
    """Store what applying would do as it is now. Returns the state that follows from it."""
    matched = sorted({item.customer_id for item in plan.rows if item.customer_id is not None})
    balances = await session.balances(matched)
    token = None if plan.errors else imports.plan_token(plan.rows)
    await session.set_import_preview(
        record.batch_id,
        {
            # Named when applying. Null while a row has a problem: such a file cannot be applied.
            "plan": token,
            "errors": _errors(plan.errors),
            "counts": _counts(plan.rows),
            "rows": [_preview_row(item, plan.customers, balances) for item in plan.rows],
        },
    )
    status = REJECTED if plan.errors else VALIDATED
    await session.set_import_batch(
        record.batch_id,
        status=status,
        summary={
            **{name: value for name, value in record.summary.items() if name != "refused"},
            "rows": total,
            "errors": _errors(plan.errors),
        },
        plan=token,
    )
    return status


async def check_in(session: TenantSession, batch_id: UUID, parsed: ParsedFile | str, now: datetime) -> None:
    """The worker's first step: validate the file in full and build the preview (BR-24: before anything
    is saved). Nothing of the ledger is touched."""
    record = await session.get_import_batch(batch_id, for_update=True)
    if record is None or record.status != UPLOADED:
        return  # discarded meanwhile, or checked by another worker
    if isinstance(parsed, str):
        # The file as a whole cannot be an import. It is of no further use.
        if record.file_id is not None:
            await session.shorten_file_retention(record.file_id, now)
        await session.set_import_batch(
            batch_id, status=REJECTED, summary={**record.summary, "file_problem": parsed, "rows": 0}, plan=None
        )
        await _tell(session, record, "import_unreadable")
        return
    plan = await _plan(session, parsed)
    status = await _write_preview(session, record, plan, parsed.total)
    if status == VALIDATED:
        await _tell(session, record, "import_checked", rows=parsed.total)
    else:
        await _tell(session, record, "import_rejected", errors=len({error.row for error in plan.errors}))


async def apply_in(session: TenantSession, batch_id: UUID, parsed: ParsedFile | str, now: datetime) -> None:
    """Apply a batch that waits to be applied, inside one transaction of its shop.

    All of it or nothing (BR-24): the batch row is locked and its state looked at again, so it is applied
    once; the customers that receive a balance are locked before their accounts are read, like in every
    write to an account. If what would be done is no longer what was confirmed, nothing is applied and
    the batch goes back with a fresh preview.
    """
    record = await session.get_import_batch(batch_id, for_update=True)
    if record is None or record.status != APPLYING or record.step_by is None:
        return  # applied by another worker meanwhile
    if isinstance(parsed, str):
        await _refuse(session, record, VALIDATED, parsed, "import_refused")
        return
    # The customers a row is added to are found, locked, and only then is the plan made that is applied.
    first = await _plan(session, parsed)
    await session.lock_customers(sorted({item.customer_id for item in first.rows if item.customer_id is not None}))
    plan = await _plan(session, parsed)
    if plan.errors or imports.plan_token(plan.rows) != record.plan:
        reason = "errors" if plan.errors else "stale"
        status = await _write_preview(session, record, plan, parsed.total)
        refused = await _batch(session, batch_id)
        await session.set_import_batch(
            batch_id,
            status=status,
            summary={**refused.summary, "refused": {"step": "apply", "reason": reason}},
            plan=refused.plan,
        )
        await _tell(session, record, "import_refused")
        return
    settings = await session.shop_settings()
    if settings is None:
        raise NotFound()

    author = record.step_by
    existing = sorted({item.customer_id for item in plan.rows if item.customer_id is not None})
    next_seq = {customer: seq + 1 for customer, seq in (await session.last_seqs(existing)).items()}
    default_date = default_promise_date(now, settings.default_promise_days)
    new_customers: list[NewImportCustomer] = []
    of_row: dict[int, UUID] = {}
    entries: list[NewImportEntry] = []
    for item in plan.rows:
        row = item.row
        if item.action == imports.CREATE:
            target = uuid4()
            of_row[row.row] = target
            new_customers.append(NewImportCustomer(target, row.name, row.name_norm, row.phone))
        elif item.customer_id is not None:
            target = item.customer_id
        else:
            assert item.first_row is not None
            target = of_row[item.first_row]
        seq = next_seq.get(target, 1)
        next_seq[target] = seq + 1
        entries.append(
            NewImportEntry(
                entry_id=uuid4(),
                customer_id=target,
                seq=seq,
                amount=row.amount,
                note=row.note,
                promised_date=row.promised or default_date,
                promise_actor=STAFF_ACTOR if row.promised is not None else DEFAULT_ACTOR,
            )
        )
    await session.add_import_customers(new_customers)
    await session.add_import_entries(batch_id, author, now, entries)
    await session.record_activity(
        membership_id=author, action="import.applied", subject_type="import", subject_id=batch_id
    )

    # OpeningBalanceImported: a customer who is linked is told, like about any other entry.
    balances = await session.balances(existing)
    for entry in entries:
        if entry.customer_id in plan.customers:
            await notify.opening_imported(
                session,
                entry.customer_id,
                entry_id=entry.entry_id,
                name=plan.customers[entry.customer_id].display_name,
                amount=entry.amount,
                balance=balances.get(entry.customer_id, 0),
                promised=entry.promised_date,
            )
    counts = _counts(plan.rows)
    await _tell(
        session,
        record,
        "s_import_applied",
        owners=True,
        amount=counts["amount"],
        entries=counts["entries"],
        customers=counts["new_customers"],
    )
    if record.file_id is not None:
        # The rows are in the ledger now; the file and the preview are not needed any more.
        await session.shorten_file_retention(record.file_id, now)
    await session.set_import_preview(batch_id, None)
    await session.set_import_batch(
        batch_id,
        status=APPLIED,
        summary={
            **record.summary,
            "applied": counts,
            "created_customers": [str(customer.customer_id) for customer in new_customers],
        },
        plan=None,
        applied_at=now,
    )


async def undo_in(session: TenantSession, batch_id: UUID, now: datetime) -> None:
    """Undo a batch that waits to be undone (BR-24), inside one transaction of its shop.

    The ledger stays insert-only (REQ-N07): every entry of the import that still stands gets a reversal.
    The reversals are written in bulk, and everything a reversal means is done for all of them: its
    activity and measurement rows, the end of a dispute on the entry, the expiry of date requests on
    entries that are thereby reversed or fully paid, the message to a linked customer, and a waiting
    removal request once nothing is owed. If a payment recorded since leaves one customer owing less than
    the import gave them, nothing is undone.
    """
    record = await session.get_import_batch(batch_id, for_update=True)
    if record is None or record.status != UNDOING or record.step_by is None:
        return  # undone by another worker meanwhile
    author = record.step_by
    customers = await session.customers_of_import(batch_id)
    await session.lock_customers(customers)
    standing = await session.standing_entries_of_import(batch_id)  # what was reversed by hand is left out
    # An import records so'm only (a file with a currency column is refused as an unknown column), so
    # what it took and what it gives back are compared in the so'm book alone.
    before = await session.balances(customers)
    taken: dict[UUID, int] = {}
    for _, customer_id, amount, _ in standing:
        taken[customer_id] = taken.get(customer_id, 0) + amount
    if any(before.get(customer_id, 0) < amount for customer_id, amount in taken.items()):
        # A reversal may not take a balance below zero (INV-3): the import's debt has been paid against.
        await _refuse(session, record, APPLIED, "balance_used", "import_undo_refused")
        return

    next_seq = {customer: seq + 1 for customer, seq in (await session.last_seqs(customers)).items()}
    reversals: list[NewReversal] = []
    for entry_id, customer_id, amount, currency in standing:
        reversals.append(NewReversal(uuid4(), entry_id, customer_id, next_seq[customer_id], amount, currency))
        next_seq[customer_id] += 1
    await session.add_reversals(author, now, reversals)
    await session.close_disputes_of([reversal.entry_id for reversal in reversals], author, now)

    after = {customer_id: before.get(customer_id, 0) - amount for customer_id, amount in taken.items()}
    for customer_id in await session.customers_with_open_date_requests(list(taken)):
        account = [row.entry for row in await session.entries_of(customer_id)]
        await expire_settled_date_requests(session, customer_id, account, now)
    linked = set(await session.linked_customers(list(taken)))
    for reversal in reversals:
        if reversal.customer_id in linked:
            customer = await session.get_customer(reversal.customer_id, for_update=False)
            assert customer is not None
            body = {
                "entry": {"id": str(reversal.reversal_id), "amount": reversal.amount},
                "customer": customer_body(customer, after[reversal.customer_id]),
            }
            await notify.entry_reversed(session, reversal.customer_id, body, "opening")
    for customer_id in await session.customers_waiting_removal(list(taken)):
        await removal.complete_if_due(session, customer_id, await owes_anything(session, customer_id), now)

    # BR-24: customers the import created are archived if they now owe nothing, in any currency: one of
    # them may have been sold to in dollars since.
    created = [UUID(text) for text in record.summary.get("created_customers", [])]
    owing = {customer_id for currency in Currency for customer_id in await session.balances(created, currency)}
    archived = await session.archive_customers([customer_id for customer_id in created if customer_id not in owing])
    await session.record_activity(
        membership_id=author, action="import.undone", subject_type="import", subject_id=batch_id
    )
    applied = record.summary.get("applied") or {}
    await _tell(
        session, record, "s_import_undone", owners=True, amount=int(applied.get("amount", 0)), entries=len(reversals)
    )
    await session.set_import_batch(
        batch_id,
        status=UNDONE,
        summary={**record.summary, "undone": {"reversed": len(reversals), "archived": archived}},
        plan=None,
    )
