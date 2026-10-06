"""Recording credit sales and payments, reversals, customer detail and the shop overview.

Balances, allocation, overdue status and the payment history indicator come from the pure functions in
`qarz.domain.ledger`; this module loads an account, asks the domain whether a new entry may be added, and
stores it. Every write locks the customer row first, so two sales to one customer cannot interleave.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency
from qarz.application.customers import (
    MAX_PAGE,
    CustomerArchived,
    customer_body,
    decode_cursor,
    encode_cursor,
    require_viewable,
    require_writable,
)
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.operations import operation
from qarz.application.ports import EntryRow, Storage
from qarz.application.shops import require_member
from qarz.domain import ledger
from qarz.domain.access import Capability
from qarz.domain.ledger import EntryKind, Refusal
from qarz.domain.promise import default_promise_date, tashkent_date, validate_promise_date

RECORD_ENTRY = operation("ledger.entry.create", Capability.RECORD)
REVERSE_ENTRY = operation("ledger.entry.reverse", Capability.MANAGE)
READ_CUSTOMER = operation("customers.read", Capability.RECORD)
READ_OVERVIEW = operation("overview.read", Capability.RECORD)
LIST_DEBTORS = operation("overview.debtors", Capability.RECORD)

MIN_AMOUNT = 100
MAX_AMOUNT = 100_000_000
HISTORY_PAGE = 100

_REFUSAL_CODES = {
    Refusal.EXCEEDS_BALANCE: "EXCEEDS_BALANCE",
    Refusal.ALREADY_REVERSED: "ALREADY_REVERSED",
    Refusal.REVERSAL_OF_REVERSAL: "CANNOT_REVERSE_REVERSAL",
    Refusal.NEGATIVE_BALANCE: "WOULD_GO_NEGATIVE",
}


class LedgerRefused(AppError):
    """The domain rules do not allow this entry. The code says which rule."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__()


def _refuse(refusal: Refusal) -> AppError:
    code = _REFUSAL_CODES.get(refusal)
    if code is None:
        # Anything else means the request itself was malformed.
        return ValidationFailed({"entry": refusal.value})
    return LedgerRefused(code)


def _entry_body(row: EntryRow, reversed_ids: set[UUID]) -> dict[str, Any]:
    entry = row.entry
    return {
        "id": str(entry.id),
        "seq": entry.seq,
        "kind": entry.kind.value,
        "amount": entry.amount,
        "note": row.note,
        "created_at": entry.created_at.isoformat(),
        "promised_date": None if entry.promised_date is None else entry.promised_date.isoformat(),
        "reverses_id": None if entry.reverses_id is None else str(entry.reverses_id),
        "reversed": entry.id in reversed_ids,
        "disputed": entry.disputed,
        "author_id": str(row.author_id),
    }


def _overdue_body(status: ledger.OverdueStatus) -> dict[str, Any]:
    return {
        "amount": status.overdue_amount,
        "since": None
        if status.earliest_unmet_promised_date is None
        else status.earliest_unmet_promised_date.isoformat(),
        "days": status.days_overdue,
        "due_today": status.due_today_amount,
    }


class LedgerService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def record(
        self,
        user_id: UUID,
        shop_id: UUID,
        customer_id: UUID,
        *,
        kind: str,
        amount: int,
        note: str | None,
        promised_date: date | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, RECORD_ENTRY)
            key = idempotency.validate_key(request_key)

            fields: dict[str, str] = {}
            if kind not in ("credit", "payment"):
                fields["kind"] = "must be credit or payment"
            if isinstance(amount, bool) or not MIN_AMOUNT <= amount <= MAX_AMOUNT:
                fields["amount"] = f"a whole amount between {MIN_AMOUNT} and {MAX_AMOUNT} UZS"
            text = " ".join(note.split()) if note else None
            if text is not None and len(text) > 200:
                fields["note"] = "at most 200 characters"
            if kind == "payment" and promised_date is not None:
                fields["promised_date"] = "only a credit sale has a promised date"
            if fields:
                raise ValidationFailed(fields)
            entry_kind = EntryKind(kind)
            await require_writable(session, self._today(), new_credit=entry_kind is EntryKind.CREDIT)

            async def apply() -> dict[str, Any]:
                now = self._now()
                customer = await session.get_customer(customer_id, for_update=True)
                if customer is None or customer.status == "anonymized":
                    raise NotFound()
                if customer.status == "archived":
                    raise CustomerArchived()

                account = await session.entries_of(customer_id)
                refusal = ledger.validate_new_entry([row.entry for row in account], entry_kind, amount)
                if refusal is not None:
                    raise _refuse(refusal)

                promised: date | None = None
                if entry_kind is EntryKind.CREDIT:
                    sale_date = tashkent_date(now)
                    if promised_date is None:
                        settings = await session.shop_settings()
                        if settings is None:
                            raise NotFound()
                        promised = default_promise_date(now, settings.default_promise_days)
                    else:
                        problem = validate_promise_date(sale_date, promised_date)
                        if problem is not None:
                            raise ValidationFailed({"promised_date": problem.value})
                        promised = promised_date

                entry_id = uuid4()
                seq = max((row.entry.seq for row in account), default=0) + 1
                await session.append_entry(
                    entry_id=entry_id,
                    customer_id=customer_id,
                    seq=seq,
                    kind=entry_kind.value,
                    amount=amount,
                    note=text,
                    reverses_id=None,
                    author_id=actor.membership_id,
                    created_at=now,
                )
                if promised is not None:
                    await session.add_promise(entry_id=entry_id, promised_date=promised, actor="staff", created_at=now)
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action=f"ledger.{entry_kind.value}_recorded",
                    subject_type="customer",
                    subject_id=customer_id,
                )
                await session.record_measure(
                    kind=entry_kind.value, entry_ref=entry_id, amount=amount, promised=promised
                )

                balance = ledger.balance([row.entry for row in account]) + (
                    amount if entry_kind is EntryKind.CREDIT else -amount
                )
                return {
                    "entry": {
                        "id": str(entry_id),
                        "seq": seq,
                        "kind": entry_kind.value,
                        "amount": amount,
                        "note": text,
                        "created_at": now.isoformat(),
                        "promised_date": None if promised is None else promised.isoformat(),
                    },
                    "customer": customer_body(customer, balance),
                }

            return await idempotency.run_once(
                session,
                key=key,
                operation=RECORD_ENTRY.name,
                user_id=user_id,
                request={
                    "customer": str(customer_id),
                    "kind": kind,
                    "amount": amount,
                    "note": text,
                    "promised_date": promised_date,
                },
                action=apply,
            )

    async def reverse(self, user_id: UUID, shop_id: UUID, entry_id: UUID, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, REVERSE_ENTRY)
            key = idempotency.validate_key(request_key)
            # A reversal is allowed in limited mode (BR-29); only a suspended shop is refused.
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                now = self._now()
                customer_id = await session.customer_of_entry(entry_id)
                if customer_id is None:
                    raise NotFound()
                customer = await session.get_customer(customer_id, for_update=True)
                if customer is None:
                    raise NotFound()
                account = await session.entries_of(customer_id)
                original = next((row.entry for row in account if row.entry.id == entry_id), None)
                if original is None:
                    raise NotFound()
                refusal = ledger.validate_new_entry(
                    [row.entry for row in account], EntryKind.REVERSAL, original.amount, entry_id
                )
                if refusal is not None:
                    raise _refuse(refusal)

                reversal_id = uuid4()
                seq = max(row.entry.seq for row in account) + 1
                await session.append_entry(
                    entry_id=reversal_id,
                    customer_id=customer_id,
                    seq=seq,
                    kind=EntryKind.REVERSAL.value,
                    amount=original.amount,
                    note=None,
                    reverses_id=entry_id,
                    author_id=actor.membership_id,
                    created_at=now,
                )
                await session.record_activity(
                    membership_id=actor.membership_id,
                    action="ledger.entry_reversed",
                    subject_type="customer",
                    subject_id=customer_id,
                )
                await session.record_measure(
                    kind="reversal", entry_ref=reversal_id, amount=original.amount, promised=None
                )

                debt_increasing = original.kind in (EntryKind.CREDIT, EntryKind.OPENING)
                balance = ledger.balance([row.entry for row in account]) + (
                    -original.amount if debt_increasing else original.amount
                )
                return {
                    "entry": {
                        "id": str(reversal_id),
                        "seq": seq,
                        "kind": "reversal",
                        "amount": original.amount,
                        "reverses_id": str(entry_id),
                        "created_at": now.isoformat(),
                    },
                    "customer": customer_body(customer, balance),
                }

            return await idempotency.run_once(
                session,
                key=key,
                operation=REVERSE_ENTRY.name,
                user_id=user_id,
                request={"entry": str(entry_id)},
                action=apply,
            )

    async def customer_detail(self, user_id: UUID, shop_id: UUID, customer_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_CUSTOMER)
            await require_viewable(session, actor, self._today())
            customer = await session.get_customer(customer_id, for_update=False)
            if customer is None or customer.status == "anonymized":
                raise NotFound()
            account = await session.entries_of(customer_id)
            entries = [row.entry for row in account]
            today = self._today()
            history = ledger.payment_history(entries, today)
            reversed_ids = {row.entry.reverses_id for row in account if row.entry.reverses_id is not None}
            newest_first = sorted(account, key=lambda row: row.entry.seq, reverse=True)
            return {
                **customer_body(customer, ledger.balance(entries)),
                "overdue": _overdue_body(ledger.overdue(entries, today)),
                # Derived from this shop's records only and shown to its staff only (REQ-045).
                "payment_history": None
                if history is None
                else {
                    "on_time_percent": history.on_time_percent,
                    "on_time_amount": history.on_time_amount,
                    "due_amount": history.due_amount,
                    "longest_delay_days": history.longest_delay_days,
                },
                "entries": [_entry_body(row, reversed_ids) for row in newest_first[:HISTORY_PAGE]],
                "entries_total": len(account),
            }

    async def overview(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, READ_OVERVIEW)
            await require_viewable(session, actor, self._today())
            totals = await session.shop_totals(self._today())
            return {
                "outstanding": totals.outstanding,
                "debtors": totals.debtors,
                "overdue": {"amount": totals.overdue_amount, "customers": totals.overdue_customers},
                "due_today": totals.due_today_amount,
            }

    async def debtors(
        self, user_id: UUID, shop_id: UUID, *, only_overdue: bool, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_DEBTORS)
            await require_viewable(session, actor, self._today())
            if not 1 <= limit <= MAX_PAGE:
                raise ValidationFailed({"limit": f"must be between 1 and {MAX_PAGE}"})
            before: tuple[int, UUID] | None = None
            if cursor:
                balance_text, customer_id = decode_cursor(cursor, 2)
                try:
                    before = (int(balance_text), UUID(customer_id))
                except ValueError as error:
                    raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error

            today = self._today()
            rows = await session.debtors_page(today=today, only_overdue=only_overdue, before=before, limit=limit + 1)
            page, more = rows[:limit], len(rows) > limit
            return {
                "items": [
                    {
                        **customer_body(customer, figures.balance),
                        "overdue": {
                            "amount": figures.overdue_amount,
                            "since": None if figures.overdue_since is None else figures.overdue_since.isoformat(),
                            "days": 0 if figures.overdue_since is None else (today - figures.overdue_since).days,
                            "due_today": figures.due_today_amount,
                        },
                    }
                    for customer, figures in page
                ],
                "next_cursor": encode_cursor(page[-1][1].balance, page[-1][0].customer_id) if more else None,
            }
