"""Uzbek written in Cyrillic, made from Uzbek written in Latin: the `uz-Cyrl` texts are not typed.

One deterministic pass over a text. It is the twin of `frontend/src/i18n/uzCyrillic.ts`; both are held to
the same cases and the same tables by `backend/tests/data/uz_cyrillic_cases.json`.

What is left exactly as it is:

- placeholders (`{name}`), links, e-mail addresses, `@names`, bot commands (`/obuna`), markup tags and
  file extensions;
- the product's name (read from `qarz.domain.brand`, never written here) and other brands (`BRANDS`); an
  Uzbek ending after a brand is still written in Cyrillic: "Telegramda" becomes "Telegramда";
- codes: the ones in `CODES`, any word in capitals of two letters or more that is not a known Uzbek
  word ("SMS", "QR", "UZS"), and letters that touch a digit ("45k");
- everything that is not a Latin letter: digits, punctuation, currency signs, Cyrillic text.

The rules are the 1995 Latin alphabet read back into the 1940 Cyrillic one. Where the Latin spelling
lost what Cyrillic keeps (the soft sign of "октябрь", the "ц" of "цирк"), the word is in `WORDS` or
`STEMS`; those tables are short and grow by review, never by guessing in the rules.
"""

import re

from qarz.domain import brand

# Product and brand names, kept in Latin. Matched at the start of a word with their capital letters, so
# that "Eskiz" the provider stays and "eskiz" the sketch does not; what follows is an ending.
BRANDS = (
    "Anorbank",
    "Authenticator",
    "Click",
    "Eskiz",
    "Excel",
    "Google",
    "Humo",
    "Payme",
    "Telegram",
    "Uzcard",
    "Visa",
    "WebP",
)

# The product's own name comes from the one place it is written. As a whole it is never touched; when it
# is a single word it is also a brand like the ones above, so an ending after it is still transliterated.
PRODUCT = brand.NAME

# Codes written in lower case too. Compared without regard to case.
CODES = frozenset(
    {
        "api",
        "csv",
        "id",
        "jpeg",
        "jpg",
        "pdf",
        "png",
        "pwa",
        "qr",
        "runbook",
        "sms",
        "totp",
        "url",
        "usd",
        "utf",
        "uuid",
        "uzs",
        "webp",
        "xls",
        "xlsx",
    }
)

# Uzbek words that the texts write in capitals: these are words, not codes.
UPPER_WORDS = frozenset({"diqqat", "sinov"})

# Whole words the rules get wrong: mostly the soft sign that the Latin spelling dropped.
WORDS = {
    "aprel": "апрель",
    "dekabr": "декабрь",
    "fevral": "февраль",
    "iyul": "июль",
    "iyun": "июнь",
    "mebel": "мебель",
    "nol": "ноль",
    "noyabr": "ноябрь",
    "oktabr": "октябрь",
    "panel": "панель",
    "parol": "пароль",
    "rol": "роль",
    "rubl": "рубль",
    "sentabr": "сентябрь",
    "sex": "цех",
    "sirk": "цирк",
    "yanvar": "январь",
}

# Beginnings of words the rules get wrong; the rest of the word follows the rules.
STEMS = {
    "aksiya": "акция",
    "albom": "альбом",
    "filtr": "фильтр",
    "film": "фильм",
    "funksiya": "функция",
    "intervyu": "интервью",
    "kalkulator": "калькулятор",
    "kompyuter": "компьютер",
    "konsert": "концерт",
    "kvitansiya": "квитанция",
    "litsenziya": "лицензия",
    "litsey": "лицей",
    "mayor": "майор",
    "mo'jiza": "мўъжиза",
    "mo'tabar": "мўътабар",
    "mo'tadil": "мўътадил",
    "obyekt": "объект",
    "oktabr": "октябр",
    "rayon": "район",
    "sement": "цемент",
    "sentabr": "сентябр",
    "subyekt": "субъект",
}

_LETTERS = {
    "a": "а",
    "b": "б",
    "d": "д",
    "f": "ф",
    "g": "г",
    "h": "ҳ",
    "i": "и",
    "j": "ж",
    "k": "к",
    "l": "л",
    "m": "м",
    "n": "н",
    "o": "о",
    "p": "п",
    "q": "қ",
    "r": "р",
    "s": "с",
    "t": "т",
    "u": "у",
    "v": "в",
    "x": "х",
    "z": "з",
}
_VOWELS = "aeiou"
_APOSTROPHES = "'ʻʼ’‘`"
_EXTENSIONS = "xlsx|xls|csv|pdf|png|jpe?g|webp|json|txt|zip|md"
_DOMAINS = "uz|com|org|net|me|ru|io|app"

_NAME = "[A-Za-z0-9_]"
# No look-behind here: the twin runs in web views that do not have it. A bot command is taken together
# with the one character before it, which is never a Latin letter and so is kept as it is anyway.
_PROTECTED = re.compile(
    r"\{[^{}]*\}"
    r"|<[^<>]+>"
    r"|https?://[^\s<>\"«»]+"
    rf"|[A-Za-z0-9_.+-]+@(?:{_NAME}|-)+(?:\.(?:{_NAME}|-)+)+"
    rf"|@{_NAME}+"
    rf"|(?:{_NAME}|-)+(?:\.(?:{_NAME}|-)+)*\.(?:{_DOMAINS})(?!{_NAME})(?:/[^\s<>\"«»]*)?"
    rf"|(?:^|[^A-Za-z0-9_/.])/[A-Za-z]{_NAME}*"
    rf"|(?:(?:{_NAME}|[-.])+/)*(?:{_NAME}|-)*\.(?:{_EXTENSIONS})(?!{_NAME})"
    rf"|{re.escape(PRODUCT)}(?!{_NAME})"
)
# A word: Latin letters, with an apostrophe inside it or after "o" and "g".
_WORD = re.compile(
    rf"(?:[oOgG][{_APOSTROPHES}]|[A-Za-z])(?:[oOgG][{_APOSTROPHES}]|[A-Za-z]|[{_APOSTROPHES}](?=[A-Za-z]))*"
)
_BRANDS = tuple(
    sorted({*BRANDS, *([PRODUCT] if _WORD.fullmatch(PRODUCT) else [])}, key=lambda name: (-len(name), name))
)
_STEMS = tuple(sorted(STEMS, key=len, reverse=True))


def _cased(cyrillic: str, latin: str) -> str:
    """`cyrillic` with the capitals of `latin`: all of them, the first one, or none."""
    if len(latin) > 1 and latin.isupper():
        return cyrillic.upper()
    if latin[:1].isupper():
        return cyrillic[:1].upper() + cyrillic[1:]
    return cyrillic


def _unit(cyrillic: str, latin: str) -> str:
    """One letter or pair read as a whole: its first capital decides, the second only for a second letter."""
    if not latin[0].isupper():
        return cyrillic
    if len(cyrillic) == 1 or not latin[1:2].isupper():
        return cyrillic[0].upper() + cyrillic[1:]
    return cyrillic.upper()


def _by_rules(word: str, before: str | None) -> str:
    """`before` is what precedes: None at the start of a word, else "v" after a vowel, "c" after a consonant."""
    out: list[str] = []
    low = word.lower()
    size = len(word)
    i = 0
    while i < size:
        ch = low[i]
        nxt = low[i + 1] if i + 1 < size else ""
        after = low[i + 2] if i + 2 < size else ""
        if ch in "og" and nxt and nxt in _APOSTROPHES:
            out.append(_unit("ў" if ch == "o" else "ғ", word[i]))
            before = "v" if ch == "o" else "c"
            i += 2
        elif ch in "sc" and nxt == "h":
            out.append(_unit("ш" if ch == "s" else "ч", word[i : i + 2]))
            before = "c"
            i += 2
        elif ch == "y" and nxt and nxt in "aou" and not (nxt == "o" and after and after in _APOSTROPHES):
            out.append(_unit({"a": "я", "o": "ё", "u": "ю"}[nxt], word[i : i + 2]))
            before = "v"
            i += 2
        elif ch == "y" and nxt == "e":
            out.append(_unit("ье" if before == "c" else "е", word[i : i + 2]))
            before = "v"
            i += 2
        elif ch == "e":
            out.append(_unit("е" if before == "c" else "э", word[i]))
            before = "v"
            i += 1
        elif ch == "t" and before == "v" and low[i : i + 5] in ("tsiya", "tsion"):
            out.append(_unit("ц", word[i : i + 2]))
            before = "c"
            i += 2
        elif ch in _APOSTROPHES:
            # Between "s" and "h" it only keeps the two letters apart ("Is'hoq"); elsewhere inside a
            # word it is the hard sign ("ma'lumot").
            if not (low[i - 1 : i] == "s" and nxt == "h") and i > 0 and nxt:
                out.append("ъ")
            elif i == 0 or not nxt:
                out.append(word[i])
            i += 1
        elif ch == "y":
            out.append(_unit("й", word[i]))
            before = "c"
            i += 1
        elif ch in _LETTERS:
            out.append(_unit(_LETTERS[ch], word[i]))
            before = "v" if ch in _VOWELS else "c"
            i += 1
        else:
            # A Latin letter Uzbek does not have ("w", a lone "c"): left as it is.
            out.append(word[i])
            before = "c"
            i += 1
    return "".join(out)


def _kind(letter: str) -> str:
    return "v" if letter.lower() in _VOWELS else "c"


def _word(word: str) -> str:
    low = word.lower().translate(_PLAIN)
    if low in CODES:
        return word
    for name in _BRANDS:
        if word.startswith(name):
            ending = word[len(name) :]
            if ending == "" or ending.islower():
                return name + _by_rules(ending, _kind(name[-1]))
    if len(word) > 1 and word.isupper() and low not in UPPER_WORDS and "'" not in low:
        return word
    if low in WORDS:
        return _cased(WORDS[low], word)
    for stem in _STEMS:
        if low.startswith(stem):
            head, ending = word[: len(stem)], word[len(stem) :]
            return _cased(STEMS[stem], head) + _by_rules(ending, _kind(stem[-1]))
    return _by_rules(word, None)


_PLAIN = str.maketrans(dict.fromkeys(_APOSTROPHES, "'"))
_DIGITS = "0123456789"


def _plain(text: str) -> str:
    def replace(found: re.Match[str]) -> str:
        start, end = found.span()
        if (start > 0 and text[start - 1] in _DIGITS) or (end < len(text) and text[end] in _DIGITS):
            return found.group()
        return _word(found.group())

    return _WORD.sub(replace, text)


def to_cyrillic(text: str) -> str:
    """The text in Uzbek Cyrillic. Text that is already Cyrillic comes back unchanged."""
    out: list[str] = []
    position = 0
    for found in _PROTECTED.finditer(text):
        out.append(_plain(text[position : found.start()]))
        out.append(found.group())
        position = found.end()
    out.append(_plain(text[position:]))
    return "".join(out)
