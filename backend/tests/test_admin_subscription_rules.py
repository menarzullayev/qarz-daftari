"""What an administrator may do to a subscription, on both sides of each limit (REQ-052, BR-30)."""

from datetime import date, timedelta

import pytest

from qarz.domain.admin_subscription import (
    MAX_PAID_DAYS_AHEAD,
    MAX_TRIAL_DAYS_AHEAD,
    ChangeRefused,
    Subscription,
    end_trial,
    set_paid_through,
    set_trial_end,
    suspend,
    unsuspend,
)

TODAY = date(2026, 10, 7)
YESTERDAY, TOMORROW = TODAY - timedelta(days=1), TODAY + timedelta(days=1)

TRIAL = Subscription("trial", TODAY + timedelta(days=5), None, None)
ENDED_TRIAL = Subscription("trial", YESTERDAY, None, None)
PAID = Subscription("active", YESTERDAY - timedelta(days=40), TODAY + timedelta(days=20), None)
LAPSED = Subscription("active", None, YESTERDAY, None)
LIMITED = Subscription("limited", YESTERDAY, None, "trial")
SUSPENDED = Subscription("suspended", TODAY + timedelta(days=5), None, "trial")


def _why(call: object) -> str:
    with pytest.raises(ChangeRefused) as refused:
        call()  # type: ignore[operator]
    return refused.value.why


# --- suspension ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("sub", [TRIAL, PAID, LIMITED, LAPSED])
def test_suspension_remembers_the_state_and_unsuspension_returns_to_it(sub: Subscription) -> None:
    held = suspend(sub)
    assert held == Subscription("suspended", sub.trial_ends, sub.paid_through, sub.state)
    back = unsuspend(held)
    assert back == Subscription(sub.state, sub.trial_ends, sub.paid_through, None)


def test_a_suspended_shop_is_not_suspended_again() -> None:
    assert _why(lambda: suspend(SUSPENDED)) == "already_suspended"


@pytest.mark.parametrize("sub", [TRIAL, PAID, LIMITED])
def test_only_a_suspended_shop_is_unsuspended(sub: Subscription) -> None:
    assert _why(lambda: unsuspend(sub)) == "not_suspended"


@pytest.mark.parametrize("prior", [None, "suspended", "nonsense"])
def test_with_nothing_sensible_remembered_a_shop_comes_back_limited(prior: str | None) -> None:
    assert unsuspend(Subscription("suspended", None, None, prior)).state == "limited"


# --- trial --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("sub", [TRIAL, ENDED_TRIAL, LIMITED, LAPSED])
def test_a_trial_is_extended_or_granted_unless_the_shop_is_paying(sub: Subscription) -> None:
    until = TODAY + timedelta(days=14)
    assert set_trial_end(sub, until, TODAY) == Subscription("trial", until, sub.paid_through, None)


def test_a_paying_shop_has_no_trial_to_extend() -> None:
    assert _why(lambda: set_trial_end(PAID, TOMORROW, TODAY)) == "paid"
    paid_through_today = Subscription("active", None, TODAY, None)
    assert _why(lambda: set_trial_end(paid_through_today, TOMORROW, TODAY)) == "paid"


def test_a_suspended_shop_is_unsuspended_first() -> None:
    assert _why(lambda: set_trial_end(SUSPENDED, TOMORROW, TODAY)) == "suspended"
    assert _why(lambda: end_trial(SUSPENDED, TODAY)) == "suspended"
    assert _why(lambda: set_paid_through(SUSPENDED, TOMORROW, TODAY)) == "suspended"


def test_a_trial_ends_between_today_and_a_year_from_now() -> None:
    last = TODAY + timedelta(days=MAX_TRIAL_DAYS_AHEAD)
    assert MAX_TRIAL_DAYS_AHEAD == 365
    assert set_trial_end(TRIAL, TODAY, TODAY).trial_ends == TODAY
    assert set_trial_end(TRIAL, last, TODAY).trial_ends == last
    assert _why(lambda: set_trial_end(TRIAL, YESTERDAY, TODAY)) == "date_out_of_range"
    assert _why(lambda: set_trial_end(TRIAL, last + timedelta(days=1), TODAY)) == "date_out_of_range"


def test_ending_a_trial_limits_the_shop_from_today() -> None:
    assert end_trial(TRIAL, TODAY) == Subscription("limited", YESTERDAY, None, "trial")
    # The last day of a trial still counts, so a trial ending today is cut to yesterday as well.
    assert end_trial(Subscription("trial", TODAY, None, None), TODAY).trial_ends == YESTERDAY
    assert end_trial(Subscription("trial", None, None, None), TODAY).trial_ends == YESTERDAY


def test_ending_a_trial_that_already_ran_out_keeps_its_real_last_day() -> None:
    long_ago = TODAY - timedelta(days=9)
    assert end_trial(Subscription("trial", long_ago, None, None), TODAY) == Subscription(
        "limited", long_ago, None, "trial"
    )


@pytest.mark.parametrize("sub", [PAID, LIMITED, LAPSED])
def test_only_a_trial_can_be_ended(sub: Subscription) -> None:
    assert _why(lambda: end_trial(sub, TODAY)) == "not_in_trial"


# --- paid period --------------------------------------------------------------------------------------


@pytest.mark.parametrize("sub", [TRIAL, ENDED_TRIAL, PAID, LIMITED, LAPSED])
def test_a_paid_through_date_from_today_on_makes_the_shop_active(sub: Subscription) -> None:
    for until in (TODAY, TODAY + timedelta(days=31)):
        assert set_paid_through(sub, until, TODAY) == Subscription("active", sub.trial_ends, until, None)


def test_the_paid_through_date_is_at_most_three_years_ahead() -> None:
    last = TODAY + timedelta(days=MAX_PAID_DAYS_AHEAD)
    assert set_paid_through(PAID, last, TODAY).paid_through == last
    assert _why(lambda: set_paid_through(PAID, last + timedelta(days=1), TODAY)) == "date_out_of_range"


def test_yesterday_ends_a_paid_period_and_nothing_earlier_is_accepted() -> None:
    assert set_paid_through(PAID, YESTERDAY, TODAY) == Subscription("limited", PAID.trial_ends, YESTERDAY, "active")
    assert _why(lambda: set_paid_through(PAID, YESTERDAY - timedelta(days=1), TODAY)) == "date_out_of_range"


@pytest.mark.parametrize("sub", [TRIAL, LIMITED])
def test_a_shop_that_is_not_paying_has_no_paid_period_to_end(sub: Subscription) -> None:
    assert _why(lambda: set_paid_through(sub, YESTERDAY, TODAY)) == "not_paid"
