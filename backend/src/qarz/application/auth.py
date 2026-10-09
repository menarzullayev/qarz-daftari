"""Sign-in and sessions (ADR-017). Identity comes only from Telegram; the server issues its own sessions."""

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from qarz.application.errors import Unauthenticated, ValidationFailed
from qarz.application.operations import public_operation, self_operation
from qarz.application.ports import PlatformSession, SessionInfo, Storage
from qarz.domain.telegram_auth import (
    InvalidTelegramData,
    TelegramIdentity,
    verify_login_data,
    verify_webapp_init_data,
)

SIGN_IN_WEBAPP = public_operation("auth.telegram_webapp")
SIGN_IN_WEB = public_operation("auth.telegram_login")
SIGN_OUT = self_operation("auth.sign_out")
SIGN_OUT_EVERYWHERE = self_operation("auth.sign_out_everywhere")
READ_ME = self_operation("me.read")
UPDATE_ME = self_operation("me.update")

LANGUAGES = ("uz", "ru")
SIGNED_DATA_MAX_AGE = timedelta(hours=1)
# The web login's signed data is accepted for a few minutes only (security review, finding 9): the widget
# signs at the moment of the press and the browser posts it at once, so anything older was kept by
# somebody. A Mini App's launch data keeps the hour: the app is opened once and may stay open.
WEB_LOGIN_MAX_AGE = timedelta(minutes=5)
# A record of a used payload must outlive the last instant at which the payload itself would still be
# accepted; the margin covers a difference between this server's clock and the database's.
REPLAY_MARGIN = timedelta(minutes=5)
WEBAPP_SESSION = timedelta(hours=12)
WEB_SESSION = timedelta(days=14)


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def _replay_key(kind: str, identity: TelegramIdentity) -> bytes:
    return hashlib.sha256(f"{kind}:{identity.signature}".encode("ascii")).digest()


def _language(code: str | None) -> str:
    return "ru" if (code or "").lower().startswith("ru") else "uz"


@dataclass(frozen=True)
class IssuedSession:
    token: str
    expires_at: datetime
    csrf_token: str | None = None


class AuthService:
    def __init__(
        self,
        storage: Storage,
        bot_token: str,
        now: Callable[[], datetime] | None = None,
        *,
        web_login_max_age: timedelta = WEB_LOGIN_MAX_AGE,
    ) -> None:
        if not bot_token:
            raise ValueError("a bot token is required to verify Telegram signatures")
        # Never longer than a Mini App's launch data is accepted: a setting may shorten the age, or
        # lengthen it up to what it was before, and nothing beyond.
        if not timedelta(0) < web_login_max_age <= SIGNED_DATA_MAX_AGE:
            raise ValueError("the age accepted for web login data must be positive and at most one hour")
        self._storage = storage
        self._bot_token = bot_token
        self._now = now or (lambda: datetime.now(UTC))
        self._web_login_max_age = web_login_max_age

    async def sign_in_webapp(self, init_data: str) -> IssuedSession:
        now = self._now()
        try:
            identity = verify_webapp_init_data(init_data, self._bot_token, now, SIGNED_DATA_MAX_AGE)
        except InvalidTelegramData as error:
            raise Unauthenticated() from error
        token = secrets.token_urlsafe(32)
        expires = now + WEBAPP_SESSION
        async with self._storage.platform() as session:
            await self._use_once(session, "webapp", identity, SIGNED_DATA_MAX_AGE)
            user_id = await session.ensure_user(identity.tg_id, _language(identity.language_code))
            await session.create_session(
                token_hash=_hash(token), user_id=user_id, kind="webapp", csrf_hash=None, now=now, expires_at=expires
            )
        return IssuedSession(token, expires)

    async def sign_in_web(self, data: dict[str, Any]) -> IssuedSession:
        now = self._now()
        try:
            identity = verify_login_data(data, self._bot_token, now, self._web_login_max_age)
        except InvalidTelegramData as error:
            raise Unauthenticated() from error
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expires = now + WEB_SESSION
        async with self._storage.platform() as session:
            await self._use_once(session, "web", identity, self._web_login_max_age)
            # `ensure_user` writes the person's row, which holds it until this transaction ends: two web
            # sign-ins of one person at once go one after the other, and the later one ends the earlier.
            user_id = await session.ensure_user(identity.tg_id, "uz")
            # One web session a person (security review, finding 9): signing in on the web ends the web
            # sessions the person had, in this browser or another. Mini App sessions are left alone.
            await session.revoke_user_sessions(user_id, now, kind="web")
            await session.create_session(
                token_hash=_hash(token), user_id=user_id, kind="web", csrf_hash=_hash(csrf), now=now, expires_at=expires
            )
        return IssuedSession(token, expires, csrf)

    @staticmethod
    async def _use_once(session: PlatformSession, kind: str, identity: TelegramIdentity, max_age: timedelta) -> None:
        """Refuse signed data that was accepted before (security review, finding 9).

        In the transaction that creates the session: a payload is either recorded with its session or not
        at all. A second use is answered exactly as a wrong signature is; the session the first use made
        is not touched.
        """
        expires = identity.auth_date + max_age + REPLAY_MARGIN
        if not await session.use_signed_data(_replay_key(kind, identity), expires):
            raise Unauthenticated()

    async def resolve(self, token: str) -> SessionInfo | None:
        if not token.isascii() or not 20 <= len(token) <= 128:
            return None
        async with self._storage.platform() as session:
            return await session.find_session(_hash(token), self._now())

    @staticmethod
    def csrf_matches(info: SessionInfo, csrf_token: str | None) -> bool:
        if info.csrf_hash is None or not csrf_token or not csrf_token.isascii():
            return False
        return secrets.compare_digest(_hash(csrf_token), info.csrf_hash)

    async def sign_out(self, token: str) -> None:
        async with self._storage.platform() as session:
            await session.revoke_session(_hash(token), self._now())

    async def sign_out_everywhere(self, user_id: UUID) -> int:
        """End every session of the caller, on every device: Mini App and web, this one included
        (security review, finding 9).

        The person is the one the request was authenticated as, never one named in the request, so
        nobody can end another person's sessions. Returns how many sessions were ended.
        """
        async with self._storage.platform() as session:
            return await session.revoke_user_sessions(user_id, self._now())

    async def me(self, user_id: UUID) -> dict[str, Any]:
        async with self._storage.platform() as session:
            lang = await session.user_language(user_id)
        if lang is None:
            raise Unauthenticated()
        return {"id": str(user_id), "lang": lang}

    async def update_me(self, user_id: UUID, lang: str) -> dict[str, Any]:
        if lang not in LANGUAGES:
            raise ValidationFailed({"lang": "must be uz or ru"})
        async with self._storage.platform() as session:
            await session.set_user_language(user_id, lang)
        return {"id": str(user_id), "lang": lang}
