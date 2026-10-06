"""Display-name normalization for matching customers (domain value object "Display name").

The normalized form exists only to decide whether two typed names mean the same customer. It is never
shown to a person: it is lossy (for example it writes every "x" as "h") and is not a transliteration
anyone should read.

What is folded together:

- letter case, surrounding and repeated whitespace, and Unicode composition (NFC);
- the apostrophe look-alikes that phone keyboards produce (U+02BC, U+2019, U+2018, U+02BB and the
  grave accent), which all become the ASCII apostrophe;
- Uzbek and Russian Cyrillic with Uzbek Latin: "Алишер" and "Alisher", "Ғани" and "G'ani";
- the letters "x" and "h" in both scripts (Cyrillic ha, Cyrillic ha with descender, Latin "x" and "h"
  all become "h"), because the Russian spelling "Яхё" and the Uzbek spelling "Yahyo" are the same name
  and the two sounds are mixed up freely.

Cyrillic letters without a one-to-one Latin letter:

- Cyrillic ie is "ye" at the start of a word and after a vowel, "ъ" or "ь"; otherwise "e";
- "ё" is "yo", "ю" is "yu", "я" is "ya";
- "ъ" is an apostrophe ("Маъруф" equals "Ma'ruf"); "ь" is dropped ("Ильхом" equals "Ilhom");
- "ц" is "ts", "щ" is "sh" (the same as "ш"), "ы" is "i", "э" is "e".

What is deliberately NOT folded, so these pairs stay different: "Aliy" and "Ali"; "Gani" and "G'ani"
(a missing apostrophe); "Elena" and "Елена" (which becomes "yelena"); "Jamshid" and "Джамшид"; Russian
respellings such as "Юлдаш" for "Yo'ldosh"; doubled letters; Cyrillic letters of other languages, which
are kept as they are. Closing those gaps is the job of customer search, not of this function.
"""

import unicodedata

APOSTROPHES = frozenset("'\u02bc\u2019\u2018\u02bb`")

_CYRILLIC = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "ё": "yo",
    "ж": "j",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sh",
    "ъ": "'",
    "ь": "",
    "ы": "i",
    "э": "e",
    "ю": "yu",
    "я": "ya",
    "ў": "o'",
    "қ": "q",
    "ғ": "g'",
    "ҳ": "h",
}
_YE_AFTER = frozenset("аеёиоуыэюяўъьaeiou")


def unify_apostrophes(text: str) -> str:
    """Replace every apostrophe look-alike with the plain ASCII apostrophe."""
    return "".join("'" if ch in APOSTROPHES else ch for ch in text)


def normalize_name(name: str) -> str:
    """Return the matching form of a display name. Total: never raises, and "" for a blank name."""
    text = unify_apostrophes(unicodedata.normalize("NFC", name).lower())
    out: list[str] = []
    previous = ""
    for ch in text:
        if ch == "е":
            out.append("ye" if not previous.isalpha() or previous in _YE_AFTER else "e")
        elif ch == "x":
            out.append("h")
        else:
            out.append(_CYRILLIC.get(ch, ch))
        previous = ch
    return " ".join("".join(out).split())
