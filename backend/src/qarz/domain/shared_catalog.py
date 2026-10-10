"""Pure rules of the shared product catalogue (the founder's seven decisions of 2026-10-10).

One catalogue for the whole platform. A shop searches it, picks an item and types its own price; what a
shop adds by hand, and a barcode it attaches to a picked item, is proposed for the catalogue and gets
there only when an administrator approves it. Nothing here knows a shop.
"""

from qarz.domain.catalog import MAX_NAME_LENGTH, item_name
from qarz.domain.files import JPEG, PNG, WEBP
from qarz.domain.languages import UZ_CYRILLIC
from qarz.domain.names import normalize_name
from qarz.domain.uz_cyrillic import to_cyrillic

# The platform setting that turns the catalogue on (off by default).
SWITCH = "catalog_on"

# The unified categories, in the order they are offered. The seed's own sections are mapped to these by
# the import; a section it does not know is "other". Their names are the clients' texts.
OTHER = "other"
CATEGORIES = (
    "drinks",
    "sweets",
    "snacks",
    "dairy",
    "cheese",
    "grocery",
    "oils",
    "canned",
    "tea",
    "meat",
    "sausage",
    "frozen",
    "bread",
    "produce",
    "ready",
    "kids",
    "beauty",
    "cleaning",
    "home",
    "pets",
    OTHER,
)

MAX_SHARED_NAME = 300
MAX_SUBCATEGORY = 120
MAX_AMOUNT = 24
MAX_QUERY = 80
# A search is "every typed word is in the names"; more words than this add nothing a person would type.
MAX_TERMS = 4
# The largest approximate price worth showing: the largest price an item may have.
MAX_PRICE_HINT = 100_000_000
# How many proposals of one shop may wait at once. Past it a shop's additions still work in the shop and
# are simply not proposed, so one shop cannot bury the administrators' queue.
MAX_PENDING = 200
# A photo of the catalogue is an image; the PDF a receipt may be is not one.
IMAGE_TYPES = frozenset({JPEG, PNG, WEBP})
IMAGE_KEY_LENGTH = 64

PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
STATUSES = (PENDING, APPROVED, REJECTED)
ITEM, BARCODE = "item", "barcode"


def shared_name(raw: str | None) -> str | None:
    """A name of the catalogue as stored: trimmed, single spaces; None for a blank one.

    Raises ValueError for a name that is too long or has no letter or digit.
    """
    name = " ".join((raw or "").split())
    if not name:
        return None
    if len(name) > MAX_SHARED_NAME:
        raise ValueError(f"at most {MAX_SHARED_NAME} characters")
    if not normalize_name(name):
        raise ValueError("must contain a letter or a digit")
    return name


def search_norm(name_ru: str | None, name_uz: str | None, amount: str | None) -> str:
    """What a search reads: both names and the package size in the matching form, so that a word typed
    in either script, in either language, finds the item."""
    return " ".join(part for part in (normalize_name(text or "") for text in (name_ru, name_uz, amount)) if part)


def search_terms(query: str | None) -> list[str]:
    """The words a search must all find, in the matching form. Raises ValueError for a query too long."""
    if query is None:
        return []
    if len(query) > MAX_QUERY:
        raise ValueError(f"at most {MAX_QUERY} characters")
    terms: list[str] = []
    for term in normalize_name(query).split():
        if term not in terms:
            terms.append(term)
    return terms[:MAX_TERMS]


def category(raw: str | None) -> str:
    """The category of a catalogue item. Raises ValueError for one the catalogue does not have."""
    if raw is None:
        return OTHER
    if raw not in CATEGORIES:
        raise ValueError("must be one of the catalogue's categories")
    return raw


def price_hint(raw: int | None) -> int | None:
    """An approximate price as stored: None when there is none (the seed writes 0 for that)."""
    if raw is None or isinstance(raw, bool) or not isinstance(raw, int) or not 0 < raw <= MAX_PRICE_HINT:
        return None
    return raw


def is_image_key(key: str) -> bool:
    return len(key) == IMAGE_KEY_LENGTH and all(ch in "0123456789abcdef" for ch in key)


def image_object_key(key: str) -> str:
    """Where a photo is kept in the file store: by its own SHA-256, so the same photo is kept once and
    what an address serves never changes."""
    if not is_image_key(key):
        raise ValueError("not the key of a catalogue photo")
    return f"catalog/{key[:2]}/{key}"


def display_name(lang: str, name_ru: str | None, name_uz: str | None) -> str:
    """The name in the reader's language: Russian for a Russian reader, Uzbek for everyone else, in
    Cyrillic for who reads Uzbek in Cyrillic. The other name stands in for a missing one."""
    if lang == "ru":
        return name_ru or name_uz or ""
    if name_uz:
        return to_cyrillic(name_uz) if lang == UZ_CYRILLIC else name_uz
    return name_ru or ""


def picked_name(name: str, amount: str | None) -> str:
    """The name a shop's own item gets when it is picked: the catalogue's name with the package size, so
    that two sizes of one product are two items, cut to what an item's name may hold.

    Raises ValueError when nothing usable is left.
    """
    name = " ".join(name.split())
    size = " ".join((amount or "").split())
    if size and normalize_name(size) not in normalize_name(name):
        room = MAX_NAME_LENGTH - len(size) - 1
        name = f"{name[:room].rstrip()} {size}" if room > 0 else name
    return item_name(name[:MAX_NAME_LENGTH].rstrip())
