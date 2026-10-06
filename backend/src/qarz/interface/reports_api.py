"""HTTP routes for the reports of a shop (REQ-046)."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Query

from qarz.application.reports import REPORT_OVERDUE, REPORT_PERIOD, ReportService

CurrentUser = Callable[..., Awaitable[UUID]]


def add_report_routes(app: FastAPI, service: ReportService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/shops/{shop_id}/reports"

    # The dates arrive as plain text and are checked by the operation, after the caller is known to be a
    # member: someone outside the shop gets "not found" whatever the query says.
    @app.get(base + "/period", name=REPORT_PERIOD.name)
    async def period(
        shop_id: UUID,
        user_id: user,
        first: Annotated[str | None, Query(alias="from")] = None,
        last: Annotated[str | None, Query(alias="to")] = None,
    ) -> dict[str, Any]:
        return await service.period(user_id, shop_id, raw_first=first, raw_last=last)

    @app.get(base + "/overdue", name=REPORT_OVERDUE.name)
    async def overdue(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.overdue(user_id, shop_id)
