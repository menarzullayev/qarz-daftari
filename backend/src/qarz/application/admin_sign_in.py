"""The administrators' other ways in: a service key and a password (ADR-017, amended 2026-10-10).

Neither decides who is an administrator. A key or a password is made only for a person who is on the
allow-list and has an active administrator's account, and each leads to an ordinary session of that
person; every administrator's operation then asks what it always asked (`AdminAccess.require_admin`).
While the second factor is required, that still means a code: a key or a password alone opens the door
and no more.

Keys and passwords are made from the server's command line (`qarz.interface.admin_sign_in`), never over
the API. A key is shown once and only its hash is kept. Making and ending one, setting a password and
every sign-in by password are in the administrators' audit.
"""

import asyncio
import hashlib
import logging
import secrets
from collections.abc import Awaitable, Callable, Container
from datetime import UTC, datetime
from uuid import UUID

from qarz.application.admin_sign_in_ports import ServiceKey
from qarz.application.auth import AuthService, IssuedSession
from qarz.application.errors import AppError, Unauthenticated
from qarz.application.ports import PlatformSession, Storage
from qarz.domain import admin_password as rules

log = logging.getLogger("qarz.admin")

KEY_PREFIX = "qdk_"
# What a wrong login is weighed against, so that an unknown login takes as long as a wrong password.
_NO_SALT = bytes(rules.SALT_BYTES)
_NO_HASH = bytes(rules.HASH_BYTES)

Announce = Callable[[str, datetime], Awaitable[None]]


class NotAnAdministrator(ValueError):
    pass


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


class AdminSignIn:
    def __init__(
        self,
        admin_storage: Storage,
        *,
        allowed_tg_ids: Container[int],
        auth: AuthService | None = None,
        announce: Announce | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = admin_storage
        self._allowed = allowed_tg_ids
        self._auth = auth
        self._announce = announce
        self._now = now or (lambda: datetime.now(UTC))

    async def _administrator(self, session: PlatformSession, tg_id: int) -> UUID:
        """The user behind this Telegram id, when they are an administrator who has come in before."""
        if tg_id not in self._allowed:
            raise NotAnAdministrator("this Telegram id is not on the allow-list (QD_ADMIN_TG_IDS)")
        user_id = await session.user_by_telegram_id(tg_id)
        account = None if user_id is None else await session.admin_account(user_id, for_update=False)
        if user_id is None or account is None or account.status != "active":
            raise NotAnAdministrator("this person has no active administrator's account: they sign in once first")
        return user_id

    async def _audit(self, session: PlatformSession, admin_id: UUID, action: str, detail: dict[str, str]) -> None:
        await session.add_admin_audit(
            admin_id=admin_id,
            action=action,
            target_type="admin",
            target_id=str(admin_id),
            shop_id=None,
            reason=None,
            detail=dict(detail),
            now=self._now(),
        )

    # --- service keys -----------------------------------------------------------------------------------

    async def create_key(self, tg_id: int, label: str) -> str:
        """Make a key for this administrator and return it. It is not kept: this is the one time it is seen."""
        token = KEY_PREFIX + secrets.token_urlsafe(32)
        async with self._storage.platform() as session:
            user_id = await self._administrator(session, tg_id)
            if not await session.create_service_key(
                token_hash=_hash(token), user_id=user_id, label=label, now=self._now()
            ):
                raise ValueError(f"a key labelled {label!r} exists: revoke it first, or choose another label")
            await self._audit(session, user_id, "admin.service_key_created", {"label": label})
        return token

    async def keys(self) -> list[ServiceKey]:
        async with self._storage.platform() as session:
            return await session.service_keys()

    async def revoke_key(self, label: str) -> bool:
        async with self._storage.platform() as session:
            user_id = await session.revoke_service_key(label, self._now())
            if user_id is None:
                return False
            await self._audit(session, user_id, "admin.service_key_revoked", {"label": label})
        return True

    # --- the password -----------------------------------------------------------------------------------

    async def set_password(self, tg_id: int, login: str, password: str) -> None:
        rules.check_login(login)
        rules.check_password(password)
        salt = rules.new_salt()
        digest = await asyncio.to_thread(rules.digest, password, salt)
        async with self._storage.platform() as session:
            user_id = await self._administrator(session, tg_id)
            if not await session.set_admin_password(
                user_id=user_id, login=login, salt=salt, hash=digest, now=self._now()
            ):
                raise ValueError("another administrator has this login")
            await self._audit(session, user_id, "admin.password_set", {"login": login})

    async def sign_in(self, login: str, password: str) -> IssuedSession:
        """A web session for the administrator this login and password belong to.

        One answer for every refusal (an unknown login, a wrong password, a locked login, a person who is
        no longer an administrator) and the same work done for each, so that neither the answer nor the
        time it takes tells them apart.
        """
        if self._auth is None:
            raise Unauthenticated()
        now = self._now()
        refusal: AppError | None = None
        user_id: UUID | None = None
        async with self._storage.platform() as session:
            stored = await session.admin_password(login) if rules.LOGIN.fullmatch(login) else None
            if stored is None:
                await asyncio.to_thread(rules.matches, password, _NO_SALT, _NO_HASH)
                refusal = Unauthenticated()
            else:
                state = rules.Attempts(stored.failures, stored.locked_until)
                accepted = await asyncio.to_thread(rules.matches, password, stored.salt, stored.hash)
                if rules.locked(state, now):
                    refusal = Unauthenticated()
                else:
                    after = rules.after(state, accepted, now)
                    await session.note_password_attempt(stored.user_id, after.failures, after.locked_until)
                    tg_id = await session.telegram_id(stored.user_id)
                    account = await session.admin_account(stored.user_id, for_update=False)
                    still = tg_id is not None and tg_id in self._allowed and account is not None
                    if not accepted or not still or account is None or account.status != "active":
                        refusal = Unauthenticated()
                        if after.locked_until is not None:
                            await self._audit(session, stored.user_id, "admin.password_locked", {"login": login})
                            log.warning("admin_password_locked user=%s", stored.user_id)
                    else:
                        user_id = stored.user_id
                        await self._audit(session, user_id, "admin.signed_in_with_password", {"login": login})
        # Raised outside the transaction: the count of wrong attempts must be kept.
        if refusal is not None or user_id is None:
            raise refusal or Unauthenticated()
        issued = await self._auth.issue_web_session(user_id)
        log.warning("admin_password_sign_in user=%s", user_id)
        if self._announce is not None:
            try:
                await self._announce(login, now)
            except Exception:
                # The sign-in stands; the audit row and the log line above tell of it.
                log.exception("admin_password_sign_in_not_announced")
        return issued
