import json
from datetime import date, timedelta

import pytest

from qarz.domain.subscription import (
    add_months,
    effective_state,
    extend_paid_through,
    may_add_customers,
    period_end,
    sms_included,
    warning_days,
    with_free_plan,
)
from qarz.interface.errors import error_response

TODAY = date(2026, 10, 7)
YESTERDAY, TOMORROW = TODAY - timedelta(days=1), TODAY + timedelta(days=1)


@pytest.mark.parametrize(
    ("state", "trial_ends", "paid_through", "expected"),
    [
        ("trial", TOMORROW, None, "trial"),
        ("trial", TODAY, None, "trial"),  # the last day still counts
        ("trial", YESTERDAY, None, "limited"),
        ("trial", None, None, "limited"),
        ("active", None, TODAY, "active"),
        ("active", None, YESTERDAY, "limited"),
        ("active", TOMORROW, None, "limited"),  # a trial date does not keep a paid state alive
        ("limited", TOMORROW, TOMORROW, "limited"),
        ("suspended", TOMORROW, TOMORROW, "suspended"),
        ("anything else", TOMORROW, TOMORROW, "limited"),
    ],
)
def test_what_applies_today(state: str, trial_ends: date | None, paid_through: date | None, expected: str) -> None:
    assert effective_state(state, trial_ends, paid_through, TODAY) == expected


def test_only_a_running_period_has_an_end() -> None:
    assert period_end("trial", TODAY, TOMORROW) == TODAY
    assert period_end("active", TODAY, TOMORROW) == TOMORROW
    assert period_end("limited", TODAY, TOMORROW) is None
    assert period_end("suspended", TODAY, TOMORROW) is None


@pytest.mark.parametrize(("days", "expected"), [(8, None), (7, 7), (6, None), (2, None), (1, 1), (0, None), (-1, None)])
def test_the_owner_is_warned_seven_days_and_one_day_before(days: int, expected: int | None) -> None:
    assert warning_days(TODAY + timedelta(days=days), TODAY) == expected


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2026, 10, 7), 1, date(2026, 11, 7)),
        (date(2026, 1, 31), 1, date(2026, 2, 28)),
        (date(2028, 1, 31), 1, date(2028, 2, 29)),
        (date(2026, 12, 15), 1, date(2027, 1, 15)),
        (date(2026, 10, 31), 6, date(2027, 4, 30)),
        (date(2026, 10, 7), 12, date(2027, 10, 7)),
    ],
)
def test_adding_months_keeps_the_day_or_takes_the_months_last(start: date, months: int, expected: date) -> None:
    assert add_months(start, months) == expected


def test_a_payment_extends_from_the_later_of_today_and_the_paid_date() -> None:
    # Paid early: the new period follows the current one, and no paid day is lost.
    assert extend_paid_through(date(2026, 10, 10), TODAY, 1) == date(2026, 11, 10)
    assert extend_paid_through(TODAY, TODAY, 1) == date(2026, 11, 7)
    # Paid after the period ended, or never before: today is the first paid day.
    assert extend_paid_through(YESTERDAY, TODAY, 1) == date(2026, 11, 6)
    assert extend_paid_through(None, TODAY, 1) == date(2026, 11, 6)
    assert extend_paid_through(date(2026, 1, 1), TODAY, 3) == date(2027, 1, 6)


@pytest.mark.parametrize("months", [0, -1, 37])
def test_months_out_of_range_are_refused(months: int) -> None:
    with pytest.raises(ValueError, match="months"):
        extend_paid_through(None, TODAY, months)


# --- the free plan (BR-33 to BR-35) ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("effective", "held", "customers", "expected"),
    [
        ("limited", 30, 0, "free"),
        ("limited", 30, 30, "free"),  # the last place still counts
        ("limited", 30, 31, "limited"),
        ("limited", None, 0, "limited"),  # the plan is switched off
        ("trial", 30, 5, "trial"),
        ("active", 30, 5, "active"),
        ("active", 30, 500, "active"),
        ("suspended", 30, 0, "suspended"),  # BR-30 is not the plan's to change
    ],
)
def test_a_shop_without_a_period_is_free_while_the_plan_holds_its_customers(
    effective: str, held: int | None, customers: int, expected: str
) -> None:
    assert with_free_plan(effective, held, customers) == expected


@pytest.mark.parametrize(
    ("effective", "held", "customers", "adding", "expected"),
    [
        ("limited", 30, 29, 1, True),
        ("limited", 30, 30, 1, False),
        ("limited", 30, 31, 1, False),  # a shop already over the number takes no more either
        ("limited", 30, 20, 10, True),
        ("limited", 30, 20, 11, False),
        ("trial", 30, 30, 1, True),
        ("active", 30, 3000, 500, True),
        ("limited", None, 3000, 500, True),  # switched off: nothing is counted
    ],
)
def test_only_a_running_period_goes_beyond_what_the_plan_holds(
    effective: str, held: int | None, customers: int, adding: int, expected: bool
) -> None:
    assert may_add_customers(effective, held, customers, adding) is expected


def test_sms_belongs_to_a_paid_period_alone_while_the_plan_is_on() -> None:
    assert sms_included("active", True)
    for state in ("trial", "limited", "free", "suspended"):
        assert not sms_included(state, True)
        assert sms_included(state, False), "with the plan off SMS does not depend on the subscription"


@pytest.mark.parametrize(("lang", "word"), [("uz", "tagacha"), ("ru", "клиентов")])
def test_the_refusal_names_how_many_the_plan_holds_in_both_languages(lang: str, word: str) -> None:
    response = error_response("FREE_PLAN_FULL", lang, {"limit": "30"})
    assert response.status_code == 402
    error = json.loads(bytes(response.body))["error"]
    assert error["fields"] == {"limit": "30"}
    assert "30" in error["message"] and word in error["message"] and "/obuna" in error["message"]
    assert "{" not in error["message"], "the number is written into the sentence"
