"""PostgreSQL storage. Tenant isolation is enforced by the database (ADR-016).

Every tenant transaction sets `qd.shop_id` with transaction scope, so the setting cannot survive into the
next use of a pooled connection, and the row-level security policies hide every other shop's rows.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from qarz.application.ports import Membership, ShopSettings
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
