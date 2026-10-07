"""Keeping a file for a shop (ADR-020; technical specification, "Security" and "Retention").

The content goes to the file store under a random key; the shop's database row says what it is, how big,
its SHA-256 and when it must be deleted. The row is written in the caller's shop transaction and read back
only inside a transaction of the same shop, so row-level security decides who can reach a file.

The store is not transactional. A file is therefore staged first, outside any shop transaction, and
discarded again by the caller if the transaction that was to record it does not commit.
"""

import contextlib
import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from qarz.application.errors import AppError, NotFound, ValidationFailed
from qarz.application.ports import FileMissing, FileStore, FileStoreError, Storage, StoredFileRecord, TenantSession
from qarz.domain.file_links import derive_key, expiry, sign, verify
from qarz.domain.files import EXTENSIONS, FileRefusal, clean_receipt, object_key

PURGE_BATCH = 100


class FileStoreUnavailable(AppError):
    """No file store is configured, or it did not answer. Nothing was stored."""

    code = "FILE_STORE_UNAVAILABLE"


@dataclass(frozen=True)
class CheckedFile:
    """Content that is a whole, well-formed file of an accepted type, already without its metadata."""

    mime: str
    content: bytes


@dataclass(frozen=True)
class StagedFile:
    """Content already in the store, not yet recorded for any shop."""

    key: str
    sha256: bytes
    size_bytes: int
    mime: str


def _new_token() -> str:
    # 256 random bits: the key cannot be guessed and carries nothing about the shop, the person or the time.
    return secrets.token_hex(32)


def _due(record: StoredFileRecord, now: datetime) -> bool:
    """Marked for deletion: its retention has run out, or its customer's data was removed."""
    return record.delete_after is not None and record.delete_after <= now


class FileService:
    def __init__(
        self,
        storage: Storage,
        store: FileStore | None,
        new_token: Callable[[], str] | None = None,
        *,
        link_secret: str | None = None,
    ) -> None:
        self._storage = storage
        self._store = store
        self._new_token = new_token or _new_token
        # Without the server secret no link can be signed or believed: no file is served at all.
        self._link_key = None if link_secret is None else derive_key(link_secret)

    @staticmethod
    def check(data: bytes) -> CheckedFile:
        """An acceptable receipt, as it will be kept. The declared type and the file name are never consulted.

        The whole file is read through, not only its first bytes; what comes back has no metadata and
        nothing after the end of the image (security review, P36-1). Nothing is stored here.
        """
        outcome = clean_receipt(data)
        if isinstance(outcome, FileRefusal):
            raise ValidationFailed({"receipt": outcome.value})
        return CheckedFile(*outcome)

    async def stage(self, checked: CheckedFile) -> StagedFile:
        if self._store is None:
            raise FileStoreUnavailable()
        data = checked.content
        staged = StagedFile(object_key(self._new_token()), hashlib.sha256(data).digest(), len(data), checked.mime)
        try:
            await self._store.put(staged.key, data, checked.mime)
        except FileStoreError:
            raise FileStoreUnavailable() from None
        return staged

    async def discard(self, staged: StagedFile) -> None:
        """Remove a staged file that was not recorded. Best effort: a failure here must not hide the cause."""
        if self._store is not None:
            with contextlib.suppress(Exception):
                await self._store.delete(staged.key)

    @staticmethod
    async def record_in(
        session: TenantSession, staged: StagedFile, *, purpose: str, now: datetime, delete_after: datetime
    ) -> UUID:
        file_id = uuid4()
        await session.add_stored_file(
            file_id=file_id,
            purpose=purpose,
            object_key=staged.key,
            sha256=staged.sha256,
            size_bytes=staged.size_bytes,
            mime=staged.mime,
            now=now,
            delete_after=delete_after,
        )
        return file_id

    @staticmethod
    async def record_of(session: TenantSession, file_id: UUID) -> StoredFileRecord:
        """What the session's shop keeps under this identifier. Another shop's file does not exist here."""
        record = await session.get_stored_file(file_id)
        if record is None:
            raise NotFound()
        return record

    def link(self, shop_id: UUID, record: StoredFileRecord, now: datetime) -> tuple[str, datetime]:
        """A link to the file for someone who has just been authorized to see it, and when it stops working.

        Called with a record read in the shop's own transaction. A file that is due for deletion gets none.
        """
        if self._link_key is None or self._store is None:
            raise FileStoreUnavailable()
        if _due(record, now):
            raise NotFound()
        expires_at = expiry(now)
        return sign(self._link_key, shop_id, record.file_id, expires_at), expires_at

    async def serve(self, token: str, now: datetime) -> tuple[str, str, bytes]:
        """Type, file name and content behind a link, for whoever holds it while it is valid.

        Anything else is simply not found: a link that was changed, signed with another key or expired,
        and a file that has been deleted or marked for deletion since the link was given.
        """
        named = None if self._link_key is None else verify(self._link_key, token, now)
        if named is None:
            raise NotFound()
        shop_id, file_id = named
        async with self._storage.tenant(shop_id) as session:
            record = await session.get_stored_file(file_id)
        if record is None or _due(record, now):
            raise NotFound()
        # The name says what kind of file it is and carries part of its identifier: nothing about a person.
        kind = "export" if record.purpose == "export" else "receipt"
        return record.mime, f"{kind}-{file_id.hex[:8]}.{EXTENSIONS[record.mime]}", await self.content(record)

    async def content(self, record: StoredFileRecord) -> bytes:
        """The content of a file whose record was read in its shop's transaction.

        Called after that transaction has ended: the file store is a network call, and no database
        connection is held while it answers (security review, P36-2).
        """
        if self._store is None:
            raise FileStoreUnavailable()
        try:
            data = await self._store.get(record.object_key)
        except FileMissing:
            raise NotFound() from None
        except FileStoreError:
            raise FileStoreUnavailable() from None
        # What was stored is what is served: content that no longer matches its record is not handed out.
        if hashlib.sha256(data).digest() != record.sha256:
            raise NotFound()
        return data

    async def purge_due_receipts(self, shop_id: UUID, now: datetime, limit: int = PURGE_BATCH) -> int:
        """Delete the shop's receipts, of payment notices and of the subscription, whose retention has
        run out. For the retention job.

        The object goes first and its row after, each row in its own short transaction, so no network
        call is made inside a transaction and a failure leaves a row that the next run finds again.
        """
        if self._store is None:
            raise FileStoreUnavailable()
        async with self._storage.tenant(shop_id) as session:
            due = await session.due_receipt_files(now, limit)
        removed = 0
        for record in due:
            try:
                await self._store.delete(record.object_key)
            except FileStoreError:
                continue
            async with self._storage.tenant(shop_id) as session:
                await session.remove_stored_file(record.file_id)
            removed += 1
        return removed
