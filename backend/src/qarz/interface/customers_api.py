"""HTTP routes for customers, ledger entries and the overview. Each route is bound to a registered operation."""

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.customers import (
    ARCHIVE_CUSTOMER,
    CREATE_CUSTOMER,
    LIST_CUSTOMERS,
    UNARCHIVE_CUSTOMER,
    UNSET,
    UPDATE_CUSTOMER,
    CustomerService,
)
from qarz.application.goods_lines import ADD_LINES, LineRequest
from qarz.application.ledger_service import (
    CHOOSE_PROMISE,
    LIST_DEBTORS,
    READ_CUSTOMER,
    READ_OVERVIEW,
    RECORD_ENTRY,
    REVERSE_ENTRY,
    LedgerService,
)
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]


class NewCustomer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    display_name: str = Field(max_length=200)
    phone: str | None = Field(default=None, max_length=40)


class CustomerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    display_name: str | None = Field(default=None, max_length=200)
    # Absent leaves the phone as it is; null removes it.
    phone: str | None = Field(default=None, max_length=40)
    reminders_off: bool | None = None
    # Absent leaves the limit as it is; null removes it, and the shop default then applies.
    credit_limit: int | None = None


class GoodsLine(BaseModel):
    # Not strict as a whole: an identifier arrives as a string. The price stays strict so "4000" or
    # 4000.0 is refused; a quantity is a decimal string, which the application reads.
    model_config = ConfigDict(extra="forbid")

    catalog_item_id: UUID | None = None
    name: str | None = Field(default=None, max_length=200)
    qty: str = Field(max_length=24)
    unit: str | None = Field(default=None, max_length=40)
    unit_price: int = Field(strict=True)

    def request(self) -> LineRequest:
        return LineRequest(self.catalog_item_id, self.name, self.qty, self.unit, self.unit_price)


class NewEntry(BaseModel):
    # Not strict: a date arrives as a string. `amount` stays strict so "45000" or 45000.0 is refused.
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(max_length=16)
    # May be left out only when goods lines are given: it is then their sum.
    amount: int | None = Field(default=None, strict=True)
    note: str | None = Field(default=None, max_length=400)
    promised_date: date | None = None
    lines: list[GoodsLine] | None = None


class NewLines(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[GoodsLine]


class PromiseChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    promised_date: date


def add_customer_routes(
    app: FastAPI, customers: CustomerService, ledger: LedgerService, current_user: CurrentUser
) -> None:
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/shops/{shop_id}/customers"

    @app.post(base, name=CREATE_CUSTOMER.name, status_code=201)
    async def create_customer(
        shop_id: UUID, body: NewCustomer, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await customers.create(user_id, shop_id, body.display_name, body.phone, idempotency_key)

    @app.get(base, name=LIST_CUSTOMERS.name)
    async def list_customers(
        shop_id: UUID,
        user_id: user,
        q: Annotated[str | None, Query()] = None,
        status: Annotated[str, Query()] = "active",
        cursor: Annotated[str | None, Query(max_length=400)] = None,
        limit: Annotated[int, Query()] = 50,
    ) -> dict[str, Any]:
        return await customers.list(user_id, shop_id, query=q, status=status, cursor=cursor, limit=limit)

    @app.get(base + "/{customer_id}", name=READ_CUSTOMER.name)
    async def read_customer(shop_id: UUID, customer_id: UUID, user_id: user) -> dict[str, Any]:
        return await ledger.customer_detail(user_id, shop_id, customer_id)

    @app.patch(base + "/{customer_id}", name=UPDATE_CUSTOMER.name)
    async def update_customer(
        shop_id: UUID, customer_id: UUID, body: CustomerPatch, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await customers.update(
            user_id,
            shop_id,
            customer_id,
            display_name=body.display_name,
            phone=body.phone if "phone" in body.model_fields_set else UNSET,
            reminders_off=body.reminders_off,
            request_key=idempotency_key,
            credit_limit=body.credit_limit if "credit_limit" in body.model_fields_set else UNSET,
        )

    @app.post(base + "/{customer_id}/archive", name=ARCHIVE_CUSTOMER.name)
    async def archive_customer(
        shop_id: UUID, customer_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await customers.set_archived(user_id, shop_id, customer_id, archived=True, request_key=idempotency_key)

    @app.post(base + "/{customer_id}/unarchive", name=UNARCHIVE_CUSTOMER.name)
    async def unarchive_customer(
        shop_id: UUID, customer_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await customers.set_archived(user_id, shop_id, customer_id, archived=False, request_key=idempotency_key)

    @app.post(base + "/{customer_id}/entries", name=RECORD_ENTRY.name, status_code=201)
    async def record_entry(
        shop_id: UUID, customer_id: UUID, body: NewEntry, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await ledger.record(
            user_id,
            shop_id,
            customer_id,
            kind=body.kind,
            amount=body.amount,
            note=body.note,
            promised_date=body.promised_date,
            request_key=idempotency_key,
            lines=None if body.lines is None else [line.request() for line in body.lines],
        )

    @app.post("/api/v1/shops/{shop_id}/entries/{entry_id}/lines", name=ADD_LINES.name, status_code=201)
    async def add_lines(
        shop_id: UUID, entry_id: UUID, body: NewLines, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await ledger.add_lines(
            user_id, shop_id, entry_id, [line.request() for line in body.lines], idempotency_key
        )

    @app.post("/api/v1/shops/{shop_id}/entries/{entry_id}/reversal", name=REVERSE_ENTRY.name, status_code=201)
    async def reverse_entry(
        shop_id: UUID, entry_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await ledger.reverse(user_id, shop_id, entry_id, idempotency_key)

    @app.post("/api/v1/shops/{shop_id}/entries/{entry_id}/promise-choice", name=CHOOSE_PROMISE.name)
    async def choose_promise(
        shop_id: UUID, entry_id: UUID, body: PromiseChoice, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await ledger.choose_promise(user_id, shop_id, entry_id, body.promised_date, idempotency_key)

    @app.get("/api/v1/shops/{shop_id}/overview", name=READ_OVERVIEW.name)
    async def overview(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await ledger.overview(user_id, shop_id)

    @app.get("/api/v1/shops/{shop_id}/overview/debtors", name=LIST_DEBTORS.name)
    async def debtors(
        shop_id: UUID,
        user_id: user,
        overdue: Annotated[bool, Query()] = False,
        cursor: Annotated[str | None, Query(max_length=400)] = None,
        limit: Annotated[int, Query()] = 50,
    ) -> dict[str, Any]:
        return await ledger.debtors(user_id, shop_id, only_overdue=overdue, cursor=cursor, limit=limit)
