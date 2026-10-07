"""HTTP routes for paying the subscription online: the owner's order, and the two providers' calls.

The provider routes are not API operations: nobody signs in to them. Each request is checked by the
provider's own signature, and while the adapter is switched off the answer is "disabled" before anything
of the request is read (ADR-019).
"""

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from urllib.parse import parse_qsl
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from qarz.application.online_payment import CREATE_ONLINE_ORDER, OnlinePaymentService
from qarz.domain.online_payment import CLICK, PAYME
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]

MAX_BODY = 16 * 1024  # a provider's call is a few hundred bytes


class OnlineOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # How many is checked by the operation, after the caller is known to be the owner.
    months: int


def _disabled() -> JSONResponse:
    return JSONResponse({"status": "disabled"}, status_code=503)


async def _body(request: Request) -> bytes | None:
    """The request body, or None when it is larger than any provider call is.

    Read piece by piece and given up on as soon as it is too large, so that a body of any size, declared
    or not, costs no more than the limit to refuse.
    """
    pieces: list[bytes] = []
    size = 0
    async for piece in request.stream():
        size += len(piece)
        if size > MAX_BODY:
            return None
        pieces.append(piece)
    return b"".join(pieces)


def add_online_order_routes(app: FastAPI, service: OnlinePaymentService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]

    @app.post("/api/v1/shops/{shop_id}/subscription/online-orders", name=CREATE_ONLINE_ORDER.name, status_code=201)
    async def create_online_order(
        shop_id: UUID, body: OnlineOrder, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.create_order(user_id, shop_id, body.months, idempotency_key)


def add_provider_routes(app: FastAPI, service: OnlinePaymentService) -> None:
    @app.post("/pay/payme", include_in_schema=False)
    async def payme(request: Request) -> JSONResponse:
        if not await service.enabled(PAYME):
            return _disabled()
        raw = await _body(request)
        if raw is None:
            return JSONResponse({"status": "too large"}, status_code=413)
        try:
            body: Any = json.loads(raw)
        except ValueError:
            body = None
        answer = await service.payme(request.headers.get("authorization"), body)
        # Payme reads errors from the answer's body; the HTTP status is 200 for every answered call.
        return _disabled() if answer is None else JSONResponse(answer)

    @app.post("/pay/click", include_in_schema=False)
    async def click(request: Request) -> JSONResponse:
        if not await service.enabled(CLICK):
            return _disabled()
        raw = await _body(request)
        if raw is None:
            return JSONResponse({"status": "too large"}, status_code=413)
        try:
            # Click posts an ordinary form. A field given twice keeps its last value.
            fields = dict(parse_qsl(raw.decode("utf-8"), keep_blank_values=True))
        except ValueError:
            fields = {}
        answer = await service.click(fields)
        return _disabled() if answer is None else JSONResponse(answer)
