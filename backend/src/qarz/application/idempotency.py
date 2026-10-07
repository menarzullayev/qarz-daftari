"""Idempotent API writes (ADR-006): a repeated request returns the stored result and has no second effect."""

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID

from qarz.application.errors import AppError, ValidationFailed

_KEY = re.compile(r"[A-Za-z0-9_-]{8,128}")


class RequestKeys(Protocol):
    """Where stored results live: a shop's tenant session, or the administrator's own store."""

    async def lock_request_key(self, key: str) -> None: ...

    async def stored_response(self, key: str) -> dict[str, Any] | None: ...

    async def store_response(self, key: str, response: dict[str, Any]) -> None: ...


class IdempotencyKeyReused(AppError):
    """The key was already used for a different request."""

    code = "IDEMPOTENCY_KEY_REUSED"


def validate_key(key: str | None) -> str:
    if key is None or not _KEY.fullmatch(key):
        raise ValidationFailed({"Idempotency-Key": "required: 8 to 128 characters of A-Z a-z 0-9 _ -"})
    return key


def fingerprint(request: dict[str, Any]) -> str:
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def run_once(
    session: RequestKeys,
    *,
    key: str,
    operation: str,
    user_id: UUID,
    request: dict[str, Any],
    action: Callable[[], Awaitable[dict[str, Any]]],
    redact: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run `action` unless this key already produced a result in this shop (or for this administrator).

    Must be called inside the tenant transaction that performs the write, after authorization. The stored
    result is saved in the same transaction, so a failed write stores nothing and can be retried.
    `redact` removes secrets from the stored copy; a repeat then returns the redacted result.
    """
    await session.lock_request_key(key)
    stored = await session.stored_response(key)
    signature = {"operation": operation, "user": str(user_id), "request": fingerprint(request)}
    if stored is not None:
        if {name: stored.get(name) for name in signature} != signature:
            raise IdempotencyKeyReused()
        body = stored["body"]
        assert isinstance(body, dict)
        return body
    body = await action()
    await session.store_response(key, {**signature, "body": redact(body) if redact else body})
    return body
