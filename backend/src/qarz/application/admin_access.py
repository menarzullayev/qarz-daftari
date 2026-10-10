"""Who is an administrator, and the second factor that opens the administrator's side (ADR-017).

A person is an administrator only if their Telegram identifier is on the allow-list from the environment
and they have an administrator account that is active. Even then nothing of the administrator's side
opens until they pass a time-based code, which gives them an admin session: a separate, short-lived
token, stored hashed, bound to the same user, and never found in the table of ordinary sessions.

A deployment may switch the second factor off (`QD_ADMIN_SECOND_FACTOR=off`, the owner's decision of
2026-10-10). Then an administrator whose second factor is confirmed is asked for no code anywhere: the
allow-list and the active account are the whole check, `status` says so, and what is written to the audit
without a code carries `SECOND_FACTOR_OFF`. Stored secrets and their state are not touched while it is
off. The database has controls of its own that no setting of the application reaches (migrations 0027
and 0028): it changes a platform setting or a shop's owner only for an account that once confirmed a
second factor and that has an admin session open. So the application keeps such a session open for the
administrator, held by nobody's browser, and a person who never confirmed a second factor still enrols
once, with one code, before anything opens.

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
from qarz.domain import brand, totp

READ_ADMIN_AUTH = admin_entry_operation("admin.auth.read")
ENROL_ADMIN = admin_entry_operation("admin.auth.enrol")
OPEN_ADMIN_SESSION = admin_entry_operation("admin.session.open")
CLOSE_ADMIN_SESSION = admin_operation("admin.session.close")

# Specification, clients table: the administrator's "session valid 8 hours".
ADMIN_SESSION = timedelta(hours=8)
# What an authenticator application shows beside the code: the product's name.
ISSUER = brand.NAME

# What the audit detail of an operation carries when it would have needed a code and none was asked for.
SECOND_FACTOR_OFF = {"second_factor": "off"}

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

    def __init__(self, session: PlatformSession, admin_id: UUID, about_shop: UUID | None = None) -> None:
        self._session = session
        self._admin_id = admin_id
        self._about_shop = about_shop

    async def lock_request_key(self, key: str) -> None:
        await self._session.lock_admin_request_key(self._admin_id, key)

    async def stored_response(self, key: str) -> dict[str, Any] | None:
        return await self._session.admin_stored_response(self._admin_id, key)

    async def store_response(self, key: str, response: dict[str, Any]) -> None:
        await self._session.store_admin_response(self._admin_id, key, response, self._about_shop)


class AdminAccess:
    def __init__(
        self,
        storage: Storage,
        *,
        allowed_tg_ids: Container[int],
        cipher: SecretCipher,
        now: Callable[[], datetime] | None = None,
        second_factor_required: bool = True,
    ) -> None:
        self._storage = storage
        self._allowed = allowed_tg_ids
        self._cipher = cipher
        self._required = second_factor_required
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def allowed_tg_ids(self) -> Container[int]:
        """The allow-list: who is told about what waits for an administrator."""
        return self._allowed

    @property
    def second_factor_required(self) -> bool:
        """False on a deployment that switched the second factor off."""
        return self._required

    def unverified(self) -> dict[str, str]:
        """What to add to the audit detail of an operation that needs a code: nothing while codes are
        asked for, `SECOND_FACTOR_OFF` on a deployment that asks for none."""
        return {} if self._required else dict(SECOND_FACTOR_OFF)

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
            if account is None:
                raise NotFound()
            if self._required:
                if await self._expiry(session, user_id, token) is None:
                    raise NotFound()
            elif not account.confirmed:
                # Second factor off: no admin session is asked for. Someone who never confirmed a second
                # factor is still at the door, where they enrol once (see the module's description).
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
            if not self._required and account is not None and account.confirmed:
                await self._hold_session(session, user_id, now)
                return {
                    "enrolled": True,
                    "confirmed": True,
                    # The panel goes on with no step. Nothing of this ends, and a lock set while codes
                    # were asked for stops nobody: no code is looked at.
                    "elevated": True,
                    "expires_at": None,
                    "locked_until": None,
                    "second_factor": "off",
                }
            expires = None if account is None else await self._expiry(session, user_id, token)
        locked_until = None
        if account is not None and account.locked_until is not None and now < account.locked_until:
            locked_until = account.locked_until
        body = {
            "enrolled": account is not None,
            "confirmed": account is not None and account.confirmed,
            "elevated": expires is not None,
            "expires_at": None if expires is None else expires.isoformat(),
            "locked_until": None if locked_until is None else locked_until.isoformat(),
        }
        # Off, and this person never confirmed a second factor: the step is the enrolment, once.
        return body if self._required else {**body, "elevated": False, "expires_at": None, "second_factor": "off"}

    async def _hold_session(self, session: PlatformSession, user_id: UUID, now: datetime) -> None:
        """Second factor off: keep an admin session open for this administrator, for the database.

        The database changes a setting or an owner only while the administrator has an admin session
        open. With no code there is no moment at which one is opened, so one is opened here whenever
        none is, with a token that is thrown away: no browser holds it and no request is let in by it.
        Each is in the audit like any other, marked as opened without a code.
        """
        if await session.admin_session_open(user_id, now):
            return
        expires = now + ADMIN_SESSION
        await session.open_admin_session(
            token_hash=_hash(secrets.token_urlsafe(32)), user_id=user_id, now=now, expires_at=expires
        )
        await session.add_admin_audit(
            admin_id=user_id,
            action="admin.session_opened",
            target_type="admin",
            target_id=str(user_id),
            shop_id=None,
            reason=None,
            detail={"expires_at": expires.isoformat()} | SECOND_FACTOR_OFF,
            now=now,
        )

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

        With the second factor off, for an administrator who has confirmed one, nothing is judged and
        nothing of the factor is stored: the change is let through, and the caller marks its audit row
        with `unverified()`. The first code of an enrolment is judged as ever.
        """
        account = await session.admin_account(user_id, for_update=True)
        if account is None:
            return AdminNotEnrolled()
        if not self._required and account.confirmed:
            await self._hold_session(session, user_id, self._now())
            return None
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
                    detail={"expires_at": expires.isoformat()} | self.unverified(),
                    now=now,
                )
        if refusal is not None:
            raise refusal
        return IssuedAdminSession(token, expires)

    async def end_sessions_of(self, user_id: UUID) -> int:
        """End the administrator sessions of a person who signs out everywhere (security review, finding 9).

        An admin session cannot be used without an ordinary session, but it has hours of its own: left
        open, it would work again as soon as the person signed in again in the same browser. Whoever
        asks is anyone who is signed in, so nothing is checked and nothing is refused: a person who is
        no administrator has no such session, and one who was taken off the allow-list may still end
        theirs. The audit row is written only when a session was ended. Returns how many were.
        """
        now = self._now()
        async with self._storage.platform() as session:
            ended = await session.revoke_admin_sessions(user_id, now)
            if ended:
                await session.add_admin_audit(
                    admin_id=user_id,
                    action="admin.session_closed",
                    target_type="admin",
                    target_id=str(user_id),
                    shop_id=None,
                    reason=None,
                    detail={"by": "sign_out_everywhere"},
                    now=now,
                )
        return ended

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
