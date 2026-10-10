"""Production wiring of the HTTP application.

Run with:  uvicorn qarz.interface.asgi:build --factory
"""

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from fastapi import FastAPI

from qarz.application.admin_access import AdminAccess
from qarz.application.admin_passkeys import PasskeySite, challenge_key
from qarz.application.admin_sign_in import Announce
from qarz.application.auth import AuthService
from qarz.application.online_payment import PaymentKeys
from qarz.application.ops_watch import password_sign_in_message
from qarz.domain.exports import MAX_EXPORT_BYTES
from qarz.infrastructure import passkey_signature
from qarz.infrastructure.db import Database
from qarz.infrastructure.file_store import build_file_store
from qarz.infrastructure.secret_box import SecretBox
from qarz.infrastructure.settings import Settings
from qarz.infrastructure.telegram_alerts import TelegramAlerts
from qarz.infrastructure.telegram_files import TelegramFileFetcher
from qarz.infrastructure.telegram_members import TelegramMemberReader
from qarz.interface.http import create_app
from qarz.interface.observability import configure_logging
from qarz.interface.rate_limit import Limit, RateLimits

log = logging.getLogger("qarz.admin")


def _password_sign_in_announcer(settings: Settings) -> Announce | None:
    """Tell the operators' chats of every sign-in by password; nobody when no chat or no bot is set."""
    chats = settings.alert_chats()
    if not chats or not settings.bot_token:
        return None
    token = settings.bot_token

    async def announce(login: str, at: datetime) -> None:
        bot = Bot(token)
        try:
            channel = TelegramAlerts(bot)
            for chat in chats:
                await channel.send(chat, password_sign_in_message(login, at))
        finally:
            await bot.session.close()

    return announce


def _passkey_site(settings: Settings) -> PasskeySite | None:
    """Where the administrators' passkeys are for; nobody's when no host or no server secret is set."""
    host = settings.passkey_site_host()
    if host is None or not settings.secrets_key:
        return None
    return PasskeySite(
        host=host,
        key=challenge_key(settings.secrets_key),
        verify=passkey_signature.verified,
        usable=passkey_signature.usable,
    )


def build(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging()
    database = Database(settings.database_url, statement_timeout_ms=settings.statement_timeout_ms)
    # Without a bot token no Telegram signature can be verified, so no API is served at all.
    auth = (
        AuthService(
            database, settings.bot_token, web_login_max_age=timedelta(seconds=settings.web_login_max_age_seconds)
        )
        if settings.bot_token
        else None
    )
    # No allow-list or no key for the second-factor secrets: nobody can be an administrator, so that side
    # of the API does not exist. A malformed list or key refuses to start instead.
    allowed = settings.admin_allow_list()
    # Read here, whoever is served: a value that is neither "required" nor "off" refuses to start.
    second_factor_required = settings.second_factor_required()
    admin: AdminAccess | None = None
    admin_database: Database | None = None
    if allowed and settings.secrets_key:
        cipher = SecretBox(settings.secrets_key, settings.secrets_key_previous)
        # The administrators' side connects as its own role (qd_admin). The ordinary role's rights no
        # longer reach the administrators' tables, so without this connection that side cannot be served.
        if not settings.admin_database_url:
            raise ValueError("QD_ADMIN_DATABASE_URL must be set when QD_ADMIN_TG_IDS is")
        admin_database = Database(settings.admin_database_url, statement_timeout_ms=settings.statement_timeout_ms)
        admin = AdminAccess(
            admin_database, allowed_tg_ids=allowed, cipher=cipher, second_factor_required=second_factor_required
        )
        if not second_factor_required:
            log.warning(
                "admin_second_factor_off QD_ADMIN_SECOND_FACTOR=off: administrators are asked for no code; "
                "the allow-list and the Telegram sign-in are the only controls on the administrators' side"
            )
    return create_app(
        database.reachable,
        database,
        auth=auth,
        admin=admin,
        admin_storage=admin_database,
        admin_announce=_password_sign_in_announcer(settings),
        passkey_site=_passkey_site(settings),
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
