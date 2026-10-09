"""Date change requests (REQ-066, REQ-067; domain rule BR-15).

A linked customer asks to move the promised date of one of their own credit entries. Nothing changes
until a manager or owner accepts (INV-15): the accepted date then becomes the entry's current promise,
and the date it replaces stays in the history. A request also ends by being declined, or by itself when
its entry is reversed or fully paid (`qarz.application.ledger_service.expire_settled_date_requests`).
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency, notify
from qarz.application.authorization import holders
from qarz.application.chat_texts import day, money, say
from qarz.application.customer_account import resolve_link
from qarz.application.customers import require_viewable, require_writable
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.ledger_service import CUSTOMER_REQUEST_ACTOR, date_request_body
from qarz.application.operations import operation, self_operation
from qarz.application.ports import DateRequestRecord, Membership, Storage, TenantSession
from qarz.application.shops import require_member
from qarz.domain import ledger, permissions
from qarz.domain.access import Capability
from qarz.domain.date_requests import DateRequestRefusal, may_request, reason_fits, tidy_reason
from qarz.domain.promise import PromiseDateError, tashkent_date

OPEN_DATE_REQUEST = self_operation("me.accounts.date_requests.open")
LIST_DATE_REQUESTS = operation("date_requests.list", Capability.MANAGE)
ACCEPT_DATE_REQUEST = operation("date_requests.accept", Capability.MANAGE)
DECLINE_DATE_REQUEST = operation("date_requests.decline", Capability.MANAGE)

ACCEPT_ACTION, DECLINE_ACTION = "dok", "dno"


class RequestAlreadyOpen(AppError):
    """One request may be open per entry (BR-15)."""

    code = "REQUEST_ALREADY_OPEN"


class DateRequestNotAllowed(AppError):
    code = "DATE_REQUEST_NOT_ALLOWED"


def staff_body(record: DateRequestRecord) -> dict[str, Any]:
    """A request as the shop sees it: with whose it is, the amount, and the date it would replace."""
    return {
        **date_request_body(record),
        "customer_id": str(record.customer_id),
        "customer_name": record.customer_name,
        "amount": record.amount,
        "promised_date": None if record.promised_date is None else record.promised_date.isoformat(),
    }


def clean_reason(raw: str | None) -> str | None:
    reason = tidy_reason(raw)
    if not reason_fits(reason):
        raise ValidationFailed({"reason": "at most 300 characters"})
    return reason


async def _tell_managers(session: TenantSession, record: DateRequestRecord, previous: date) -> None:
    """The owner and the managers decide (REQ-067): each is told in their own language, with both buttons."""
    settings = await session.shop_settings()
    shop = "" if settings is None else settings.name
    for tg_id, lang in holders(await session.staff_contacts(), permissions.PROMISES_CHANGE):
        text = say(
            lang,
            "s_date_request",
            shop=shop,
            name=record.customer_name,
            amount=money(lang, record.amount),
            old=day(previous),
            date=day(record.requested_date),
        )
        buttons = [
            {"text": say(lang, label), "callback_data": f"v2:{action}:{record.request_id.hex}"}
            for label, action in (("accept_button", ACCEPT_ACTION), ("decline_button", DECLINE_ACTION))
        ]
        await session.enqueue(
            recipient=str(tg_id),
            payload={
                "text": notify.with_reason(lang, text, record.reason),
                "reply_markup": {"inline_keyboard": [buttons]},
            },
            dedupe_key=f"date-request:{record.request_id}:opened:{tg_id}",
        )


class DateRequestService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    # --- the customer ---------------------------------------------------------------------------------

    async def open(
        self, user_id: UUID, link_id: UUID, entry_id: UUID, requested_date: date, reason: str | None
    ) -> dict[str, Any]:
        shop_id, customer_id = await resolve_link(self._storage, user_id, link_id)
        text = clean_reason(reason)
        now = self._now()
        async with self._storage.tenant(shop_id) as session:
            # Nobody could decide the request in a suspended shop (BR-30); limited mode does not matter.
            await require_writable(session, self._today(), new_credit=False)
            # Locked as in every write to the account: a payment or a reversal recorded at the same
            # moment is seen either before or after, never in between.
            customer = await session.get_customer(customer_id, for_update=True)
            if customer is None:
                raise NotFound()
            account = await session.entries_of(customer_id)
            entry = next((row.entry for row in account if row.entry.id == entry_id), None)
            if entry is None:
                # Not an entry of this customer's account: for them it does not exist.
                raise NotFound()
            earlier = [r for r in await session.date_requests_of_customer(customer_id) if r.entry_id == entry_id]
            owed = {a.entry_id: a.remaining for a in ledger.allocate(row.entry for row in account)}
            refusal = may_request(
                kind=entry.kind,
                is_reversed=entry_id in {other.entry.reverses_id for other in account},
                remaining=owed.get(entry_id, 0),
                has_open=any(r.status == "open" for r in earlier),
                sale_date=tashkent_date(entry.created_at),
                current=entry.promised_date,
                requested=requested_date,
                last_declined_at=max(
                    (r.closed_at for r in earlier if r.status == "declined" and r.closed_at is not None), default=None
                ),
                now=now,
            )
            if refusal is DateRequestRefusal.ALREADY_OPEN:
                raise RequestAlreadyOpen()
            if isinstance(refusal, PromiseDateError):
                raise ValidationFailed({"requested_date": refusal.value})
            if refusal is not None:
                raise DateRequestNotAllowed({"reason": refusal.value})
            previous = entry.promised_date
            assert previous is not None  # a credit or opening entry always has a promised date (INV-9)

            record = await session.open_date_request(
                request_id=uuid4(), entry_id=entry_id, requested_date=requested_date, reason=text, now=now
            )
            await session.record_customer_activity(action="date_request.opened", subject_id=customer_id)
            await session.record_measure(
                kind="date_request_opened", entry_ref=entry_id, amount=entry.amount, promised=requested_date
            )
            await _tell_managers(session, record, previous)
            return date_request_body(record)

    # --- the shop -------------------------------------------------------------------------------------

    async def list_open(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_DATE_REQUESTS)
            await require_viewable(session, actor, self._today())
            return {"items": [staff_body(record) for record in await session.open_date_requests()]}

    async def accept(self, user_id: UUID, shop_id: UUID, request_id: UUID, request_key: str | None) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, ACCEPT_DATE_REQUEST)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                return await accept_in(session, actor, request_id, self._now())

            return await idempotency.run_once(
                session,
                key=key,
                operation=ACCEPT_DATE_REQUEST.name,
                user_id=user_id,
                request={"request": str(request_id)},
                action=apply,
            )

    async def decline(
        self, user_id: UUID, shop_id: UUID, request_id: UUID, reason: str | None, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, DECLINE_DATE_REQUEST)
            key = idempotency.validate_key(request_key)
            text = clean_reason(reason)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                return await decline_in(session, actor, request_id, text, self._now())

            return await idempotency.run_once(
                session,
                key=key,
                operation=DECLINE_DATE_REQUEST.name,
                user_id=user_id,
                request={"request": str(request_id), "reason": text},
                action=apply,
            )


async def _open_request(session: TenantSession, request_id: UUID) -> DateRequestRecord:
    """The request, still open, read again once its customer's account is locked."""
    found = await session.get_date_request(request_id)
    if found is None:
        raise NotFound()
    await session.get_customer(found.customer_id, for_update=True)
    record = await session.get_date_request(request_id)
    if record is None:
        raise NotFound()
    if record.status != "open":
        raise DateRequestNotAllowed({"reason": "not_open"})
    return record


async def accept_in(session: TenantSession, actor: Membership, request_id: UUID, now: datetime) -> dict[str, Any]:
    """Accept an open request inside a tenant transaction the caller has opened and authorized.

    The requested date becomes the entry's current promise, so overdue status and reminders follow it
    from now on (BR-15). An open request is always about a debt that still stands and asks for a date
    after the current one: anything else has closed it already.
    """
    record = await _open_request(session, request_id)
    await session.add_promise(
        entry_id=record.entry_id,
        promised_date=record.requested_date,
        actor=CUSTOMER_REQUEST_ACTOR,
        created_at=now,
        reason=record.reason,
    )
    closed = await session.close_date_request(
        request_id, status="accepted", decline_reason=None, decided_by=actor.membership_id, now=now
    )
    await session.record_activity(
        membership_id=actor.membership_id,
        action="date_request.accepted",
        subject_type="customer",
        subject_id=record.customer_id,
    )
    await session.record_measure(
        kind="date_request_accepted", entry_ref=record.entry_id, amount=record.amount, promised=record.requested_date
    )
    await notify.date_request_decided(
        session,
        record.customer_id,
        request_id=request_id,
        accepted=True,
        amount=record.amount,
        requested=record.requested_date,
        reason=None,
    )
    return staff_body(closed)


async def decline_in(
    session: TenantSession, actor: Membership, request_id: UUID, reason: str | None, now: datetime
) -> dict[str, Any]:
    """Decline an open request inside a tenant transaction the caller has opened and authorized."""
    record = await _open_request(session, request_id)
    closed = await session.close_date_request(
        request_id, status="declined", decline_reason=reason, decided_by=actor.membership_id, now=now
    )
    await session.record_activity(
        membership_id=actor.membership_id,
        action="date_request.declined",
        subject_type="customer",
        subject_id=record.customer_id,
    )
    await session.record_measure(
        kind="date_request_declined", entry_ref=record.entry_id, amount=record.amount, promised=None
    )
    await notify.date_request_decided(
        session,
        record.customer_id,
        request_id=request_id,
        accepted=False,
        amount=record.amount,
        requested=record.requested_date,
        reason=reason,
    )
    return staff_body(closed)
