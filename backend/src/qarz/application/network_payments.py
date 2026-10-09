"""Payments between two linked shops (module J of the expansion).

The buyer pays the supplier outside the service; either of them writes it down here. The side that
records a payment writes it into its OWN books at once, by the ordinary path: the buyer as a payment on
the supplier's account (`suppliers.pay_in`, with its cash-book expense), the supplier as a payment of the
linked customer (`ledger.append_entry_in`, with its cash-book income). The other side sees it as awaiting
its confirmation, and its books do not move until it confirms: then it writes its own entry, by the same
ordinary path of its own, in its own transaction.

So between recording and confirming the two shops' books differ, and the reconciliation of the link says
so. Nothing here makes them agree by itself: a payment the other side declines stays in the books of the
shop that recorded it until that shop cancels its own entry the ordinary way. Taking a payment back while
it still waits cancels the recorder's entry and the record together.

Each side's entry needs that side's ordinary permission as well as `network.confirm`: `suppliers.pay` to
pay a supplier (and to take that back), `payments.record` to take a customer's payment (`entries.cancel`
to take that back).
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency, stock_cash
from qarz.application import suppliers as supplier_account
from qarz.application.authorization import require_permission
from qarz.application.chat_texts import say
from qarz.application.customers import MAX_PAGE, decode_cursor, encode_cursor, require_viewable, require_writable
from qarz.application.errors import NotFound, ValidationFailed
from qarz.application.ledger_service import Advance, append_entry_in, reverse_entry_in
from qarz.application.network import (
    NetworkState,
    ensure_counterpart_in,
    events_body,
    iso,
    locked_link,
    own_link,
    refusals,
    require_on,
    tell_partner,
)
from qarz.application.network_ports import LinkRecord, PaymentRecord
from qarz.application.operations import operation
from qarz.application.ports import Membership, Storage, TenantSession
from qarz.application.shops import require_member
from qarz.application.stock_currency import require_currency
from qarz.application.suppliers import SupplierArchived
from qarz.domain import network, permissions
from qarz.domain import suppliers as supplier_rules
from qarz.domain.access import Capability
from qarz.domain.cash import Method
from qarz.domain.ledger import EntryKind
from qarz.domain.money import Currency, valid_entry_amount
from qarz.domain.promise import tashkent_date

LIST_PAYMENTS = operation("network.payments.list", Capability.MANAGE)
READ_PAYMENT = operation("network.payments.read", Capability.MANAGE)
RECORD_PAYMENT = operation("network.payments.record", Capability.MANAGE)
CONFIRM_PAYMENT = operation("network.payments.confirm", Capability.MANAGE)
DECLINE_PAYMENT = operation("network.payments.decline", Capability.MANAGE)
WITHDRAW_PAYMENT = operation("network.payments.withdraw", Capability.MANAGE)


def payment_body(payment: PaymentRecord) -> dict[str, Any]:
    return {
        "id": str(payment.payment_id),
        "link_id": str(payment.link_id),
        "role": payment.role,
        "partner": {"name": payment.peer_name},
        "recorded_by": "own" if payment.recorded_by_own else "partner",
        "status": payment.status,
        "amount": payment.amount,
        "currency": payment.currency,
        "note": payment.note,
        "recorded_at": payment.recorded_at.isoformat(),
        "decided_at": iso(payment.decided_at),
        "decline_reason": payment.decline_reason,
        # Whether this shop's own books hold it now: before it confirms, they do not; after the partner
        # declined, they still do until the shop cancels its own entry.
        "in_own_books": bool(payment.own_entry_stands),
    }


def _entry_permission(link: LinkRecord) -> str:
    return permissions.SUPPLIERS_PAY if link.role == network.BUYER else permissions.PAYMENTS_RECORD


async def post_own_in(
    session: TenantSession,
    actor: Membership,
    link: LinkRecord,
    counterpart_id: UUID,
    *,
    amount: int,
    currency: str,
    note: str | None,
    method: Method | None,
    now: datetime,
    advance: Advance = Advance.REFUSE,
) -> UUID:
    """Write a payment of the link into this shop's own books, by the path that side always uses.

    `advance` matters on the supplier's side only, where the payment lowers a customer's debt: what to
    do when it is more than the buyer owes (INV-3). On the buyer's side an account with a supplier
    has always been allowed to stand in the shop's favour (BR-72).
    """
    if link.role == network.BUYER:
        supplier = await session.get_supplier(counterpart_id, for_update=True)
        if supplier is None:
            raise NotFound()
        if supplier.status == supplier_rules.ARCHIVED:
            raise SupplierArchived()
        entry_id = await supplier_account.pay_in(
            session, actor, counterpart_id, amount=amount, currency=currency, note=note, now=now, method=method
        )
        await session.record_activity(
            membership_id=actor.membership_id,
            action="supplier.payment_recorded",
            subject_type="supplier",
            subject_id=counterpart_id,
            detail={"amount": amount, "currency": currency},
        )
        return entry_id
    written = await append_entry_in(
        session,
        actor,
        counterpart_id,
        kind=EntryKind.PAYMENT,
        amount=amount,
        note=note,
        promised_date=None,
        now=now,
        currency=Currency(currency),
        method=method,
        advance=advance,
    )
    return UUID(written["entry"]["id"])


class PaymentService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def list(
        self, user_id: UUID, shop_id: UUID, *, status: str | None, link_id: UUID | None, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, LIST_PAYMENTS)
            await require_viewable(session, actor, self._today())
            fields: dict[str, str] = {}
            if not 1 <= limit <= MAX_PAGE:
                fields["limit"] = f"must be between 1 and {MAX_PAGE}"
            if status is not None and status not in network.PAYMENT_STATES:
                fields["status"] = "not a status"
            if fields:
                raise ValidationFailed(fields)
            before: tuple[datetime, UUID] | None = None
            if cursor:
                at, last = decode_cursor(cursor, 2)
                try:
                    before = (datetime.fromisoformat(at), UUID(last))
                except ValueError as error:
                    raise ValidationFailed({"cursor": "not a cursor returned by this API"}) from error
            rows = await session.network_payments(status=status, link_id=link_id, before=before, limit=limit + 1)
            page, more = rows[:limit], len(rows) > limit
            return {
                "payments": [payment_body(row) for row in page],
                "next_cursor": encode_cursor(page[-1].recorded_at.isoformat(), page[-1].payment_id) if more else None,
            }

    async def read(self, user_id: UUID, shop_id: UUID, payment_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, READ_PAYMENT)
            await require_viewable(session, actor, self._today())
            payment = await session.network_payment(payment_id)
            if payment is None:
                raise NotFound()
            return {**payment_body(payment), "events": await events_body(session, payment_id)}

    async def record(
        self,
        user_id: UUID,
        shop_id: UUID,
        *,
        link_id: UUID,
        amount: int,
        currency: str | None,
        method: str | None,
        note: str | None,
        request_key: str | None,
    ) -> dict[str, Any]:
        """Record a payment of the buyer to the supplier, from either side. It is in this shop's books
        at once, and awaits the partner's confirmation."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, RECORD_PAYMENT)
            key = idempotency.validate_key(request_key)
            money = await require_currency(session, currency)
            fields: dict[str, str] = {}
            clean_note: str | None = None
            # Both books must be able to hold it: a customer's payment has the narrower range.
            if not valid_entry_amount(Currency(money), amount):
                fields["amount"] = "not an amount of one payment in this currency"
            try:
                clean_note = network.text(note, limit=network.MAX_NOTE)
            except ValueError as error:
                fields["note"] = str(error)
            if fields:
                raise ValidationFailed(fields)
            paid_by = stock_cash.clean_method(method)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                now = self._now()
                link = await session.network_link(link_id)
                if link is None:
                    raise ValidationFailed({"link_id": "not a link of this shop"})
                if link.state != network.ACTIVE:
                    raise NetworkState()
                require_permission(actor, _entry_permission(link))
                # The link's locks before this shop's own: the same order as every other step.
                await locked_link(session, link)
                counterpart = await ensure_counterpart_in(session, actor, link, today=self._today())
                entry_id = await post_own_in(
                    session,
                    actor,
                    link,
                    counterpart,
                    amount=amount,
                    currency=money,
                    note=clean_note,
                    method=paid_by,
                    now=now,
                )
                payment_id = uuid4()
                with refusals():
                    await session.network_record_payment(
                        link.peer_shop_id,
                        link.link_id,
                        payment_id,
                        member_id=actor.membership_id,
                        amount=amount,
                        currency=money,
                        note=clean_note,
                        entry_id=entry_id,
                        now=now,
                    )
                await tell_partner(
                    session,
                    link,
                    permissions.NETWORK_CONFIRM,
                    "net_payment_recorded",
                    payment_id,
                    amount=(amount, money),
                )
                payment = await session.network_payment(payment_id)
                assert payment is not None
                return payment_body(payment)

            return await idempotency.run_once(
                session,
                key=key,
                operation=RECORD_PAYMENT.name,
                user_id=user_id,
                request={
                    "link": str(link_id),
                    "amount": amount,
                    "currency": money,
                    "method": None if paid_by is None else paid_by.value,
                    "note": clean_note,
                },
                action=apply,
            )

    async def decide(
        self,
        user_id: UUID,
        shop_id: UUID,
        payment_id: UUID,
        *,
        confirm: bool,
        method: str | None = None,
        reason: str | None = None,
        request_key: str | None,
    ) -> dict[str, Any]:
        """Answer a payment the partner recorded. Confirming writes it into this shop's own books, now;
        declining says why and writes nothing anywhere."""
        op = CONFIRM_PAYMENT if confirm else DECLINE_PAYMENT
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, op)
            key = idempotency.validate_key(request_key)
            why: str | None = None
            if not confirm:
                try:
                    why = network.reason(reason or "")
                except ValueError as error:
                    raise ValidationFailed({"reason": str(error)}) from error
            paid_by = stock_cash.clean_method(method) if confirm else None
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                now = self._now()
                payment = await session.network_payment(payment_id)
                if payment is None:
                    raise NotFound()
                if not network.may_answer_payment(payment.status, recorded_by_own=payment.recorded_by_own):
                    raise NetworkState()
                link = await own_link(session, payment.link_id)
                await locked_link(session, link)
                entry_id: UUID | None = None
                if confirm:
                    require_permission(actor, _entry_permission(link))
                    await require_currency(session, payment.currency)
                    counterpart = await ensure_counterpart_in(session, actor, link, today=self._today())
                    entry_id = await post_own_in(
                        session,
                        actor,
                        link,
                        counterpart,
                        amount=payment.amount,
                        currency=payment.currency,
                        note=payment.note,
                        method=paid_by,
                        now=now,
                        # The buyer named the amount and this member confirms it: that is the answer
                        # to "more than they owe?". It lands as the linked customer's advance where
                        # this shop accepts advances, and is refused with EXCEEDS_BALANCE where it does
                        # not, as before; the payment then stays awaiting, to be declined with a reason.
                        advance=Advance.ACCEPT,
                    )
                with refusals():
                    await session.network_decide_payment(
                        link.peer_shop_id,
                        payment_id,
                        member_id=actor.membership_id,
                        confirm=confirm,
                        reason=why,
                        entry_id=entry_id,
                        now=now,
                    )
                await tell_partner(
                    session,
                    link,
                    permissions.NETWORK_CONFIRM,
                    "net_payment_confirmed" if confirm else "net_payment_declined",
                    payment_id,
                    amount=(payment.amount, payment.currency),
                    **({} if why is None else {"reason": why}),
                )
                decided = await session.network_payment(payment_id)
                assert decided is not None
                return payment_body(decided)

            return await idempotency.run_once(
                session,
                key=key,
                operation=op.name,
                user_id=user_id,
                request={
                    "payment": str(payment_id),
                    "method": None if paid_by is None else paid_by.value,
                    "reason": why,
                },
                action=apply,
            )

    async def withdraw(
        self, user_id: UUID, shop_id: UUID, payment_id: UUID, *, request_key: str | None
    ) -> dict[str, Any]:
        """Take back a payment this shop recorded, while the partner has not answered: its own entry is
        cancelled the ordinary way (unless it already was) and the record is withdrawn with it."""
        async with self._storage.tenant(shop_id) as session:
            await require_on(session)
            actor = await require_member(session, user_id, WITHDRAW_PAYMENT)
            key = idempotency.validate_key(request_key)
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                now = self._now()
                payment = await session.network_payment(payment_id)
                if payment is None:
                    raise NotFound()
                if not network.may_withdraw_payment(payment.status, recorded_by_own=payment.recorded_by_own):
                    raise NetworkState()
                link = await own_link(session, payment.link_id)
                await locked_link(session, link, active=False)
                if payment.own_entry_stands:
                    settings = await session.shop_settings()
                    lang = "uz" if settings is None else settings.lang
                    if payment.supplier_entry_id is not None:
                        require_permission(actor, permissions.SUPPLIERS_PAY)
                        entry = await session.get_supplier_entry(payment.supplier_entry_id)
                        if entry is None:
                            raise NotFound()
                        await session.get_supplier(entry.supplier_id, for_update=True)
                        await supplier_account.reverse_entry_in(
                            session, actor, entry, reason=say(lang, "net_withdrawn_reason"), now=now
                        )
                    elif payment.ledger_entry_id is not None:
                        require_permission(actor, permissions.ENTRIES_CANCEL)
                        await reverse_entry_in(session, actor, payment.ledger_entry_id, now=now)
                with refusals():
                    await session.network_withdraw_payment(
                        link.peer_shop_id, payment_id, member_id=actor.membership_id, now=now
                    )
                await tell_partner(
                    session,
                    link,
                    permissions.NETWORK_CONFIRM,
                    "net_payment_withdrawn",
                    payment_id,
                    amount=(payment.amount, payment.currency),
                )
                withdrawn = await session.network_payment(payment_id)
                assert withdrawn is not None
                return payment_body(withdrawn)

            return await idempotency.run_once(
                session,
                key=key,
                operation=WITHDRAW_PAYMENT.name,
                user_id=user_id,
                request={"payment": str(payment_id)},
                action=apply,
            )
