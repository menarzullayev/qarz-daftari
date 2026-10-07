"""Who is an administrator, and the second factor that opens the administrator's side (ADR-017).

A person is an administrator only if their Telegram identifier is on the allow-list from the environment
and they have an administrator account that is active. Even then nothing of the administrator's side
opens until they pass a time-based code, which gives them an admin session: a separate, short-lived
token, stored hashed, bound to the same user, and never found in the table of ordinary sessions.

Everyone else is told "not found", exactly as for a route that does not exist. Codes, secrets and tokens
are never logged and never stored in the audit or in an idempotent response.
"""

import hashlib
import logging
import secrets
from collections.abc import Callable, Container
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from qarz.application import idempotency
from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.operations import admin_entry_operation, admin_operation
from qarz.application.ports import AdminAccount, PlatformSession, SecretCipher, SecretUnreadable, Storage
from qarz.domain import totp

READ_ADMIN_AUTH = admin_entry_operation("admin.auth.read")
ENROL_ADMIN = admin_entry_operation("admin.auth.enrol")
OPEN_ADMIN_SESSION = admin_entry_operation("admin.session.open")
CLOSE_ADMIN_SESSION = admin_operation("admin.session.close")

# Specification, clients table: the administrator's "session valid 8 hours".
ADMIN_SESSION = timedelta(hours=8)
ISSUER = "Qarz Daftari"

log = logging.getLogger("qarz.admin")


class SecondFactorInvalid(AppError):
    """The code is wrong, too old, or was already used."""

    code = "SECOND_FACTOR_INVALID"


class SecondFactorLocked(AppError):
    """Too many wrong codes; no code is looked at until the lock ends."""

    code = "SECOND_FACTOR_LOCKED"


class AdminAlreadyEnrolled(AppError):
    """The second factor is confirmed. Replacing it is an operator's procedure, not an API call."""

    code = "ADMIN_ALREADY_ENROLLED"


class AdminNotEnrolled(AppError):
    code = "ADMIN_NOT_ENROLLED"


@dataclass(frozen=True)
class IssuedAdminSession:
    token: str
    expires_at: datetime


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


class AdminRequestKeys:
    """Stored results of one administrator's writes, in the shape `idempotency.run_once` expects."""

    def __init__(self, session: PlatformSession, admin_id: UUID) -> None:
        self._session = session
        self._admin_id = admin_id

    async def lock_request_key(self, key: str) -> None:
        await self._session.lock_admin_request_key(self._admin_id, key)

    async def stored_response(self, key: str) -> dict[str, Any] | None:
        return await self._session.admin_stored_response(self._admin_id, key)

    async def store_response(self, key: str, response: dict[str, Any]) -> None:
        await self._session.store_admin_response(self._admin_id, key, response)


class AdminAccess:
    def __init__(
        self,
        storage: Storage,
        *,
        allowed_tg_ids: Container[int],
        cipher: SecretCipher,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._allowed = allowed_tg_ids
        self._cipher = cipher
        self._now = now or (lambda: datetime.now(UTC))

    # --- who may come in --------------------------------------------------------------------------------

    async def _candidate(self, session: PlatformSession, user_id: UUID) -> AdminAccount | None:
        """The caller's account, which may not exist yet. NotFound unless they are on the allow-list."""
        tg_id = await session.telegram_id(user_id)
        if tg_id is None or tg_id not in self._allowed:
            raise NotFound()
        account = await session.admin_account(user_id, for_update=False)
        if account is not None and account.status != "active":
            raise NotFound()
        return account

    async def require_candidate(self, user_id: UUID) -> None:
        """The door: allow-listed, and not a disabled administrator."""
        async with self._storage.platform() as session:
            await self._candidate(session, user_id)

    async def require_admin(self, user_id: UUID, token: str | None) -> None:
        """The full check every administrator operation starts with."""
        async with self._storage.platform() as session:
            account = await self._candidate(session, user_id)
            # The schema lets an admin session exist only for an administrator account. The account is
            # asked for here all the same: two controls, not one (security review, finding 1).
            if account is None or await self._expiry(session, user_id, token) is None:
                raise NotFound()

    async def _expiry(self, session: PlatformSession, user_id: UUID, token: str | None) -> datetime | None:
        if not token or not token.isascii() or not 20 <= len(token) <= 128:
            return None
        return await session.admin_session_expiry(_hash(token), user_id, self._now())

    # --- the door ---------------------------------------------------------------------------------------

    async def status(self, user_id: UUID, token: str | None) -> dict[str, Any]:
        """What the panel needs to know to show the right step: enrol, enter a code, or go on."""
        now = self._now()
        async with self._storage.platform() as session:
            account = await self._candidate(session, user_id)
            expires = None if account is None else await self._expiry(session, user_id, token)
        locked_until = None
        if account is not None and account.locked_until is not None and now < account.locked_until:
            locked_until = account.locked_until
        return {
            "enrolled": account is not None,
            "confirmed": account is not None and account.confirmed,
            "elevated": expires is not None,
            "expires_at": None if expires is None else expires.isoformat(),
            "locked_until": None if locked_until is None else locked_until.isoformat(),
        }

    async def enrol(self, user_id: UUID, request_key: str | None) -> dict[str, Any]:
        """Give an allow-listed person their second factor. The secret is shown once, here.

        Until a first code is accepted the enrolment can be repeated, which replaces the secret: a person
        whose first answer was lost is not locked out. After that it cannot be.
        """
        key = idempotency.validate_key(request_key)
        now = self._now()
        async with self._storage.platform() as session:
            await self._candidate(session, user_id)
            tg_id = await session.telegram_id(user_id)

            async def apply() -> dict[str, Any]:
                secret = secrets.token_bytes(totp.SECRET_BYTES)
                if not await session.enrol_admin(user_id, self._cipher.encrypt(secret, user_id.bytes)):
                    raise AdminAlreadyEnrolled()
                await session.add_admin_audit(
                    admin_id=user_id,
                    action="admin.enrolled",
                    target_type="admin",
                    target_id=str(user_id),
                    shop_id=None,
                    reason=None,
                    detail={},
                    now=now,
                )
                return {"enrolled": True, "otpauth_uri": totp.provisioning_uri(secret, f"admin-{tg_id}", ISSUER)}

            return await idempotency.run_once(
                AdminRequestKeys(session, user_id),
                key=key,
                operation=ENROL_ADMIN.name,
                user_id=user_id,
                request={},
                action=apply,
                # A repeat must not show the secret again; it says only that the enrolment happened.
                redact=lambda body: {**body, "otpauth_uri": None},
            )

    async def check_code(self, session: PlatformSession, user_id: UUID, code: str | None) -> AppError | None:
        """Judge a code inside the caller's transaction and store what must be remembered.

        Returns the refusal instead of raising it: a wrong code has to be counted, so the transaction
        that counts it must commit. The caller leaves its transaction normally and raises afterwards.
        """
        account = await session.admin_account(user_id, for_update=True)
        if account is None:
            return AdminNotEnrolled()
        if code is None:
            return ValidationFailed({"code": "this change needs a current code from your authenticator"})
        now = self._now()
        try:
            secret: bytes | None = self._cipher.decrypt(account.secret, user_id.bytes)
        except SecretUnreadable:
            log.error("admin_secret_unreadable user=%s", user_id)
            secret = None
        before = totp.FactorState(account.failures, account.locked_until, account.last_step)
        outcome, after = totp.check_code(before, secret, code, now)
        if after != before:
            await session.save_factor_state(
                user_id,
                failures=after.failures,
                locked_until=after.locked_until,
                last_step=after.last_step,
                confirmed_at=now if outcome is totp.Outcome.ACCEPTED else None,
            )
        if outcome is totp.Outcome.ACCEPTED:
            return None
        if outcome is totp.Outcome.REFUSED:
            log.warning("admin_code_refused user=%s", user_id)
            return SecondFactorInvalid()
        until = after.locked_until
        assert until is not None, "a locked factor says until when"
        if after != before:
            # This attempt set the lock. Whoever holds a session of this administrator loses it.
            await session.revoke_admin_sessions(user_id, now)
            await session.add_admin_audit(
                admin_id=user_id,
                action="admin.second_factor_locked",
                target_type="admin",
                target_id=str(user_id),
                shop_id=None,
                reason=None,
                detail={"locked_until": until.isoformat()},
                now=now,
            )
            log.warning("admin_second_factor_locked user=%s", user_id)
        seconds = max(1, int((until - now).total_seconds()))
        return SecondFactorLocked({"retry_after_seconds": str(seconds)})

    async def open_session(self, user_id: UUID, code: str) -> IssuedAdminSession:
        now = self._now()
        token = secrets.token_urlsafe(32)
        expires = now + ADMIN_SESSION
        async with self._storage.platform() as session:
            await self._candidate(session, user_id)
            refusal = await self.check_code(session, user_id, code)
            if refusal is None:
                await session.open_admin_session(token_hash=_hash(token), user_id=user_id, now=now, expires_at=expires)
                await session.add_admin_audit(
                    admin_id=user_id,
                    action="admin.session_opened",
                    target_type="admin",
                    target_id=str(user_id),
                    shop_id=None,
                    reason=None,
                    detail={"expires_at": expires.isoformat()},
                    now=now,
                )
        if refusal is not None:
            raise refusal
        return IssuedAdminSession(token, expires)

    async def close_session(self, user_id: UUID) -> None:
        now = self._now()
        async with self._storage.platform() as session:
            await session.revoke_admin_sessions(user_id, now)
            await session.add_admin_audit(
                admin_id=user_id,
                action="admin.session_closed",
                target_type="admin",
                target_id=str(user_id),
                shop_id=None,
                reason=None,
                detail={},
                now=now,
            )
