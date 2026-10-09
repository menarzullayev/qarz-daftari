"""Suppliers and what the shop owes them (module I of the expansion).

A supplier's account is an append-only ledger, each currency a book of its own. Goods received on credit
raise what is owed, a payment lowers it, and a wrong entry is cancelled by a reversal that says why; the
entry itself stays. The balance is kept by the database as the sum of the entries that stand.

A payment may be more than what is owed: shops pay before the goods come, and the balance then shows an
advance. While the cash book is on, a payment is also an expense of the cash book, written in the same
transaction (`stock_cash`).

Behind the platform switch `stock_on`, like the stock itself.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency, stock_cash
from qarz.application.authorization import require_permission
from qarz.application.customers import (
    MAX_PAGE,
    clean_phone,
    decode_cursor,
    encode_cursor,
    require_viewable,
    require_writable,
)
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.operations import Operation, operation
from qarz.application.ports import Membership, Storage, TenantSession
from qarz.application.shops import require_member
from qarz.application.stock_currency import require_currency
from qarz.application.stock_moves import EntryOfDocument, require_on
from qarz.application.stock_ports import SupplierEntryRecord, SupplierRecord
from qarz.domain import permissions
from qarz.domain import suppliers as rules
from qarz.domain.access import Capability
from qarz.domain.cash import GOODS_PURCHASE, Method
from qarz.domain.names import normalize_name
from qarz.domain.promise import tashkent_date

LIST_SUPPLIERS = operation("suppliers.list", Capability.MANAGE)
READ_SUPPLIER = operation("suppliers.read", Capability.MANAGE)
CREATE_SUPPLIER = operation("suppliers.create", Capability.MANAGE)
UPDATE_SUPPLIER = operation("suppliers.update", Capability.MANAGE)
ARCHIVE_SUPPLIER = operation("suppliers.archive", Capability.MANAGE)
UNARCHIVE_SUPPLIER = operation("suppliers.unarchive", Capability.MANAGE)
ADD_ENTRY = operation("suppliers.entries.create", Capability.MANAGE)
CANCEL_ENTRY = operation("suppliers.entries.cancel", Capability.MANAGE)

# What each kind a person records directly needs: paying is one decision, stating an old debt another.
_ENTRY_PERMISSION = {rules.PAYMENT: permissions.SUPPLIERS_PAY, rules.OPENING: permissions.SUPPLIERS_MANAGE}


class SupplierNameTaken(AppError):
    code = "SUPPLIER_NAME_TAKEN"


class SupplierArchived(AppError):
    code = "SUPPLIER_ARCHIVED"


class SupplierHasBalance(AppError):
    """A supplier the shop still owes, or who holds an advance, cannot be archived."""

    code = "SUPPLIER_HAS_BALANCE"


class SupplierEntryRefused(AppError):
    """Base of the refusals to cancel an entry; the code says which."""


def _balances(owed: dict[str, int]) -> list[dict[str, Any]]:
    return [{"currency": currency, "balance": balance} for currency, balance in owed.items()]


def supplier_body(supplier: SupplierRecord, owed: dict[str, int]) -> dict[str, Any]:
    return {
        "id": str(supplier.supplier_id),
        "name": supplier.name,
        "phone": supplier.phone,
        "note": supplier.note,
        "status": supplier.status,
        "balances": _balances(owed),
    }


def entry_body(entry: SupplierEntryRecord) -> dict[str, Any]:
    return {
        "id": str(entry.entry_id),
        "seq": entry.seq,
        "kind": entry.kind,
        "amount": entry.amount,
        "currency": entry.currency,
        "note": entry.note,
        "reverses_id": None if entry.reverses_id is None else str(entry.reverses_id),
        "reversed": entry.is_reversed,
        "document": None
        if entry.document_id is None
        else {"id": str(entry.document_id), "kind": entry.document_kind, "number": entry.document_number},
        "in_cash_book": entry.in_cash_book,
        "author_id": str(entry.author_id),
        "created_at": entry.created_at.isoformat(),
    }


def _clean(name: str, phone: str | None, note: str | None) -> tuple[str, str | None, str | None]:
    fields: dict[str, str] = {}
    clean_name = ""
    clean_note: str | None = None
    try:
        clean_name = rules.supplier_name(name)
    except ValueError as error:
        fields["name"] = str(error)
    try:
        clean_note = rules.note(note)
    except ValueError as error:
        fields["note"] = str(error)
    try:
        phone = clean_phone(phone)
    except ValidationFailed as error:
        fields.update(error.fields)
    if fields:
        raise ValidationFailed(fields)
    return clean_name, phone, clean_note


async def append_entry_in(
    session: TenantSession,
    actor: Membership,
    supplier_id: UUID,
    *,
    kind: str,
    amount: int,
    currency: str,
    note: str | None,
    now: datetime,
    document_id: UUID | None = None,
    reverses_id: UUID | None = None,
) -> UUID:
    """Add one entry to a supplier's account. The caller holds the supplier's row lock, which makes two
    writers to one account take turns for the sequence number."""
    entry_id = uuid4()
    await session.append_supplier_entry(
        entry_id=entry_id,
        supplier_id=supplier_id,
        seq=await session.last_supplier_seq(supplier_id) + 1,
        kind=kind,
        amount=amount,
        currency=currency,
        note=note,
        reverses_id=reverses_id,
        document_id=document_id,
        author_id=actor.membership_id,
        created_at=now,
    )
    return entry_id


async def reverse_entry_in(
    session: TenantSession, actor: Membership, entry: SupplierEntryRecord, *, reason: str, now: datetime
) -> UUID:
    """Cancel an entry by its reversal, and with it the cash-book expense a payment wrote."""
    await stock_cash.cancel_expense(session, actor, reason=reason, now=now, supplier_entry_id=entry.entry_id)
    return await append_entry_in(
        session,
        actor,
        entry.supplier_id,
        kind=rules.REVERSAL,
        amount=entry.amount,
        currency=entry.currency,
        note=reason,
        now=now,
        document_id=entry.document_id,
        reverses_id=entry.entry_id,
    )


async def pay_in(
    session: TenantSession,
    actor: Membership,
    supplier_id: UUID,
    *,
    amount: int,
    currency: str,
    note: str | None,
    now: datetime,
    method: Method | None = None,
    document_id: UUID | None = None,
) -> UUID:
    """A payment to a supplier, with its cash-book expense while the cash book is on."""
    entry_id = await append_entry_in(
        session,
        actor,
        supplier_id,
        kind=rules.PAYMENT,
        amount=amount,
        currency=currency,
        note=note,
        now=now,
        document_id=document_id,
    )
    await stock_cash.record_expense(
        session,
        actor,
        system_key=GOODS_PURCHASE,
        amount=amount,
        currency=currency,
        method=method,
        note=note,
        now=now,
        supplier_entry_id=entry_id,
    )
    return entry_id


class SupplierService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def list(
        self, user_id: UUID, shop_id: UUID, *, query: str | None, status: str, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, LIST_SUPPLIERS)
            await require_viewable(session, actor, self._today())
            fields: dict[str, str] = {}
            if not 1 <= limit <= MAX_PAGE:
                fields["limit"] = f"must be between 1 and {MAX_PAGE}"
            if status not in (rules.ACTIVE, rules.ARCHIVED):
                fields["status"] = "must be active or archived"
            if query is not None and len(query) > 80:
                fields["q"] = "at most 80 characters"
            if fields:
                raise ValidationFailed(fields)
            after: tuple[str, UUID] | None = None
            if cursor:
                name_norm, supplier_id = decode_cursor(cursor, 2)
                try:
                    after = (name_norm, UUID(supplier_id))
                except ValueError as error:
                    raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error
            rows = await session.search_suppliers(
                name_part=normalize_name(query or "") or None, status=status, after=after, limit=limit + 1
            )
            page, more = rows[:limit], len(rows) > limit
            owed = await session.supplier_balances([row.supplier_id for row in page])
            return {
                "suppliers": [supplier_body(row, owed.get(row.supplier_id, {})) for row in page],
                "totals": [
                    {"currency": currency, "owed": total}
                    for currency, total in (await session.supplier_totals()).items()
                ],
                "next_cursor": encode_cursor(page[-1].name_norm, page[-1].supplier_id) if more else None,
            }

    async def detail(
        self, user_id: UUID, shop_id: UUID, supplier_id: UUID, *, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, READ_SUPPLIER)
            await require_viewable(session, actor, self._today())
            if not 1 <= limit <= MAX_PAGE:
                raise ValidationFailed({"limit": f"must be between 1 and {MAX_PAGE}"})
            before: int | None = None
            if cursor:
                (raw,) = decode_cursor(cursor, 1)
                if not raw.isascii() or not raw.isdigit():
                    raise ValidationFailed({"cursor": "not a cursor returned by this API"})
                before = int(raw)
            supplier = await session.get_supplier(supplier_id, for_update=False)
            if supplier is None:
                raise NotFound()
            rows = await session.supplier_entries(supplier_id, before_seq=before, limit=limit + 1)
            page, more = rows[:limit], len(rows) > limit
            owed = await session.supplier_balances([supplier_id])
            return {
                "supplier": supplier_body(supplier, owed.get(supplier_id, {})),
                "entries": [entry_body(row) for row in page],
                "next_cursor": encode_cursor(page[-1].seq) if more else None,
            }

    async def create(
        self, user_id: UUID, shop_id: UUID, *, name: str, phone: str | None, note: str | None, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, CREATE_SUPPLIER)
            key = idempotency.validate_key(request_key)
            clean_name, clean_tel, clean_note = _clean(name, phone, note)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                supplier = await session.insert_supplier(
                    supplier_id=uuid4(),
                    name=clean_name,
                    name_norm=normalize_name(clean_name),
                    phone=clean_tel,
                    note=clean_note,
                )
                if supplier is None:
                    raise SupplierNameTaken()
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="supplier.created",
                    subject_type="supplier",
                    subject_id=supplier.supplier_id,
                )
                return supplier_body(supplier, {})

            return await idempotency.run_once(
                session,
                key=key,
                operation=CREATE_SUPPLIER.name,
                user_id=user_id,
                request={"name": clean_name, "phone": clean_tel, "note": clean_note},
                action=apply,
            )

    async def update(
        self,
        user_id: UUID,
        shop_id: UUID,
        supplier_id: UUID,
        *,
        name: str,
        phone: str | None,
        note: str | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        """Replace the supplier's name, phone and note as a whole. The account is untouched."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, UPDATE_SUPPLIER)
            key = idempotency.validate_key(request_key)
            clean_name, clean_tel, clean_note = _clean(name, phone, note)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                if await session.get_supplier(supplier_id, for_update=True) is None:
                    raise NotFound()
                updated = await session.update_supplier(
                    supplier_id,
                    name=clean_name,
                    name_norm=normalize_name(clean_name),
                    phone=clean_tel,
                    note=clean_note,
                )
                if updated is None:
                    raise SupplierNameTaken()
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="supplier.updated",
                    subject_type="supplier",
                    subject_id=supplier_id,
                )
                owed = await session.supplier_balances([supplier_id])
                return supplier_body(updated, owed.get(supplier_id, {}))

            return await idempotency.run_once(
                session,
                key=key,
                operation=UPDATE_SUPPLIER.name,
                user_id=user_id,
                request={"supplier": str(supplier_id), "name": clean_name, "phone": clean_tel, "note": clean_note},
                action=apply,
            )

    async def set_archived(
        self, user_id: UUID, shop_id: UUID, supplier_id: UUID, *, archived: bool, request_key: str | None
    ) -> dict[str, Any]:
        op: Operation = ARCHIVE_SUPPLIER if archived else UNARCHIVE_SUPPLIER
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, op)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                supplier = await session.get_supplier(supplier_id, for_update=True)
                if supplier is None:
                    raise NotFound()
                owed = (await session.supplier_balances([supplier_id])).get(supplier_id, {})
                if archived and owed:
                    # Like a customer with a debt: an account that is not settled stays in sight.
                    raise SupplierHasBalance()
                wanted = rules.ARCHIVED if archived else rules.ACTIVE
                if supplier.status != wanted:
                    supplier = await session.set_supplier_status(supplier_id, wanted)
                    await session.record_activity(
                        membership_id=actor.membership_id,
                        action="supplier.archived" if archived else "supplier.unarchived",
                        subject_type="supplier",
                        subject_id=supplier_id,
                    )
                return supplier_body(supplier, owed)

            return await idempotency.run_once(
                session,
                key=key,
                operation=op.name,
                user_id=user_id,
                request={"supplier": str(supplier_id)},
                action=apply,
            )

    async def add_entry(
        self,
        user_id: UUID,
        shop_id: UUID,
        supplier_id: UUID,
        *,
        kind: str,
        amount: int,
        currency: str | None,
        note: str | None,
        method: str | None = None,
        request_key: str | None,
    ) -> dict[str, Any]:
        """Record a payment to the supplier, or what the shop already owed them (an opening balance)."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, ADD_ENTRY)
            key = idempotency.validate_key(request_key)
            if kind not in rules.DIRECT_KINDS:
                raise ValidationFailed({"kind": "must be payment or opening"})
            require_permission(actor, _ENTRY_PERMISSION[kind])
            fields: dict[str, str] = {}
            clean_note: str | None = None
            try:
                rules.amount(amount)
            except ValueError as error:
                fields["amount"] = str(error)
            try:
                clean_note = rules.note(note)
            except ValueError as error:
                fields["note"] = str(error)
            if fields:
                raise ValidationFailed(fields)
            paid_by = stock_cash.clean_method(method)
            if paid_by is not None and kind != rules.PAYMENT:
                raise ValidationFailed({"method": "only a payment has a method"})
            money = await require_currency(session, currency)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                supplier = await session.get_supplier(supplier_id, for_update=True)
                if supplier is None:
                    raise NotFound()
                if supplier.status == rules.ARCHIVED:
                    raise SupplierArchived()
                now = self._now()
                if kind == rules.PAYMENT:
                    entry_id = await pay_in(
                        session,
                        actor,
                        supplier_id,
                        amount=amount,
                        currency=money,
                        note=clean_note,
                        now=now,
                        method=paid_by,
                    )
                else:
                    entry_id = await append_entry_in(
                        session, actor, supplier_id, kind=kind, amount=amount, currency=money, note=clean_note, now=now
                    )
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action=f"supplier.{kind}_recorded",
                    subject_type="supplier",
                    subject_id=supplier_id,
                    detail={"amount": amount, "currency": money},
                )
                entry = await session.get_supplier_entry(entry_id)
                assert entry is not None
                owed = await session.supplier_balances([supplier_id])
                return {"entry": entry_body(entry), "supplier": supplier_body(supplier, owed.get(supplier_id, {}))}

            return await idempotency.run_once(
                session,
                key=key,
                operation=ADD_ENTRY.name,
                user_id=user_id,
                request={
                    "supplier": str(supplier_id),
                    "kind": kind,
                    "amount": amount,
                    "currency": money,
                    "note": clean_note,
                    "method": None if paid_by is None else paid_by.value,
                },
                action=apply,
            )

    async def cancel_entry(
        self, user_id: UUID, shop_id: UUID, supplier_id: UUID, entry_id: UUID, *, reason: str, request_key: str | None
    ) -> dict[str, Any]:
        """Cancel a payment or an opening balance, saying why. An entry a document wrote is cancelled by
        cancelling the document."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, CANCEL_ENTRY)
            key = idempotency.validate_key(request_key)
            try:
                why = rules.reason(reason)
            except ValueError as error:
                raise ValidationFailed({"reason": str(error)}) from error
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                supplier = await session.get_supplier(supplier_id, for_update=True)
                if supplier is None:
                    raise NotFound()
                entry = await session.get_supplier_entry(entry_id)
                if entry is None or entry.supplier_id != supplier_id:
                    raise NotFound()
                refusal = rules.may_cancel(
                    entry.kind, is_reversed=entry.is_reversed, from_document=entry.document_id is not None
                )
                if refusal == "ENTRY_OF_DOCUMENT":
                    raise EntryOfDocument()
                if refusal is not None:
                    error = SupplierEntryRefused()
                    error.code = refusal
                    raise error
                require_permission(actor, _ENTRY_PERMISSION[entry.kind])
                now = self._now()
                reversal_id = await reverse_entry_in(session, actor, entry, reason=why, now=now)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="supplier.entry_cancelled",
                    subject_type="supplier",
                    subject_id=supplier_id,
                    detail={"amount": entry.amount, "currency": entry.currency, "kind": entry.kind},
                )
                reversal = await session.get_supplier_entry(reversal_id)
                assert reversal is not None
                owed = await session.supplier_balances([supplier_id])
                return {"entry": entry_body(reversal), "supplier": supplier_body(supplier, owed.get(supplier_id, {}))}

            return await idempotency.run_once(
                session,
                key=key,
                operation=CANCEL_ENTRY.name,
                user_id=user_id,
                request={"supplier": str(supplier_id), "entry": str(entry_id), "reason": why},
                action=apply,
            )
