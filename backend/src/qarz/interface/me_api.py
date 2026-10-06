"""HTTP routes for a person's own customer accounts and an owner's combined totals."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI

from qarz.application.customer_account import (
    DISCONNECT,
    LIST_ACCOUNTS,
    OWNER_TOTALS,
    READ_ACCOUNT,
    REQUEST_REMOVAL,
    CustomerAccountService,
)

CurrentUser = Callable[..., Awaitable[UUID]]


def add_me_routes(app: FastAPI, service: CustomerAccountService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/me/accounts"

    @app.get(base, name=LIST_ACCOUNTS.name)
    async def list_accounts(user_id: user) -> dict[str, Any]:
        return await service.accounts(user_id)

    @app.get(base + "/{link_id}", name=READ_ACCOUNT.name)
    async def read_account(link_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.account(user_id, link_id)

    @app.post(base + "/{link_id}/disconnect", name=DISCONNECT.name)
    async def disconnect(link_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.disconnect(user_id, link_id)

    @app.post(base + "/{link_id}/removal", name=REQUEST_REMOVAL.name)
    async def request_removal(link_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.request_removal(user_id, link_id)

    @app.get("/api/v1/me/owner-totals", name=OWNER_TOTALS.name)
    async def owner_totals(user_id: user) -> dict[str, Any]:
        return await service.owner_totals(user_id)
