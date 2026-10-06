"""HTTP routes for payment notices: the customer sends one, any staff member lists, accepts or declines.

A notice is sent as JSON (`{"amount": 50000}`) or, with a receipt, as `multipart/form-data` with the
fields `amount` and `receipt`. The body is read with a hard limit and only after the caller's link has
been checked, so nobody can make the service hold more than one receipt's worth of bytes.
"""

import json
from collections.abc import Awaitable, Callable
from email import policy
from email.parser import BytesParser
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.errors import ValidationFailed
from qarz.application.payment_notices import (
    ACCEPT_NOTICE,
    DECLINE_NOTICE,
    LIST_NOTICES,
    READ_RECEIPT,
    SEND_NOTICE,
    PaymentNoticeService,
)
from qarz.domain.files import MAX_FILE_BYTES
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]

# The receipt plus room for the form's own boundaries and the amount field.
MAX_BODY_BYTES = MAX_FILE_BYTES + 16 * 1024
_FIELDS = {"amount", "receipt"}


class Accept(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # Absent or null records the amount the customer stated; a number corrects it (BR-14).
    amount: int | None = None


class Decline(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: str = Field(max_length=1000)


async def read_limited(request: Request, limit: int) -> bytes:
    """The request body, refused as soon as it is known to be longer than `limit`."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ValidationFailed({"receipt": "too_large"})
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ValidationFailed({"receipt": "too_large"})
        chunks.append(chunk)
    return b"".join(chunks)


def parse_form(content_type: str, body: bytes) -> dict[str, bytes]:
    """The parts of a `multipart/form-data` body by field name, each as the bytes that were sent.

    Parsed with the standard library's MIME parser. A part's own file name and content type are ignored:
    what a file is, is decided from its bytes.
    """
    if "\r" in content_type or "\n" in content_type:
        raise ValidationFailed({"_": "malformed form"})
    head = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("latin-1", "replace")
    message = BytesParser(policy=policy.HTTP).parsebytes(head + body)
    if not message.is_multipart() or message.defects:
        raise ValidationFailed({"_": "malformed form"})
    fields: dict[str, bytes] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        content = part.get_payload(decode=True)
        if not isinstance(name, str) or name not in _FIELDS or name in fields or not isinstance(content, bytes):
            raise ValidationFailed({"_": "the form has the fields amount and receipt, each at most once"})
        fields[name] = content
    return fields


def _whole_number(raw: bytes) -> int:
    # Anything that is not ASCII becomes a replacement character here, so only the digits 0-9 pass below.
    text = raw.decode("ascii", "replace").strip()
    # Digits only: no sign, no separators, no exponent. Twelve digits are far above any amount accepted.
    if not text.isdigit() or len(text) > 12:
        raise ValidationFailed({"amount": "a whole number of UZS"})
    return int(text)


def parse_notice(content_type: str, body: bytes) -> tuple[Any, bytes | None]:
    """Amount and receipt of a new notice. The amount's bounds are the application's to check."""
    kind = content_type.split(";", 1)[0].strip().lower()
    if kind == "application/json":
        try:
            data = json.loads(body)
        except ValueError:
            raise ValidationFailed({"_": "malformed JSON"}) from None
        if not isinstance(data, dict) or set(data) != {"amount"}:
            raise ValidationFailed({"amount": "required, and the only field"})
        return data["amount"], None
    if kind == "multipart/form-data":
        fields = parse_form(content_type, body)
        if "amount" not in fields:
            raise ValidationFailed({"amount": "required"})
        return _whole_number(fields["amount"]), fields.get("receipt")
    raise ValidationFailed({"_": "send application/json or multipart/form-data"})


def add_payment_notice_routes(app: FastAPI, service: PaymentNoticeService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]

    @app.post("/api/v1/me/accounts/{link_id}/payment-notices", name=SEND_NOTICE.name, status_code=201)
    async def send_notice(
        link_id: UUID, request: Request, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        # Whose link it is comes first: for anyone else the account does not exist, whatever they send.
        await service.require_link(user_id, link_id)
        body = await read_limited(request, MAX_BODY_BYTES)
        amount, receipt = parse_notice(request.headers.get("content-type", ""), body)
        return await service.send(user_id, link_id, amount, receipt, idempotency_key)

    @app.get("/api/v1/shops/{shop_id}/payment-notices", name=LIST_NOTICES.name)
    async def list_notices(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.list_open(user_id, shop_id)

    @app.post("/api/v1/shops/{shop_id}/payment-notices/{notice_id}/accept", name=ACCEPT_NOTICE.name)
    async def accept_notice(
        shop_id: UUID,
        notice_id: UUID,
        user_id: user,
        body: Accept | None = None,
        idempotency_key: IdempotencyKey = None,
    ) -> dict[str, Any]:
        return await service.accept(user_id, shop_id, notice_id, None if body is None else body.amount, idempotency_key)

    @app.post("/api/v1/shops/{shop_id}/payment-notices/{notice_id}/decline", name=DECLINE_NOTICE.name)
    async def decline_notice(
        shop_id: UUID, notice_id: UUID, body: Decline, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.decline(user_id, shop_id, notice_id, body.reason, idempotency_key)

    @app.get("/api/v1/shops/{shop_id}/payment-notices/{notice_id}/receipt", name=READ_RECEIPT.name)
    async def read_receipt(shop_id: UUID, notice_id: UUID, user_id: user) -> Response:
        mime, name, content = await service.receipt(user_id, shop_id, notice_id)
        return Response(
            content,
            media_type=mime,
            headers={
                # Always a download, never rendered in the page that asked: the bytes came from a customer.
                "Content-Disposition": f'attachment; filename="{name}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )
