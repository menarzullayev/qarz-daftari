"""Paying the subscription by card transfer: the owner's side (REQ-054, REQ-055; ADR-019).

The owner transfers the money outside the system and sends the receipt with what they paid and for how
many months. Nothing changes by that: the receipt waits for an administrator
(`qarz.application.admin_receipts`). The administrators and the review group are told a receipt is
waiting; the text names the shop, the amount, the months and the card the owner says they paid to, by
its label and last four digits, never by its number, and the image itself is not forwarded: a reviewer
opens it through a signed link from the administrator's side.
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
CARDS = "payment_cards"
MAX_PAID_TO = 60  # the column's limit: a label of forty characters, the mark and four digits fit
HISTORY = 50
_HINTS = {
    ReceiptRefusal.AMOUNT_OUT_OF_RANGE: ("amount", f"a whole amount between {MIN_AMOUNT} and {MAX_AMOUNT} UZS"),
    ReceiptRefusal.MONTHS_OUT_OF_RANGE: ("months", f"a whole number between 1 and {MAX_MONTHS}"),
}


class SubscriptionReceiptNotAllowed(AppError):
    """The shop may not send another receipt now. The field says why."""

    code = "SUBSCRIPTION_RECEIPT_NOT_ALLOWED"


def callback_data(action: str, identifier: UUID) -> str:
    # The same format as qarz.application.chat.callback; kept here so that this module does not import the chat.
    return f"v2:{action}:{identifier.hex}"


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
        # The card the owner chose to pay to, as its label and last four digits; null when not said.
        "paid_to_card": record.paid_to_card,
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
        group_chat = str(group) if isinstance(group, int) and not isinstance(group, bool) else None
        if group_chat is not None:
            recipients.append((group_chat, "uz"))
        for recipient, lang in recipients:
            # The two decisions as buttons, in each administrator's private chat and in the review group
            # (the founder's decision of 2026-10-07: the group's message has them too). Who presses one
            # is checked when it is pressed (qarz.application.chat): only an administrator on the
            # allow-list who passed the second factor decides; for any other member nothing happens.
            payload: dict[str, Any] = {
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {
                                "text": say(lang, "receipt_approve_button"),
                                "callback_data": callback_data("sra", record.receipt_id),
                            },
                            {
                                "text": say(lang, "receipt_reject_button"),
                                "callback_data": callback_data("srj", record.receipt_id),
                            },
                        ]
                    ]
                }
            }
            text = say(
                lang,
                "a_receipt_new",
                shop=shop,
                amount=money(lang, record.stated_amount),
                months=record.stated_months,
            )
            if record.paid_to_card is not None:
                # Which account's statement to look at. The label and four digits, not the number.
                text += "\n" + say(lang, "receipt_card", card=record.paid_to_card)
            if copies > 0:
                text += "\n" + say(lang, "a_receipt_copies", count=copies)
            await session.enqueue(
                recipient=recipient,
                payload={"text": text, **payload},
                dedupe_key=f"subreceipt:{record.receipt_id}:new:{recipient}",
            )

    async def _submit_in(
        self,
        session: TenantSession,
        user_id: UUID,
        amount: int,
        months: int,
        staged: StagedFile,
        paid_to: str | None,
    ) -> dict[str, Any]:
        now = self._now()
        actor = await require_member(session, user_id, SUBMIT_RECEIPT)
        await self._require_sendable(session, amount, months, lock=True)
        file_id = await self._files.record_in(
            session, staged, purpose=FILE_PURPOSE, now=now, delete_after=delete_file_after(now)
        )
        record = await session.add_subscription_receipt(
            receipt_id=uuid4(),
            stated_amount=amount,
            stated_months=months,
            file_id=file_id,
            paid_to_card=paid_to,
            now=now,
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
        card: str | None = None,
        paid_to: str | None = None,
    ) -> dict[str, Any]:
        """Send a receipt for the caller's shop. Open in every mode: paying is how a shop leaves one.

        The chat passes `update_key`, the key it derives from the Telegram update, which has a form no
        API caller can send; an API caller must give `request_key`.

        `card` is the number of the card the owner says they paid to; it must be one of the cards offered
        now. The chat, where the card was chosen from the list before the receipt was asked for, passes
        `paid_to` instead: the card's label and last four digits as they were then. Neither is required.
        """
        if paid_to is not None and not 1 <= len(paid_to) <= MAX_PAID_TO:
            paid_to = None
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
            if card is not None:
                offered = platform_settings.payment_cards(await session.platform_setting(CARDS))
                chosen = platform_settings.find_card(offered, card)
                if chosen is None:
                    raise ValidationFailed({"card": "must be the number of one of the cards to pay to"})
                paid_to = platform_settings.card_tag(chosen)
        # Stored before the writing transaction opens: no network call is made while a row is locked.
        staged = await self._files.stage(checked)
        recorded = False
        try:
            async with self._storage.tenant(shop_id) as session:

                async def apply() -> dict[str, Any]:
                    nonlocal recorded
                    body = await self._submit_in(session, user_id, int(amount), int(months), staged, paid_to)
                    recorded = True
                    return body

                body = await idempotency.run_once(
                    session,
                    key=key,
                    operation=SUBMIT_RECEIPT.name,
                    user_id=user_id,
                    request={
                        "amount": amount,
                        "months": months,
                        "receipt": hashlib.sha256(receipt).hexdigest(),
                        # Absent, not null, when no card was named: a request made before cards could be
                        # named is then the same request when it is repeated.
                        **({} if paid_to is None else {"card": paid_to}),
                    },
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
