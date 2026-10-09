"""HTTP routes of the network between shops (module J of the expansion).

Every route here depends on `switched_on` before anything else, so while the platform switch `network_on`
(or the stock's, which it needs) is off each of them answers as a route that does not exist: to a member
of staff, to a stranger, and to someone who is not signed in, alike. Each route is bound to a registered
operation.

A route names the caller's own shop and an object of that shop's own side. No route takes, returns or
searches for another shop's identifier: a partner is reached only by a code that partner handed over.
"""

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.network import (
    ACCEPT_LINK,
    ATTACH_LINK,
    CREATE_INVITE,
    DECLINE_LINK,
    END_LINK,
    OVERVIEW,
    READ_LINK,
    REQUEST_LINK,
    REVOKE_INVITE,
    NetworkService,
)
from qarz.application.network_orders import (
    ACCEPT_ORDER,
    CANCEL_ORDER,
    CONFIRM_NOTE,
    CORRECT_NOTE,
    CREATE_DRAFT,
    DECLINE_ORDER,
    DELETE_DRAFT,
    ISSUE_NOTE,
    LIST_DRAFTS,
    LIST_NOTES,
    LIST_ORDERS,
    READ_DRAFT,
    READ_NOTE,
    READ_ORDER,
    REJECT_NOTE,
    SEND_ORDER,
    UPDATE_DRAFT,
    CountedLine,
    NoteService,
    OfferLine,
    OrderLineRequest,
    OrderRequest,
    OrderService,
    ReceiptLine,
)
from qarz.application.network_payments import (
    CONFIRM_PAYMENT,
    DECLINE_PAYMENT,
    LIST_PAYMENTS,
    READ_PAYMENT,
    RECORD_PAYMENT,
    WITHDRAW_PAYMENT,
    PaymentService,
)
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]


class InviteBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # What this shop will be in the link: "buyer" or "supplier".
    as_: str = Field(alias="as", max_length=10)


class RequestLinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: str = Field(max_length=100)
    as_: str = Field(alias="as", max_length=10)


class CounterpartBody(BaseModel):
    # Not strict: an identifier arrives as a string.
    model_config = ConfigDict(extra="forbid")

    # One of the shop's own suppliers (for a buyer) or customers (for a supplier).
    counterpart_id: UUID


class AcceptLinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counterpart_id: UUID | None = None


class OrderLineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=200)
    unit: str = Field(max_length=12)
    # A decimal string, like every quantity of the API.
    qty: str = Field(max_length=20)
    item_id: UUID | None = None


class OrderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: UUID
    note: str | None = Field(default=None, max_length=400)
    wanted_date: date | None = None
    lines: list[OrderLineBody] = Field(max_length=200)

    def request(self) -> OrderRequest:
        return OrderRequest(
            link_id=self.link_id,
            note=self.note,
            wanted_date=self.wanted_date,
            lines=[OrderLineRequest(line.name, line.unit, line.qty, line.item_id) for line in self.lines],
        )


class OfferLineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_no: Annotated[int, Field(strict=True, ge=1, le=1000)]
    qty: str = Field(max_length=20)
    unit_price: Annotated[int, Field(strict=True)]
    # The supplier's own item for the line; only on an acceptance.
    item_id: UUID | None = None

    def line(self) -> OfferLine:
        return OfferLine(self.line_no, self.qty, self.unit_price, self.item_id)


class AcceptOrderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str | None = Field(default=None, max_length=3)
    lines: list[OfferLineBody] = Field(max_length=200)


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: str = Field(max_length=400)


class DeliverBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # Handed over on delivery, in the order's currency; the rest of the note is on credit.
    paid: int = 0


class CorrectNoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(max_length=400)
    paid: Annotated[int, Field(strict=True)] = 0
    # The lines as they should be, by the order's line numbers; left out, the accepted lines stand.
    lines: list[OfferLineBody] | None = Field(default=None, max_length=200)


class ReceiptLineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_no: Annotated[int, Field(strict=True, ge=1, le=1000)]
    item_id: UUID | None = None
    # A good the shop does not have yet: its selling price (and a barcode); it joins the catalogue.
    new_price: Annotated[int, Field(strict=True)] | None = None
    new_barcode: str | None = Field(default=None, max_length=100)


class ConfirmNoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[ReceiptLineBody] = Field(default_factory=list, max_length=200)
    # How what was paid on delivery was paid: cash, card or transfer (the cash book's).
    method: str | None = Field(default=None, max_length=20)


class CountedLineBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    line_no: int = Field(ge=1, le=1000)
    received_qty: str = Field(max_length=20)


class RejectNoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: str = Field(max_length=400)
    lines: list[CountedLineBody] | None = Field(default=None, max_length=200)


class PaymentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: UUID
    amount: Annotated[int, Field(strict=True)]
    currency: str | None = Field(default=None, max_length=3)
    method: str | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=400)


class ConfirmPaymentBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    method: str | None = Field(default=None, max_length=20)


def add_network_routes(
    app: FastAPI,
    network: NetworkService,
    orders: OrderService,
    notes: NoteService,
    payments: PaymentService,
    current_user: CurrentUser,
) -> None:
    async def switched_on() -> None:
        await network.require_on()

    # A route's own dependencies are resolved before those of its parameters: the switch is asked before
    # the caller is, so an "off" answer is the same with and without a session.
    behind_switch = [Depends(switched_on)]
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/shops/{shop_id}/network"

    # --- links ------------------------------------------------------------------------------------

    @app.get(base, name=OVERVIEW.name, dependencies=behind_switch)
    async def overview(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await network.overview(user_id, shop_id)

    @app.post(base + "/invites", name=CREATE_INVITE.name, status_code=201, dependencies=behind_switch)
    async def create_invite(
        shop_id: UUID, body: InviteBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await network.create_invite(user_id, shop_id, as_role=body.as_, request_key=idempotency_key)

    @app.delete(base + "/invites/{invite_id}", name=REVOKE_INVITE.name, dependencies=behind_switch)
    async def revoke_invite(
        shop_id: UUID, invite_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await network.revoke_invite(user_id, shop_id, invite_id, request_key=idempotency_key)

    @app.post(base + "/links", name=REQUEST_LINK.name, status_code=201, dependencies=behind_switch)
    async def request_link(
        shop_id: UUID, body: RequestLinkBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await network.request_link(
            user_id, shop_id, code=body.code, as_role=body.as_, request_key=idempotency_key
        )

    @app.get(base + "/links/{link_id}", name=READ_LINK.name, dependencies=behind_switch)
    async def read_link(shop_id: UUID, link_id: UUID, user_id: user) -> dict[str, Any]:
        return await network.link(user_id, shop_id, link_id)

    @app.post(base + "/links/{link_id}/accept", name=ACCEPT_LINK.name, dependencies=behind_switch)
    async def accept_link(
        shop_id: UUID,
        link_id: UUID,
        user_id: user,
        body: AcceptLinkBody | None = None,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        return await network.decide(
            user_id,
            shop_id,
            link_id,
            accept=True,
            counterpart_id=None if body is None else body.counterpart_id,
            request_key=idempotency_key,
        )

    @app.post(base + "/links/{link_id}/decline", name=DECLINE_LINK.name, dependencies=behind_switch)
    async def decline_link(
        shop_id: UUID, link_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await network.decide(user_id, shop_id, link_id, accept=False, request_key=idempotency_key)

    @app.post(base + "/links/{link_id}/end", name=END_LINK.name, dependencies=behind_switch)
    async def end_link(
        shop_id: UUID, link_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await network.end(user_id, shop_id, link_id, request_key=idempotency_key)

    @app.put(base + "/links/{link_id}/counterpart", name=ATTACH_LINK.name, dependencies=behind_switch)
    async def attach_link(
        shop_id: UUID, link_id: UUID, body: CounterpartBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await network.attach(
            user_id, shop_id, link_id, counterpart_id=body.counterpart_id, request_key=idempotency_key
        )

    # --- orders -----------------------------------------------------------------------------------

    @app.get(base + "/drafts", name=LIST_DRAFTS.name, dependencies=behind_switch)
    async def list_drafts(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await orders.drafts(user_id, shop_id)

    @app.post(base + "/drafts", name=CREATE_DRAFT.name, status_code=201, dependencies=behind_switch)
    async def create_draft(
        shop_id: UUID, body: OrderBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await orders.save_draft(user_id, shop_id, None, body.request(), request_key=idempotency_key)

    @app.get(base + "/drafts/{draft_id}", name=READ_DRAFT.name, dependencies=behind_switch)
    async def read_draft(shop_id: UUID, draft_id: UUID, user_id: user) -> dict[str, Any]:
        return await orders.draft(user_id, shop_id, draft_id)

    @app.put(base + "/drafts/{draft_id}", name=UPDATE_DRAFT.name, dependencies=behind_switch)
    async def update_draft(
        shop_id: UUID, draft_id: UUID, body: OrderBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await orders.save_draft(user_id, shop_id, draft_id, body.request(), request_key=idempotency_key)

    @app.delete(base + "/drafts/{draft_id}", name=DELETE_DRAFT.name, dependencies=behind_switch)
    async def delete_draft(
        shop_id: UUID, draft_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await orders.delete_draft(user_id, shop_id, draft_id, request_key=idempotency_key)

    @app.post(base + "/drafts/{draft_id}/send", name=SEND_ORDER.name, dependencies=behind_switch)
    async def send_order(
        shop_id: UUID, draft_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await orders.send(user_id, shop_id, draft_id, request_key=idempotency_key)

    @app.get(base + "/orders", name=LIST_ORDERS.name, dependencies=behind_switch)
    async def list_orders(
        shop_id: UUID,
        user_id: user,
        role: Annotated[str | None, Query(max_length=10)] = None,
        status: Annotated[str | None, Query(max_length=20)] = None,
        link: UUID | None = None,
        cursor: Annotated[str | None, Query(max_length=400)] = None,
        limit: Annotated[int, Query()] = 50,
    ) -> dict[str, Any]:
        return await orders.list(user_id, shop_id, role=role, status=status, link_id=link, cursor=cursor, limit=limit)

    @app.get(base + "/orders/{order_id}", name=READ_ORDER.name, dependencies=behind_switch)
    async def read_order(shop_id: UUID, order_id: UUID, user_id: user) -> dict[str, Any]:
        return await orders.read(user_id, shop_id, order_id)

    @app.post(base + "/orders/{order_id}/accept", name=ACCEPT_ORDER.name, dependencies=behind_switch)
    async def accept_order(
        shop_id: UUID, order_id: UUID, body: AcceptOrderBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await orders.accept(
            user_id,
            shop_id,
            order_id,
            currency=body.currency,
            lines=[line.line() for line in body.lines],
            request_key=idempotency_key,
        )

    @app.post(base + "/orders/{order_id}/decline", name=DECLINE_ORDER.name, dependencies=behind_switch)
    async def decline_order(
        shop_id: UUID, order_id: UUID, body: ReasonBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await orders.close(
            user_id, shop_id, order_id, as_buyer=False, reason=body.reason, request_key=idempotency_key
        )

    @app.post(base + "/orders/{order_id}/cancel", name=CANCEL_ORDER.name, dependencies=behind_switch)
    async def cancel_order(
        shop_id: UUID, order_id: UUID, body: ReasonBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await orders.close(
            user_id, shop_id, order_id, as_buyer=True, reason=body.reason, request_key=idempotency_key
        )

    # --- delivery notes ---------------------------------------------------------------------------

    @app.post(base + "/orders/{order_id}/deliver", name=ISSUE_NOTE.name, status_code=201, dependencies=behind_switch)
    async def issue_note(
        shop_id: UUID,
        order_id: UUID,
        user_id: user,
        body: DeliverBody | None = None,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        return await notes.issue(
            user_id, shop_id, order_id=order_id, paid=0 if body is None else body.paid, request_key=idempotency_key
        )

    @app.get(base + "/notes", name=LIST_NOTES.name, dependencies=behind_switch)
    async def list_notes(
        shop_id: UUID,
        user_id: user,
        role: Annotated[str | None, Query(max_length=10)] = None,
        status: Annotated[str | None, Query(max_length=20)] = None,
        link: UUID | None = None,
        cursor: Annotated[str | None, Query(max_length=400)] = None,
        limit: Annotated[int, Query()] = 50,
    ) -> dict[str, Any]:
        return await notes.list(user_id, shop_id, role=role, status=status, link_id=link, cursor=cursor, limit=limit)

    @app.get(base + "/notes/{note_id}", name=READ_NOTE.name, dependencies=behind_switch)
    async def read_note(shop_id: UUID, note_id: UUID, user_id: user) -> dict[str, Any]:
        return await notes.read(user_id, shop_id, note_id)

    @app.post(base + "/notes/{note_id}/correct", name=CORRECT_NOTE.name, status_code=201, dependencies=behind_switch)
    async def correct_note(
        shop_id: UUID, note_id: UUID, body: CorrectNoteBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await notes.issue(
            user_id,
            shop_id,
            corrects=note_id,
            paid=body.paid,
            reason=body.reason,
            lines=None if body.lines is None else [line.line() for line in body.lines],
            request_key=idempotency_key,
        )

    @app.post(base + "/notes/{note_id}/confirm", name=CONFIRM_NOTE.name, dependencies=behind_switch)
    async def confirm_note(
        shop_id: UUID,
        note_id: UUID,
        user_id: user,
        body: ConfirmNoteBody | None = None,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        chosen = [] if body is None else body.lines
        return await notes.confirm(
            user_id,
            shop_id,
            note_id,
            lines=[ReceiptLine(line.line_no, line.item_id, line.new_price, line.new_barcode) for line in chosen],
            method=None if body is None else body.method,
            request_key=idempotency_key,
        )

    @app.post(base + "/notes/{note_id}/reject", name=REJECT_NOTE.name, dependencies=behind_switch)
    async def reject_note(
        shop_id: UUID, note_id: UUID, body: RejectNoteBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await notes.reject(
            user_id,
            shop_id,
            note_id,
            reason=body.reason,
            lines=None if body.lines is None else [CountedLine(line.line_no, line.received_qty) for line in body.lines],
            request_key=idempotency_key,
        )

    # --- payments ---------------------------------------------------------------------------------

    @app.get(base + "/payments", name=LIST_PAYMENTS.name, dependencies=behind_switch)
    async def list_payments(
        shop_id: UUID,
        user_id: user,
        status: Annotated[str | None, Query(max_length=20)] = None,
        link: UUID | None = None,
        cursor: Annotated[str | None, Query(max_length=400)] = None,
        limit: Annotated[int, Query()] = 50,
    ) -> dict[str, Any]:
        return await payments.list(user_id, shop_id, status=status, link_id=link, cursor=cursor, limit=limit)

    @app.post(base + "/payments", name=RECORD_PAYMENT.name, status_code=201, dependencies=behind_switch)
    async def record_payment(
        shop_id: UUID, body: PaymentBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await payments.record(
            user_id,
            shop_id,
            link_id=body.link_id,
            amount=body.amount,
            currency=body.currency,
            method=body.method,
            note=body.note,
            request_key=idempotency_key,
        )

    @app.get(base + "/payments/{payment_id}", name=READ_PAYMENT.name, dependencies=behind_switch)
    async def read_payment(shop_id: UUID, payment_id: UUID, user_id: user) -> dict[str, Any]:
        return await payments.read(user_id, shop_id, payment_id)

    @app.post(base + "/payments/{payment_id}/confirm", name=CONFIRM_PAYMENT.name, dependencies=behind_switch)
    async def confirm_payment(
        shop_id: UUID,
        payment_id: UUID,
        user_id: user,
        body: ConfirmPaymentBody | None = None,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        return await payments.decide(
            user_id,
            shop_id,
            payment_id,
            confirm=True,
            method=None if body is None else body.method,
            request_key=idempotency_key,
        )

    @app.post(base + "/payments/{payment_id}/decline", name=DECLINE_PAYMENT.name, dependencies=behind_switch)
    async def decline_payment(
        shop_id: UUID, payment_id: UUID, body: ReasonBody, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await payments.decide(
            user_id, shop_id, payment_id, confirm=False, reason=body.reason, request_key=idempotency_key
        )

    @app.post(base + "/payments/{payment_id}/withdraw", name=WITHDRAW_PAYMENT.name, dependencies=behind_switch)
    async def withdraw_payment(
        shop_id: UUID, payment_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await payments.withdraw(user_id, shop_id, payment_id, request_key=idempotency_key)
