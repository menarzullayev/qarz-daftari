"""List what each language lacks of the server's texts. CI runs it with `--strict`.

Uzbek and Russian are complete by test. Since the last module of the expansion was merged, Tajik,
Karakalpak and English are too: a text one of them lacks fails CI here and in `tests/test_languages.py`,
so whoever adds an Uzbek text adds it in every language in the same pull request
(docs/10-operations/translation-review.md). At run time a text a language lacks is still read in Uzbek.

For Uzbek Cyrillic, which is made from the Uzbek text, it lists the Latin words left in what the rules
write: each is a brand or a code that is meant to stay, or a word for a reviewer to look at.

Usage, from `backend/`:  python scripts/i18n_missing.py            (exit 0 whatever it finds)
                         python scripts/i18n_missing.py --keys     (every missing key, not only counts)
                         python scripts/i18n_missing.py --strict   (exit 1 when a key is missing)
The front end's texts have a twin: `npm run i18n:missing` in `frontend/`.
"""

import re
import sys
from collections.abc import Mapping

from qarz.application import chat_texts, export_texts, texts_en, texts_kaa, texts_tg
from qarz.domain import languages, permissions
from qarz.domain.uz_cyrillic import to_cyrillic
from qarz.interface.errors import _MESSAGES

TRAILING = {"tg": texts_tg, "kaa": texts_kaa, "en": texts_en}
# The SMS wordings exist in Uzbek and Russian only, on purpose: each must be registered with the provider.
NOT_TRANSLATED = frozenset(key for key in chat_texts.UZ if "sms" in key)


def sources() -> dict[str, Mapping[str, object]]:
    """Each catalog's Uzbek keys, by the name the language modules give its translation."""
    names = {f"group.{group.key}": group.uz for group in permissions.GROUPS}
    names.update({item.key: item.uz for item in permissions.CATALOGUE})
    return {
        "CHAT": {key: text for key, text in chat_texts.UZ.items() if key not in NOT_TRANSLATED},
        "EXPORT": export_texts.UZ,
        "ERRORS": _MESSAGES["uz"],
        "PERMISSIONS": names,
    }


def missing(lang: str) -> dict[str, list[str]]:
    module = TRAILING[lang]
    return {name: [key for key in source if key not in getattr(module, name)] for name, source in sources().items()}


def latin_left() -> dict[str, str]:
    """Latin words in the Uzbek Cyrillic texts, each with the first key it is in."""
    found: dict[str, str] = {}
    for name, source in sources().items():
        for key, entry in source.items():
            for text in [entry] if isinstance(entry, str) else list(entry):  # type: ignore[call-overload]
                made = re.sub(r"\{[^{}]*\}", " ", to_cyrillic(text))
                for word in re.findall(r"[A-Za-z][A-Za-z0-9_'./@:?=-]*", made):
                    found.setdefault(word, f"{name}.{key}")
    return found


def main(arguments: list[str]) -> int:
    strict = "--strict" in arguments
    show_keys = strict or "--keys" in arguments
    total = 0
    for lang in TRAILING:
        print(f"\n{lang}")
        for name, keys in missing(lang).items():
            size = len(sources()[name])
            total += len(keys)
            print(f"  {name:<12} {size - len(keys):>4} of {size:>4}  missing {len(keys)}")
            if show_keys:
                for key in keys:
                    print(f"      {key}")
    left = latin_left()
    print(f"\n{languages.UZ_CYRILLIC}: made from the Uzbek text. Latin left in it ({len(left)} different):")
    for word, where in sorted(left.items()):
        print(f"  {word:<28} {where}")
    print(f"\nnot translated on purpose (SMS is Uzbek and Russian only): {', '.join(sorted(NOT_TRANSLATED))}")
    print(f"missing keys in all: {total}")
    return 1 if strict and total else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    raise SystemExit(main(sys.argv[1:]))
