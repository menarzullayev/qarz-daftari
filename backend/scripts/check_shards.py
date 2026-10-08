"""Prove that the shards of the test suite are disjoint and that together they are the whole suite.

CI runs the suite as several parallel jobs, each with `pytest --shard=INDEX/TOTAL` (tests/sharding.py).
A test that fell into no shard would stop running without anything turning red, so this is not assumed:
the suite is collected once without the option and once for each shard, by the same command the jobs use,
and the lists of node identifiers are compared. Nothing is run and no database is needed.

Usage, from backend/:  python scripts/check_shards.py 4
"""

import subprocess
import sys
from collections import Counter
from collections.abc import Sequence


def partition_errors(full: Sequence[str], shards: Sequence[Sequence[str]]) -> list[str]:
    """What keeps `shards` from being a partition of `full`; empty when they are one."""
    errors: list[str] = []
    if not full:
        errors.append("the collection without --shard is empty")
    repeated = sorted(node_id for node_id, count in Counter(full).items() if count > 1)
    if repeated:
        errors.append(f"{len(repeated)} node identifier(s) collected more than once, first: {repeated[0]}")
    for number, shard in enumerate(shards, start=1):
        if not shard:
            errors.append(f"shard {number} is empty")
    total = sum(len(shard) for shard in shards)
    if total != len(full):
        errors.append(f"the shards hold {total} tests together, the whole suite {len(full)}")
    seen: dict[str, int] = {}
    for number, shard in enumerate(shards, start=1):
        for node_id in shard:
            if node_id in seen:
                errors.append(f"in shard {seen[node_id]} and again in shard {number}: {node_id}")
            else:
                seen[node_id] = number
    whole = set(full)
    missing = sorted(whole - seen.keys())
    unknown = sorted(seen.keys() - whole)
    if missing:
        errors.append(f"{len(missing)} test(s) in no shard, first: {missing[0]}")
    if unknown:
        errors.append(f"{len(unknown)} test(s) in a shard but not in the whole suite, first: {unknown[0]}")
    return errors


def node_ids(output: str) -> list[str]:
    """The node identifiers in the output of `pytest --collect-only -q`."""
    return [line for line in output.splitlines() if "::" in line and not line.startswith((" ", "="))]


def collect(shard: str | None) -> list[str]:
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
    if shard is not None:
        command.append(f"--shard={shard}")
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)  # noqa: S603
    if result.returncode != 0:
        sys.stderr.write(result.stdout[-4000:] + result.stderr[-4000:])
        raise SystemExit(f"collection failed ({'whole suite' if shard is None else 'shard ' + shard})")
    return node_ids(result.stdout)


def costly(ids: Sequence[str]) -> int:
    """How many of the tests are API tests: nearly all of the suite's time is theirs."""
    return sum(1 for node_id in ids if node_id.startswith("tests/api/"))


def main(arguments: Sequence[str]) -> int:
    if len(arguments) != 1 or not arguments[0].isdecimal() or int(arguments[0]) < 1:
        sys.stderr.write("usage: python scripts/check_shards.py TOTAL\n")
        return 2
    total = int(arguments[0])
    full = collect(None)
    shards = [collect(f"{number}/{total}") for number in range(1, total + 1)]
    print(f"whole suite: {len(full)} tests, {costly(full)} of them API tests")
    for number, shard in enumerate(shards, start=1):
        print(f"shard {number}/{total}: {len(shard)} tests, {costly(shard)} of them API tests")
    print(f"sum of the shards: {sum(len(shard) for shard in shards)}")
    errors = partition_errors(full, shards)
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print("the shards are disjoint and their union is the whole suite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
