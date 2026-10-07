"""Import of customers with opening balances from a spreadsheet (REQ-062, REQ-063; BR-24; DOM-017).

A batch goes Uploaded → Validated → Applied → Undone, or Validated → Discarded. The uploaded file is the
only place the rows are kept: it is read again for the preview and for applying, and deleted once the
batch is applied or discarded, or after thirty days. The batch itself holds counts, problem codes with
row numbers, and identifiers.

Applying is one transaction: every opening balance and every new customer of the file, or none (BR-24).
What it will do is decided again at that moment and compared with the plan the caller was shown, so a
customer added or renamed since the preview can never be merged with a row unseen.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency, notify
from qarz.application.chat_texts import money, say
from qarz.application.customers import require_viewable, require_writable
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.files import CheckedFile, FileService, StagedFile
from qarz.application.ledger_service import DEFAULT_ACTOR, STAFF_ACTOR, LedgerRefused, reverse_entry_in
from qarz.application.operations import operation
from qarz.application.ports import (
    ImportBatchRecord,
    ImportCandidate,
    Membership,
    NewImportCustomer,
    NewImportEntry,
    Storage,
    StoredFileRecord,
    TenantSession,
)
from qarz.application.shops import refuse_suspended, require_member
from qarz.domain import imports
from qarz.domain.access import Capability
from qarz.domain.imports import Candidate, FileProblem, ParsedFile, PlannedRow, RowError
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
UPLOADED, VALIDATED, APPLIED, UNDONE, DISCARDED = "uploaded", "validated", "applied", "undone", "discarded"
OWNERS = ("owner",)


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
        # Until when the whole import can still be undone (BR-24); null when there is nothing to undo.
        "undo_until": (applied + imports.UNDO_WINDOW).isoformat()
        if applied is not None and record.status == APPLIED
        else None,
        "rows": summary.get("rows", 0),
        "errors": summary.get("errors", []),
        "applied": summary.get("applied"),
        "undone": summary.get("undone"),
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


async def _tell_owner(session: TenantSession, batch_id: UUID, key: str, **values: Any) -> None:
    """ImportApplied and ImportUndone are told to the owner (technical specification, "Events")."""
    settings = await session.shop_settings()
    shop = "" if settings is None else settings.name
    amount = int(values.pop("amount"))
    for tg_id, lang in await session.staff_recipients(list(OWNERS)):
        await session.enqueue(
            recipient=str(tg_id),
            payload={"text": say(lang, key, shop=shop, amount=money(lang, amount), **values)},
            dedupe_key=f"import:{batch_id}:{key}:{tg_id}",
        )


class ImportService:
    def __init__(self, storage: Storage, files: FileService, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._files = files
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def _content(self, record: StoredFileRecord | None) -> ParsedFile | None:
        """The rows of a batch, read from its file. Called outside any transaction: the store is a network call.

        None when the file is no longer there: it was deleted when its thirty days ran out.
        """
        if record is None:
            return None
        try:
            data = await self._files.content(record)
        except NotFound:
            return None
        parsed = imports.parse(data, self._today())
        return None if isinstance(parsed, FileProblem) else parsed

    # --- the template and the list --------------------------------------------------------------------

    async def template(self, user_id: UUID, shop_id: UUID) -> bytes:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, IMPORT_TEMPLATE)
            await require_viewable(session, actor, self._today())
        return imports.template(await self._storage.user_language(user_id) or "uz")

    async def list(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_IMPORTS)
            await require_viewable(session, actor, self._today())
            return {"items": [batch_body(record) for record in await session.list_import_batches(LIST_LIMIT)]}

    # --- upload ---------------------------------------------------------------------------------------

    async def may_upload(self, user_id: UUID, shop_id: UUID, request_key: str | None) -> None:
        """Everything that can refuse an upload without the file. Called before the body is read."""
        async with self._storage.tenant(shop_id) as session:
            await self._authorize_upload(session, user_id, request_key)

    async def _authorize_upload(
        self, session: TenantSession, user_id: UUID, request_key: str | None
    ) -> tuple[Membership, str]:
        actor = await require_member(session, user_id, UPLOAD_IMPORT)
        key = idempotency.validate_key(request_key)
        # BR-29: an import is refused in limited mode, like a new credit sale; BR-30: and in a suspended shop.
        await require_writable(session, self._today(), new_credit=True)
        return actor, key

    async def upload(self, user_id: UUID, shop_id: UUID, data: bytes, request_key: str | None) -> dict[str, Any]:
        await self.may_upload(user_id, shop_id, request_key)
        parsed = imports.parse(data, self._today())
        if isinstance(parsed, FileProblem):
            # Not a file an import can be made of: nothing of it is kept.
            raise ValidationFailed({"file": parsed.value})
        staged = await self._files.stage(CheckedFile(imports.MIMES[parsed.kind], data))
        recorded = False
        try:
            async with self._storage.tenant(shop_id) as session:
                actor, key = await self._authorize_upload(session, user_id, request_key)

                async def apply() -> dict[str, Any]:
                    nonlocal recorded
                    body = await self._record(session, actor, parsed, staged)
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

    async def _record(
        self, session: TenantSession, actor: Membership, parsed: ParsedFile, staged: StagedFile
    ) -> dict[str, Any]:
        now = self._now()
        plan = await _plan(session, parsed)
        batch_id = uuid4()
        file_id = await FileService.record_in(
            session, staged, purpose="import", now=now, delete_after=now + FILE_RETENTION
        )
        await session.create_import_batch(
            batch_id=batch_id,
            status=UPLOADED if plan.errors else VALIDATED,
            file_id=file_id,
            summary={"format": parsed.kind, "rows": parsed.total, "errors": _errors(plan.errors)},
            author_id=actor.membership_id,
            now=now,
        )
        await session.record_activity(
            membership_id=actor.membership_id, action="import.uploaded", subject_type="import", subject_id=batch_id
        )
        record = await session.get_import_batch(batch_id, for_update=False)
        assert record is not None
        return batch_body(record)

    # --- preview --------------------------------------------------------------------------------------

    async def read(self, user_id: UUID, shop_id: UUID, batch_id: UUID) -> dict[str, Any]:
        """The batch and, while it can still be applied or corrected, what applying it would do."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_IMPORT)
            await require_viewable(session, actor, self._today())
            record = await session.get_import_batch(batch_id, for_update=False)
            if record is None:
                raise NotFound()
            file_id = record.file_id if record.status in (UPLOADED, VALIDATED) else None
            stored = None if file_id is None else await session.get_stored_file(file_id)
        parsed = await self._content(stored)
        if parsed is None:
            return {**batch_body(record), "preview": None}
        async with self._storage.tenant(shop_id) as session:
            # Asked again: the first transaction has ended, and with it everything it established.
            actor = await require_member(session, user_id, READ_IMPORT)
            await require_viewable(session, actor, self._today())
            plan = await _plan(session, parsed)
            matched = sorted({item.customer_id for item in plan.rows if item.customer_id is not None})
            balances = await session.balances(matched)
        return {
            **batch_body(record),
            "preview": {
                # Named when applying. Null while a row has a problem: such a file cannot be applied.
                "plan": None if plan.errors else imports.plan_token(plan.rows),
                "errors": _errors(plan.errors),
                "counts": _counts(plan.rows),
                "rows": [_preview_row(item, plan.customers, balances) for item in plan.rows],
            },
        }

    # --- apply ----------------------------------------------------------------------------------------

    async def apply(
        self, user_id: UUID, shop_id: UUID, batch_id: UUID, plan_token: str, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await self._authorize_apply(session, user_id, request_key)
            record = await session.get_import_batch(batch_id, for_update=False)
            if record is None:
                raise NotFound()
            file_id = record.file_id if record.status == VALIDATED else None
            stored = None if file_id is None else await session.get_stored_file(file_id)
        parsed = await self._content(stored)
        async with self._storage.tenant(shop_id) as session:
            actor, key = await self._authorize_apply(session, user_id, request_key)

            async def apply() -> dict[str, Any]:
                return await apply_in(session, actor, batch_id, parsed, plan_token, self._now())

            return await idempotency.run_once(
                session,
                key=key,
                operation=APPLY_IMPORT.name,
                user_id=user_id,
                request={"import": str(batch_id), "plan": plan_token},
                action=apply,
            )

    async def _authorize_apply(
        self, session: TenantSession, user_id: UUID, request_key: str | None
    ) -> tuple[Membership, str]:
        actor = await require_member(session, user_id, APPLY_IMPORT)
        key = idempotency.validate_key(request_key)
        await require_writable(session, self._today(), new_credit=True)
        return actor, key

    # --- undo and discard -----------------------------------------------------------------------------

    async def undo(self, user_id: UUID, shop_id: UUID, batch_id: UUID, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, UNDO_IMPORT)
            key = idempotency.validate_key(request_key)
            # Undoing is a set of reversals, which limited mode allows (BR-29); a suspended shop changes nothing.
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                return await undo_in(session, actor, batch_id, self._now())

            return await idempotency.run_once(
                session,
                key=key,
                operation=UNDO_IMPORT.name,
                user_id=user_id,
                request={"import": str(batch_id)},
                action=apply,
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
                if record.status not in (UPLOADED, VALIDATED):
                    raise ImportNotApplicable({"reason": record.status})
                now = self._now()
                if record.file_id is not None:
                    await session.shorten_file_retention(record.file_id, now)
                await session.set_import_batch(batch_id, status=DISCARDED, summary=record.summary, applied_at=None)
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


async def apply_in(
    session: TenantSession,
    actor: Membership,
    batch_id: UUID,
    parsed: ParsedFile | None,
    plan_token: str,
    now: datetime,
) -> dict[str, Any]:
    """Apply a validated batch inside a tenant transaction the caller has opened and authorized.

    All of it or nothing (BR-24): the batch row is locked, so it is applied once; the customers that
    receive a balance are locked before their accounts are read, like in every write to an account.
    """
    record = await session.get_import_batch(batch_id, for_update=True)
    if record is None:
        raise NotFound()
    if record.status != VALIDATED:
        raise ImportNotApplicable({"reason": record.status})
    if parsed is None:
        raise ImportNotApplicable({"reason": "file_gone"})
    # The customers a row is added to are found, locked, and only then is the plan made that is applied.
    first = await _plan(session, parsed)
    await session.lock_customers(sorted({item.customer_id for item in first.rows if item.customer_id is not None}))
    plan = await _plan(session, parsed)
    if plan.errors:
        raise ImportNotApplicable({"reason": "errors"})
    if imports.plan_token(plan.rows) != plan_token:
        raise ImportNotApplicable({"reason": "stale"})
    settings = await session.shop_settings()
    if settings is None:
        raise NotFound()

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
    await session.add_import_entries(batch_id, actor.membership_id, now, entries)
    await session.record_activity(
        membership_id=actor.membership_id, action="import.applied", subject_type="import", subject_id=batch_id
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
    await _tell_owner(
        session,
        batch_id,
        "s_import_applied",
        amount=counts["amount"],
        entries=counts["entries"],
        customers=counts["new_customers"],
    )
    if record.file_id is not None:
        # The rows are in the ledger now; the file is not needed any more.
        await session.shorten_file_retention(record.file_id, now)
    await session.set_import_batch(
        batch_id,
        status=APPLIED,
        summary={
            **record.summary,
            "applied": counts,
            "created_customers": [str(customer.customer_id) for customer in new_customers],
        },
        applied_at=now,
    )
    return batch_body(await _batch(session, batch_id))


async def undo_in(session: TenantSession, actor: Membership, batch_id: UUID, now: datetime) -> dict[str, Any]:
    """Undo an applied import within 24 hours (BR-24), inside a transaction the caller has authorized.

    The ledger stays insert-only (REQ-N07): every entry of the import that still stands is reversed, by
    the same reversal as any other entry, so disputes, date requests and messages follow as usual. If one
    of them cannot be reversed, because a payment has since been recorded against it, nothing is undone.
    """
    record = await session.get_import_batch(batch_id, for_update=True)
    if record is None:
        raise NotFound()
    refusal = imports.may_undo(record.status, record.applied_at, now)
    if refusal is not None:
        raise ImportUndoRefused({"reason": refusal.value})
    reversed_count = 0
    for entry_id, _, already in await session.entries_of_import(batch_id):
        if already:
            continue  # reversed by hand since; it is cancelled already
        try:
            await reverse_entry_in(session, actor, entry_id, now=now)
        except LedgerRefused:
            raise ImportUndoRefused({"reason": "balance_used"}) from None
        reversed_count += 1

    # BR-24: customers the import created are archived if they now owe nothing.
    created = [UUID(text) for text in record.summary.get("created_customers", [])]
    balances = await session.balances(created)
    archived = 0
    for customer_id in created:
        customer = await session.get_customer(customer_id, for_update=True)
        if customer is not None and customer.status == "active" and balances.get(customer_id, 0) == 0:
            await session.set_customer_status(customer_id, "archived")
            archived += 1
    await session.record_activity(
        membership_id=actor.membership_id, action="import.undone", subject_type="import", subject_id=batch_id
    )
    applied = record.summary.get("applied") or {}
    await _tell_owner(
        session, batch_id, "s_import_undone", amount=int(applied.get("amount", 0)), entries=reversed_count
    )
    await session.set_import_batch(
        batch_id,
        status=UNDONE,
        summary={**record.summary, "undone": {"reversed": reversed_count, "archived": archived}},
        applied_at=None,
    )
    return batch_body(await _batch(session, batch_id))
