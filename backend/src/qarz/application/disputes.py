"""Disputes (REQ-016, REQ-017; domain rules BR-10 to BR-13).

A linked customer objects to an entry with a short reason. The entry stays in the balance and is marked
disputed until it ends in one of three ways: staff reverse the entry, a manager or owner declines the
dispute with a reason, or the customer withdraws it.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency
from qarz.application.chat_texts import money, say
from qarz.application.currencies import USD, UZS, dollars_on, tag
from qarz.application.customer_account import resolve_link
from qarz.application.customers import require_viewable, require_writable
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.operations import operation, self_operation
from qarz.application.ports import DisputeRecord, Membership, Storage, TenantSession
from qarz.application.shops import require_member
from qarz.domain.access import Capability
from qarz.domain.disputes import clean_reason, may_dispute
from qarz.domain.promise import tashkent_date

OPEN_DISPUTE = self_operation("me.accounts.disputes.open")
WITHDRAW_DISPUTE = self_operation("me.accounts.disputes.withdraw")
LIST_DISPUTES = operation("disputes.list", Capability.MANAGE)
DECLINE_DISPUTE = operation("disputes.decline", Capability.MANAGE)

MANAGERS = ("manager", "owner")


class DisputeNotAllowed(AppError):
    code = "DISPUTE_NOT_ALLOWED"


def dispute_body(record: DisputeRecord) -> dict[str, Any]:
    return {
        "id": str(record.dispute_id),
        "entry_id": str(record.entry_id),
        "status": record.status,
        "reason": record.reason,
        "decline_reason": record.decline_reason,
        "created_at": record.created_at.isoformat(),
    }


def callback_data(action: str, identifier: UUID) -> str:
    # The same format as qarz.application.chat.callback; kept here so that this module does not import the chat.
    return f"v2:{action}:{identifier.hex}"


async def _tell_managers(
    session: TenantSession, dedupe_key: str, key: str, buttons: list[tuple[str, str]] | None = None, **values: Any
) -> None:
    """A notice to the owner and the managers, each in their own language (REQ-017)."""
    settings = await session.shop_settings()
    shop = "" if settings is None else settings.name
    amount = int(values.pop("amount"))
    currency = values.pop("currency", UZS)
    for tg_id, lang in await session.staff_recipients(list(MANAGERS)):
        payload: dict[str, Any] = {"text": say(lang, key, shop=shop, amount=money(lang, amount, currency), **values)}
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": say(lang, label), "callback_data": data} for label, data in buttons]]
            }
        await session.enqueue(recipient=str(tg_id), payload=payload, dedupe_key=f"{dedupe_key}:{tg_id}")


class DisputeService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    # --- the customer ---------------------------------------------------------------------------------

    async def open(self, user_id: UUID, link_id: UUID, entry_id: UUID, reason: str) -> dict[str, Any]:
        shop_id, customer_id = await resolve_link(self._storage, user_id, link_id)
        text = clean_reason(reason)
        if text is None:
            raise ValidationFailed({"reason": "between 3 and 300 characters"})
        now = self._now()
        async with self._storage.tenant(shop_id) as session:
            # The customer row is locked, as in every write to the account, so a reversal recorded at
            # the same moment is seen either before or after, never in between.
            customer = await session.get_customer(customer_id, for_update=True)
            link = await session.link_state(customer_id)
            if customer is None or link is None:
                raise NotFound()
            account = await session.entries_of(customer_id)
            row = next((candidate for candidate in account if candidate.entry.id == entry_id), None)
            if row is None or (row.entry.currency is USD and not await dollars_on(session)):
                # Not an entry of this customer's account, or one in dollars of a shop that does not show
                # dollars now: for them it does not exist.
                raise NotFound()
            refusal = may_dispute(
                kind=row.entry.kind,
                is_reversed=entry_id in {other.entry.reverses_id for other in account},
                disputed_before=await session.dispute_of_entry(entry_id) is not None,
                notified_at=max(row.entry.created_at, link[1]),
                now=now,
            )
            if refusal is not None:
                raise DisputeNotAllowed({"reason": refusal.value})

            record = await session.open_dispute(dispute_id=uuid4(), entry_id=entry_id, reason=text, now=now)
            await session.record_customer_activity(action="dispute.opened", subject_id=customer_id)
            await session.record_measure(
                kind="dispute_opened",
                entry_ref=entry_id,
                amount=row.entry.amount,
                promised=None,
                currency=row.entry.currency,
            )
            await _tell_managers(
                session,
                f"dispute:{record.dispute_id}:opened",
                "s_dispute",
                buttons=[
                    ("reverse", callback_data("rv", entry_id)),
                    ("decline_button", callback_data("dcl", record.dispute_id)),
                ],
                name=customer.display_name,
                amount=row.entry.amount,
                currency=row.entry.currency,
                reason=text,
            )
            return dispute_body(record)

    async def withdraw(self, user_id: UUID, link_id: UUID, dispute_id: UUID) -> dict[str, Any]:
        shop_id, customer_id = await resolve_link(self._storage, user_id, link_id)
        async with self._storage.tenant(shop_id) as session:
            customer = await session.get_customer(customer_id, for_update=True)
            record = await session.get_dispute(dispute_id)
            if customer is None or record is None or record.customer_id != customer_id:
                raise NotFound()
            if record.status != "open":
                raise DisputeNotAllowed({"reason": "not_open"})
            closed = await session.close_dispute(
                dispute_id, status="withdrawn", decline_reason=None, decided_by=None, now=self._now()
            )
            await session.record_customer_activity(action="dispute.withdrawn", subject_id=customer_id)
            await session.record_measure(
                kind="dispute_withdrawn",
                entry_ref=record.entry_id,
                amount=record.amount,
                promised=None,
                currency=record.currency,
            )
            await _tell_managers(
                session,
                f"dispute:{dispute_id}:withdrawn",
                "s_dispute_withdrawn",
                name=customer.display_name,
                amount=record.amount,
                currency=record.currency,
            )
            return dispute_body(closed)

    # --- the shop -------------------------------------------------------------------------------------

    async def list_open(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, LIST_DISPUTES)
            await require_viewable(session, actor, self._today())
            rows = await session.open_disputes()
            dollars = await dollars_on(session)
            return {
                "items": [
                    tag(
                        {
                            **dispute_body(record),
                            "customer_id": str(record.customer_id),
                            "customer_name": name,
                            "amount": record.amount,
                        },
                        record.currency,
                    )
                    for record, name in rows
                    # A dispute over a dollar entry waits, unseen, while the shop does not show dollars.
                    if dollars or record.currency is UZS
                ]
            }

    async def decline(
        self, user_id: UUID, shop_id: UUID, dispute_id: UUID, reason: str, request_key: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, DECLINE_DISPUTE)
            key = idempotency.validate_key(request_key)
            text = clean_reason(reason)
            if text is None:
                raise ValidationFailed({"reason": "between 3 and 300 characters"})
            await require_writable(session, self._today(), new_credit=False)

            async def apply() -> dict[str, Any]:
                return await decline_in(session, actor, dispute_id, text, self._now())

            return await idempotency.run_once(
                session,
                key=key,
                operation=DECLINE_DISPUTE.name,
                user_id=user_id,
                request={"dispute": str(dispute_id), "reason": text},
                action=apply,
            )


async def decline_in(
    session: TenantSession, actor: Membership, dispute_id: UUID, reason: str, now: datetime
) -> dict[str, Any]:
    """Decline an open dispute inside a tenant transaction the caller has opened and authorized."""
    found = await session.get_dispute(dispute_id)
    if found is None:
        raise NotFound()
    await session.get_customer(found.customer_id, for_update=True)
    record = await session.get_dispute(dispute_id)
    if record is None:
        raise NotFound()
    if record.status != "open":
        raise DisputeNotAllowed({"reason": "not_open"})
    closed = await session.close_dispute(
        dispute_id, status="declined", decline_reason=reason, decided_by=actor.membership_id, now=now
    )
    await session.record_activity(
        membership_id=actor.membership_id,
        action="dispute.declined",
        subject_type="customer",
        subject_id=record.customer_id,
    )
    await session.record_measure(
        kind="dispute_declined",
        entry_ref=record.entry_id,
        amount=record.amount,
        promised=None,
        currency=record.currency,
    )
    recipient = await session.customer_recipient(record.customer_id)
    if recipient is not None:
        tg_id, lang = recipient
        settings = await session.shop_settings()
        await session.enqueue(
            recipient=str(tg_id),
            payload={
                "text": say(
                    lang,
                    "n_dispute_declined",
                    shop="" if settings is None else settings.name,
                    amount=money(lang, record.amount, record.currency),
                    reason=reason,
                )
            },
            dedupe_key=f"dispute:{dispute_id}:declined",
        )
    return dispute_body(closed)
