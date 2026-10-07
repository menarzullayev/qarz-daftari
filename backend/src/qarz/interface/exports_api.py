"""HTTP routes for exports: a manager or owner asks for one, lists the shop's jobs, and gets a download link."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI

from qarz.application.exports import DOWNLOAD_EXPORT, LIST_EXPORTS, REQUEST_EXPORT, ExportService
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]


def add_export_routes(app: FastAPI, service: ExportService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]

    @app.post("/api/v1/shops/{shop_id}/exports", name=REQUEST_EXPORT.name, status_code=201)
    async def request_export(shop_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
        return await service.request(user_id, shop_id, idempotency_key)

    @app.get("/api/v1/shops/{shop_id}/exports", name=LIST_EXPORTS.name)
    async def list_exports(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.list(user_id, shop_id)

    @app.get("/api/v1/shops/{shop_id}/exports/{job_id}/download", name=DOWNLOAD_EXPORT.name)
    async def download_export(shop_id: UUID, job_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.download(user_id, shop_id, job_id)
