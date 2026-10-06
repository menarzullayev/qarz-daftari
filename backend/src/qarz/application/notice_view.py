"""How a payment notice looks in the API, for the customer and for staff alike."""

from datetime import datetime
from typing import Any
from uuid import UUID

from qarz.application.ports import PaymentNoticeRecord, TenantSession
from qarz.domain.payment_notices import NOTICE_LIFETIME, effective_status

# How many of a customer's notices their own page shows.
NOTICES_SHOWN = 20


def notice_body(record: PaymentNoticeRecord, now: datetime) -> dict[str, Any]:
    return {
        "id": str(record.notice_id),
        # A notice nobody decided in fourteen days reads as expired even before the hourly job marks it.
        "status": effective_status(record.status, record.created_at, now),
        "amount": record.amount,
        "recorded_amount": record.recorded_amount,
        "payment_entry_id": None if record.payment_entry is None else str(record.payment_entry),
        "has_receipt": record.file_id is not None,
        "decline_reason": record.decline_reason,
        "created_at": record.created_at.isoformat(),
        "closed_at": None if record.closed_at is None else record.closed_at.isoformat(),
        "expires_at": (record.created_at + NOTICE_LIFETIME).isoformat(),
    }


async def open_notices_of(session: TenantSession, customer_id: UUID, now: datetime) -> list[dict[str, Any]]:
    """The customer's notices still waiting for the shop, oldest first."""
    waiting = await session.open_payment_notices(now - NOTICE_LIFETIME)
    return [notice_body(record, now) for record, _ in waiting if record.customer_id == customer_id]
