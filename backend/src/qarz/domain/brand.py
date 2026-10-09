"""The product's name, read from `brand.json` beside this module: the one place it is written.

The front end reads the same file (`frontend/src/shared/brand.ts` imports it by path, and the proxy
image's build copies it to where that import looks), so the two sides cannot name the product
differently; `tests/test_brand.py` holds both to it and fails when the name or a former name is
written out anywhere else. The mark (a prototype, not a registered logo) is geometry in the same file:
the icons and the mark beside the name are drawn from it.

The name is the brand and nothing more. Technical identifiers are not derived from it and do not change
with it: the package `qarz`, the `QD_*` variables, the `X-Qarz-*` headers, the `qd.*` storage keys, the
`--qd-*` style variables, the Compose project, the database and its roles, the repository
(docs/08-technical-spec/OUTPUT.md, "Brand").
"""

import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final

SOURCE: Final = Path(__file__).with_name("brand.json")
# What a text writes where the name belongs; `qarz.application.chat_texts.template` fills it in.
PLACEHOLDER: Final = "{brand}"
TAGLINE_PLACEHOLDER: Final = "{tagline}"
# The languages a tagline is typed in. Uzbek Cyrillic is made from the Uzbek one, like every other text.
TAGLINE_LANGUAGES: Final = ("uz", "ru", "tg", "kaa", "en")


def _text(value: object, what: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > limit:
        raise ValueError(f"brand.json: {what} must be a text of 1 to {limit} characters without outer spaces")
    if "{" in value or "}" in value:
        raise ValueError(f"brand.json: {what} must not hold a brace: it is put into texts that are formatted")
    return value


def _mark(value: object) -> Mapping[str, object]:
    """The mark's geometry: rounded blocks on a rounded square, all inside the part a launcher keeps."""
    if not isinstance(value, dict) or sorted(value) != ["about", "blocks", "canvas", "corner"]:
        raise ValueError("brand.json: mark must have about, canvas, corner and blocks")
    canvas, corner, blocks = value["canvas"], value["corner"], value["blocks"]
    if not isinstance(canvas, int) or canvas <= 0 or not isinstance(corner, int) or not 0 <= corner <= canvas // 2:
        raise ValueError("brand.json: mark.canvas and mark.corner must be whole numbers, the corner at most half")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("brand.json: mark.blocks must list at least one block")
    for block in blocks:
        if not isinstance(block, dict) or sorted(block) != ["h", "r", "w", "x", "y"]:
            raise ValueError("brand.json: a block of the mark has x, y, w, h and r")
        x, y, w, h, r = (block[key] for key in "xywhr")
        if not all(isinstance(number, int) for number in (x, y, w, h, r)) or w <= 0 or h <= 0 or r < 0:
            raise ValueError("brand.json: a block of the mark is given in whole numbers")
        # A maskable icon keeps only the middle 80% of its side for certain.
        if min(x, y) < canvas / 10 or max(x + w, y + h) > canvas * 9 / 10 or 2 * r > min(w, h):
            raise ValueError("brand.json: a block of the mark must stay within the middle 80% of the canvas")
    return MappingProxyType(dict(value))


def load(
    source: Path = SOURCE,
) -> tuple[str, str, Mapping[str, str], Mapping[str, object], tuple[tuple[str, str], ...]]:
    """Name, short name, taglines by language, the mark, and the former (name, initials) pairs."""
    data = json.loads(source.read_text(encoding="utf-8"))
    if sorted(data) != ["former", "mark", "name", "short_name", "tagline"]:
        raise ValueError(f"brand.json: unexpected or missing keys: {sorted(data)}")
    taglines = data["tagline"]
    if not isinstance(taglines, dict) or sorted(taglines) != sorted(TAGLINE_LANGUAGES):
        raise ValueError(f"brand.json: tagline must have exactly the languages {TAGLINE_LANGUAGES}")
    former = tuple(
        (_text(old["name"], "a former name", 40), _text(old["initials"], "former initials", 2))
        for old in data["former"]
    )
    return (
        _text(data["name"], "name", 40),
        # What a launcher shows under the installed panel's icon: longer names are cut.
        _text(data["short_name"], "short_name", 12),
        MappingProxyType({lang: _text(taglines[lang], f"tagline.{lang}", 80) for lang in TAGLINE_LANGUAGES}),
        _mark(data["mark"]),
        former,
    )


# FORMER: what the product was called before, with the initials its icon once showed. Kept so that the
# search for a written-out name (tests/test_brand.py) finds the old one too.
NAME, SHORT_NAME, TAGLINES, MARK, FORMER = load()
