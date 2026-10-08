"""Which tests one of several parallel CI jobs runs (`pytest --shard=INDEX/TOTAL`).

A test belongs to exactly one shard, chosen from its node identifier alone: adding or removing another
test moves nothing, and two processes that collect the same suite agree without talking to each other.
The identifier is hashed rather than counted off by file, because the cost is uneven: the API tests take
some 350 ms each and the unit tests almost nothing, and one file (the authorization suite) holds half of
the cost. A hash spreads every file over every shard.

`backend/scripts/check_shards.py` proves in CI, from real collections, that the shards are disjoint and
that together they are the whole suite.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Shard:
    index: int  # 1-based
    total: int


def parse_shard(value: str) -> Shard:
    """Read `INDEX/TOTAL`, 1 <= INDEX <= TOTAL. Anything else is an error, never "run everything"."""
    index_text, separator, total_text = value.partition("/")
    # isdecimal alone would let other scripts' digits and "+1" forms through int(); keep to ASCII digits.
    if separator != "/" or not (index_text.isascii() and index_text.isdecimal()):
        raise ValueError(f"--shard takes INDEX/TOTAL, for example 2/4; got {value!r}")
    if not (total_text.isascii() and total_text.isdecimal()):
        raise ValueError(f"--shard takes INDEX/TOTAL, for example 2/4; got {value!r}")
    index, total = int(index_text), int(total_text)
    if total < 1 or not 1 <= index <= total:
        raise ValueError(f"--shard INDEX/TOTAL needs 1 <= INDEX <= TOTAL; got {value!r}")
    return Shard(index, total)


def shard_of(node_id: str, total: int) -> int:
    """The 1-based shard of a test among `total`. Stable across processes, platforms and Python versions."""
    if total < 1:
        raise ValueError("total must be at least 1")
    digest = hashlib.sha256(node_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % total + 1


def split(node_ids: Sequence[str], shard: Shard) -> tuple[list[int], list[int]]:
    """Positions of the tests the shard runs and of those it leaves to the others, each in the given order."""
    selected: list[int] = []
    deselected: list[int] = []
    for position, node_id in enumerate(node_ids):
        (selected if shard_of(node_id, shard.total) == shard.index else deselected).append(position)
    return selected, deselected
