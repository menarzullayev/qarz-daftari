"""HTTP routes for the import of customers with opening balances (REQ-062, REQ-063).

The file is sent as the request body itself, an `.xlsx` workbook or UTF-8 CSV: what it is, is decided
from its bytes, never from a name or a declared type. The body is read with a hard limit and only after
the caller has been found to be a manager or owner of the shop who may import now.
"""

import re
from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from qarz.application.errors import ValidationFailed
from qarz.application.imports import (
    APPLY_IMPORT,
    DISCARD_IMPORT,
    IMPORT_TEMPLATE,
    LIST_IMPORTS,
    READ_IMPORT,
    UNDO_IMPORT,
    UPLOAD_IMPORT,
    ImportService,
)
from qarz.domain.files import MAX_FILE_BYTES
from qarz.domain.imports import XLSX_MIME
from qarz.interface.body_limit import Allowance
from qarz.interface.shops_api import IdempotencyKey

CurrentUser = Callable[..., Awaitable[UUID]]

# The one import route whose body may exceed the general limit: the upload of the file.
IMPORT_UPLOAD = Allowance(
    "POST",
    re.compile(r"/api/v1/shops/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}/imports"),
    MAX_FILE_BYTES,
)
TEMPLATE_NAME = "qarz-daftari-import.xlsx"


class Apply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # The `plan` of the preview that was shown: applying anything else is refused.
    plan: str = Field(min_length=1, max_length=64)


async def read_file(request: Request) -> bytes:
    """The request body, refused as soon as it is known to be longer than a file may be."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_FILE_BYTES:
        raise ValidationFailed({"file": "too_large"})
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_FILE_BYTES:
            raise ValidationFailed({"file": "too_large"})
        chunks.append(chunk)
    return b"".join(chunks)


def add_import_routes(app: FastAPI, service: ImportService, current_user: CurrentUser) -> None:
    user = Annotated[UUID, Depends(current_user)]
    base = "/api/v1/shops/{shop_id}/imports"

    # Before the routes that take an import's identifier, so that "template" is never read as one.
    @app.get(base + "/template", name=IMPORT_TEMPLATE.name)
    async def template(shop_id: UUID, user_id: user) -> Response:
        content = await service.template(user_id, shop_id)
        headers = {"Content-Disposition": f'attachment; filename="{TEMPLATE_NAME}"', "Cache-Control": "no-store"}
        return Response(content, media_type=XLSX_MIME, headers=headers)

    @app.post(base, name=UPLOAD_IMPORT.name, status_code=201)
    async def upload(
        shop_id: UUID, request: Request, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        await service.may_upload(user_id, shop_id, idempotency_key)
        return await service.upload(user_id, shop_id, await read_file(request), idempotency_key)

    @app.get(base, name=LIST_IMPORTS.name)
    async def list_imports(shop_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.list(user_id, shop_id)

    @app.get(base + "/{import_id}", name=READ_IMPORT.name)
    async def read_import(shop_id: UUID, import_id: UUID, user_id: user) -> dict[str, Any]:
        return await service.read(user_id, shop_id, import_id)

    @app.post(base + "/{import_id}/apply", name=APPLY_IMPORT.name)
    async def apply_import(
        shop_id: UUID, import_id: UUID, body: Apply, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.apply(user_id, shop_id, import_id, body.plan, idempotency_key)

    @app.post(base + "/{import_id}/undo", name=UNDO_IMPORT.name)
    async def undo_import(
        shop_id: UUID, import_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.undo(user_id, shop_id, import_id, idempotency_key)

    @app.post(base + "/{import_id}/discard", name=DISCARD_IMPORT.name)
    async def discard_import(
        shop_id: UUID, import_id: UUID, user_id: user, idempotency_key: IdempotencyKey = None
    ) -> dict[str, Any]:
        return await service.discard(user_id, shop_id, import_id, idempotency_key)
