"""PostgreSQL storage. Tenant isolation is enforced by the database (ADR-016).

Every tenant transaction sets `qd.shop_id` with transaction scope, so the setting cannot survive into the
next use of a pooled connection, and the row-level security policies hide every other shop's rows.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from qarz.application.ports import Membership, OutboxMessage, SessionInfo, ShopSettings
from qarz.domain.access import Role


def _async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


class PgTenantSession:
    def __init__(self, conn: AsyncConnection, shop_id: UUID) -> None:
        self._conn = conn
        self._shop_id = shop_id

    async def active_membership(self, user_id: UUID) -> Membership | None:
        row = (
            await self._conn.execute(
                text("SELECT id, role FROM membership WHERE user_id = :user_id AND status = 'active'"),
                {"user_id": user_id},
            )
        ).first()
        return None if row is None else Membership(row.id, Role(row.role))

    async def shop_settings(self) -> ShopSettings | None:
        row = (
            await self._conn.execute(
                text("SELECT id, name, lang, default_promise_days FROM shop WHERE status <> 'erased'")
            )
        ).first()
        return None if row is None else ShopSettings(row.id, row.name, row.lang, row.default_promise_days)

    async def update_shop_settings(
        self, *, name: str | None, lang: str | None, default_promise_days: int | None
    ) -> ShopSettings:
        row = (
            await self._conn.execute(
                text(
                    "UPDATE shop SET name = coalesce(:name, name), lang = coalesce(:lang, lang), "
                    "default_promise_days = coalesce(:days, default_promise_days) "
                    "WHERE id = :shop_id RETURNING id, name, lang, default_promise_days"
                ),
                {"name": name, "lang": lang, "days": default_promise_days, "shop_id": self._shop_id},
            )
        ).one()
        return ShopSettings(row.id, row.name, row.lang, row.default_promise_days)

    async def record_activity(self, *, membership_id: UUID, action: str, subject_type: str, subject_id: UUID) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO activity (id, shop_id, actor_kind, actor_id, action, subject_type, subject_id) "
                "VALUES (:id, :shop_id, 'staff', :actor_id, :action, :subject_type, :subject_id)"
            ),
            {
                "id": uuid4(),
                "shop_id": self._shop_id,
                "actor_id": membership_id,
                "action": action,
                "subject_type": subject_type,
                "subject_id": subject_id,
            },
        )

    async def lock_request_key(self, key: str) -> None:
        # Transaction-scoped advisory lock: a second request with the same key in the same shop waits here
        # until the first commits, then finds the stored response.
        await self._conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"request_key:{self._shop_id}:{key}"},
        )

    async def stored_response(self, key: str) -> dict[str, Any] | None:
        row = (
            await self._conn.execute(text("SELECT response FROM request_key WHERE key = :key"), {"key": key})
        ).first()
        if row is None:
            return None
        response = row.response
        return json.loads(response) if isinstance(response, str) else dict(response)

    async def store_response(self, key: str, response: dict[str, Any]) -> None:
        await self._conn.execute(
            text("INSERT INTO request_key (shop_id, key, response) VALUES (:shop_id, :key, CAST(:response AS jsonb))"),
            {"shop_id": self._shop_id, "key": key, "response": json.dumps(response, ensure_ascii=False)},
        )


class PgPlatformSession:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def claim_update(self, update_id: int) -> bool:
        row = (
            await self._conn.execute(
                text(
                    "INSERT INTO processed_update (update_id) VALUES (:id) "
                    "ON CONFLICT (update_id) DO NOTHING RETURNING update_id"
                ),
                {"id": update_id},
            )
        ).first()
        return row is not None

    async def language_of_telegram_user(self, tg_id: int) -> str | None:
        row = (
            await self._conn.execute(text("SELECT lang FROM app_user WHERE tg_id = :tg_id"), {"tg_id": tg_id})
        ).first()
        return None if row is None else str(row.lang)

    async def ensure_user(self, tg_id: int, lang: str) -> UUID:
        # The no-op update makes RETURNING yield the existing row without changing its language.
        row = (
            await self._conn.execute(
                text(
                    "INSERT INTO app_user (id, tg_id, lang) VALUES (:id, :tg_id, :lang) "
                    "ON CONFLICT (tg_id) DO UPDATE SET tg_id = EXCLUDED.tg_id RETURNING id"
                ),
                {"id": uuid4(), "tg_id": tg_id, "lang": lang},
            )
        ).one()
        return UUID(str(row.id))

    async def user_language(self, user_id: UUID) -> str | None:
        row = (await self._conn.execute(text("SELECT lang FROM app_user WHERE id = :id"), {"id": user_id})).first()
        return None if row is None else str(row.lang)

    async def set_user_language(self, user_id: UUID, lang: str) -> None:
        await self._conn.execute(text("UPDATE app_user SET lang = :lang WHERE id = :id"), {"id": user_id, "lang": lang})

    async def create_session(
        self,
        *,
        token_hash: bytes,
        user_id: UUID,
        kind: str,
        csrf_hash: bytes | None,
        now: datetime,
        expires_at: datetime,
    ) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO user_session (id, token_hash, user_id, kind, csrf_hash, created_at, expires_at) "
                "VALUES (:id, :token_hash, :user_id, :kind, :csrf_hash, :now, :expires_at)"
            ),
            {
                "id": uuid4(),
                "token_hash": token_hash,
                "user_id": user_id,
                "kind": kind,
                "csrf_hash": csrf_hash,
                "now": now,
                "expires_at": expires_at,
            },
        )

    async def find_session(self, token_hash: bytes, now: datetime) -> SessionInfo | None:
        row = (
            await self._conn.execute(
                text(
                    "SELECT user_id, kind, csrf_hash FROM user_session "
                    "WHERE token_hash = :token_hash AND revoked_at IS NULL AND expires_at > :now"
                ),
                {"token_hash": token_hash, "now": now},
            )
        ).first()
        if row is None:
            return None
        return SessionInfo(row.user_id, row.kind, None if row.csrf_hash is None else bytes(row.csrf_hash))

    async def revoke_session(self, token_hash: bytes, now: datetime) -> None:
        await self._conn.execute(
            text("UPDATE user_session SET revoked_at = :now WHERE token_hash = :token_hash AND revoked_at IS NULL"),
            {"token_hash": token_hash, "now": now},
        )

    async def enqueue(
        self, *, channel: str, recipient: str, payload: dict[str, Any], dedupe_key: str, shop_id: UUID | None = None
    ) -> bool:
        row = (
            await self._conn.execute(
                text(
                    "INSERT INTO outbox_message (id, channel, recipient, shop_id, payload, dedupe_key) "
                    "VALUES (:id, :channel, :recipient, :shop_id, CAST(:payload AS jsonb), :dedupe_key) "
                    "ON CONFLICT (dedupe_key) DO NOTHING RETURNING id"
                ),
                {
                    "id": uuid4(),
                    "channel": channel,
                    "recipient": recipient,
                    "shop_id": shop_id,
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "dedupe_key": dedupe_key,
                },
            )
        ).first()
        return row is not None

    async def claim_due_messages(self, *, now: datetime, lease_seconds: int, limit: int) -> list[OutboxMessage]:
        rows = (
            await self._conn.execute(
                text(
                    "UPDATE outbox_message SET next_try_at = :lease_until "
                    "WHERE id IN (SELECT id FROM outbox_message WHERE status = 'pending' AND next_try_at <= :now "
                    "             ORDER BY created_at, id LIMIT :limit FOR UPDATE SKIP LOCKED) "
                    "RETURNING id, channel, recipient, payload, attempts, created_at"
                ),
                {"now": now, "lease_until": now + timedelta(seconds=lease_seconds), "limit": limit},
            )
        ).all()
        messages = [
            OutboxMessage(
                message_id=row.id,
                channel=row.channel,
                recipient=row.recipient,
                payload=json.loads(row.payload) if isinstance(row.payload, str) else dict(row.payload),
                attempts=row.attempts,
                created_at=row.created_at,
            )
            for row in rows
        ]
        # RETURNING does not preserve the subquery's order.
        return sorted(messages, key=lambda m: (m.created_at, str(m.message_id)))

    async def mark_sent(self, message_id: UUID, *, now: datetime) -> None:
        await self._conn.execute(
            text("UPDATE outbox_message SET status = 'sent', sent_at = :now WHERE id = :id AND status = 'pending'"),
            {"id": message_id, "now": now},
        )

    async def reschedule(self, message_id: UUID, *, next_try_at: datetime, count_attempt: bool) -> None:
        await self._conn.execute(
            text(
                "UPDATE outbox_message SET next_try_at = :next_try_at, attempts = attempts + :inc "
                "WHERE id = :id AND status = 'pending'"
            ),
            {"id": message_id, "next_try_at": next_try_at, "inc": 1 if count_attempt else 0},
        )

    async def mark_failed(self, message_id: UUID) -> None:
        await self._conn.execute(
            text("UPDATE outbox_message SET status = 'failed' WHERE id = :id AND status = 'pending'"),
            {"id": message_id},
        )

    async def fail_pending_for(self, *, channel: str, recipient: str) -> int:
        result = await self._conn.execute(
            text(
                "UPDATE outbox_message SET status = 'failed' "
                "WHERE status = 'pending' AND channel = :channel AND recipient = :recipient"
            ),
            {"channel": channel, "recipient": recipient},
        )
        return int(result.rowcount)

    async def mark_recipient_unreachable(self, tg_id: int) -> int:
        row = (await self._conn.execute(text("SELECT mark_recipient_unreachable(:tg_id) AS n"), {"tg_id": tg_id})).one()
        return int(row.n)


class Database:
    """Connection pool for the application role, which cannot bypass row-level security."""

    def __init__(self, url: str, *, pool_size: int = 5, max_overflow: int = 5) -> None:
        self._engine: AsyncEngine = create_async_engine(
            _async_url(url), pool_pre_ping=True, pool_size=pool_size, max_overflow=max_overflow
        )

    @asynccontextmanager
    async def tenant(self, shop_id: UUID) -> AsyncIterator[PgTenantSession]:
        async with self._engine.begin() as conn:
            # is_local = true: the setting ends with this transaction.
            await conn.execute(text("SELECT set_config('qd.shop_id', :shop_id, true)"), {"shop_id": str(shop_id)})
            yield PgTenantSession(conn, shop_id)

    @asynccontextmanager
    async def platform(self) -> AsyncIterator[PgPlatformSession]:
        async with self._engine.begin() as conn:
            yield PgPlatformSession(conn)

    async def user_language(self, user_id: UUID) -> str | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(text("SELECT lang FROM app_user WHERE id = :id"), {"id": user_id})).first()
        return None if row is None else str(row.lang)

    async def reachable(self) -> bool:
        async with self._engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def dispose(self) -> None:
        await self._engine.dispose()
