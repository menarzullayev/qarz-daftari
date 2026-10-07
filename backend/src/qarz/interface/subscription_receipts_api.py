"""HTTP routes for paying the subscription by card transfer: the owner sends a receipt and sees what
became of the receipts sent before (REQ-054, REQ-055).

A receipt is sent as `multipart/form-data` with the fields `amount`, `months` and `receipt`. The body is
read with a hard limit and only after the caller is known to be the shop's owner.
"""

import re
from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Request

from qarz.application.errors import ValidationFailed
from qarz.application.subscription_receipts import LIST_RECEIPTS, SUBMIT_RECEIPT, SubscriptionReceiptService
from qarz.interface.body_limit import Allowance
from qarz.interface.payment_notices_api import MAX_BODY_BYTES, parse_form, read_limited
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]

_PATH = "/api/v1/shops/{shop_id}/subscription/receipts"
_FIELDS = frozenset({"amount", "months", "receipt"})
# The route whose body may exceed the general limit: it carries the receipt.
SUBSCRIPTION_RECEIPT_UPLOAD = Allowance(
    "POST",
    re.compile(r"/api/v1/shops/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}/subscription/receipts"),
    MAX_BODY_BYTES,
)


def _whole_number(raw: bytes | None, field: str) -> int:
    if raw is None:
        raise ValidationFailed({field: "required"})
    # Anything that is not ASCII becomes a replacement character here, so only the digits 0-9 pass below.
    text = raw.decode("ascii", "replace").strip()
    # Digits only: no sign, no separators, no exponent.
    if not text.isdigit() or len(text) > 12:
        raise ValidationFailed({field: "a whole number"})
    return int(text)


def parse_receipt_form(content_type: str, body: bytes) -> tuple[int, int, bytes]:
    """Amount, months and file of a new receipt. Their bounds are the application's to check."""
    if content_type.split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise ValidationFailed({"_": "send multipart/form-data with the fields amount, months and receipt"})
    fields = parse_form(content_type, body, _FIELDS)
    amount, months = _whole_number(fields.get("amount"), "amount"), _whole_number(fields.get("months"), "months")
    if "receipt" not in fields:
        raise ValidationFailed({"receipt": "required"})
    return amount, months, fields["receipt"]


def add_subscription_receipt_routes(
    app: FastAPI, service: SubscriptionReceiptService, current_user: CurrentUser
) -> None:
    user = Annotated[UUID, Depends(current_user)]

    @app.post(_PATH, name=SUBMIT_RECEIPT.name, status_code=201)
    async def submit_receipt(
        shop_id: UUID, request: Request, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        # Who is asking comes first: for anyone but the owner nothing of the body is read.
        await service.require_owner(user_id, shop_id, idempotency_key)
        body = await read_limited(request, MAX_BODY_BYTES)
        amount, months, receipt = parse_receipt_form(request.headers.get("content-type", ""), body)
        return await service.submit(user_id, shop_id, amount, months, receipt, idempotency_key)

    @app.get(_PATH, name=LIST_RECEIPTS.name)
    async def list_receipts(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.history(user_id, shop_id)
