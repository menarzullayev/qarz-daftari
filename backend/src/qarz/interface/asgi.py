"""Production wiring of the HTTP application.

Run with:  uvicorn qarz.interface.asgi:build --factory
"""

from fastapi import FastAPI

from qarz.application.auth import AuthService
from qarz.application.online_payment import PaymentKeys
from qarz.domain.files import MAX_FILE_BYTES
from qarz.infrastructure.db import Database
from qarz.infrastructure.file_store import build_file_store
from qarz.infrastructure.settings import Settings
from qarz.infrastructure.telegram_files import TelegramFileFetcher
from qarz.interface.http import create_app
from qarz.interface.rate_limit import Limit, RateLimits


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
        # A store that is named but misconfigured stops the start; none at all only refuses receipts.
        file_store=build_file_store(settings, max_object_bytes=MAX_FILE_BYTES),
        telegram_files=TelegramFileFetcher.for_token(settings.bot_token) if settings.bot_token else None,
        payment_keys=PaymentKeys(
            payme_merchant_id=settings.payme_merchant_id,
            payme_key=settings.payme_secret_key,
            click_service_id=settings.click_service_id,
            click_merchant_id=settings.click_merchant_id,
            click_key=settings.click_secret_key,
        ),
        rate_limits=RateLimits(
            user=Limit(settings.rate_user_per_minute, settings.rate_user_burst),
            shop=Limit(settings.rate_shop_per_minute, settings.rate_shop_burst),
        ),
    )
