import random
import time
from dataclasses import FrozenInstanceError

import pytest

from qarz.domain.chat_entry import (
    MAX_AMOUNT,
    MAX_INPUT_LENGTH,
    MIN_AMOUNT,
    EntryKind,
    ParsedEntry,
    ParseError,
    ParseErrorCode,
    parse_entry,
)
from qarz.domain.names import normalize_name

E = ParseErrorCode
CREDIT = EntryKind.CREDIT
PAYMENT = EntryKind.PAYMENT


def _entry(text: str) -> ParsedEntry:
    result = parse_entry(text)
    assert isinstance(result, ParsedEntry), f"{text!r} gave {result}"
    return result


def _error(text: str) -> ParseErrorCode:
    result = parse_entry(text)
    assert isinstance(result, ParseError), f"{text!r} gave {result}"
    return result.code


@pytest.mark.parametrize(
    ("text", "kind", "name", "amount", "note"),
    [
        # The plain form, whitespace tolerated
        ("Ali 45000", CREDIT, "Ali", 45_000, None),
        ("  Ali   45000  ", CREDIT, "Ali", 45_000, None),
        ("\tAli\t45000 non   va  sut ", CREDIT, "Ali", 45_000, "non va sut"),
        ("Ali Valiyev 45000 non", CREDIT, "Ali Valiyev", 45_000, "non"),
        ("Ali   Valiyev   aka 45000", CREDIT, "Ali Valiyev aka", 45_000, None),
        # Names: apostrophes, hyphens, dots, Cyrillic, digits inside a word
        ("O'g'il 45000", CREDIT, "O'g'il", 45_000, None),
        ("G'ani 45000", CREDIT, "G'ani", 45_000, None),
        ("G\u02bbani aka 45000", CREDIT, "G\u02bbani aka", 45_000, None),
        ("Abdu-Karim 45000", CREDIT, "Abdu-Karim", 45_000, None),
        ("A. Karimov 45000", CREDIT, "A. Karimov", 45_000, None),
        ("Алишер 45000", CREDIT, "Алишер", 45_000, None),
        ("Ғани ака 45000 нон", CREDIT, "Ғани ака", 45_000, "нон"),
        ("Ali2 45000", CREDIT, "Ali2", 45_000, None),
        ("Karim uy-5 45000", CREDIT, "Karim uy-5", 45_000, None),
        ("Berdi 45000", CREDIT, "Berdi", 45_000, None),  # a payment word alone is a name
        ("Berdi aka 45000", CREDIT, "Berdi aka", 45_000, None),
        # Thousand separators
        ("Ali 45 000", CREDIT, "Ali", 45_000, None),
        ("Ali 45\u00a0000", CREDIT, "Ali", 45_000, None),
        ("Ali 45\u202f000", CREDIT, "Ali", 45_000, None),
        ("Ali 45.000", CREDIT, "Ali", 45_000, None),
        ("Ali 1.250.000", CREDIT, "Ali", 1_250_000, None),
        ("Ali 1 250 000", CREDIT, "Ali", 1_250_000, None),
        ("Ali 1 250.000", CREDIT, "Ali", 1_250_000, None),
        ("Ali 1 250 000 non", CREDIT, "Ali", 1_250_000, "non"),
        ("Ali 450 500 gramm", CREDIT, "Ali", 450_500, "gramm"),  # groups are read greedily
        ("Ali 045000", CREDIT, "Ali", 45_000, None),
        # Thousand words
        ("Ali 45k", CREDIT, "Ali", 45_000, None),
        ("Ali 45K", CREDIT, "Ali", 45_000, None),
        ("Ali 45 k", CREDIT, "Ali", 45_000, None),
        ("Ali 45ming", CREDIT, "Ali", 45_000, None),
        ("Ali 45 ming", CREDIT, "Ali", 45_000, None),
        ("Ali 45 MING", CREDIT, "Ali", 45_000, None),
        ("Ali 45тыс", CREDIT, "Ali", 45_000, None),
        ("Ali 45 тыс", CREDIT, "Ali", 45_000, None),
        ("Ali 45 тыс.", CREDIT, "Ali", 45_000, None),
        ("Ali 45тыс. хлеб", CREDIT, "Ali", 45_000, "хлеб"),
        ("Али 45 ТЫС", CREDIT, "Али", 45_000, None),
        ("Али 45к", CREDIT, "Али", 45_000, None),
        ("Али 45 минг", CREDIT, "Али", 45_000, None),
        ("Ali 1.500k", CREDIT, "Ali", 1_500_000, None),
        ("Ali 1 500 ming", CREDIT, "Ali", 1_500_000, None),
        ("Ali 45 ming 2 kg guruch", CREDIT, "Ali", 45_000, "2 kg guruch"),
        # Currency words are not part of the note
        ("Ali 45000 so'm", CREDIT, "Ali", 45_000, None),
        ("Ali 45000 so\u2018m", CREDIT, "Ali", 45_000, None),
        ("Ali 45000 сум", CREDIT, "Ali", 45_000, None),
        ("Ali 45000 sum", CREDIT, "Ali", 45_000, None),
        ("Ali 45000 SUM non", CREDIT, "Ali", 45_000, "non"),
        ("Ali 45000so'm", CREDIT, "Ali", 45_000, None),
        ("Ali 45 ming so'm non", CREDIT, "Ali", 45_000, "non"),
        ("Ali 45000 сўм", CREDIT, "Ali", 45_000, None),
        ("Ali 45000 so'm 2 kg", CREDIT, "Ali", 45_000, "2 kg"),
        # One trailing comma or full stop ends the amount
        ("Ali 45000, non", CREDIT, "Ali", 45_000, "non"),
        ("Ali 45000.", CREDIT, "Ali", 45_000, None),
        ("Ali 45000, 2 kg non", CREDIT, "Ali", 45_000, "2 kg non"),
        # Payments by sign
        ("Ali -20000", PAYMENT, "Ali", 20_000, None),
        ("Ali \u221220000", PAYMENT, "Ali", 20_000, None),
        ("Ali \u201320000", PAYMENT, "Ali", 20_000, None),
        ("Ali -20 000 naqd", PAYMENT, "Ali", 20_000, "naqd"),
        ("Ali -20k", PAYMENT, "Ali", 20_000, None),
        ("Ali -20000 kecha berdi", PAYMENT, "Ali", 20_000, "kecha berdi"),
        ("Ali Valiyev -1.250.000 so'm", PAYMENT, "Ali Valiyev", 1_250_000, None),
        # Payments by word
        ("Ali 20000 berdi", PAYMENT, "Ali", 20_000, None),
        ("Ali 20000 BERDI", PAYMENT, "Ali", 20_000, None),
        ("Ali 20000 to'ladi", PAYMENT, "Ali", 20_000, None),
        ("Ali 20000 to\u02bcladi", PAYMENT, "Ali", 20_000, None),
        ("Ali 20000 to\u2019ladi", PAYMENT, "Ali", 20_000, None),
        ("Ali 20000 to`ladi", PAYMENT, "Ali", 20_000, None),
        ("Ali 20000 to\u02bbladi", PAYMENT, "Ali", 20_000, None),
        ("Ali 20000 To'ladi", PAYMENT, "Ali", 20_000, None),
        ("Ali 20000 toladi", PAYMENT, "Ali", 20_000, None),
        ("Али 20000 оплатил", PAYMENT, "Али", 20_000, None),
        ("Али 20000 ОПЛАТИЛ", PAYMENT, "Али", 20_000, None),
        ("Анна 20000 оплатила", PAYMENT, "Анна", 20_000, None),
        ("Али 20000 отдал", PAYMENT, "Али", 20_000, None),
        ("Анна 20000 отдала", PAYMENT, "Анна", 20_000, None),
        ("Али 20000 берди", PAYMENT, "Али", 20_000, None),
        ("Али 20000 тўлади", PAYMENT, "Али", 20_000, None),
        ("Ali 20000 berdi naqd pul", PAYMENT, "Ali", 20_000, "naqd pul"),
        ("Ali 20000 berdi.", PAYMENT, "Ali", 20_000, None),
        ("Ali 20 ming so'm berdi karta", PAYMENT, "Ali", 20_000, "karta"),
        ("Ali -20000 berdi", PAYMENT, "Ali", 20_000, None),
        ("Berdi 20000 berdi", PAYMENT, "Berdi", 20_000, None),
        # Notes are free text once the amount is closed
        ("Ali 45000 non 👍", CREDIT, "Ali", 45_000, "non 👍"),
        ("Ali 45000 non (2 dona); sut", CREDIT, "Ali", 45_000, "non (2 dona); sut"),
        ("Ali 45000 '); DROP TABLE customer;--", CREDIT, "Ali", 45_000, "'); DROP TABLE customer;--"),
    ],
)
def test_parse_entry_accepts(text: str, kind: EntryKind, name: str, amount: int, note: str | None) -> None:
    assert parse_entry(text) == ParsedEntry(
        kind=kind, name=name, normalized_name=normalize_name(name), amount=amount, note=note
    )


def test_normalized_name_is_latin_lower_case() -> None:
    assert _entry("Ғани  Ака 45000").normalized_name == "g'ani aka"
    assert _entry("Алишер 45000").normalized_name == _entry("alisher 45000").normalized_name == "alisher"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        # Nothing to read
        ("", E.EMPTY),
        ("   ", E.EMPTY),
        (" \t\n\u00a0\u202f ", E.EMPTY),
        # Commands and plain chatter
        ("/start", E.NOT_AN_ENTRY),
        ("/qarzim 45000", E.NOT_AN_ENTRY),
        ("  /dokon", E.NOT_AN_ENTRY),
        ("salom", E.NOT_AN_ENTRY),
        ("Ali ming so'm", E.NOT_AN_ENTRY),
        ("Ali qirq besh ming", E.NOT_AN_ENTRY),
        ("Ali ٤٥٠٠٠", E.NOT_AN_ENTRY),  # only ASCII digits are digits
        # Name problems
        ("45000", E.NO_NAME),
        ("45000 Ali", E.NO_NAME),
        ("-20000", E.NO_NAME),
        ("45k non", E.NO_NAME),
        ("5-uy Karim 45000", E.NO_NAME),  # a name may not start with a digit
        ("2Ali 45000", E.NO_NAME),
        ("Ali (usta) 45000", E.NAME_INVALID),
        ("Ali, Vali 45000", E.NAME_INVALID),
        ("Ali+Vali 45000", E.NAME_INVALID),
        ("Ali 👍 45000", E.NAME_INVALID),
        ("👍 45000", E.NAME_INVALID),
        ("-Ali 45000", E.NAME_INVALID),
        ("'Ali 45000", E.NAME_INVALID),
        ("Ali\u2013Vali 45000", E.NAME_INVALID),
        ("Ali ٢ 45000", E.NAME_INVALID),
        ("Ali +20000", E.NO_AMOUNT),
        # Amount missing or malformed
        ("Ali2", E.NO_AMOUNT),
        ("Ali-20000", E.NO_AMOUNT),
        ("Ali 45000abc", E.NO_AMOUNT),
        ("Ali 5-uy", E.NO_AMOUNT),
        ("Ali 20000berdi", E.NO_AMOUNT),
        ("Ali 45..000", E.NO_AMOUNT),
        ("Ali 45mingso'm", E.NO_AMOUNT),
        ("Ali \u201420000", E.NO_AMOUNT),  # an em dash is not a minus
        ("Ali --20000", E.NO_AMOUNT),
        # Decimals are rejected, never rounded
        ("Ali 45.5", E.AMOUNT_NOT_WHOLE),
        ("Ali 45,5", E.AMOUNT_NOT_WHOLE),
        ("Ali 45.00", E.AMOUNT_NOT_WHOLE),
        ("Ali 45000.00", E.AMOUNT_NOT_WHOLE),
        ("Ali 45,000", E.AMOUNT_NOT_WHOLE),  # a comma is never a thousand separator
        ("Ali 1.5k", E.AMOUNT_NOT_WHOLE),
        ("Ali 1,5 ming", E.AMOUNT_NOT_WHOLE),
        ("Ali 45.0000", E.AMOUNT_NOT_WHOLE),
        ("Ali 4500.000", E.AMOUNT_NOT_WHOLE),
        ("Ali 1.25.000", E.AMOUNT_NOT_WHOLE),
        ("Ali 0.500", E.AMOUNT_NOT_WHOLE),
        ("Ali -45.5", E.AMOUNT_NOT_WHOLE),
        ("Ali 45.5 berdi", E.AMOUNT_NOT_WHOLE),
        ("Ali 99.9", E.AMOUNT_NOT_WHOLE),  # not AMOUNT_TOO_SMALL: the shape is judged first
        # Range
        ("Ali 0", E.AMOUNT_TOO_SMALL),
        ("Ali 45", E.AMOUNT_TOO_SMALL),
        ("Ali 000", E.AMOUNT_TOO_SMALL),
        ("Ali -50", E.AMOUNT_TOO_SMALL),
        ("Ali 100001k", E.AMOUNT_TOO_LARGE),
        ("Ali 100.000.001", E.AMOUNT_TOO_LARGE),
        ("Ali 1 000 000 000", E.AMOUNT_TOO_LARGE),
        ("Ali 9223372036854775808", E.AMOUNT_TOO_LARGE),  # 2**63
        ("Ali -18446744073709551616", E.AMOUNT_TOO_LARGE),  # 2**64
        ("Ali " + "9" * 400, E.AMOUNT_TOO_LARGE),
        # Two readings: never guess
        ("Ali 2 45000", E.AMBIGUOUS),  # customer "Ali 2", or 2 with a note?
        ("Ali 2 45 000", E.AMBIGUOUS),
        ("Ali 45000 500 gramm", E.AMBIGUOUS),
        ("Ali 45 000 2 kg", E.AMBIGUOUS),
        ("Ali 45000 -20000", E.AMBIGUOUS),
        ("Ali 0 500", E.AMBIGUOUS),
        ("Ali 45 000,5", E.AMBIGUOUS),
        ("Karim 5-uy 45000", E.AMBIGUOUS),
        ("Ali 3ta 45000", E.AMBIGUOUS),
        ("Ali - 20000", E.AMBIGUOUS),  # a separator dash, or a payment?
        ("Ali \u2013 20000", E.AMBIGUOUS),
        ("Ali berdi 20000", E.AMBIGUOUS),  # a payment written out of order, or the customer "Ali berdi"?
        ("Али оплатил 20000", E.AMBIGUOUS),
        ("Ali 20000 kecha berdi", E.AMBIGUOUS),  # must not become a new debt
        ("Ali 20000 naqd to'ladi", E.AMBIGUOUS),
        ("Ali 45000\nVali 30000", E.AMBIGUOUS),  # one entry with a note, or two entries?
        ("Ali 45000\r\nnon", E.AMBIGUOUS),
        ("Ali\u202845000", E.AMBIGUOUS),
    ],
)
def test_parse_entry_rejects(text: str, code: ParseErrorCode) -> None:
    assert _error(text) is code


@pytest.mark.parametrize(
    ("amount_text", "expected"),
    [
        ("99", E.AMOUNT_TOO_SMALL),
        ("100", 100),
        ("100000000", 100_000_000),
        ("100.000.000", 100_000_000),
        ("100 000 000", 100_000_000),
        ("100000k", 100_000_000),
        ("100000001", E.AMOUNT_TOO_LARGE),
    ],
)
def test_amount_boundaries(amount_text: str, expected: int | ParseErrorCode) -> None:
    assert (MIN_AMOUNT, MAX_AMOUNT) == (100, 100_000_000)
    for text in (f"Ali {amount_text}", f"Ali -{amount_text}"):
        result = parse_entry(text)
        assert (result.amount if isinstance(result, ParsedEntry) else result.code) == expected


def test_note_length_boundary() -> None:
    assert _entry("Ali 45000 " + "n" * 120).note == "n" * 120
    assert _error("Ali 45000 " + "n" * 121) is E.NOTE_TOO_LONG
    assert _entry("Ali 45000 berdi " + "n" * 120).note == "n" * 120
    assert _error("Ali 45000 so'm berdi " + "n" * 121) is E.NOTE_TOO_LONG
    # the limit counts the note after its whitespace is collapsed
    assert _entry("Ali 45000 " + "n" * 60 + "    " + "n" * 59).note == "n" * 60 + " " + "n" * 59


def test_name_length_boundary() -> None:
    assert _entry("A" * 80 + " 45000").name == "A" * 80
    assert _error("A" * 81 + " 45000") is E.NAME_TOO_LONG
    assert _entry("A" * 40 + "    " + "B" * 39 + " 45000").name == "A" * 40 + " " + "B" * 39
    assert _error("A" * 40 + " " + "B" * 40 + " 45000") is E.NAME_TOO_LONG


def test_input_length_boundary() -> None:
    assert MAX_INPUT_LENGTH == 500
    assert _entry("Ali 45000" + " " * 491).amount == 45_000  # exactly 500 characters
    assert _error("Ali 45000" + " " * 492) is E.TOO_LONG
    assert _error(" " * 501) is E.TOO_LONG  # judged before anything else, even emptiness
    assert _error("/" + "x" * 500) is E.TOO_LONG


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (" " * 200, E.EMPTY),
        ("🙂🙂🙂", E.NOT_AN_ENTRY),
        ("\u200f\u200e\u202e", E.NOT_AN_ENTRY),  # only direction marks
        ("\u200b\u200c\u200d\ufeff", E.NOT_AN_ENTRY),  # only zero-width characters
        ("\x00\x00\x00", E.NOT_AN_ENTRY),
        ("Ali\u200b 45000", E.INVALID_CHARACTERS),
        ("A\u200dli 45000", E.INVALID_CHARACTERS),
        ("Ali 45\u200b000", E.INVALID_CHARACTERS),
        ("Ali \u202e00054", E.INVALID_CHARACTERS),  # right-to-left override shows this as 45000
        ("\u200fAli 45000", E.INVALID_CHARACTERS),
        ("\ufeffAli 45000", E.INVALID_CHARACTERS),
        ("Ali 45000 non\u200f", E.INVALID_CHARACTERS),
        ("Ali\x00 45000", E.INVALID_CHARACTERS),
        ("Ali 45000\x00", E.INVALID_CHARACTERS),
        ("Ali 45000 \x1b[31m", E.INVALID_CHARACTERS),
        ("Ali \ud800 45000", E.INVALID_CHARACTERS),  # a lone surrogate
        ("x" * 10_000, E.TOO_LONG),
        ("Ali 45000 " * 1_000, E.TOO_LONG),
        ("9" * 10_000, E.TOO_LONG),
        ("'; DROP TABLE customer; --", E.NOT_AN_ENTRY),
        ("Robert'); DROP TABLE customer;-- 45000", E.NAME_INVALID),
        ("' OR 1=1 --", E.NAME_INVALID),
        ("Ali' OR '1'='1 45000", E.NAME_INVALID),
        ("1; DELETE FROM ledger_entry", E.NO_NAME),
        ("Ali 1e9", E.NO_AMOUNT),
        ("Ali 0x45000", E.NO_AMOUNT),
        ("Ali ４５０００", E.NOT_AN_ENTRY),  # full-width digits
        ("Ali NaN 45000e", E.NO_AMOUNT),
        ("Ali " + "1 " * 240, E.AMBIGUOUS),
        ("Ali " + "9" * 30, E.AMOUNT_TOO_LARGE),
        ("Ali -" + "9" * 30 + " berdi", E.AMOUNT_TOO_LARGE),
    ],
)
def test_hostile_input_is_an_error(text: str, code: ParseErrorCode) -> None:
    assert _error(text) is code


def test_never_raises_on_generated_garbage() -> None:
    rng = random.Random(20261006)  # noqa: S311
    alphabet = "Ali Вали 0123456789 .,-\u2212\u2013'\u02bbk мингberdi so'm/\n\t\x00\u200b\u202e\ud800🙂()"
    for _ in range(3_000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 60)))
        result = parse_entry(text)
        assert isinstance(result, ParsedEntry | ParseError)
        if isinstance(result, ParsedEntry):
            assert MIN_AMOUNT <= result.amount <= MAX_AMOUNT
            assert result.name and result.name[0].isalpha() and len(result.name) <= 80
            assert result.note is None or 0 < len(result.note) <= 120


def test_results_are_immutable() -> None:
    entry = _entry("Ali 45000")
    with pytest.raises(FrozenInstanceError):
        entry.amount = 1  # type: ignore[misc]
    error = parse_entry("")
    with pytest.raises(FrozenInstanceError):
        error.code = E.EMPTY  # type: ignore[misc,union-attr]


def test_error_codes_are_stable() -> None:
    # The interface layer keys its Uzbek and Russian messages on these values.
    assert {code.value for code in ParseErrorCode} == {
        "too_long",
        "empty",
        "not_an_entry",
        "invalid_characters",
        "ambiguous",
        "no_amount",
        "no_name",
        "name_invalid",
        "name_too_long",
        "amount_not_whole",
        "amount_too_small",
        "amount_too_large",
        "note_too_long",
    }
    assert (EntryKind.CREDIT.value, EntryKind.PAYMENT.value) == ("credit", "payment")


_NAME_LETTERS = "abdeghilmnoqrstuvxyz" + "абвгдежзиклмнопрстуўқғҳ"
_NOTE_LETTERS = "bdfghjlnpqrtvxz" + "бвгджзлнпрфхц"  # no reserved word can be spelled with these


def _name(rng: random.Random) -> str:
    words = []
    for _ in range(rng.randrange(1, 4)):
        word = "".join(rng.choice(_NAME_LETTERS) for _ in range(rng.randrange(1, 9)))
        if rng.random() < 0.3:
            word += rng.choice(["'", "\u02bb", "-"]) + rng.choice(_NAME_LETTERS)
        if rng.random() < 0.15:
            word += str(rng.randrange(0, 100))
        words.append(word.capitalize() if rng.random() < 0.7 else word)
    return " ".join(words)


def _note(rng: random.Random) -> str | None:
    if rng.random() < 0.4:
        return None
    words = []
    for _ in range(rng.randrange(1, 5)):
        word = "".join(rng.choice(_NOTE_LETTERS) for _ in range(rng.randrange(1, 8)))
        words.append(word + rng.choice(["", "", "7", "-2", "!", "👍"]))
    return " ".join(words)


def _amount(rng: random.Random) -> int:
    return rng.choice(
        [
            rng.randrange(MIN_AMOUNT, 1_000),
            rng.randrange(1, 100_000) * 1_000,
            rng.randrange(MIN_AMOUNT, MAX_AMOUNT + 1),
            MIN_AMOUNT,
            MAX_AMOUNT,
        ]
    )


def _format_amount(rng: random.Random, amount: int) -> str:
    grouped = f"{amount:,}"
    forms = [str(amount), grouped.replace(",", "."), grouped.replace(",", " ")]
    forms += [grouped.replace(",", "\u00a0"), grouped.replace(",", "\u202f")]
    if amount % 1_000 == 0:
        thousands = f"{amount // 1_000:,}".replace(",", ".")
        forms += [f"{thousands}{word}" for word in ("k", "K", "ming", " ming", "тыс", " тыс.", " минг")]
    return rng.choice(forms) + rng.choice(["", "", " so'm", " сум", " sum"])


def test_format_then_parse_round_trips() -> None:
    rng = random.Random(42)  # noqa: S311
    kinds_seen = set()
    for _ in range(600):
        name, amount, note, kind = _name(rng), _amount(rng), _note(rng), rng.choice([CREDIT, PAYMENT])
        amount_text = _format_amount(rng, amount)
        if kind is PAYMENT and rng.random() < 0.5:
            amount_text = rng.choice(["-", "\u2212", "\u2013"]) + amount_text
        elif kind is PAYMENT:
            amount_text += " " + rng.choice(["berdi", "to'ladi", "to\u02bbladi", "toladi", "оплатил", "отдал", "Берди"])
        gap = rng.choice([" ", "  ", "\t"])
        text = rng.choice(["", " "]) + name + gap + amount_text + (gap + note if note else "") + rng.choice(["", " "])
        assert parse_entry(text) == ParsedEntry(
            kind=kind, name=name, normalized_name=normalize_name(name), amount=amount, note=note
        ), text
        kinds_seen.add(kind)
    assert kinds_seen == {CREDIT, PAYMENT}


@pytest.mark.parametrize(
    "text",
    [
        "A" * 500,
        "Ali " + "1" * 496,
        "Ali " + "1 " * 248,
        "Ali " + "000 " * 124,
        "Ali 1" + ".000" * 123 + "x",
        "Ali 1" + " 000" * 123,
        "Ali " + "9." * 248,
        "Ali " + "-" * 496,
        "a " * 249 + "1",
        "Ali 45000 " + "berdi " * 81,
        "Ali " + "'" * 495 + "1",
        ("Ali " + "45 " * 165)[:500],
        "\u200b" * 499 + "1",
    ],
)
def test_pathological_input_is_parsed_in_linear_time(text: str) -> None:
    assert len(text) <= MAX_INPUT_LENGTH
    parse_entry(text)  # warm-up, so that the measurement excludes first-call costs
    best = float("inf")
    for _ in range(5):
        started = time.perf_counter()
        parse_entry(text)
        best = min(best, time.perf_counter() - started)
    assert best < 0.05, f"{best * 1000:.2f} ms"
