"""Phone numbers as stored for customers: international format, digits only after the plus sign."""

_UZ_COUNTRY = "998"
_UZ_NATIONAL_DIGITS = 9
_SEPARATORS = str.maketrans("", "", "  -()")


def normalize_phone(raw: str) -> str | None:
    """Return the number as +<country><number>, or None when it cannot be a phone number.

    A nine-digit number is taken to be an Uzbek number without its country code. Anything else must
    carry a country code and have between 8 and 15 digits (ITU-T E.164).
    """
    text = raw.strip().translate(_SEPARATORS)
    has_plus = text.startswith("+")
    digits = text[1:] if has_plus else text
    if not digits.isascii() or not digits.isdigit():
        return None
    if not has_plus and len(digits) == _UZ_NATIONAL_DIGITS:
        return f"+{_UZ_COUNTRY}{digits}"
    if not has_plus and len(digits) == len(_UZ_COUNTRY) + _UZ_NATIONAL_DIGITS and digits.startswith(_UZ_COUNTRY):
        return f"+{digits}"
    if has_plus and 8 <= len(digits) <= 15 and not digits.startswith("0"):
        return f"+{digits}"
    return None
