"""An administrator's passkeys: registering one, signing in with one, ending one (ADR-017, amended 2026-10-10).

A passkey decides nothing about who is an administrator. One is registered only by an administrator who
is already inside (every operation here but the sign-in asks what the rest of that side asks), and
signing in with one gives the ordinary web session of its owner, who must still be on the allow-list
with an active account. While the second factor is required, that session opens the door and no more.

What is checked of a browser's answer: that the challenge is one this server issued for this purpose (and,
when registering, for this person) and is not over; that the client data is of the right kind and from
this site's own page; that the authenticator made its data for this site and verified its holder; that
the signature is the stored key's; that a device which counts has counted upwards; and that an accepted
sign-in is not accepted twice. No attestation is asked for: which make of device holds the key is not
this server's concern, and the person registering is an administrator already.
"""

import hashlib
import logging
from collections.abc import Callable, Container
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from qarz.application.admin_sign_in_ports import Passkey
from qarz.application.auth import AuthService, IssuedSession
from qarz.application.errors import AppError, NotFound, Unauthenticated, ValidationFailed
from qarz.application.operations import admin_operation, public_operation
from qarz.application.ports import Storage
from qarz.domain import brand, passkey

log = logging.getLogger("qarz.admin")

PASSKEY_CHALLENGE = public_operation("auth.admin_passkey_challenge")
SIGN_IN_PASSKEY = public_operation("auth.admin_passkey")
LIST_PASSKEYS = admin_operation("admin.passkeys.list")
START_PASSKEY = admin_operation("admin.passkeys.challenge")
ADD_PASSKEY = admin_operation("admin.passkeys.add")
REMOVE_PASSKEY = admin_operation("admin.passkeys.remove")

MAX_PASSKEYS = 10
TIMEOUT_MS = 120_000

Verify = Callable[[bytes, int, bytes, bytes], bool]
Usable = Callable[[bytes, int], bool]


@dataclass(frozen=True)
class PasskeySite:
    """Where passkeys are for: the public host (the relying party's id) and the key challenges are
    sealed with. `verify` and `usable` are the signature checks, which need a cryptography library."""

    host: str
    key: bytes
    verify: Verify
    usable: Usable

    @property
    def origin(self) -> str:
        return f"https://{self.host}"


def challenge_key(secret: str) -> bytes:
    """The key of the challenges, drawn from the server's secret and good for nothing else."""
    return hashlib.sha256(b"qd-admin-passkey-challenge|" + secret.encode("utf-8")).digest()


class AdminPasskeys:
    def __init__(
        self,
        admin_storage: Storage,
        *,
        sessions: Storage,
        allowed_tg_ids: Container[int],
        site: PasskeySite,
        auth: AuthService | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = admin_storage
        self._sessions = sessions
        self._allowed = allowed_tg_ids
        self._site = site
        self._auth = auth
        self._now = now or (lambda: datetime.now(UTC))

    @staticmethod
    def _view(row: Passkey) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "label": row.label,
            "created_at": row.created_at.isoformat(),
            "last_used_at": None if row.last_used_at is None else row.last_used_at.isoformat(),
        }

    # --- an administrator's own passkeys ----------------------------------------------------------------

    async def list(self, user_id: UUID) -> dict[str, Any]:
        async with self._storage.platform() as session:
            return {"items": [self._view(row) for row in await session.passkeys_of(user_id)]}

    async def start(self, user_id: UUID) -> dict[str, Any]:
        """What the browser needs to make a credential for this administrator on this site."""
        async with self._storage.platform() as session:
            existing = await session.passkeys_of(user_id)
        return {
            "challenge": passkey.b64(
                passkey.new_challenge(self._site.key, passkey.REGISTER, self._now(), user_id.bytes)
            ),
            "rp": {"id": self._site.host, "name": brand.NAME},
            "user": {"id": passkey.b64(user_id.bytes), "name": brand.NAME + " admin"},
            "algorithms": list(passkey.ALGORITHMS),
            "exclude": [passkey.b64(row.credential_id) for row in existing],
            "timeout": TIMEOUT_MS,
        }

    async def add(
        self, user_id: UUID, *, label: str, client_data: str, authenticator_data: str, public_key: str, algorithm: int
    ) -> dict[str, Any]:
        label = " ".join(label.split())
        if not 1 <= len(label) <= 60:
            raise ValidationFailed({"label": "required"})
        now = self._now()
        try:
            challenge = passkey.check_client_data(passkey.unb64(client_data), "webauthn.create", self._site.origin)
            passkey.challenge_expiry(self._site.key, passkey.REGISTER, challenge, now, user_id.bytes)
            data = passkey.read_authenticator_data(passkey.unb64(authenticator_data), self._site.host)
            key = passkey.unb64(public_key)
            if (
                data.credential_id is None
                or algorithm not in passkey.ALGORITHMS
                or not self._site.usable(key, algorithm)
            ):
                raise passkey.Refused("no credential, or a key that cannot be used")
        except passkey.Refused as refused:
            log.warning("admin_passkey_not_registered user=%s why=%s", user_id, refused)
            raise ValidationFailed({"passkey": "invalid"}) from refused
        async with self._storage.platform() as session:
            if len(await session.passkeys_of(user_id)) >= MAX_PASSKEYS:
                raise ValidationFailed({"passkey": "too_many"})
            passkey_id = await session.add_passkey(
                user_id=user_id,
                credential_id=data.credential_id,
                public_key=key,
                algorithm=algorithm,
                sign_count=data.sign_count,
                label=label,
                now=now,
            )
            if passkey_id is None:
                raise ValidationFailed({"passkey": "exists"})
            await session.add_admin_audit(
                admin_id=user_id,
                action="admin.passkey_added",
                target_type="admin",
                target_id=str(user_id),
                shop_id=None,
                reason=None,
                detail={"passkey": str(passkey_id), "label": label},
                now=now,
            )
            rows = await session.passkeys_of(user_id)
        return self._view(next(row for row in rows if row.id == passkey_id))

    async def remove(self, user_id: UUID, passkey_id: UUID) -> None:
        now = self._now()
        async with self._storage.platform() as session:
            if not await session.revoke_passkey(user_id, passkey_id, now):
                raise NotFound()
            await session.add_admin_audit(
                admin_id=user_id,
                action="admin.passkey_removed",
                target_type="admin",
                target_id=str(user_id),
                shop_id=None,
                reason=None,
                detail={"passkey": str(passkey_id)},
                now=now,
            )

    # --- signing in -------------------------------------------------------------------------------------

    def challenge(self) -> dict[str, Any]:
        """What the browser needs to ask a device for a signature. Nothing is stored and nobody is named:
        the device offers the passkeys it holds for this site."""
        return {
            "challenge": passkey.b64(passkey.new_challenge(self._site.key, passkey.SIGN_IN, self._now())),
            "rp_id": self._site.host,
            "timeout": TIMEOUT_MS,
        }

    async def sign_in(
        self, *, credential_id: str, client_data: str, authenticator_data: str, signature: str
    ) -> IssuedSession:
        """A web session for the administrator this passkey belongs to. Every refusal is one answer."""
        if self._auth is None:
            raise Unauthenticated()
        now = self._now()
        refusal: AppError | None = None
        user_id: UUID | None = None
        try:
            raw_client, raw_data = passkey.unb64(client_data), passkey.unb64(authenticator_data)
            challenge = passkey.check_client_data(raw_client, "webauthn.get", self._site.origin)
            expires = passkey.challenge_expiry(self._site.key, passkey.SIGN_IN, challenge, now)
            data = passkey.read_authenticator_data(raw_data, self._site.host)
            credential, signed = passkey.unb64(credential_id), passkey.unb64(signature)
        except passkey.Refused as refused:
            log.warning("admin_passkey_refused why=%s", refused)
            raise Unauthenticated() from refused
        async with self._storage.platform() as session:
            stored = await session.passkey_by_credential(credential)
            if stored is None or not self._site.verify(
                stored.public_key, stored.algorithm, signed, passkey.signed_message(raw_data, raw_client)
            ):
                refusal = Unauthenticated()
            elif not passkey.counter_moved_on(stored.sign_count, data.sign_count):
                # A copy of the key is in use somewhere. The passkey is left as it is for its owner to end;
                # the audit and the log say what was seen.
                await session.add_admin_audit(
                    admin_id=stored.user_id,
                    action="admin.passkey_counter_went_back",
                    target_type="admin",
                    target_id=str(stored.user_id),
                    shop_id=None,
                    reason=None,
                    detail={"passkey": str(stored.id)},
                    now=now,
                )
                log.warning("admin_passkey_counter_went_back user=%s passkey=%s", stored.user_id, stored.id)
                refusal = Unauthenticated()
            else:
                tg_id = await session.telegram_id(stored.user_id)
                account = await session.admin_account(stored.user_id, for_update=False)
                if tg_id is None or tg_id not in self._allowed or account is None or account.status != "active":
                    refusal = Unauthenticated()
                else:
                    user_id = stored.user_id
                    await session.note_passkey_use(stored.id, data.sign_count, now)
                    await session.add_admin_audit(
                        admin_id=user_id,
                        action="admin.signed_in_with_passkey",
                        target_type="admin",
                        target_id=str(user_id),
                        shop_id=None,
                        reason=None,
                        detail={"passkey": str(stored.id)},
                        now=now,
                    )
        if refusal is not None or user_id is None:
            raise refusal or Unauthenticated()
        # An accepted answer is accepted once: its challenge is remembered until it would have run out.
        async with self._sessions.platform() as session:
            fresh = await session.use_signed_data(hashlib.sha256(b"passkey:" + challenge).digest(), expires)
        if not fresh:
            raise Unauthenticated()
        return await self._auth.issue_web_session(user_id)
