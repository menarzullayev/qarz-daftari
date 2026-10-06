"""HTTP routes for reminders: settings, a manual reminder, and who cannot be reached."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict

from qarz.application.reminders import (
    LIST_UNREACHABLE,
    READ_REMINDER_SETTINGS,
    SEND_REMINDER,
    UPDATE_REMINDER_SETTINGS,
    ReminderService,
)
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    on: bool | None = None
    hour: int | None = None
    template: int | None = None
    sms_on: bool | None = None


class Manual(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: UUID


def add_reminder_routes(app: FastAPI, service: ReminderService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/shops/{shop_id}/reminders"

    @app.get(base, name=READ_REMINDER_SETTINGS.name)
    async def read_settings(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.settings(user_id, shop_id)

    @app.patch(base, name=UPDATE_REMINDER_SETTINGS.name)
    async def update_settings(
        shop_id: UUID, body: SettingsPatch, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.update_settings(
            user_id,
            shop_id,
            on=body.on,
            hour=body.hour,
            template=body.template,
            sms_on=body.sms_on,
            request_key=idempotency_key,
        )

    @app.post(base + "/manual", name=SEND_REMINDER.name, status_code=201)
    async def send_manual(
        shop_id: UUID, body: Manual, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.send_manual(user_id, shop_id, body.customer_id, idempotency_key)

    @app.get(base + "/unreachable", name=LIST_UNREACHABLE.name)
    async def unreachable(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.unreachable(user_id, shop_id)
