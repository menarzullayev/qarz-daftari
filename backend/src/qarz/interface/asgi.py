"""Production wiring of the HTTP application.

Run with:  uvicorn qarz.interface.asgi:build --factory
"""

from fastapi import FastAPI

from qarz.application.admin_access import AdminAccess
from qarz.application.auth import AuthService
from qarz.infrastructure.db import Database
from qarz.infrastructure.secret_box import SecretBox
from qarz.infrastructure.settings import Settings
from qarz.interface.http import create_app


def build(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    database = Database(settings.database_url)
    # Without a bot token no Telegram signature can be verified, so no API is served at all.
    auth = AuthService(database, settings.bot_token) if settings.bot_token else None
    # No allow-list or no key for the second-factor secrets: nobody can be an administrator, so that side
    # of the API does not exist. A malformed list or key refuses to start instead.
    allowed = settings.admin_allow_list()
    admin = (
        AdminAccess(database, allowed_tg_ids=allowed, cipher=SecretBox(settings.secrets_key))
        if allowed and settings.secrets_key
        else None
    )
    return create_app(
        database.reachable,
        database,
        auth=auth,
        admin=admin,
        webhook_secret=settings.webhook_secret or None,
    )
