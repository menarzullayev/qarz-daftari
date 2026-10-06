"""HTTP route for the shop's subscription as its owner sees it."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI

from qarz.application.subscription import READ_SUBSCRIPTION, SubscriptionService

CurrentUser = Callable[..., Awaitable[UUID]]


def add_subscription_routes(app: FastAPI, service: SubscriptionService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]

    @app.get("/api/v1/shops/{shop_id}/subscription", name=READ_SUBSCRIPTION.name)
    async def read_subscription(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.state(user_id, shop_id)
