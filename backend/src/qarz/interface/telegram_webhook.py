"""Telegram webhook endpoint (technical specification, "Webhook")."""

import hmac
import logging
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from qarz.application.telegram_updates import UpdateProcessor

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105  (a header name, not a secret)

log = logging.getLogger("qarz.webhook")


def add_webhook_route(app: FastAPI, processor: UpdateProcessor, secret: str) -> None:
    if len(secret) < 16:
        raise ValueError("the webhook secret is too short")
    expected = secret.encode("utf-8")

    @app.post("/tg/webhook", include_in_schema=False)
    async def telegram_webhook(request: Request) -> Response:
        given = request.headers.get(SECRET_HEADER, "").encode("utf-8")
        # Constant-time comparison; the body is not even read when the secret is wrong.
        if not hmac.compare_digest(given, expected):
            return Response(status_code=403)

        try:
            update: Any = await request.json()
        except ValueError:
            return Response(status_code=400)
        update_id = update.get("update_id") if isinstance(update, dict) else None
        # bool is a subclass of int in Python; `true` is not an update identifier.
        if not isinstance(update_id, int) or isinstance(update_id, bool):
            return Response(status_code=400)

        try:
            processed = await processor.handle(update)
        except Exception:
            # 500 makes Telegram deliver the update again; the failed transaction recorded nothing.
            log.exception("update_failed", extra={"update_id": update.get("update_id")})
            return Response(status_code=500)
        if processed.webhook_reply is not None:
            # Telegram runs one Bot API call given as the response body.
            return JSONResponse(processed.webhook_reply)
        return Response(status_code=200)
