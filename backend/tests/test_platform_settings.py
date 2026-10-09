"""Platform settings: each value's type and range, the defaults, and which changes need a code (ADR-018)."""

from typing import Any

import pytest

from qarz.domain.platform_settings import (
    MAX_CARD_LABEL,
    MAX_CARDS,
    SETTINGS,
    InvalidSetting,
    card_tag,
    effective,
    find_card,
    free_plan_customers,
    masked,
    needs_code,
    payment_cards,
    validate,
)

HUMO = {"number": "8600123456789012", "label": "Humo · Anorbank"}
UZCARD = {"number": "5614681234567890", "label": "Uzcard · Kapitalbank"}


def test_the_settings_are_the_ones_the_administrator_controls() -> None:
    assert set(SETTINGS) == {
        "trial_on",
        "trial_days",
        "price_uzs",
        "payment_cards",
        "review_group",
        "sms_on",
        "sms_monthly_quota",
        "online_pay_on",
        "free_plan_on",
        "free_plan_customers",
        "customer_links_on",
    }
    assert "card_number" not in SETTINGS, "the single card became the list"


def test_defaults_when_nothing_is_stored() -> None:
    assert {key: effective(key, None) for key in SETTINGS} == {
        "trial_on": True,  # REQ-052
        "trial_days": 30,
        "price_uzs": 100_000,  # REQ-053
        "payment_cards": [],
        "review_group": None,
        "sms_on": False,
        "sms_monthly_quota": 0,
        "online_pay_on": False,
        "free_plan_on": False,
        "free_plan_customers": 30,
        "customer_links_on": False,
    }


def test_the_two_integration_switches_are_off_by_default() -> None:
    assert SETTINGS["sms_on"].default is False
    assert SETTINGS["online_pay_on"].default is False


def test_the_free_plan_is_off_until_it_is_switched_on_and_holds_thirty_customers() -> None:
    assert SETTINGS["free_plan_on"].default is False
    assert needs_code("free_plan_on")
    assert validate("free_plan_customers", 1) == 1
    assert validate("free_plan_customers", 10_000) == 10_000
    for wrong in (0, 10_001, -1, True, "30", 30.0, None):
        with pytest.raises(InvalidSetting):
            validate("free_plan_customers", wrong)


def test_how_many_customers_the_free_plan_holds_is_nothing_while_it_is_off() -> None:
    assert free_plan_customers(True, None) == 30
    assert free_plan_customers(True, 50) == 50
    assert free_plan_customers(True, 0) == 30, "a stored value outside the range does not apply"
    # Only a stored true is on: nothing stored, false, or anything else leaves the plan off.
    for off in (None, False, 1, "true"):
        assert free_plan_customers(off, 50) is None


def test_price_card_and_switches_need_a_code() -> None:
    """Specification: "changes to price, card number, and switches ask for the code again"."""
    assert {key for key in SETTINGS if needs_code(key)} == {
        "price_uzs",
        "payment_cards",
        "trial_on",
        "sms_on",
        "online_pay_on",
        "customer_links_on",
        "review_group",
        "free_plan_on",
    }
    assert not needs_code("trial_days")
    assert not needs_code("sms_monthly_quota")
    assert not needs_code("free_plan_customers")


@pytest.mark.parametrize("key", ["trial_on", "sms_on", "online_pay_on", "customer_links_on"])
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


def _card(number: Any = "8600123456789012", label: Any = "Humo") -> dict[str, Any]:
    return {"number": number, "label": label}


def test_the_cards_are_stored_in_order_without_spaces_and_with_trimmed_labels() -> None:
    given = [
        {"number": "8600 1234 5678 9012", "label": "  Humo · Anorbank "},
        {"label": "Uzcard · Kapitalbank", "number": "5614681234567890"},
    ]
    assert validate("payment_cards", given) == [HUMO, UZCARD], "the first stays the first: it is the primary"
    assert validate("payment_cards", [UZCARD, HUMO]) == [UZCARD, HUMO]


def test_null_and_an_empty_list_both_clear_the_cards() -> None:
    assert validate("payment_cards", None) == []
    assert validate("payment_cards", []) == []


@pytest.mark.parametrize(
    "number",
    [
        "860012345678901",  # fifteen digits
        "86001234567890123",  # seventeen
        "8600-1234-5678-9012",
        "86001234567890ab",
        "",
        "٨٦٠٠١٢٣٤٥٦٧٨٩٠١٢",  # digits, but not the ones a card carries
        8600123456789012,
        None,
        True,
    ],
)
def test_a_card_number_is_sixteen_ascii_digits(number: Any) -> None:
    with pytest.raises(InvalidSetting, match="card 2: the number must be 16 digits"):
        validate("payment_cards", [HUMO, _card(number=number)])


@pytest.mark.parametrize("label", ["", "   ", "x" * (MAX_CARD_LABEL + 1), None, 5, ["Humo"]])
def test_a_card_label_is_one_to_forty_characters(label: Any) -> None:
    with pytest.raises(InvalidSetting, match="card 1: the label"):
        validate("payment_cards", [_card(label=label)])


def test_a_label_of_exactly_forty_characters_is_kept() -> None:
    longest = "x" * MAX_CARD_LABEL
    assert validate("payment_cards", [_card(label=f" {longest} ")]) == [_card(label=longest)]


def test_the_same_number_twice_is_refused_however_it_is_spaced() -> None:
    with pytest.raises(InvalidSetting, match="card 2: the same number is in the list twice"):
        validate("payment_cards", [HUMO, {"number": "8600 1234 5678 9012", "label": "Boshqa nom"}])
    # The same label on two different cards is the administrator's business.
    same_name = [HUMO, {**UZCARD, "label": HUMO["label"]}]
    assert validate("payment_cards", same_name) == same_name


def test_at_most_ten_cards() -> None:
    def cards(count: int) -> list[dict[str, Any]]:
        return [_card(number=f"86001234567890{place:02d}", label=f"Karta {place}") for place in range(count)]

    assert MAX_CARDS == 10
    assert validate("payment_cards", cards(10)) == cards(10)
    with pytest.raises(InvalidSetting, match="at most 10 cards"):
        validate("payment_cards", cards(11))


@pytest.mark.parametrize(
    "wrong",
    [
        "8600123456789012",  # the single number the setting used to be
        {"number": "8600123456789012", "label": "Humo"},  # one card, not a list
        ["8600123456789012"],
        [{"number": "8600123456789012"}],
        [{"label": "Humo"}],
        [{"number": "8600123456789012", "label": "Humo", "primary": True}],
        [None],
        8600,
        True,
    ],
)
def test_the_cards_are_a_list_of_number_and_label_and_nothing_else(wrong: Any) -> None:
    with pytest.raises(InvalidSetting):
        validate("payment_cards", wrong)


def test_a_receipt_names_a_card_by_its_label_and_last_four_digits() -> None:
    assert card_tag(HUMO) == "Humo · Anorbank ··9012"
    assert HUMO["number"] not in card_tag(HUMO)
    assert len(card_tag(_card(label="x" * MAX_CARD_LABEL))) <= 60, "what the receipt's column holds"


def test_a_card_is_found_by_its_number_with_or_without_spaces() -> None:
    assert find_card([HUMO, UZCARD], "5614 6812 3456 7890") == UZCARD
    assert find_card([HUMO, UZCARD], "8600123456789012") == HUMO
    for unknown in ("8600123456789013", "9012", "", "Humo · Anorbank", None, 8600123456789012):
        assert find_card([HUMO, UZCARD], unknown) is None
    assert find_card([], HUMO["number"]) is None


def test_stored_cards_apply_only_if_the_whole_list_is_valid() -> None:
    assert payment_cards([HUMO, UZCARD]) == [HUMO, UZCARD]
    assert payment_cards(None) == []
    assert payment_cards("8600123456789012") == []
    assert payment_cards([HUMO, {"number": "86", "label": "x"}]) == [], "a damaged list offers no card at all"


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
        ("payment_cards", [HUMO], [HUMO]),
        ("payment_cards", "8600123456789012", []),
        ("payment_cards", [HUMO, HUMO], []),
    ],
)
def test_a_stored_value_applies_only_if_it_is_valid(key: str, stored: Any, expected: Any) -> None:
    assert effective(key, stored) == expected


def test_the_audit_never_holds_a_whole_card_number() -> None:
    shown = masked("payment_cards", [HUMO, UZCARD])
    assert shown == [
        {"number": "************9012", "label": "Humo · Anorbank"},
        {"number": "************7890", "label": "Uzcard · Kapitalbank"},
    ]
    assert HUMO["number"] not in str(shown) and UZCARD["number"] not in str(shown)
    assert masked("payment_cards", []) == []
    assert masked("price_uzs", 120_000) == 120_000
