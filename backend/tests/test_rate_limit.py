"""The token buckets behind the API's rate limits, without HTTP or a database."""

import uuid

import pytest

from qarz.interface.rate_limit import MEMBER_TTL, PRUNE_EVERY, Limit, RateLimited, RateLimiter, RateLimits


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


USER, SHOP = Limit(60, 3), Limit(60, 5)


def limiter(clock: Clock, user: Limit = USER, shop: Limit = SHOP) -> RateLimiter:
    return RateLimiter(RateLimits(user=user, shop=shop), clock)


def refused(limits: RateLimiter, user: uuid.UUID, shop: uuid.UUID | None = None) -> int | None:
    try:
        limits.check(user, shop)
    except RateLimited as error:
        return error.retry_after
    return None


def test_a_burst_passes_and_then_the_rate_holds() -> None:
    clock = Clock()
    limits, me = limiter(clock), uuid.uuid4()
    assert [refused(limits, me) for _ in range(4)] == [None, None, None, 1]
    clock.now += 0.5
    assert refused(limits, me) == 1, "half a second is half a token at 60 a minute"
    clock.now += 0.5
    assert refused(limits, me) is None
    assert refused(limits, me) == 1
    # A long pause refills no more than the burst.
    clock.now += 3600
    assert [refused(limits, me) for _ in range(4)] == [None, None, None, 1]


def test_refused_requests_do_not_push_the_wait_further() -> None:
    clock = Clock()
    limits, me = limiter(clock, user=Limit(6, 1)), uuid.uuid4()
    assert refused(limits, me) is None
    for _ in range(50):
        assert refused(limits, me) == 10, "one token every ten seconds, however often it is asked for"
    clock.now += 10
    assert refused(limits, me) is None


def test_the_wait_is_whole_seconds_rounded_up_and_never_zero() -> None:
    clock = Clock()
    limits, me = limiter(clock, user=Limit(6000, 1)), uuid.uuid4()
    assert refused(limits, me) is None
    assert refused(limits, me) == 1, "a hundredth of a second is still said as one"
    slow = limiter(clock, user=Limit(7, 1))
    assert refused(slow, me) is None
    assert refused(slow, me) == 9  # 60 / 7 = 8.57
    quick = limiter(clock, user=Limit(50, 1))
    assert refused(quick, me) is None
    assert refused(quick, me) == 2  # 60 / 50 = 1.2: up, not to the nearest


def test_each_user_has_their_own_bucket() -> None:
    clock = Clock()
    limits, one, other = limiter(clock), uuid.uuid4(), uuid.uuid4()
    assert [refused(limits, one) for _ in range(4)][-1] == 1
    assert refused(limits, other) is None


def test_a_shop_is_limited_only_for_those_known_as_its_members() -> None:
    clock = Clock()
    limits = limiter(clock, user=Limit(6000, 1000), shop=Limit(60, 2))
    member, colleague, stranger, shop, other_shop = (uuid.uuid4() for _ in range(5))
    for _ in range(2):
        assert refused(limits, member, shop) is None
        limits.answered(member, shop)
    assert refused(limits, member, shop) == 1, "the shop's bucket is empty"
    # A colleague not seen yet passes once, is answered, and from then on shares the shop's limit.
    assert refused(limits, colleague, shop) is None
    limits.answered(colleague, shop)
    assert refused(limits, colleague, shop) == 1
    # A stranger is never told "too many requests" about a shop: they go on to "not found".
    for _ in range(20):
        assert refused(limits, stranger, shop) is None
    # Another shop, and requests about no shop, are not held back by this one.
    assert refused(limits, member, other_shop) is None
    assert refused(limits, member) is None
    clock.now += 1
    assert refused(limits, member, shop) is None


def test_requests_refused_for_the_shop_still_count_against_the_user() -> None:
    clock = Clock()
    limits = limiter(clock, user=Limit(60, 3), shop=Limit(60, 1))
    member, shop = uuid.uuid4(), uuid.uuid4()
    assert refused(limits, member, shop) is None
    limits.answered(member, shop)
    assert [refused(limits, member, shop) for _ in range(3)] == [1, 1, 1]
    assert refused(limits, member) == 1, "three tokens went on the first and the two refused for the shop"


def test_membership_is_forgotten_after_a_while() -> None:
    clock = Clock()
    limits = limiter(clock, user=Limit(6000, 1000), shop=Limit(1, 1))
    member, shop = uuid.uuid4(), uuid.uuid4()
    limits.answered(member, shop)
    assert refused(limits, member, shop) == 60
    clock.now += MEMBER_TTL - 30
    limits.answered(member, shop)  # seen again: known for another while
    clock.now += 31
    assert refused(limits, member, shop) is not None


def test_a_member_is_known_for_ten_minutes_after_their_last_answered_request() -> None:
    clock = Clock()
    limits = limiter(clock, user=Limit(6000, 1000), shop=Limit(1, 1))
    member, colleague, shop = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    limits.answered(member, shop)
    clock.now += MEMBER_TTL - 1
    limits.answered(colleague, shop)  # the shop's bucket is empty again
    assert refused(limits, member, shop) is not None, "a second before the end the member is still known"
    clock.now += 1
    assert refused(limits, member, shop) is None, "exactly at the end they are no longer known"
    assert refused(limits, colleague, shop) is not None


def test_idle_buckets_and_old_members_are_forgotten() -> None:
    clock = Clock()
    limits = limiter(clock, user=Limit(60, 3), shop=Limit(60, 5))
    shop = uuid.uuid4()
    for _ in range(PRUNE_EVERY - 1):
        user = uuid.uuid4()
        limits.check(user, shop)
        limits.answered(user, shop)
    assert limits.size() == (PRUNE_EVERY, PRUNE_EVERY - 1)
    clock.now += 0.5  # not yet full again: kept
    busy = uuid.uuid4()
    limits.check(busy, None)
    assert limits.size()[0] == PRUNE_EVERY + 1
    clock.now += MEMBER_TTL + 1
    for _ in range(PRUNE_EVERY):
        refused(limits, busy)  # the sweep runs among these, whether or not the request is let through
        clock.now += 0.001
    assert limits.size() == (1, 0), "only the bucket still in use is held"


@pytest.mark.parametrize(("per_minute", "burst"), [(0, 1), (1, 0), (-5, 10), (10, -1)])
def test_a_limit_must_be_positive(per_minute: int, burst: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        Limit(per_minute, burst)
