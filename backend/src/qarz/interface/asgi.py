"""Production wiring of the HTTP application.

Run with:  uvicorn qarz.interface.asgi:build --factory
"""

from fastapi import FastAPI

from qarz.application.auth import AuthService
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
    )
