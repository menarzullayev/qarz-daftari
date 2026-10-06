"""Reports for managers and owners (REQ-046): one period of Tashkent days, and overdue debt by age.

Every figure is derived from the ledger when asked for; nothing is stored. An entry and its reversal
cancel out wherever each of them falls: a sale recorded in the period and reversed after it is in neither
the credit given nor the balances, and its reversal shows only in the reversals of the period it was
recorded in. The balances are therefore the ledger as it is known now, cut at an instant, and
`start + credit + opening - payments = end` holds exactly.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from qarz.application.customers import require_viewable
from qarz.application.errors import ValidationFailed
from qarz.application.operations import operation
from qarz.application.ports import Storage
from qarz.application.shops import require_member
from qarz.domain.access import Capability
from qarz.domain.promise import tashkent_date
from qarz.domain.reports import (
    AGE_BANDS,
    AgeBand,
    PeriodProblem,
    age_band,
    due_before,
    on_time_percent,
    parse_day,
    period_bounds,
    period_days,
    validate_period,
)

REPORT_PERIOD = operation("reports.period", Capability.MANAGE)
REPORT_OVERDUE = operation("reports.overdue", Capability.MANAGE)

TOP_DEBTORS = 10


def clean_period(raw_first: str | None, raw_last: str | None, today: date) -> tuple[date, date]:
    first, last = parse_day(raw_first), parse_day(raw_last)
    fields = {name: PeriodProblem.DATE_INVALID.value for name, day in (("from", first), ("to", last)) if day is None}
    if first is None or last is None:
        raise ValidationFailed(fields)
    problem = validate_period(first, last, today)
    if problem is not None:
        raise ValidationFailed({"from" if problem is PeriodProblem.FROM_AFTER_TO else "to": problem.value})
    return first, last


class ReportService:
    def __init__(self, storage: Storage, now: Callable[[], datetime] | None = None) -> None:
        self._storage = storage
        self._now = now or (lambda: datetime.now(UTC))

    def _today(self) -> date:
        return tashkent_date(self._now())

    async def period(
        self, user_id: UUID, shop_id: UUID, *, raw_first: str | None, raw_last: str | None
    ) -> dict[str, Any]:
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, REPORT_PERIOD)
            today = self._today()
            await require_viewable(session, actor, today)
            first, last = clean_period(raw_first, raw_last, today)
            start, end = period_bounds(first, last)

            totals = await session.period_totals(start, end)
            by_day = {figures.day: figures for figures in await session.period_days(start, end)}
            staff = await session.period_staff(start, end)
            debtors = await session.debtors_as_of(end, TOP_DEBTORS)
            on_time_amount, due_amount = await session.fell_due(first, due_before(last, today))
            return {
                "from": first.isoformat(),
                "to": last.isoformat(),
                "outstanding": {"start": totals.outstanding_start, "end": totals.outstanding_end},
                "credit": {
                    "amount": totals.credit_amount,
                    "count": totals.credit_count,
                    "customers": totals.credit_customers,
                },
                "payments": {
                    "amount": totals.payment_amount,
                    "count": totals.payment_count,
                    "customers": totals.payment_customers,
                },
                "opening": {"amount": totals.opening_amount, "count": totals.opening_count},
                "net_change": totals.outstanding_end - totals.outstanding_start,
                "reversals": {"amount": totals.reversal_amount, "count": totals.reversal_count},
                "new_customers": totals.new_customers,
                "disputes_opened": totals.disputes_opened,
                "on_time": {
                    "due_amount": due_amount,
                    "on_time_amount": on_time_amount,
                    "percent": on_time_percent(on_time_amount, due_amount),
                },
                "days": [
                    {
                        "date": day.isoformat(),
                        "credit": by_day[day].credit if day in by_day else 0,
                        "payments": by_day[day].payments if day in by_day else 0,
                    }
                    for day in period_days(first, last)
                ],
                "top_debtors": [
                    {"customer_id": str(customer_id), "display_name": name, "balance": balance}
                    for customer_id, name, balance in debtors
                ],
                "staff": [
                    {
                        "membership_id": str(member.membership_id),
                        "role": member.role.value,
                        "credit": {"amount": member.credit_amount, "count": member.credit_count},
                        "payments": {"amount": member.payment_amount, "count": member.payment_count},
                    }
                    for member in staff
                ],
            }

    async def overdue(self, user_id: UUID, shop_id: UUID) -> dict[str, Any]:
        """Overdue debt by how long past its promised date it is, as of today. Disputed entries count (BR-13)."""
        async with self._storage.tenant(shop_id) as session:
            actor = await require_member(session, user_id, REPORT_OVERDUE)
            today = self._today()
            await require_viewable(session, actor, today)
            amounts = {band: 0 for band, _, _ in AGE_BANDS}
            customers: dict[AgeBand, set[UUID]] = {band: set() for band, _, _ in AGE_BANDS}
            for debt in await session.uncovered_debts():
                band = age_band(debt.promised_date, today)
                if band is not None:
                    amounts[band] += debt.remaining
                    customers[band].add(debt.customer_id)
            return {
                "as_of": today.isoformat(),
                "total": {
                    "amount": sum(amounts.values()),
                    "customers": len(set().union(*customers.values())),
                },
                "bands": [
                    {
                        "band": band.value,
                        "from_days": first_day,
                        "to_days": last_day,
                        "amount": amounts[band],
                        "customers": len(customers[band]),
                    }
                    for band, first_day, last_day in AGE_BANDS
                ],
            }
