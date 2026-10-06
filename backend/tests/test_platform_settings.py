"""Platform settings: each value's type and range, the defaults, and which changes need a code (ADR-018)."""

from typing import Any

import pytest

from qarz.domain.platform_settings import SETTINGS, InvalidSetting, effective, masked, needs_code, validate


def test_the_settings_are_the_ones_the_administrator_controls() -> None:
    assert set(SETTINGS) == {
        "trial_on",
        "trial_days",
        "price_uzs",
        "card_number",
        "review_group",
        "sms_on",
        "sms_monthly_quota",
        "online_pay_on",
    }


def test_defaults_when_nothing_is_stored() -> None:
    assert {key: effective(key, None) for key in SETTINGS} == {
        "trial_on": True,  # REQ-052
        "trial_days": 30,
        "price_uzs": 100_000,  # REQ-053
        "card_number": None,
        "review_group": None,
        "sms_on": False,
        "sms_monthly_quota": 0,
        "online_pay_on": False,
    }


def test_the_two_integration_switches_are_off_by_default() -> None:
    assert SETTINGS["sms_on"].default is False
    assert SETTINGS["online_pay_on"].default is False


def test_price_card_and_switches_need_a_code() -> None:
    """Specification: "changes to price, card number, and switches ask for the code again"."""
    assert {key for key in SETTINGS if needs_code(key)} == {
        "price_uzs",
        "card_number",
        "trial_on",
        "sms_on",
        "online_pay_on",
        "review_group",
    }
    assert not needs_code("trial_days")
    assert not needs_code("sms_monthly_quota")


@pytest.mark.parametrize("key", ["trial_on", "sms_on", "online_pay_on"])
def test_a_switch_is_true_or_false_and_nothing_else(key: str) -> None:
    assert validate(key, True) is True
    assert validate(key, False) is False
    for wrong in (1, 0, "true", "on", None, [True]):
        with pytest.raises(InvalidSetting):
            validate(key, wrong)


@pytest.mark.parametrize(
    ("key", "low", "high"),
    [("trial_days", 1, 365), ("price_uzs", 1_000, 10_000_000), ("sms_monthly_quota", 0, 100_000)],
)
def test_a_number_stays_inside_its_range(key: str, low: int, high: int) -> None:
    assert validate(key, low) == low
    assert validate(key, high) == high
    for wrong in (low - 1, high + 1, True, 30.0, "30", None):
        with pytest.raises(InvalidSetting):
            validate(key, wrong)


def test_a_card_number_is_sixteen_digits_or_cleared() -> None:
    assert validate("card_number", "8600 1234 5678 9012") == "8600123456789012"
    assert validate("card_number", "8600123456789012") == "8600123456789012"
    assert validate("card_number", None) is None
    for wrong in ("860012345678901", "86001234567890123", "8600-1234-5678-9012", "86001234567890ab", "", 8600, True):
        with pytest.raises(InvalidSetting):
            validate("card_number", wrong)
    with pytest.raises(InvalidSetting):
        validate("card_number", "٨٦٠٠١٢٣٤٥٦٧٨٩٠١٢")  # digits, but not the ones a card carries


def test_the_review_group_is_a_group_chat_or_cleared() -> None:
    assert validate("review_group", -1001234567890) == -1001234567890
    assert validate("review_group", -1) == -1
    assert validate("review_group", -(10**15)) == -(10**15)
    assert validate("review_group", None) is None
    for wrong in (0, 1, 123456789, -(10**15) - 1, "-100123", True, -1.0):
        with pytest.raises(InvalidSetting):
            validate("review_group", wrong)


def test_an_unknown_setting_is_refused() -> None:
    with pytest.raises(InvalidSetting, match="unknown"):
        validate("price", 1000)


@pytest.mark.parametrize(
    ("key", "stored", "expected"),
    [
        ("price_uzs", 150_000, 150_000),
        ("price_uzs", 5, 100_000),  # out of range: the default applies, not the damaged value
        ("price_uzs", "150000", 100_000),
        ("sms_on", "yes", False),
        ("sms_on", True, True),
        ("trial_on", False, False),
        ("card_number", "8600123456789012", "8600123456789012"),
        ("card_number", "not a card", None),
    ],
)
def test_a_stored_value_applies_only_if_it_is_valid(key: str, stored: Any, expected: Any) -> None:
    assert effective(key, stored) == expected


def test_the_audit_never_holds_a_whole_card_number() -> None:
    assert masked("card_number", "8600123456789012") == "************9012"
    assert masked("card_number", None) is None
    assert masked("price_uzs", 120_000) == 120_000
