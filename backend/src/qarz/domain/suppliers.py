"""Pure rules of suppliers and of what a shop owes them.

A supplier's account is an append-only ledger, like a customer's, seen from the other side: the shop is
the one who owes. Each currency is a book of its own; nothing here adds so'm to dollars.

- `purchase`  goods received on credit: the shop owes more. Written by posting a purchase receipt.
- `opening`   what the shop already owed when it started keeping this book: the shop owes more.
- `payment`   money paid to the supplier: the shop owes less.
- `return`    goods sent back: the shop owes less. Written by posting a return to the supplier.
- `reversal`  cancels exactly one earlier entry, which stays as it was; the reason is its note.

A balance above zero is what the shop owes. A balance below zero is an advance: the shop paid before
the goods came, which shops do, so a payment is not limited to what is owed.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from qarz.domain.names import normalize_name

MAX_NAME = 80
MAX_NOTE = 200
MAX_REASON = 200
MIN_AMOUNT = 1
MAX_AMOUNT = 1_000_000_000_000

PURCHASE, OPENING, PAYMENT, RETURN, REVERSAL = "purchase", "opening", "payment", "return", "reversal"
KINDS = (PURCHASE, OPENING, PAYMENT, RETURN, REVERSAL)
# What a person records directly on a supplier's account; the other kinds come from documents.
DIRECT_KINDS = frozenset({OPENING, PAYMENT})
_OWES_MORE = frozenset({PURCHASE, OPENING})

ACTIVE, ARCHIVED = "active", "archived"


@dataclass(frozen=True)
class SupplierEntry:
    id: UUID
    kind: str
    amount: int
    currency: str
    reverses_id: UUID | None = None


def supplier_name(raw: str) -> str:
    """The name as stored and shown: trimmed, single spaces. Raises ValueError when unusable."""
    name = " ".join(raw.split())
    if not 1 <= len(name) <= MAX_NAME:
        raise ValueError(f"length must be between 1 and {MAX_NAME}")
    if not normalize_name(name):
        raise ValueError("must contain a letter or a digit")
    return name


def note(raw: str | None, *, limit: int = MAX_NOTE) -> str | None:
    """A note as stored: trimmed, None when empty. Raises ValueError when too long."""
    if raw is None:
        return None
    text = " ".join(raw.split())
    if len(text) > limit:
        raise ValueError(f"at most {limit} characters")
    return text or None


def reason(raw: str) -> str:
    """Why an entry or a document is cancelled: required, so the book says why it changed."""
    text = " ".join(raw.split())
    if not 1 <= len(text) <= MAX_REASON:
        raise ValueError(f"between 1 and {MAX_REASON} characters")
    return text


def amount(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_AMOUNT <= value <= MAX_AMOUNT:
        raise ValueError(f"a whole amount between {MIN_AMOUNT} and {MAX_AMOUNT} of the currency's smallest unit")
    return value


def effect(kind: str, value: int) -> int:
    """What a standing entry does to what the shop owes."""
    return value if kind in _OWES_MORE else -value


def balances(entries: Iterable[SupplierEntry]) -> dict[str, int]:
    """What the shop owes in each currency that occurs: entries that still stand, each in its own book."""
    rows = list(entries)
    reversed_ids = {row.reverses_id for row in rows if row.reverses_id is not None}
    owed: dict[str, int] = {}
    for row in rows:
        if row.kind == REVERSAL or row.id in reversed_ids:
            continue
        owed[row.currency] = owed.get(row.currency, 0) + effect(row.kind, row.amount)
    return owed


def may_cancel(kind: str, *, is_reversed: bool, from_document: bool) -> str | None:
    """Why an entry cannot be cancelled on its own, or None when it can."""
    if kind == REVERSAL:
        return "CANNOT_REVERSE_REVERSAL"
    if is_reversed:
        return "ALREADY_REVERSED"
    if from_document:
        # The receipt or the return wrote it: cancelling the document cancels it, with the goods.
        return "ENTRY_OF_DOCUMENT"
    return None
