import pytest

from qarz.domain.phones import normalize_phone


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("901234567", "+998901234567"),
        ("90 123 45 67", "+998901234567"),
        ("(90) 123-45-67", "+998901234567"),
        ("998901234567", "+998901234567"),
        ("+998901234567", "+998901234567"),
        (" +998 90 123 45 67 ", "+998901234567"),
        ("+79123456789", "+79123456789"),
        ("+12025550123", "+12025550123"),
        ("+12345678", "+12345678"),  # the shortest number allowed
        ("+123456789012345", "+123456789012345"),  # the longest number allowed
    ],
)
def test_numbers_are_stored_in_international_format(typed: str, stored: str) -> None:
    assert normalize_phone(typed) == stored


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "   ",
        "+",
        "12345",  # too short to be anything
        "90123456",  # eight digits: not an Uzbek national number
        "9012345678",  # ten digits without a country code
        "79123456789",  # a foreign number without the plus sign cannot be told from a mistyped one
        "+1234567",  # below the shortest length
        "+1234567890123456",  # above the longest length
        "+0123456789",  # no country code starts with zero
        "++998901234567",
        "+998 90 123 45 6a",
        "90.123.45.67",
        "٩٠١٢٣٤٥٦٧",  # digits of another script
        "tel:901234567",
    ],
)
def test_what_cannot_be_a_phone_number_is_refused(typed: str) -> None:
    assert normalize_phone(typed) is None
