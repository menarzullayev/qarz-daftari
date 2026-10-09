"""Subscription receipts decided from the review group by one of the group's Telegram administrators
(REQ-055; BR-27; INV-16; DEC-064, which changes DEC-051).

The founder decided on 2026-10-08 that any Telegram administrator of the review group may approve or
reject a receipt with the buttons on the group's announcement, whether or not they are a platform
administrator. This is the whole of what such a person can do: there is no administrator account for
them, no session, no panel and no API. Whether the person administers the group is asked of Telegram at
the moment of the press (`qarz.application.telegram_updates`); nothing here decides that.

A decision made this way is recorded with the person's Telegram user identifier, in the receipt and in
the admin audit, and with no administrator. It does what an administrator's decision does: an approval
extends the paid period by the months the owner stated, by the same rule as every other payment
(`qarz.domain.subscription.after_payment`), and the owner is told; a rejection tells the owner why. The
months cannot be corrected from the group: that is done in the panel.

Everything is written in the caller's transaction, the one that claimed the Telegram update, so a press
that is delivered twice decides once and a decision is never kept without its answer.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from qarz.application.admin_receipts import ReceiptAlreadyDecided
from qarz.application.chat_texts import day, money, say
from qarz.application.errors import NotFound, ValidationFailed
from qarz.application.ports import GroupReceipt, PlatformSession
from qarz.domain.promise import tashkent_date
from qarz.domain.subscription import MAX_MONTHS, after_payment
from qarz.domain.subscription_receipts import APPROVED, REJECTED, SUBMITTED, months_to_record

# Where the decision was made, as the audit row says it; beside "panel" and "chat".
GROUP = "group"


class GroupReceiptService:
    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    @staticmethod
    async def _waiting(session: PlatformSession, group_id: int, receipt_id: UUID, *, lock: bool) -> GroupReceipt:
        row = await session.review_group_receipt(group_id, receipt_id, lock=lock)
        if row is None:
            raise NotFound()
        if row.status != SUBMITTED:
            raise ReceiptAlreadyDecided({"status": row.status})
        return row

    async def require_waiting(self, session: PlatformSession, group_id: int, receipt_id: UUID) -> None:
        """Refuse at once a receipt that does not exist or was decided, before a reason is asked for."""
        await self._waiting(session, group_id, receipt_id, lock=False)

    async def approve(
        self, session: PlatformSession, *, group_id: int, decider_tg: int, receipt_id: UUID
    ) -> dict[str, Any]:
        """Approve a waiting receipt for the months its owner stated."""
        now, today = self._now(), self._today()
        row = await self._waiting(session, group_id, receipt_id, lock=True)
        paid_for = months_to_record(row.stated_months, None)
        if paid_for is None:
            raise ValidationFailed({"months": f"must be between 1 and {MAX_MONTHS}"})
        if row.state is None:
            # No subscription to extend, as on the administrator's side.
            raise NotFound()
        state, paid_through, prior_state = after_payment(row.state, row.trial_ends, row.paid_through, today, paid_for)
        decided = await session.review_group_decide_receipt(
            group_id,
            decider_tg,
            receipt_id,
            status=APPROVED,
            months=paid_for,
            reason=None,
            state=state,
            paid_through=paid_through,
            prior_state=prior_state,
            detail={
                "via": GROUP,
                "months": paid_for,
                "before": {
                    "state": row.state,
                    "paid_through": None if row.paid_through is None else row.paid_through.isoformat(),
                },
                "after": {"state": state, "paid_through": paid_through.isoformat()},
            },
            now=now,
        )
        if not decided:
            # The review group was changed, or the shop erased, between the lock and here.
            raise NotFound()
        await session.record_shop_measure(
            row.shop_id, kind=f"subscription_receipt_{APPROVED}", entry_ref=receipt_id, amount=row.stated_amount
        )
        if row.owner_tg is not None:
            lang = row.owner_lang or "uz"
            await session.enqueue(
                channel="telegram",
                recipient=str(row.owner_tg),
                payload={
                    "text": say(
                        lang, "sub_receipt_approved", shop=row.shop_name, months=paid_for, date=day(paid_through)
                    )
                },
                dedupe_key=f"subreceipt:{receipt_id}:decided",
                shop_id=row.shop_id,
            )
        return {
            "shop_name": row.shop_name,
            "months": paid_for,
            "subscription": {"state": state, "paid_through": paid_through.isoformat()},
        }

    async def reject(
        self, session: PlatformSession, *, group_id: int, decider_tg: int, receipt_id: UUID, reason: str
    ) -> dict[str, Any]:
        """Reject a waiting receipt. `reason` is already cleaned; it is told to the owner as written."""
        row = await self._waiting(session, group_id, receipt_id, lock=True)
        decided = await session.review_group_decide_receipt(
            group_id,
            decider_tg,
            receipt_id,
            status=REJECTED,
            months=None,
            reason=reason,
            state=None,
            paid_through=None,
            prior_state=None,
            detail={"via": GROUP},
            now=self._now(),
        )
        if not decided:
            raise NotFound()
        await session.record_shop_measure(
            row.shop_id, kind=f"subscription_receipt_{REJECTED}", entry_ref=receipt_id, amount=row.stated_amount
        )
        if row.owner_tg is not None:
            lang = row.owner_lang or "uz"
            await session.enqueue(
                channel="telegram",
                recipient=str(row.owner_tg),
                payload={
                    "text": say(
                        lang,
                        "sub_receipt_rejected",
                        shop=row.shop_name,
                        amount=money(lang, row.stated_amount),
                        reason=reason,
                    )
                },
                dedupe_key=f"subreceipt:{receipt_id}:decided",
                shop_id=row.shop_id,
            )
        return {"shop_name": row.shop_name}
