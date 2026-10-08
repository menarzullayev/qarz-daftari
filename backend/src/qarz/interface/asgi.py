"""Production wiring of the HTTP application.

Run with:  uvicorn qarz.interface.asgi:build --factory
"""

from fastapi import FastAPI

from qarz.application.admin_access import AdminAccess
from qarz.application.auth import AuthService
from qarz.application.online_payment import PaymentKeys
from qarz.domain.exports import MAX_EXPORT_BYTES
from qarz.infrastructure.db import Database
from qarz.infrastructure.file_store import build_file_store
from qarz.infrastructure.secret_box import SecretBox
from qarz.infrastructure.settings import Settings
from qarz.infrastructure.telegram_files import TelegramFileFetcher
from qarz.infrastructure.telegram_members import TelegramMemberReader
from qarz.interface.http import create_app
from qarz.interface.observability import configure_logging
from qarz.interface.rate_limit import Limit, RateLimits


def build(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging()
    database = Database(settings.database_url, statement_timeout_ms=settings.statement_timeout_ms)
    # Without a bot token no Telegram signature can be verified, so no API is served at all.
    auth = AuthService(database, settings.bot_token) if settings.bot_token else None
    # No allow-list or no key for the second-factor secrets: nobody can be an administrator, so that side
    # of the API does not exist. A malformed list or key refuses to start instead.
    allowed = settings.admin_allow_list()
    admin = (
        AdminAccess(
            database, allowed_tg_ids=allowed, cipher=SecretBox(settings.secrets_key, settings.secrets_key_previous)
        )
        if allowed and settings.secrets_key
        else None
    )
    return create_app(
        database.reachable,
        database,
        auth=auth,
        admin=admin,
        webhook_secret=settings.webhook_secret or None,
        # A store that is named but misconfigured stops the start; none at all only refuses receipts.
        file_store=build_file_store(settings, max_object_bytes=MAX_EXPORT_BYTES),
        secrets_key=settings.secrets_key or None,
        previous_secrets_key=settings.secrets_key_previous or None,
        telegram_files=TelegramFileFetcher.for_token(settings.bot_token) if settings.bot_token else None,
        telegram_members=TelegramMemberReader.for_token(settings.bot_token) if settings.bot_token else None,
        metrics_token=settings.metrics_token or None,
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
