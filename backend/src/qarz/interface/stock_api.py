"""HTTP routes of the stock, its documents and the suppliers (module I of the expansion).

Every route here depends on `switched_on` before anything else, so while the platform switch `stock_on`
is off each of them answers as a route that does not exist: to a member of staff, to a stranger, and to
someone who is not signed in, alike. Each route is bound to a registered operation.
"""

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.stock import (
    DEFAULT_IDLE_DAYS,
    LIST_ITEMS,
    LIST_MOVEMENTS,
    LOOKUP,
    READ_ITEM,
    READ_REPORT,
    READ_SETTINGS,
    UPDATE_ITEM,
    UPDATE_SETTINGS,
    StockService,
)
from qarz.application.stock_documents import (
    CANCEL_DOCUMENT,
    CREATE_DOCUMENT,
    LIST_DOCUMENTS,
    POST_DOCUMENT,
    READ_DOCUMENT,
    UPDATE_DOCUMENT,
    DocumentRequest,
    DocumentService,
    LineRequest,
    NewItemRequest,
)
from qarz.application.suppliers import (
    ADD_ENTRY,
    ARCHIVE_SUPPLIER,
    CANCEL_ENTRY,
    CREATE_SUPPLIER,
    LIST_SUPPLIERS,
    READ_SUPPLIER,
    UNARCHIVE_SUPPLIER,
    UPDATE_SUPPLIER,
    SupplierService,
)
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]


class StockSettingsChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    refuse_negative: bool


class ItemStockPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tracked: bool | None = None
    # A decimal string, like every quantity of the API.
    low_stock: str | None = Field(default=None, max_length=20)
    clear_low_stock: bool = False
    unit: str | None = Field(default=None, max_length=12)
    barcodes: list[Annotated[str, Field(max_length=100)]] | None = Field(default=None, max_length=20)


class NewItemBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(max_length=200)
    unit: str | None = Field(default=None, max_length=12)
    price: int
    barcode: str | None = Field(default=None, max_length=100)


class DocumentLineBody(BaseModel):
    # Not strict: an identifier arrives as a string.
    model_config = ConfigDict(extra="forbid")

    item_id: UUID | None = None
    new_item: NewItemBody | None = None
    qty: str = Field(max_length=20)
    unit_cost: Annotated[int, Field(strict=True)] | None = None


class DocumentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(max_length=20)
    doc_date: date | None = None
    supplier_id: UUID | None = None
    customer_id: UUID | None = None
    currency: str | None = Field(default=None, max_length=3)
    paid: Annotated[int, Field(strict=True)] | None = None
    reason: str | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=400)
    # How what is paid at once was paid: cash, card or transfer (the cash book's).
    method: str | None = Field(default=None, max_length=20)
    lines: list[DocumentLineBody] = Field(max_length=400)

    def request(self) -> DocumentRequest:
        return DocumentRequest(
            kind=self.kind,
            doc_date=self.doc_date,
            supplier_id=self.supplier_id,
            customer_id=self.customer_id,
            currency=self.currency,
            paid=self.paid,
            reason=self.reason,
            note=self.note,
            method=self.method,
            lines=[
                LineRequest(
                    item_id=line.item_id,
                    new_item=None
                    if line.new_item is None
                    else NewItemRequest(
                        line.new_item.name, line.new_item.unit, line.new_item.price, line.new_item.barcode
                    ),
                    qty=line.qty,
                    unit_cost=line.unit_cost,
                )
                for line in self.lines
            ],
        )


class NewDocumentBody(DocumentBody):
    # Write it and post it in one step: what the counter does for a quick receipt.
    post: Annotated[bool, Field(strict=True)] = False


class CancelBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: str = Field(max_length=400)


class SupplierBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=400)


class SupplierEntryBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: str = Field(max_length=20)
    amount: int
    currency: str | None = Field(default=None, max_length=3)
    note: str | None = Field(default=None, max_length=400)
    method: str | None = Field(default=None, max_length=20)


def add_stock_routes(
    app: FastAPI,
    stock: StockService,
    documents: DocumentService,
    suppliers: SupplierService,
    current_user: CurrentUser,
) -> None:
    async def switched_on() -> None:
        await stock.require_on()

    # A route's own dependencies are resolved before those of its parameters: the switch is asked before
    # the caller is, so an "off" answer is the same with and without a session.
    behind_switch = [Depends(switched_on)]
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/shops/{shop_id}/stock"
    people = "/api/v1/shops/{shop_id}/suppliers"

    # --- the stock --------------------------------------------------------------------------------

    @app.get(base + "/settings", name=READ_SETTINGS.name, dependencies=behind_switch)
    async def read_settings(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await stock.settings(user_id, shop_id)

    @app.put(base + "/settings", name=UPDATE_SETTINGS.name, dependencies=behind_switch)
    async def update_settings(
        shop_id: UUID, body: StockSettingsChange, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await stock.update_settings(
            user_id, shop_id, refuse_negative=body.refuse_negative, request_key=idempotency_key
        )

    @app.get(base + "/items", name=LIST_ITEMS.name, dependencies=behind_switch)
    async def list_items(
        shop_id: UUID,
        user_id: user,
        q: Annotated[str | None, Query()] = None,
        only: Annotated[str, Query(alias="filter")] = "tracked",
        cursor: Annotated[str | None, Query(max_length=400)] = None,
        limit: Annotated[int, Query()] = 50,
    ) -> dict[str, Any]:
        return await stock.items(user_id, shop_id, query=q, only=only, cursor=cursor, limit=limit)

    @app.get(base + "/lookup", name=LOOKUP.name, dependencies=behind_switch)
    async def lookup(shop_id: UUID, user_id: user, code: Annotated[str, Query(max_length=100)]) -> dict[str, Any]:
        return await stock.lookup(user_id, shop_id, code)

    @app.get(base + "/report", name=READ_REPORT.name, dependencies=behind_switch)
    async def report(shop_id: UUID, user_id: user, days: Annotated[int, Query()] = DEFAULT_IDLE_DAYS) -> dict[str, Any]:
        return await stock.report(user_id, shop_id, days=days)

    @app.get(base + "/items/{item_id}", name=READ_ITEM.name, dependencies=behind_switch)
    async def read_item(shop_id: UUID, item_id: UUID, user_id: user) -> dict[str, Any]:
        return await stock.item(user_id, shop_id, item_id)

    @app.patch(base + "/items/{item_id}", name=UPDATE_ITEM.name, dependencies=behind_switch)
    async def update_item(
        shop_id: UUID, item_id: UUID, body: ItemStockPatch, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await stock.update_item(
            user_id,
            shop_id,
            item_id,
            tracked=body.tracked,
            low_stock=body.low_stock,
            clear_low_stock=body.clear_low_stock,
            unit=body.unit,
            barcodes=body.barcodes,
            request_key=idempotency_key,
        )

    @app.get(base + "/items/{item_id}/movements", name=LIST_MOVEMENTS.name, dependencies=behind_switch)
    async def list_movements(
        shop_id: UUID,
        item_id: UUID,
        user_id: user,
        cursor: Annotated[str | None, Query(max_length=400)] = None,
        limit: Annotated[int, Query()] = 50,
    ) -> dict[str, Any]:
        return await stock.movements(user_id, shop_id, item_id, cursor=cursor, limit=limit)

    # --- documents --------------------------------------------------------------------------------

    @app.get(base + "/documents", name=LIST_DOCUMENTS.name, dependencies=behind_switch)
    async def list_documents(
        shop_id: UUID,
        user_id: user,
        kind: Annotated[str | None, Query(max_length=20)] = None,
        status: Annotated[str | None, Query(max_length=20)] = None,
        supplier_id: UUID | None = None,
        cursor: Annotated[str | None, Query(max_length=400)] = None,
        limit: Annotated[int, Query()] = 50,
    ) -> dict[str, Any]:
        return await documents.list(
            user_id, shop_id, kind=kind, status=status, supplier_id=supplier_id, cursor=cursor, limit=limit
        )

    @app.post(base + "/documents", name=CREATE_DOCUMENT.name, status_code=201, dependencies=behind_switch)
    async def create_document(
        shop_id: UUID, body: NewDocumentBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await documents.create(user_id, shop_id, body.request(), post=body.post, request_key=idempotency_key)

    @app.get(base + "/documents/{document_id}", name=READ_DOCUMENT.name, dependencies=behind_switch)
    async def read_document(shop_id: UUID, document_id: UUID, user_id: user) -> dict[str, Any]:
        return await documents.read(user_id, shop_id, document_id)

    @app.put(base + "/documents/{document_id}", name=UPDATE_DOCUMENT.name, dependencies=behind_switch)
    async def update_document(
        shop_id: UUID, document_id: UUID, body: DocumentBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await documents.update(user_id, shop_id, document_id, body.request(), request_key=idempotency_key)

    @app.post(base + "/documents/{document_id}/post", name=POST_DOCUMENT.name, dependencies=behind_switch)
    async def post_document(
        shop_id: UUID, document_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await documents.post(user_id, shop_id, document_id, request_key=idempotency_key)

    @app.post(base + "/documents/{document_id}/cancel", name=CANCEL_DOCUMENT.name, dependencies=behind_switch)
    async def cancel_document(
        shop_id: UUID, document_id: UUID, body: CancelBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await documents.cancel(user_id, shop_id, document_id, reason=body.reason, request_key=idempotency_key)

    # --- suppliers --------------------------------------------------------------------------------

    @app.get(people, name=LIST_SUPPLIERS.name, dependencies=behind_switch)
    async def list_suppliers(
        shop_id: UUID,
        user_id: user,
        q: Annotated[str | None, Query()] = None,
        status: Annotated[str, Query()] = "active",
        cursor: Annotated[str | None, Query(max_length=400)] = None,
        limit: Annotated[int, Query()] = 50,
    ) -> dict[str, Any]:
        return await suppliers.list(user_id, shop_id, query=q, status=status, cursor=cursor, limit=limit)

    @app.post(people, name=CREATE_SUPPLIER.name, status_code=201, dependencies=behind_switch)
    async def create_supplier(
        shop_id: UUID, body: SupplierBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await suppliers.create(
            user_id, shop_id, name=body.name, phone=body.phone, note=body.note, request_key=idempotency_key
        )

    @app.get(people + "/{supplier_id}", name=READ_SUPPLIER.name, dependencies=behind_switch)
    async def read_supplier(
        shop_id: UUID,
        supplier_id: UUID,
        user_id: user,
        cursor: Annotated[str | None, Query(max_length=400)] = None,
        limit: Annotated[int, Query()] = 50,
    ) -> dict[str, Any]:
        return await suppliers.detail(user_id, shop_id, supplier_id, cursor=cursor, limit=limit)

    @app.put(people + "/{supplier_id}", name=UPDATE_SUPPLIER.name, dependencies=behind_switch)
    async def update_supplier(
        shop_id: UUID, supplier_id: UUID, body: SupplierBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await suppliers.update(
            user_id, shop_id, supplier_id, name=body.name, phone=body.phone, note=body.note, request_key=idempotency_key
        )

    @app.post(people + "/{supplier_id}/archive", name=ARCHIVE_SUPPLIER.name, dependencies=behind_switch)
    async def archive_supplier(
        shop_id: UUID, supplier_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await suppliers.set_archived(user_id, shop_id, supplier_id, archived=True, request_key=idempotency_key)

    @app.post(people + "/{supplier_id}/unarchive", name=UNARCHIVE_SUPPLIER.name, dependencies=behind_switch)
    async def unarchive_supplier(
        shop_id: UUID, supplier_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await suppliers.set_archived(user_id, shop_id, supplier_id, archived=False, request_key=idempotency_key)

    @app.post(people + "/{supplier_id}/entries", name=ADD_ENTRY.name, status_code=201, dependencies=behind_switch)
    async def add_supplier_entry(
        shop_id: UUID, supplier_id: UUID, body: SupplierEntryBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await suppliers.add_entry(
            user_id,
            shop_id,
            supplier_id,
            kind=body.kind,
            amount=body.amount,
            currency=body.currency,
            note=body.note,
            method=body.method,
            request_key=idempotency_key,
        )

    @app.post(people + "/{supplier_id}/entries/{entry_id}/cancel", name=CANCEL_ENTRY.name, dependencies=behind_switch)
    async def cancel_supplier_entry(
        shop_id: UUID,
        supplier_id: UUID,
        entry_id: UUID,
        body: CancelBody,
        user_id: user,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        return await suppliers.cancel_entry(
            user_id, shop_id, supplier_id, entry_id, reason=body.reason, request_key=idempotency_key
        )
