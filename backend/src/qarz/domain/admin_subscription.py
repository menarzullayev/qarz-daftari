"""What an administrator may do to a shop's subscription (REQ-052, REQ-058; BR-30).

Each function takes the stored subscription and returns the one to store, or refuses with the reason.
Suspension remembers the state it interrupted and returns to it; everything else is refused while a
shop is suspended, so there is one way out of suspension.
"""

from dataclasses import dataclass, replace
from datetime import date, timedelta

from qarz.domain.subscription import ACTIVE, LIMITED, SUSPENDED, TRIAL, effective_state

MAX_TRIAL_DAYS_AHEAD = 365
MAX_PAID_DAYS_AHEAD = 3 * 366  # three years, the longest a single payment may cover


class ChangeRefused(Exception):
    """The change does not apply to the subscription as it stands. `why` is a stable word for the API."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


@dataclass(frozen=True)
class Subscription:
    state: str
    trial_ends: date | None
    paid_through: date | None
    prior_state: str | None

    def effective(self, today: date) -> str:
        return effective_state(self.state, self.trial_ends, self.paid_through, today)


def suspend(sub: Subscription) -> Subscription:
    if sub.state == SUSPENDED:
        raise ChangeRefused("already_suspended")
    return replace(sub, state=SUSPENDED, prior_state=sub.state)


def unsuspend(sub: Subscription) -> Subscription:
    if sub.state != SUSPENDED:
        raise ChangeRefused("not_suspended")
    # A row suspended by hand, with nothing remembered, comes back limited: the most cautious state.
    back = sub.prior_state if sub.prior_state in (TRIAL, ACTIVE, LIMITED) else LIMITED
    return replace(sub, state=back, prior_state=None)


def set_trial_end(sub: Subscription, trial_ends: date, today: date) -> Subscription:
    """Extend a trial, or grant one to a shop that is limited. A paying shop has no trial to extend."""
    now = sub.effective(today)
    if now == SUSPENDED:
        raise ChangeRefused("suspended")
    if now == ACTIVE:
        raise ChangeRefused("paid")
    if not today <= trial_ends <= today + timedelta(days=MAX_TRIAL_DAYS_AHEAD):
        raise ChangeRefused("date_out_of_range")
    return replace(sub, state=TRIAL, trial_ends=trial_ends, prior_state=None)


def end_trial(sub: Subscription, today: date) -> Subscription:
    """End a trial at once: the shop is limited from today, and the trial is recorded as over yesterday."""
    if sub.state != TRIAL:
        raise ChangeRefused("suspended" if sub.state == SUSPENDED else "not_in_trial")
    yesterday = today - timedelta(days=1)
    ended = sub.trial_ends if sub.trial_ends is not None and sub.trial_ends < today else yesterday
    return replace(sub, state=LIMITED, trial_ends=ended, prior_state=TRIAL)


def set_paid_through(sub: Subscription, paid_through: date, today: date) -> Subscription:
    """Set the last paid day. Today or later makes the shop active; yesterday ends a paid period."""
    if sub.state == SUSPENDED:
        raise ChangeRefused("suspended")
    if not today - timedelta(days=1) <= paid_through <= today + timedelta(days=MAX_PAID_DAYS_AHEAD):
        raise ChangeRefused("date_out_of_range")
    if paid_through >= today:
        return replace(sub, state=ACTIVE, paid_through=paid_through, prior_state=None)
    if sub.state != ACTIVE:
        # Only a paid period can be ended this way; a trial has its own operation.
        raise ChangeRefused("not_paid")
    return replace(sub, state=LIMITED, paid_through=paid_through, prior_state=ACTIVE)
