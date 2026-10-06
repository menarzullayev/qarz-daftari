"""A path of the administrator's service that the real database reaches only through a race.

Between locking a subscription and storing the change, the administrator's account can be disabled (or
the shop erased) by another transaction; the store function then changes nothing and says so. The
service must not audit, tell the owner, or remember an answer for a change that did not happen.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import uuid4

import pytest

from qarz.application.admin import AdminService
from qarz.application.admin_access import AdminAccess
from qarz.application.errors import NotFound
from qarz.application.ports import LockedSubscription, Storage


class _Session:
    def __init__(self, *, stored: bool) -> None:
        self._stored = stored
        self.calls: list[str] = []

    async def lock_admin_request_key(self, admin_id: Any, key: str) -> None:
        self.calls.append("lock_key")

    async def admin_stored_response(self, admin_id: Any, key: str) -> None:
        return None

    async def admin_lock_subscription(self, admin_id: Any, shop_id: Any) -> LockedSubscription:
        return LockedSubscription("trial", date(2026, 11, 1), None, None, "Shop", 42, "uz")

    async def admin_store_subscription(self, admin_id: Any, shop_id: Any, **values: Any) -> bool:
        self.calls.append(f"store:{values['state']}")
        return self._stored

    def __getattr__(self, name: str) -> Any:
        async def record(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            return [] if name == "admin_shop_search" else uuid4()

        return record


class _Storage:
    def __init__(self, session: _Session) -> None:
        self._session = session

    @asynccontextmanager
    async def platform(self) -> AsyncIterator[_Session]:
        yield self._session


def _suspend(session: _Session) -> Any:
    service = AdminService(
        cast(Storage, _Storage(session)), cast(AdminAccess, None), lambda: datetime(2026, 10, 7, tzinfo=UTC)
    )
    return asyncio.run(service.suspend(uuid4(), uuid4(), "Tekshiruv uchun", "unit-test-key-0001"))


def test_a_change_the_database_did_not_store_is_not_found_and_leaves_no_trace() -> None:
    session = _Session(stored=False)
    with pytest.raises(NotFound):
        _suspend(session)
    assert session.calls == ["lock_key", "store:suspended"]


def test_the_same_path_when_the_change_is_stored_audits_and_tells_the_owner() -> None:
    """The control: the fake does carry a change through when the store succeeds."""
    session = _Session(stored=True)
    with pytest.raises(NotFound):
        # The fake has no shop to read back, which is where this ends; by then everything was written.
        _suspend(session)
    assert session.calls == ["lock_key", "store:suspended", "add_admin_audit", "enqueue", "admin_shop_search"]
