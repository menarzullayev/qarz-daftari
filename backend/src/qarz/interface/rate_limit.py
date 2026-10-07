"""Rate limits of the API per signed-in user and per shop (technical specification, "Abuse"; REQ-N13).

One person's script, or one shop's staff together, must not slow the service for every other shop. Limits
by network address belong to the proxy in front of the application; these are the ones only the
application can apply, because only it knows who is asking and for which shop.

Each key has a bucket of tokens: a request takes one, and tokens come back at a steady rate up to the
bucket's size. So a short burst passes and a sustained flood is held to the rate.

The counters live in this process's memory. That is exact while one application process serves requests,
which is the approved deployment; with several processes each would allow the full rate.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from qarz.application.errors import AppError

MEMBER_TTL = 600.0  # seconds a caller stays known as a member of a shop after an answered request
PRUNE_EVERY = 1024  # requests between sweeps of idle buckets


class RateLimited(AppError):
    code = "RATE_LIMITED"

    def __init__(self, retry_after: float) -> None:
        super().__init__()
        # Whole seconds, rounded up: the wait is always more than nothing, and "retry after 0" would invite
        # an immediate retry that fails again.
        self.retry_after = math.ceil(retry_after)


@dataclass(frozen=True)
class Limit:
    """A bucket of `burst` tokens refilled at `per_minute` tokens a minute."""

    per_minute: int
    burst: int

    def __post_init__(self) -> None:
        if self.per_minute < 1 or self.burst < 1:
            raise ValueError("a rate limit needs a positive rate and a positive burst")


@dataclass(frozen=True)
class RateLimits:
    user: Limit
    shop: Limit


class RateLimiter:
    def __init__(self, limits: RateLimits, clock: Callable[[], float]) -> None:
        self._limits = limits
        self._clock = clock
        # key -> (tokens left, when that was true)
        self._buckets: dict[tuple[str, UUID], tuple[float, float]] = {}
        self._members: dict[tuple[UUID, UUID], float] = {}
        self._since_prune = 0

    def _tokens(self, key: tuple[str, UUID], limit: Limit, now: float) -> float:
        tokens, at = self._buckets.get(key, (float(limit.burst), now))
        return min(float(limit.burst), tokens + (now - at) * limit.per_minute / 60.0)

    def _take(self, key: tuple[str, UUID], limit: Limit) -> float | None:
        """Take one token. None when there was one; otherwise the seconds until there will be."""
        now = self._clock()
        tokens = self._tokens(key, limit, now)
        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            return (1.0 - tokens) * 60.0 / limit.per_minute
        self._buckets[key] = (tokens - 1.0, now)
        return None

    def check(self, user_id: UUID, shop_id: UUID | None) -> None:
        """Count a request of a signed-in user. Raises RateLimited when the user or the shop is over its rate.

        The shop's limit is applied only to a caller already known as its member. Anyone else goes on to
        be told the shop does not exist, as always: a busy shop must not answer differently to a stranger.
        """
        self._prune()
        wait = self._take(("user", user_id), self._limits.user)
        if wait is not None:
            raise RateLimited(wait)
        if shop_id is None:
            return
        now = self._clock()
        if self._members.get((user_id, shop_id), 0.0) <= now:
            return
        tokens = self._tokens(("shop", shop_id), self._limits.shop, now)
        if tokens < 1.0:
            raise RateLimited((1.0 - tokens) * 60.0 / self._limits.shop.per_minute)

    def answered(self, user_id: UUID, shop_id: UUID) -> None:
        """A request about a shop was answered to its member: it counts against the shop."""
        self._members[(user_id, shop_id)] = self._clock() + MEMBER_TTL
        self._take(("shop", shop_id), self._limits.shop)

    def _prune(self) -> None:
        """Forget buckets that are full again and members not seen lately, so memory follows activity."""
        self._since_prune += 1
        if self._since_prune < PRUNE_EVERY:
            return
        self._since_prune = 0
        now = self._clock()
        limits = {"user": self._limits.user, "shop": self._limits.shop}
        self._buckets = {
            key: value
            for key, value in self._buckets.items()
            if self._tokens(key, limits[key[0]], now) < limits[key[0]].burst
        }
        self._members = {key: until for key, until in self._members.items() if until > now}

    def size(self) -> tuple[int, int]:
        """How many buckets and known members are held; for tests of pruning."""
        return len(self._buckets), len(self._members)
