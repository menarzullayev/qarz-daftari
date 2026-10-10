"""What the administrators' other ways in (a service key, a password) ask of the store."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class ServiceKey:
    """A service key as the store knows it: never the key, which is kept nowhere."""

    label: str
    user_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class StoredPassword:
    user_id: UUID
    salt: bytes
    hash: bytes
    failures: int
    locked_until: datetime | None


class AdminSignInSession(Protocol):
    async def user_by_telegram_id(self, tg_id: int) -> UUID | None: ...

    async def create_service_key(self, *, token_hash: bytes, user_id: UUID, label: str, now: datetime) -> bool:
        """Store a key's hash under its label. False when a live key already has the label."""
        ...

    async def service_keys(self) -> list[ServiceKey]:
        """The keys that are not revoked, oldest first."""
        ...

    async def revoke_service_key(self, label: str, now: datetime) -> UUID | None:
        """End the live key with this label. The administrator it was for, or None when there is none."""
        ...

    async def admin_password(self, login: str) -> StoredPassword | None:
        """The password stored under this login, with its row held until the transaction ends."""
        ...

    async def set_admin_password(self, *, user_id: UUID, login: str, salt: bytes, hash: bytes, now: datetime) -> bool:
        """Store or replace an administrator's password and clear its count. False when another
        administrator has the login."""
        ...

    async def note_password_attempt(self, user_id: UUID, failures: int, locked_until: datetime | None) -> None: ...


@dataclass(frozen=True)
class Passkey:
    """A passkey as stored: the credential's identifier and public key, never a private one."""

    id: UUID
    user_id: UUID
    credential_id: bytes
    public_key: bytes
    algorithm: int
    sign_count: int
    label: str
    created_at: datetime
    last_used_at: datetime | None


class AdminPasskeySession(Protocol):
    async def add_passkey(
        self,
        *,
        user_id: UUID,
        credential_id: bytes,
        public_key: bytes,
        algorithm: int,
        sign_count: int,
        label: str,
        now: datetime,
    ) -> UUID | None:
        """Store a credential. None when this credential is already stored, for anyone."""
        ...

    async def passkeys_of(self, user_id: UUID) -> list[Passkey]:
        """This administrator's passkeys that are not ended, oldest first."""
        ...

    async def passkey_by_credential(self, credential_id: bytes) -> Passkey | None:
        """The live passkey with this credential, with its row held until the transaction ends."""
        ...

    async def note_passkey_use(self, passkey_id: UUID, sign_count: int, now: datetime) -> None: ...

    async def revoke_passkey(self, user_id: UUID, passkey_id: UUID, now: datetime) -> bool:
        """End one of this administrator's passkeys. False when it is not theirs or is already ended."""
        ...
