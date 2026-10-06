"""HTTP routes for the shop's credit settings."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict

from qarz.application.credit import READ_CREDIT_SETTINGS, UNSET, UPDATE_CREDIT_SETTINGS, CreditService
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]


class CreditPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # Absent leaves the default as it is; null removes it.
    default_credit_limit: int | None = None
    sellers_may_exceed: bool | None = None


def add_credit_routes(app: FastAPI, service: CreditService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]
    path = "/api/v1/shops/{shop_id}/credit-settings"

    @app.get(path, name=READ_CREDIT_SETTINGS.name)
    async def read_settings(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.settings(user_id, shop_id)

    @app.patch(path, name=UPDATE_CREDIT_SETTINGS.name)
    async def update_settings(
        shop_id: UUID, body: CreditPatch, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.update(
            user_id,
            shop_id,
            default_credit_limit=body.default_credit_limit
            if "default_credit_limit" in body.model_fields_set
            else UNSET,
            sellers_may_exceed=body.sellers_may_exceed,
            request_key=idempotency_key,
        )
