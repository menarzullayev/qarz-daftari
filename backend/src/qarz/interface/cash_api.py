"""HTTP routes of the cash book (expansion module H).

Every route here depends on `switched_on` before anything else, so while the platform switch
`cash_book_on` is off each of them answers as a route that does not exist: to a member of staff, to a
stranger, and to someone who is not signed in, alike.

`POST .../cash/export` writes one period as a workbook (`qarz.application.cash_export`) and answers with
a link to it, as a download of the shop's export does.
"""

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.cash_book import (
    BACKFILL,
    CANCEL_CASH,
    CREATE_CATEGORY,
    DELETE_CATEGORY,
    LIST_CATEGORIES,
    READ_DAY,
    READ_SUMMARY,
    RECORD_CASH,
    UPDATE_CATEGORY,
    CashBookService,
)
from qarz.application.cash_export import EXPORT_CASH, CashExportService
from qarz.domain.cash import PAGE
from qarz.interface.cash_answers import CashCategories, CashDay, CashSummary
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]


class NewCashEntry(BaseModel):
    # Not strict: an identifier and a date arrive as strings. `amount` stays strict so "45000" or
    # 45000.0 is refused.
    model_config = ConfigDict(extra="forbid")

    direction: str = Field(max_length=16)
    method: str = Field(max_length=16)
    # Left out: UZS.
    currency: str | None = Field(default=None, max_length=8)
    # A whole number of the currency's minor unit.
    amount: int = Field(strict=True)
    category_id: UUID
    note: str | None = Field(default=None, max_length=400)
    # The Tashkent day the money belongs to. Left out: today.
    day: date | None = None


class CashCancellationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: str = Field(max_length=1000)


class NewCashCategory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    direction: str = Field(max_length=16)
    name: str = Field(max_length=200)


class CashCategoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # What is left out stays as it is.
    name: str | None = Field(default=None, max_length=200)
    archived: bool | None = None


class CashBackfill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Payments recorded before this Tashkent day are left out. Null: every payment that stands.
    since: date | None = None


class CashExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # The first and the last Tashkent day of the period, as "YYYY-MM-DD". Plain text, checked by the
    # operation after the caller is known to be a member, like the period of the summary.
    first: str = Field(alias="from", max_length=32)
    last: str = Field(alias="to", max_length=32)


def add_cash_routes(
    app: FastAPI, service: CashBookService, current_user: CurrentUser, exports: CashExportService | None = None
) -> None:
    async def switched_on() -> None:
        await service.require_on()

    # A route's own dependencies are resolved before those of its parameters: the switch is asked before
    # the caller is, so an "off" answer is the same with and without a session.
    behind_switch = [Depends(switched_on)]
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/shops/{shop_id}/cash"

    # The dates arrive as plain text and are checked by the operation, after the caller is known to be a
    # member: someone outside the shop gets "not found" whatever the query says.
    @app.get(base + "/day", name=READ_DAY.name, response_model=CashDay, dependencies=behind_switch)
    async def read_day(
        shop_id: UUID,
        user_id: user,
        day: Annotated[str | None, Query(alias="date")] = None,
        cursor: str | None = None,
        limit: int = PAGE,
    ) -> dict[str, Any]:
        return await service.day(user_id, shop_id, raw_day=day, cursor=cursor, limit=limit)

    @app.get(base + "/summary", name=READ_SUMMARY.name, response_model=CashSummary, dependencies=behind_switch)
    async def read_summary(
        shop_id: UUID,
        user_id: user,
        first: Annotated[str | None, Query(alias="from")] = None,
        last: Annotated[str | None, Query(alias="to")] = None,
    ) -> dict[str, Any]:
        return await service.summary(user_id, shop_id, raw_first=first, raw_last=last)

    if exports is not None:
        period_export = exports

        @app.post(base + "/export", name=EXPORT_CASH.name, status_code=201, dependencies=behind_switch)
        async def export_period(
            shop_id: UUID, body: CashExportRequest, user_id: user, idempotency_key: IdempotencyKey = None
        ) -> dict[str, Any]:
            return await period_export.export(
                user_id, shop_id, raw_first=body.first, raw_last=body.last, request_key=idempotency_key
            )

    @app.post(base + "/entries", name=RECORD_CASH.name, status_code=201, dependencies=behind_switch)
    async def record_entry(
        shop_id: UUID, body: NewCashEntry, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.record(
            user_id,
            shop_id,
            direction=body.direction,
            method=body.method,
            currency=body.currency,
            amount=body.amount,
            category_id=body.category_id,
            note=body.note,
            day=body.day,
            request_key=idempotency_key,
        )

    @app.post(
        base + "/entries/{entry_id}/cancellation", name=CANCEL_CASH.name, status_code=201, dependencies=behind_switch
    )
    async def cancel_entry(
        shop_id: UUID,
        entry_id: UUID,
        body: CashCancellationRequest,
        user_id: user,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        return await service.cancel(user_id, shop_id, entry_id, body.reason, idempotency_key)

    @app.get(base + "/categories", name=LIST_CATEGORIES.name, response_model=CashCategories, dependencies=behind_switch)
    async def list_categories(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.categories(user_id, shop_id)

    @app.post(base + "/categories", name=CREATE_CATEGORY.name, status_code=201, dependencies=behind_switch)
    async def create_category(
        shop_id: UUID, body: NewCashCategory, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.create_category(
            user_id, shop_id, direction=body.direction, name=body.name, request_key=idempotency_key
        )

    @app.patch(base + "/categories/{category_id}", name=UPDATE_CATEGORY.name, dependencies=behind_switch)
    async def update_category(
        shop_id: UUID,
        category_id: UUID,
        body: CashCategoryPatch,
        user_id: user,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        return await service.update_category(
            user_id, shop_id, category_id, name=body.name, archived=body.archived, request_key=idempotency_key
        )

    @app.delete(base + "/categories/{category_id}", name=DELETE_CATEGORY.name, dependencies=behind_switch)
    async def delete_category(
        shop_id: UUID, category_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.delete_category(user_id, shop_id, category_id, idempotency_key)

    @app.post(base + "/backfill", name=BACKFILL.name, dependencies=behind_switch)
    async def backfill(
        shop_id: UUID, body: CashBackfill, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.backfill(user_id, shop_id, since=body.since, request_key=idempotency_key)
