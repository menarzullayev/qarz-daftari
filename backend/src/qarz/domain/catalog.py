"""Pure rules for catalog items (DOM-013; REQ-039, REQ-040).

A catalog item is a name, a unit and a current price. Nothing here counts stock.
"""

import unicodedata

from qarz.domain.names import normalize_name, unify_apostrophes

MAX_NAME_LENGTH = 80
DEFAULT_UNIT = "dona"
MAX_UNIT_LENGTH = 12
MIN_PRICE = 1
# The largest single credit sale the ledger accepts; one unit of a good cannot cost more than that.
MAX_PRICE = 100_000_000

# Spellings of the usual grocery units in Uzbek Latin, Uzbek Cyrillic and Russian, folded to one form so
# that "кг", "KG" and "kilo" do not become three different units on a receipt.
_UNIT_ALIASES = {
    "dona": "dona",
    "ta": "dona",
    "дона": "dona",
    "та": "dona",
    "шт": "dona",
    "штука": "dona",
    "kg": "kg",
    "kilo": "kg",
    "кг": "kg",
    "кило": "kg",
    "g": "g",
    "gr": "g",
    "gramm": "g",
    "г": "g",
    "гр": "g",
    "грамм": "g",
    "l": "l",
    "litr": "l",
    "л": "l",
    "литр": "l",
    "ml": "ml",
    "мл": "ml",
    "m": "m",
    "metr": "m",
    "м": "m",
    "метр": "m",
    "quti": "quti",
    "қути": "quti",
    "кути": "quti",
    "коробка": "quti",
    "paket": "paket",
    "пакет": "paket",
}
_UNIT_PUNCTUATION = frozenset("' .")


def item_name(raw: str) -> str:
    """The name as it is stored and shown: trimmed, with single spaces. Raises ValueError when unusable."""
    name = " ".join(raw.split())
    if not 1 <= len(name) <= MAX_NAME_LENGTH:
        raise ValueError(f"length must be between 1 and {MAX_NAME_LENGTH}")
    if not normalize_name(name):
        # For example a lone soft sign: it would match every other name that normalizes to nothing.
        raise ValueError("must contain a letter or a digit")
    return name


def normalize_unit(raw: str | None) -> str:
    """The stored form of a unit. A missing or blank unit is a piece ("dona").

    Known spellings fold to one canonical unit. Any other short unit a shop uses ("bog'", "blok", "qop")
    is kept, lower-cased, so the catalog does not dictate how a shop sells.
    """
    if raw is None or not raw.strip():
        return DEFAULT_UNIT
    text = " ".join(unify_apostrophes(unicodedata.normalize("NFC", raw).lower()).split())
    known = _UNIT_ALIASES.get(text.rstrip("."))
    if known is not None:
        return known
    if len(text) > MAX_UNIT_LENGTH:
        raise ValueError(f"at most {MAX_UNIT_LENGTH} characters")
    if not all(ch.isalnum() or ch in _UNIT_PUNCTUATION for ch in text):
        raise ValueError("letters, digits, apostrophe and full stop only")
    if not any(ch.isalpha() for ch in text):
        raise ValueError("must contain a letter")
    return text


def check_price(price: int) -> int:
    """A catalog price is a whole number of UZS within the bounds. Raises ValueError otherwise."""
    if isinstance(price, bool) or not isinstance(price, int):
        raise ValueError("must be a whole number of UZS")
    if not MIN_PRICE <= price <= MAX_PRICE:
        raise ValueError(f"a whole amount between {MIN_PRICE} and {MAX_PRICE} UZS")
    return price
