"""Production wiring of the HTTP application.

Run with:  uvicorn qarz.interface.asgi:build --factory
"""

from fastapi import FastAPI

from qarz.application.auth import AuthService
from qarz.application.online_payment import PaymentKeys
from qarz.infrastructure.db import Database
from qarz.infrastructure.settings import Settings
from qarz.interface.http import create_app


def build(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    database = Database(settings.database_url)
    # Without a bot token no Telegram signature can be verified, so no API is served at all.
    auth = AuthService(database, settings.bot_token) if settings.bot_token else None
    return create_app(
        database.reachable,
        database,
        auth=auth,
        webhook_secret=settings.webhook_secret or None,
        payment_keys=PaymentKeys(
            payme_merchant_id=settings.payme_merchant_id,
            payme_key=settings.payme_secret_key,
            click_service_id=settings.click_service_id,
            click_merchant_id=settings.click_merchant_id,
            click_key=settings.click_secret_key,
        ),
    )
