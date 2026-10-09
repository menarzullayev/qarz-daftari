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

Dollars. Only for a shop that works in US dollars (`dollars=True`; without it this module reads exactly
what it read before dollars existed, and "$" is not a currency):

    amount   := ["$"] number [thousand-word] [dollar-word]      with "$" or a dollar-word present
    number   := digits, thousands grouped by spaces, with at most two decimals after "." or ","

"Ali 50$", "Ali $50", "Ali 50 usd", "Ali 50.5$", "Ali 1 250,50 dollar" are dollars, held in whole cents.
An amount without a dollar mark is so'm, as it always was: "Ali 45000". The mark decides, never the size
of the number. What could be read two ways is AMBIGUOUS: "1.250$" (1 250 dollars, or one and a
quarter?), "50$ so'm".
"""

import unicodedata
from dataclasses import dataclass
from enum import Enum, StrEnum

from qarz.domain.money import RULES, Currency
from qarz.domain.names import APOSTROPHES, normalize_name, unify_apostrophes

MAX_INPUT_LENGTH = 500
# The range of one so'm entry, under the names it had before dollars existed (qarz.domain.money).
MIN_AMOUNT = RULES[Currency.UZS].min_entry
MAX_AMOUNT = RULES[Currency.UZS].max_entry
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
    AMOUNT_TOO_PRECISE = "amount_too_precise"  # dollars with more than two decimals
    AMOUNT_TOO_SMALL = "amount_too_small"
    AMOUNT_TOO_LARGE = "amount_too_large"
    NOTE_TOO_LONG = "note_too_long"


@dataclass(frozen=True, slots=True)
class ParsedEntry:
    kind: EntryKind
    name: str  # as typed: trimmed, inner whitespace collapsed
    normalized_name: str  # for matching only, see qarz.domain.names
    amount: int  # whole so'm, or whole cents when `currency` is dollars; always positive
    note: str | None
    currency: Currency = Currency.UZS


@dataclass(frozen=True, slots=True)
class ParseError:
    code: ParseErrorCode
    # Which currency's amount was being read when it failed: an amount out of range is told that range.
    currency: Currency = Currency.UZS


_DIGITS = frozenset("0123456789")
_NUMERIC = _DIGITS | {".", ","}
_MINUS = frozenset("-\u2212\u2013")  # hyphen-minus, minus sign, en dash
_LINE_BREAKS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
_NAME_PUNCTUATION = APOSTROPHES | {"-", "."}

_THOUSAND_WORDS = frozenset({"k", "ming", "тыс", "к", "минг"})
_CURRENCY_WORDS = frozenset({"so'm", "som", "sum", "uzs", "сум", "сўм", "сом"})
_DOLLAR_WORDS = frozenset({"$", "usd", "dollar", "dollars", "dol", "доллар", "доллара", "долларов", "долл"})
_SEPARATORS = frozenset({".", ","})
_PAYMENT_WORDS = frozenset({"berdi", "to'ladi", "toladi", "оплатил", "оплатила", "отдал", "отдала", "берди", "тўлади"})


def _word(token: str) -> str:
    """A token as a keyword candidate: one trailing "." or "," dropped, apostrophes unified, lower case."""
    if token[-1:] in {".", ","}:
        token = token[:-1]
    return unify_apostrophes(token).lower()


def _starts_amount(token: str, dollars: bool = False) -> bool:
    if token[0] in _DIGITS or (len(token) > 1 and token[0] in _MINUS and token[1] in _DIGITS):
        return True
    if not dollars:
        return False
    # "$50" and "-$50", for a shop that works in dollars.
    bare = token[1:] if token[0] in _MINUS else token
    return len(bare) > 1 and bare[0] == "$" and bare[1] in _DIGITS


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


def _cents(number: str, spaced: bool) -> int | ParseErrorCode:
    """A dollar number as whole cents. `number` is digits with "." and "," as typed, spaces already removed.

    One separator followed by one or two digits is the decimal mark. Separators between groups of three
    digits are thousands. Exactly three digits after a single separator could be either, and so could
    anything else that fits neither reading: those are ambiguous and never guessed.
    """
    pieces: list[str] = [""]
    marks: list[str] = []
    for ch in number:
        if ch in _SEPARATORS:
            marks.append(ch)
            pieces.append("")
        else:
            pieces[-1] += ch
    if not all(pieces):
        return ParseErrorCode.NO_AMOUNT
    if not marks:
        return int(pieces[0]) * 100
    whole, last = pieces[:-1], pieces[-1]
    grouped = len(whole[0]) <= 3 and whole[0][0] != "0" and all(len(piece) == 3 for piece in whole[1:])
    if len(marks) == 1:
        if len(last) > 3:
            return ParseErrorCode.AMOUNT_TOO_PRECISE
        if len(last) == 3:
            return ParseErrorCode.AMBIGUOUS  # "1.250": thousands, or a third decimal?
        return int(whole[0]) * 100 + int(last.ljust(2, "0"))
    if spaced:
        return ParseErrorCode.AMBIGUOUS  # "1 250.500,5": nobody writes an amount so
    if len(set(marks)) == 1:
        # "1.250.000": the same mark throughout can only be thousands.
        return int("".join(pieces)) * 100 if grouped and len(last) == 3 else ParseErrorCode.AMBIGUOUS
    if len(set(marks[:-1])) == 1 and marks[-1] != marks[0] and grouped and len(last) in (1, 2):
        # "1,250.50" and "1.250,50": thousands, then a decimal mark of the other kind.
        return int("".join(whole)) * 100 + int(last.ljust(2, "0"))
    return ParseErrorCode.AMBIGUOUS


def _read_dollars(tokens: list[str], start: int) -> tuple[EntryKind, int, int] | ParseErrorCode | None:
    """Read a dollar amount that begins at tokens[start]: the kind, whole cents and the note's index.

    None when the amount carries no dollar mark: it is then not dollars, and is read as so'm.
    """
    first = tokens[start]
    kind = EntryKind.CREDIT
    if first[0] in _MINUS:
        kind, first = EntryKind.PAYMENT, first[1:]
    marked = first[:1] == "$"
    if marked:
        first = first[1:]
    head, tail, closed = _split_number(first)
    if not head:
        return None
    index = start + 1
    number = head
    spaced = False
    # Thousands separated by spaces, as in the so'm grammar: "1 250$", "1 250.50 usd".
    open_group = len(head) <= 3 and head[0] != "0"
    while open_group and not tail and not closed and index < len(tokens):
        next_head, next_tail, next_closed = _split_number(tokens[index])
        digits = 0
        while digits < len(next_head) and next_head[digits] in _DIGITS:
            digits += 1
        if digits != 3:
            break
        number += next_head
        spaced = True
        tail, closed = next_tail, next_closed
        index += 1
        if digits != len(next_head):
            break  # the group carried the decimals: the number ends here

    multiplier = 1
    if tail in _THOUSAND_WORDS:
        multiplier, tail = 1000, ""
    elif not tail and index < len(tokens) and _word(tokens[index]) in _THOUSAND_WORDS:
        multiplier = 1000
        index += 1
    if tail in _DOLLAR_WORDS:
        marked, tail = True, ""
    elif not tail and index < len(tokens) and _word(tokens[index]) in _DOLLAR_WORDS:
        marked = True
        index += 1
    if not marked:
        return None
    if tail:
        # "$50abc": marked as dollars, and then something that is not an amount. "50$ so'm" is below.
        return ParseErrorCode.AMBIGUOUS if tail in _CURRENCY_WORDS else ParseErrorCode.NO_AMOUNT
    if index < len(tokens) and _word(tokens[index]) in _CURRENCY_WORDS | _DOLLAR_WORDS:
        return ParseErrorCode.AMBIGUOUS  # "50$ so'm", "50 usd $": which is it?
    if index < len(tokens) and _word(tokens[index]) in _PAYMENT_WORDS:
        kind = EntryKind.PAYMENT
        index += 1
    if kind is EntryKind.CREDIT and any(_word(token) in _PAYMENT_WORDS for token in tokens[index:]):
        return ParseErrorCode.AMBIGUOUS  # as in so'm: must not be booked as a new debt

    cents = _cents(number, spaced)
    if isinstance(cents, ParseErrorCode):
        return cents
    amount = cents * multiplier
    limits = RULES[Currency.USD]
    if amount < limits.min_entry:
        return ParseErrorCode.AMOUNT_TOO_SMALL
    if amount > limits.max_entry:
        return ParseErrorCode.AMOUNT_TOO_LARGE
    return kind, amount, index


def _parse(text: str, dollars: bool = False) -> ParsedEntry | ParseError | ParseErrorCode:
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
    start = next((i for i, token in enumerate(tokens) if _starts_amount(token, dollars)), None)
    if start is None:
        return ParseErrorCode.NO_AMOUNT  # digits only inside words, as in "Ali2"
    if start == 0:
        return ParseErrorCode.NO_NAME
    name_error = _name_error(tokens[:start])
    if name_error is not None:
        return name_error

    currency = Currency.UZS
    amount = _read_dollars(tokens, start) if dollars else None
    if amount is None:
        amount = _read_amount(tokens, start)
    else:
        currency = Currency.USD
    if isinstance(amount, ParseErrorCode):
        return ParseError(amount, currency)
    kind, value, note_index = amount
    note = " ".join(tokens[note_index:])
    if len(note) > MAX_NOTE_LENGTH:
        return ParseErrorCode.NOTE_TOO_LONG
    name = " ".join(tokens[:start])
    return ParsedEntry(
        kind=kind,
        name=name,
        normalized_name=normalize_name(name),
        amount=value,
        note=note or None,
        currency=currency,
    )


def parse_amount(text: str) -> int | ParseError:
    """Read a message that is an amount and nothing else, in the same grammar as an entry's amount.

    "50000", "50 000", "50.000", "50k", "50 ming so'm" are amounts. A name, a minus sign, a payment word
    or a note make the message something else, and it is refused rather than guessed at.
    """
    if len(text) > MAX_INPUT_LENGTH:
        return ParseError(ParseErrorCode.TOO_LONG)
    stripped = text.strip()
    if not stripped:
        return ParseError(ParseErrorCode.EMPTY)
    if any(_is_hidden(ch) for ch in stripped) or any(ch in _LINE_BREAKS for ch in stripped):
        return ParseError(ParseErrorCode.INVALID_CHARACTERS)
    tokens = stripped.split()
    if tokens[0][0] not in _DIGITS:
        return ParseError(ParseErrorCode.NO_AMOUNT)
    amount = _read_amount(tokens, 0)
    if isinstance(amount, ParseErrorCode):
        return ParseError(amount)
    kind, value, rest = amount
    if kind is not EntryKind.CREDIT or rest != len(tokens):
        return ParseError(ParseErrorCode.AMBIGUOUS)
    return value


def parse_money(text: str, *, dollars: bool = False) -> tuple[Currency, int] | ParseError:
    """Read a message that is an amount and nothing else, in so'm or, with `dollars`, in dollars.

    As `parse_amount`, which it is without `dollars`. With them, "50$", "$50", "50.5 usd" are dollars in
    whole cents, and an amount without a dollar mark is so'm.
    """
    stripped = text.strip()
    if dollars and stripped and len(text) <= MAX_INPUT_LENGTH:
        if any(_is_hidden(ch) for ch in stripped) or any(ch in _LINE_BREAKS for ch in stripped):
            return ParseError(ParseErrorCode.INVALID_CHARACTERS)
        tokens = stripped.split()
        if tokens[0][0] in _DIGITS or (_starts_amount(tokens[0], dollars=True) and tokens[0][0] == "$"):
            read = _read_dollars(tokens, 0)
            if isinstance(read, ParseErrorCode):
                return ParseError(read, Currency.USD)
            if read is not None:
                kind, cents, rest = read
                if kind is not EntryKind.CREDIT or rest != len(tokens):
                    return ParseError(ParseErrorCode.AMBIGUOUS, Currency.USD)
                return Currency.USD, cents
    amount = parse_amount(text)
    return amount if isinstance(amount, ParseError) else (Currency.UZS, amount)


def parse_entry(text: str, *, dollars: bool = False) -> ParsedEntry | ParseError:
    """Parse one chat message into a credit sale or a payment, or say with a code why it is not one.

    `dollars` is whether the shop the message is for works in US dollars. Without it a message is read
    exactly as before dollars existed.
    """
    result = _parse(text, dollars)
    return ParseError(result) if isinstance(result, ParseErrorCode) else result
