"""Subscription receipts on the administrator's side: the queue, one receipt, approve, reject
(REQ-055; BR-27; INV-16; ADR-019).

An approval extends the shop's paid period by the months the administrator records, by the same rule as
an online payment (`qarz.domain.subscription.after_payment`), and the owner is told; a rejection tells
the owner why. A receipt is decided once: the row is locked while it is decided, and the database changes
only a receipt that still waits. Every decision, and every look at one receipt, is in the admin audit.

Every method here assumes the caller already passed `AdminAccess.require_admin`.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from qarz.application import idempotency
from qarz.application.admin import MAX_PAGE, _decode_cursor, _encode_cursor, _iso
from qarz.application.admin_access import AdminRequestKeys
from qarz.application.chat_texts import day, money, say
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.files import FileService, FileStoreUnavailable
from qarz.application.operations import admin_operation
from qarz.application.ports import AdminReceipt, PlatformSession, Storage
from qarz.domain.promise import tashkent_date
from qarz.domain.subscription import MAX_MONTHS, after_payment
from qarz.domain.subscription_receipts import (
    APPROVED,
    MAX_REASON,
    MIN_REASON,
    REJECTED,
    STATUSES,
    SUBMITTED,
    clean_reason,
    months_to_record,
)

LIST_RECEIPTS = admin_operation("admin.receipts.list")
READ_RECEIPT = admin_operation("admin.receipts.read")
APPROVE_RECEIPT = admin_operation("admin.receipts.approve")
REJECT_RECEIPT = admin_operation("admin.receipts.reject")

FILE_LINK_PATH = "/files"
# Where a decision was made: the administrator's panel, or a button in their private chat with the bot.
PANEL, CHAT = "panel", "chat"
_REASON_HINT = f"length must be between {MIN_REASON} and {MAX_REASON}"


class ReceiptAlreadyDecided(AppError):
    """The receipt was approved or rejected before; `fields.status` says which. Nothing was changed."""

    code = "RECEIPT_ALREADY_DECIDED"


def receipt_body(row: AdminReceipt) -> dict[str, Any]:
    return {
        "id": str(row.receipt_id),
        "shop_id": str(row.shop_id),
        "shop_name": row.shop_name,
        "stated_amount": row.stated_amount,
        "stated_months": row.stated_months,
        "status": row.status,
        "months": row.months,
        "reject_reason": row.reject_reason,
        "created_at": row.created_at.isoformat(),
        "decided_at": _iso(row.decided_at),
        "decided_by": None if row.decided_by is None else str(row.decided_by),
        # Set instead of `decided_by` when a Telegram administrator of the review group decided (DEC-064).
        "decided_by_tg_id": row.decided_by_tg,
        # The card the owner says they paid to: its label and last four digits. Null when not said.
        "paid_to_card": row.paid_to_card,
        "has_file": row.has_file,
    }


class AdminReceiptService:
    def __init__(self, storage: Storage, files: FileService, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._files = files
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def list_receipts(
        self, admin_id: UUID, *, status: str | None, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        """Receipts of one status across all shops, oldest first; those awaiting a decision by default."""
        fields: dict[str, str] = {}
        if not 1 <= limit <= MAX_PAGE:
            fields["limit"] = f"must be between 1 and {MAX_PAGE}"
        wanted = SUBMITTED if status is None else status
        if wanted not in STATUSES:
            fields["status"] = "must be submitted, approved or rejected"
        if fields:
            raise ValidationFailed(fields)
        after = _decode_cursor(cursor) if cursor else None
        async with self._storage.platform() as session:
            rows = await session.admin_receipts(admin_id, status=wanted, after=after, limit=limit + 1)
        page, more = rows[:limit], len(rows) > limit
        return {
            # `copies`: how many other receipts, of any shop, carry the same file (ADR-019).
            "items": [{**receipt_body(row), "copies": row.copies} for row in page],
            "next_cursor": _encode_cursor(page[-1].created_at, page[-1].receipt_id) if more else None,
        }

    async def read_receipt(self, admin_id: UUID, receipt_id: UUID) -> dict[str, Any]:
        """One receipt, the receipts that carry the same file, and a link to the file valid five minutes.

        The look is audited. The file is served by the link, not here.
        """
        now = self._now()
        async with self._storage.platform() as session:
            row = await session.admin_receipt(admin_id, receipt_id, lock=False)
            if row is None:
                raise NotFound()
            copies = await session.admin_receipt_copies(admin_id, receipt_id)
            await session.add_admin_audit(
                admin_id=admin_id,
                action="receipt.viewed",
                target_type="receipt",
                target_id=str(receipt_id),
                shop_id=row.shop_id,
                reason=None,
                detail={},
                now=now,
            )
        file: dict[str, Any] | None = None
        if row.file is not None:
            try:
                token, expires_at = self._files.link(row.shop_id, row.file, now)
                file = {"url": f"{FILE_LINK_PATH}/{token}", "expires_at": expires_at.isoformat()}
            except (NotFound, FileStoreUnavailable):
                # Past its retention, or no file store: the receipt is still shown, without its file.
                file = None
        return {
            **receipt_body(row),
            "file": file,
            "copies": [
                {
                    "id": str(copy.receipt_id),
                    "shop_id": str(copy.shop_id),
                    "shop_name": copy.shop_name,
                    "stated_amount": copy.stated_amount,
                    "status": copy.status,
                    "created_at": copy.created_at.isoformat(),
                }
                for copy in copies
            ],
        }

    async def _shop_of(self, admin_id: UUID, receipt_id: UUID) -> UUID:
        async with self._storage.platform() as session:
            row = await session.admin_receipt(admin_id, receipt_id, lock=False)
        if row is None:
            raise NotFound()
        return row.shop_id

    @staticmethod
    async def _locked_waiting(session: PlatformSession, admin_id: UUID, receipt_id: UUID) -> AdminReceipt:
        """The receipt, locked, if it still waits: two decisions cannot both find it waiting."""
        row = await session.admin_receipt(admin_id, receipt_id, lock=True)
        if row is None:
            raise NotFound()
        if row.status != SUBMITTED:
            raise ReceiptAlreadyDecided({"status": row.status})
        return row

    @staticmethod
    async def _record(
        session: PlatformSession,
        admin_id: UUID,
        row: AdminReceipt,
        *,
        status: str,
        months: int | None,
        reason: str | None,
        detail: dict[str, Any],
        now: datetime,
    ) -> AdminReceipt:
        """Write the decision, the audit, the shop's activity and the measure; return the receipt as it is now."""
        decided = await session.admin_decide_receipt(
            admin_id, row.receipt_id, status=status, months=months, reason=reason, now=now
        )
        if not decided:
            # The account was disabled, or the shop erased, between the lock and here.
            raise NotFound()
        await session.add_admin_audit(
            admin_id=admin_id,
            # Under "subscription.", so the shop's page shows it with the other changes to its subscription.
            action=f"subscription.receipt_{status}",
            target_type="receipt",
            target_id=str(row.receipt_id),
            shop_id=row.shop_id,
            reason=reason,
            detail={"stated_amount": row.stated_amount, "stated_months": row.stated_months, **detail},
            now=now,
        )
        await session.admin_shop_activity(
            admin_id, row.shop_id, action=f"subscription.receipt_{status}", subject_id=row.receipt_id
        )
        await session.record_shop_measure(
            row.shop_id, kind=f"subscription_receipt_{status}", entry_ref=row.receipt_id, amount=row.stated_amount
        )
        after = await session.admin_receipt(admin_id, row.receipt_id, lock=False)
        if after is None:
            raise NotFound()
        return after

    async def require_waiting(self, admin_id: UUID, receipt_id: UUID) -> None:
        """Refuse at once a receipt that does not exist or was decided, before a reason is asked for."""
        async with self._storage.platform() as session:
            row = await session.admin_receipt(admin_id, receipt_id, lock=False)
        if row is None:
            raise NotFound()
        if row.status != SUBMITTED:
            raise ReceiptAlreadyDecided({"status": row.status})

    async def approve(
        self,
        admin_id: UUID,
        receipt_id: UUID,
        months: int | None,
        reason: str | None,
        request_key: str | None,
        *,
        update_key: str | None = None,
        via: str = PANEL,
    ) -> dict[str, Any]:
        """Approve a waiting receipt for the months stated, or for `months` when the administrator corrects them.

        The chat passes `update_key`, the key it derives from the Telegram update, and `via=CHAT`; the
        audit row says where the decision came from.
        """
        key = update_key if update_key is not None else idempotency.validate_key(request_key)
        why = None
        if reason is not None and " ".join(reason.split()):
            why = clean_reason(reason)
            if why is None:
                raise ValidationFailed({"reason": _REASON_HINT})
        shop_id = await self._shop_of(admin_id, receipt_id)
        now, today = self._now(), self._today()
        async with self._storage.platform() as session:

            async def apply() -> dict[str, Any]:
                row = await self._locked_waiting(session, admin_id, receipt_id)
                paid_for = months_to_record(row.stated_months, months)
                if paid_for is None:
                    raise ValidationFailed({"months": f"must be between 1 and {MAX_MONTHS}"})
                locked = await session.admin_lock_subscription(admin_id, row.shop_id)
                if locked is None:
                    raise NotFound()
                state, paid_through, prior_state = after_payment(
                    locked.state, locked.trial_ends, locked.paid_through, today, paid_for
                )
                # Should the account have been disabled since the lock, this changes nothing and the decision
                # below, which then changes nothing either, ends the request.
                await session.admin_store_subscription(
                    admin_id,
                    row.shop_id,
                    state=state,
                    trial_ends=locked.trial_ends,
                    paid_through=paid_through,
                    prior_state=prior_state,
                    now=now,
                )
                after = await self._record(
                    session,
                    admin_id,
                    row,
                    status=APPROVED,
                    months=paid_for,
                    reason=why,
                    detail={
                        "via": via,
                        "months": paid_for,
                        "before": {"state": locked.state, "paid_through": _iso(locked.paid_through)},
                        "after": {"state": state, "paid_through": paid_through.isoformat()},
                    },
                    now=now,
                )
                if locked.owner_tg is not None:
                    lang = locked.owner_lang or "uz"
                    text = say(
                        lang, "sub_receipt_approved", shop=locked.shop_name, months=paid_for, date=day(paid_through)
                    )
                    await session.enqueue(
                        channel="telegram",
                        recipient=str(locked.owner_tg),
                        payload={"text": text},
                        dedupe_key=f"subreceipt:{receipt_id}:decided",
                        shop_id=row.shop_id,
                    )
                return {
                    **receipt_body(after),
                    "subscription": {"state": state, "paid_through": paid_through.isoformat()},
                }

            return await idempotency.run_once(
                # The stored answer names the shop; it is kept under the shop and erased with it.
                AdminRequestKeys(session, admin_id, about_shop=shop_id),
                key=key,
                operation=APPROVE_RECEIPT.name,
                user_id=admin_id,
                request={"receipt": str(receipt_id), "months": months, "reason": why},
                action=apply,
            )

    async def reject(
        self,
        admin_id: UUID,
        receipt_id: UUID,
        reason: str | None,
        request_key: str | None,
        *,
        update_key: str | None = None,
        via: str = PANEL,
    ) -> dict[str, Any]:
        """Reject a waiting receipt. The reason is required and is told to the owner as written."""
        key = update_key if update_key is not None else idempotency.validate_key(request_key)
        why = clean_reason(reason)
        if why is None:
            raise ValidationFailed({"reason": _REASON_HINT})
        shop_id = await self._shop_of(admin_id, receipt_id)
        now = self._now()
        async with self._storage.platform() as session:

            async def apply() -> dict[str, Any]:
                row = await self._locked_waiting(session, admin_id, receipt_id)
                # Read for where to reach the owner; the subscription itself is not changed.
                locked = await session.admin_lock_subscription(admin_id, row.shop_id)
                after = await self._record(
                    session, admin_id, row, status=REJECTED, months=None, reason=why, detail={"via": via}, now=now
                )
                if locked is not None and locked.owner_tg is not None:
                    lang = locked.owner_lang or "uz"
                    text = say(
                        lang,
                        "sub_receipt_rejected",
                        shop=locked.shop_name,
                        amount=money(lang, row.stated_amount),
                        reason=why,
                    )
                    await session.enqueue(
                        channel="telegram",
                        recipient=str(locked.owner_tg),
                        payload={"text": text},
                        dedupe_key=f"subreceipt:{receipt_id}:decided",
                        shop_id=row.shop_id,
                    )
                return receipt_body(after)

            return await idempotency.run_once(
                AdminRequestKeys(session, admin_id, about_shop=shop_id),
                key=key,
                operation=REJECT_RECEIPT.name,
                user_id=admin_id,
                request={"receipt": str(receipt_id), "reason": why},
                action=apply,
            )
