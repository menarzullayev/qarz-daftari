"""Chat fast-entry parser: one message such as "Ali 45000" becomes a credit sale or a payment.

Grammar (docs/08-technical-spec, "Chat contract"; requirements REQ-006, REQ-009, REQ-N01, REQ-N03, REQ-N06):

    entry    := name SP amount [SP note]
    payment  := name SP "-" amount [SP note]  |  name SP amount SP payment-word [SP note]
    amount   := digits with optional space or dot separators, optional "k" / "ming" / "тыс" (x1000)

The parser is a pure, total function: it never raises, it never rounds, and when a message can be read
in two ways it answers AMBIGUOUS instead of guessing, because a wrong guess is a wrong debt.

It works on whitespace-separated tokens with single left-to-right passes, so the cost is linear in the
length of the message; no regular expressions are used.

How the amount is found: it starts at the first token that begins with an ASCII digit (or with a minus
sign directly followed by one). Everything before it is the name, everything after the amount and its
optional thousand word, currency word and payment word is the note.
"""

import unicodedata
from dataclasses import dataclass
from enum import Enum, StrEnum

from qarz.domain.names import APOSTROPHES, normalize_name, unify_apostrophes

MAX_INPUT_LENGTH = 500
MIN_AMOUNT = 100
MAX_AMOUNT = 100_000_000
MAX_NAME_LENGTH = 80
MAX_NOTE_LENGTH = 120  # the chat grammar is stricter than the 200 the schema allows


class EntryKind(StrEnum):
    CREDIT = "credit"
    PAYMENT = "payment"


class ParseErrorCode(Enum):
    """Stable codes; the interface layer turns them into Uzbek and Russian text."""

    TOO_LONG = "too_long"
    EMPTY = "empty"
    NOT_AN_ENTRY = "not_an_entry"  # a command, or text without any digit
    INVALID_CHARACTERS = "invalid_characters"  # control or invisible formatting characters
    AMBIGUOUS = "ambiguous"
    NO_AMOUNT = "no_amount"
    NO_NAME = "no_name"
    NAME_INVALID = "name_invalid"  # a name starts with a letter and holds letters, digits, ' - . only
    NAME_TOO_LONG = "name_too_long"
    AMOUNT_NOT_WHOLE = "amount_not_whole"
    AMOUNT_TOO_SMALL = "amount_too_small"
    AMOUNT_TOO_LARGE = "amount_too_large"
    NOTE_TOO_LONG = "note_too_long"


@dataclass(frozen=True, slots=True)
class ParsedEntry:
    kind: EntryKind
    name: str  # as typed: trimmed, inner whitespace collapsed
    normalized_name: str  # for matching only, see qarz.domain.names
    amount: int  # whole UZS, always positive; the kind carries the direction
    note: str | None


@dataclass(frozen=True, slots=True)
class ParseError:
    code: ParseErrorCode


_DIGITS = frozenset("0123456789")
_NUMERIC = _DIGITS | {".", ","}
_MINUS = frozenset("-\u2212\u2013")  # hyphen-minus, minus sign, en dash
_LINE_BREAKS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
_NAME_PUNCTUATION = APOSTROPHES | {"-", "."}

_THOUSAND_WORDS = frozenset({"k", "ming", "тыс", "к", "минг"})
_CURRENCY_WORDS = frozenset({"so'm", "som", "sum", "uzs", "сум", "сўм", "сом"})
_PAYMENT_WORDS = frozenset({"berdi", "to'ladi", "toladi", "оплатил", "оплатила", "отдал", "отдала", "берди", "тўлади"})


def _word(token: str) -> str:
    """A token as a keyword candidate: one trailing "." or "," dropped, apostrophes unified, lower case."""
    if token[-1:] in {".", ","}:
        token = token[:-1]
    return unify_apostrophes(token).lower()


def _starts_amount(token: str) -> bool:
    return token[0] in _DIGITS or (len(token) > 1 and token[0] in _MINUS and token[1] in _DIGITS)


def _split_number(token: str) -> tuple[str, str, bool]:
    """Split a token into its numeric head, the word glued to it, and whether punctuation closed it."""
    end = 0
    while end < len(token) and token[end] in _NUMERIC:
        end += 1
    head, tail = token[:end], _word(token[end:])
    closed = False
    if end == len(token) and len(head) > 1 and head[-1] in {".", ","}:
        head, closed = head[:-1], True
    return head, tail, closed


def _is_hidden(ch: str) -> bool:
    return unicodedata.category(ch) in _HIDDEN_CATEGORIES and not ch.isspace()


def _name_error(tokens: list[str]) -> ParseErrorCode | None:
    if tokens[-1] in _MINUS:
        return ParseErrorCode.AMBIGUOUS  # "Ali - 20000": a separator dash or a payment?
    if not tokens[0][0].isalpha():
        return ParseErrorCode.NAME_INVALID
    for token in tokens:
        for ch in token:
            if not (ch.isalpha() or ch in _DIGITS or ch in _NAME_PUNCTUATION or unicodedata.category(ch)[0] == "M"):
                return ParseErrorCode.NAME_INVALID
    if sum(len(token) for token in tokens) + len(tokens) - 1 > MAX_NAME_LENGTH:
        return ParseErrorCode.NAME_TOO_LONG
    if len(tokens) > 1 and _word(tokens[-1]) in _PAYMENT_WORDS:
        return ParseErrorCode.AMBIGUOUS  # "Ali berdi 20000": a payment, or a customer called "Ali berdi"?
    return None


def _read_amount(tokens: list[str], start: int) -> tuple[EntryKind, int, int] | ParseErrorCode:
    """Read the amount that begins at tokens[start]; return the kind, whole UZS and the note's index."""
    first = tokens[start]
    kind = EntryKind.CREDIT
    if first[0] in _MINUS:
        kind, first = EntryKind.PAYMENT, first[1:]
    malformed = (
        ParseErrorCode.AMBIGUOUS
        if any(_starts_amount(token) for token in tokens[start + 1 :])
        else ParseErrorCode.NO_AMOUNT
    )

    head, tail, closed = _split_number(first)
    if "," in head:
        return ParseErrorCode.AMOUNT_NOT_WHOLE
    groups = head.split(".")
    if not all(groups):
        return malformed
    if len(groups) > 1 and (len(groups[0]) > 3 or groups[0][0] == "0" or any(len(g) != 3 for g in groups[1:])):
        return ParseErrorCode.AMOUNT_NOT_WHOLE  # "45.5", "45.00", "0.500": a decimal, not thousand groups
    index = start + 1

    # Thousand groups separated by spaces: "45 000", "1 250 000", also mixed as "1 250.000".
    open_group = len(groups[0]) <= 3 and groups[0][0] != "0"
    while open_group and not tail and not closed and index < len(tokens):
        next_head, next_tail, next_closed = _split_number(tokens[index])
        more = next_head.split(".")
        if "," in next_head or any(len(g) != 3 for g in more):
            break
        groups += more
        tail, closed = next_tail, next_closed
        index += 1

    multiplier = 1
    delimited = closed
    currency_seen = False
    if tail:
        if tail in _THOUSAND_WORDS:
            multiplier = 1000
        elif tail in _CURRENCY_WORDS:
            currency_seen = True
        else:
            return malformed  # "45000abc", "5-uy"
        delimited = True
    elif index < len(tokens) and _word(tokens[index]) in _THOUSAND_WORDS:
        multiplier = 1000
        delimited = True
        index += 1
    if not currency_seen and index < len(tokens) and _word(tokens[index]) in _CURRENCY_WORDS:
        delimited = True
        index += 1
    if index < len(tokens) and _word(tokens[index]) in _PAYMENT_WORDS:
        kind = EntryKind.PAYMENT
        delimited = True
        index += 1

    if not delimited and index < len(tokens) and _starts_amount(tokens[index]):
        return ParseErrorCode.AMBIGUOUS  # "Ali 2 45000", "Ali 45000 500 gramm": which number is the money?
    if kind is EntryKind.CREDIT and any(_word(token) in _PAYMENT_WORDS for token in tokens[index:]):
        return ParseErrorCode.AMBIGUOUS  # "Ali 20000 kecha berdi": must not be booked as a new debt

    amount = int("".join(groups)) * multiplier
    if amount < MIN_AMOUNT:
        return ParseErrorCode.AMOUNT_TOO_SMALL
    if amount > MAX_AMOUNT:
        return ParseErrorCode.AMOUNT_TOO_LARGE
    return kind, amount, index


def _parse(text: str) -> ParsedEntry | ParseErrorCode:
    if len(text) > MAX_INPUT_LENGTH:
        return ParseErrorCode.TOO_LONG
    stripped = text.strip()
    if not stripped:
        return ParseErrorCode.EMPTY
    if stripped[0] == "/" or not any(ch in _DIGITS for ch in stripped):
        return ParseErrorCode.NOT_AN_ENTRY
    if any(_is_hidden(ch) for ch in stripped):
        return ParseErrorCode.INVALID_CHARACTERS
    if any(ch in _LINE_BREAKS for ch in stripped):
        return ParseErrorCode.AMBIGUOUS  # one entry with a note, or several entries?

    tokens = stripped.split()
    start = next((i for i, token in enumerate(tokens) if _starts_amount(token)), None)
    if start is None:
        return ParseErrorCode.NO_AMOUNT  # digits only inside words, as in "Ali2"
    if start == 0:
        return ParseErrorCode.NO_NAME
    name_error = _name_error(tokens[:start])
    if name_error is not None:
        return name_error

    amount = _read_amount(tokens, start)
    if isinstance(amount, ParseErrorCode):
        return amount
    kind, value, note_index = amount
    note = " ".join(tokens[note_index:])
    if len(note) > MAX_NOTE_LENGTH:
        return ParseErrorCode.NOTE_TOO_LONG
    name = " ".join(tokens[:start])
    return ParsedEntry(kind=kind, name=name, normalized_name=normalize_name(name), amount=value, note=note or None)


def parse_entry(text: str) -> ParsedEntry | ParseError:
    """Parse one chat message into a credit sale or a payment, or say with a code why it is not one."""
    result = _parse(text)
    return ParseError(result) if isinstance(result, ParseErrorCode) else result
