"""Paying the subscription by card transfer: the owner's side (REQ-054, REQ-055; ADR-019).

The owner transfers the money outside the system and sends the receipt with what they paid and for how
many months. Nothing changes by that: the receipt waits for an administrator
(`qarz.application.admin_receipts`). The administrators and the review group are told a receipt is
waiting; the text names the shop, the amount and the months, never a card, and the image itself is not
forwarded: a reviewer opens it through a signed link from the administrator's side.
"""

import hashlib
from collections.abc import Callable, Container
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from qarz.application import idempotency
from qarz.application.chat_texts import money, say
from qarz.application.errors import AppError, ValidationFailed
from qarz.application.files import FileService, StagedFile
from qarz.application.operations import operation
from qarz.application.ports import Storage, SubscriptionReceiptRecord, TenantSession
from qarz.application.shops import require_member
from qarz.domain import platform_settings
from qarz.domain.access import Capability
from qarz.domain.subscription import MAX_MONTHS
from qarz.domain.subscription_receipts import (
    MAX_AMOUNT,
    MIN_AMOUNT,
    ReceiptRefusal,
    delete_file_after,
    may_submit,
)

SUBMIT_RECEIPT = operation("shop.subscription.receipts.submit", Capability.ADMINISTER_SHOP)
LIST_RECEIPTS = operation("shop.subscription.receipts.list", Capability.ADMINISTER_SHOP)

FILE_PURPOSE = "subscription_receipt"
REVIEW_GROUP = "review_group"
HISTORY = 50
_HINTS = {
    ReceiptRefusal.AMOUNT_OUT_OF_RANGE: ("amount", f"a whole amount between {MIN_AMOUNT} and {MAX_AMOUNT} UZS"),
    ReceiptRefusal.MONTHS_OUT_OF_RANGE: ("months", f"a whole number between 1 and {MAX_MONTHS}"),
}


class SubscriptionReceiptNotAllowed(AppError):
    """The shop may not send another receipt now. The field says why."""

    code = "SUBSCRIPTION_RECEIPT_NOT_ALLOWED"


def receipt_body(record: SubscriptionReceiptRecord) -> dict[str, Any]:
    return {
        "id": str(record.receipt_id),
        "stated_amount": record.stated_amount,
        "stated_months": record.stated_months,
        "status": record.status,
        "months": record.months,
        "reject_reason": record.reject_reason,
        "created_at": record.created_at.isoformat(),
        "decided_at": None if record.decided_at is None else record.decided_at.isoformat(),
    }


class SubscriptionReceiptService:
    def __init__(
        self,
        storage: Storage,
        files: FileService,
        now: Callable[[], datetime] | None = None,
        *,
        admin_tg_ids: Container[int] = (),
    ) -> None:
        self._storage = storage
        self._files = files
        self._now = now or (lambda: datetime.now(UTC))
        # The allow-list of the administrator's side: an account alone does not make someone a reviewer.
        self._admins = admin_tg_ids

    async def require_owner(self, user_id: UUID, shop_id: UUID, request_key: str | None) -> None:
        """Refuse anyone who may not send a receipt for this shop, and a request without its key, before
        anything of the body is read."""
        async with self._storage.tenant(shop_id) as session:
            await require_member(session, user_id, SUBMIT_RECEIPT)
        idempotency.validate_key(request_key)

    @staticmethod
    async def _require_sendable(session: TenantSession, amount: Any, months: Any, *, lock: bool) -> None:
        if lock:
            # The shop's subscription row is the lock: two receipts of one shop are counted one after another.
            await session.subscription_locked()
        refusal = may_submit(amount=amount, months=months, waiting=await session.count_waiting_receipts())
        if refusal in _HINTS:
            field, hint = _HINTS[refusal]
            raise ValidationFailed({field: hint})
        if refusal is not None:
            raise SubscriptionReceiptNotAllowed({"reason": refusal.value})

    async def _announce(self, session: TenantSession, record: SubscriptionReceiptRecord, copies: int) -> None:
        """Tell every administrator and the review group that a receipt waits (REQ-055)."""
        settings = await session.shop_settings()
        shop = "" if settings is None else settings.name
        recipients = [(str(tg_id), lang) for tg_id, lang in await session.admin_recipients() if tg_id in self._admins]
        group = platform_settings.effective(REVIEW_GROUP, await session.platform_setting(REVIEW_GROUP))
        if isinstance(group, int) and not isinstance(group, bool):
            recipients.append((str(group), "uz"))
        for recipient, lang in recipients:
            text = say(
                lang,
                "a_receipt_new",
                shop=shop,
                amount=money(lang, record.stated_amount),
                months=record.stated_months,
            )
            if copies > 0:
                text += "\n" + say(lang, "a_receipt_copies", count=copies)
            await session.enqueue(
                recipient=recipient,
                payload={"text": text},
                dedupe_key=f"subreceipt:{record.receipt_id}:new:{recipient}",
            )

    async def _submit_in(
        self, session: TenantSession, user_id: UUID, amount: int, months: int, staged: StagedFile
    ) -> dict[str, Any]:
        now = self._now()
        actor = await require_member(session, user_id, SUBMIT_RECEIPT)
        await self._require_sendable(session, amount, months, lock=True)
        file_id = await self._files.record_in(
            session, staged, purpose=FILE_PURPOSE, now=now, delete_after=delete_file_after(now)
        )
        record = await session.add_subscription_receipt(
            receipt_id=uuid4(), stated_amount=amount, stated_months=months, file_id=file_id, now=now
        )
        await session.record_activity(
            membership_id=actor.membership_id,
            action="subscription.receipt_sent",
            subject_type="subscription_receipt",
            subject_id=record.receipt_id,
        )
        await session.record_measure(
            kind="subscription_receipt_sent", entry_ref=record.receipt_id, amount=amount, promised=None
        )
        await self._announce(session, record, await session.subscription_receipt_copies(file_id))
        return receipt_body(record)

    async def submit(
        self,
        user_id: UUID,
        shop_id: UUID,
        amount: Any,
        months: Any,
        receipt: bytes | None,
        request_key: str | None = None,
        *,
        update_key: str | None = None,
    ) -> dict[str, Any]:
        """Send a receipt for the caller's shop. Open in every mode: paying is how a shop leaves one.

        The chat passes `update_key`, the key it derives from the Telegram update, which has a form no
        API caller can send; an API caller must give `request_key`.
        """
        async with self._storage.tenant(shop_id) as session:
            await require_member(session, user_id, SUBMIT_RECEIPT)
            key = update_key if update_key is not None else idempotency.validate_key(request_key)
            if receipt is None:
                raise ValidationFailed({"receipt": "required"})
            checked = self._files.check(receipt)
            # A receipt that would be refused anyway is refused before its file is uploaded. The same is
            # checked again under the lock, where it counts.
            if await session.stored_response(key) is None:
                await self._require_sendable(session, amount, months, lock=False)
        # Stored before the writing transaction opens: no network call is made while a row is locked.
        staged = await self._files.stage(checked)
        recorded = False
        try:
            async with self._storage.tenant(shop_id) as session:

                async def apply() -> dict[str, Any]:
                    nonlocal recorded
                    body = await self._submit_in(session, user_id, int(amount), int(months), staged)
                    recorded = True
                    return body

                body = await idempotency.run_once(
                    session,
                    key=key,
                    operation=SUBMIT_RECEIPT.name,
                    user_id=user_id,
                    request={"amount": amount, "months": months, "receipt": hashlib.sha256(receipt).hexdigest()},
                    action=apply,
                )
        except BaseException:
            await self._files.discard(staged)
            raise
        if not recorded:
            # A repeat of a request already carried out: the first copy of the file is the one kept.
            await self._files.discard(staged)
        return body

    async def history(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        """The shop's receipts and what became of each, newest first."""
        async with self._storage.tenant(shop_id) as session:
            await require_member(session, user_id, LIST_RECEIPTS)
            return {"items": [receipt_body(record) for record in await session.subscription_receipts(HISTORY)]}
