"""Removing a customer's identifying data (REQ-029, BR-32).

Carried out at once when the customer owes nothing; otherwise recorded, and carried out by the entry that
brings the balance to zero. The amounts stay in the ledger under an anonymous label, so totals stay
correct. Everything here runs inside a tenant transaction that the caller has opened.
"""

from datetime import datetime
from uuid import UUID

from qarz.application.ports import TenantSession
from qarz.domain.names import normalize_name


def anonymous_label(customer_id: UUID) -> str:
    """What staff see in place of the name. The same in every language, and says nothing about the person."""
    return f"Anonim {customer_id.hex[:6].upper()}"


async def _anonymize(session: TenantSession, customer_id: UUID, now: datetime) -> None:
    label = anonymous_label(customer_id)
    await session.anonymize_customer(customer_id, label=label, name_norm=normalize_name(label), now=now)
    await session.record_customer_activity(action="customer.anonymized", subject_id=customer_id)


async def request_removal(session: TenantSession, customer_id: UUID, balance: int, now: datetime) -> str:
    """Returns "done" when the data was removed now, "waiting" when it will be once the debt is settled."""
    if balance == 0:
        await session.close_removal_request(customer_id, now)
        await _anonymize(session, customer_id, now)
        return "done"
    await session.open_removal_request(customer_id, now)
    await session.record_customer_activity(action="customer.removal_requested", subject_id=customer_id)
    return "waiting"


async def complete_if_due(session: TenantSession, customer_id: UUID, balance: int, now: datetime) -> bool:
    """Called after every entry: carry out a waiting removal request once nothing is owed."""
    if balance != 0 or not await session.removal_waiting(customer_id):
        return False
    await session.close_removal_request(customer_id, now)
    await _anonymize(session, customer_id, now)
    return True
