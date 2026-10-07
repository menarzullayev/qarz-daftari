"""Print the part of the hash-pinned lock that the running service needs.

`requirements.lock` pins the runtime and the development tools together. The image must carry no test or
build tools, so the image build keeps only the packages reachable from `[project].dependencies`, each with
its hashes, and installs that with `pip install --require-hashes`. If this selection ever misses a
package, that install fails (an unpinned requirement is refused), so a mistake here cannot reach an image.

Usage:  python runtime_requirements.py requirements.lock pyproject.toml > runtime.lock
"""

import re
import sys
import tomllib
from pathlib import Path

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==\S+")
_VIA_ONE = re.compile(r"^\s+# via (\S.*)$")
_VIA_MANY = re.compile(r"^\s+#   (\S.*)$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    found = _NAME.match(requirement.strip())
    if found is None:
        raise ValueError(f"not a requirement: {requirement!r}")
    return normalise(found.group(0))


def parse_lock(text: str) -> dict[str, tuple[list[str], set[str]]]:
    """Package name -> (its lines as written, the names that require it)."""
    blocks: dict[str, tuple[list[str], set[str]]] = {}
    current: tuple[list[str], set[str]] | None = None
    for line in text.splitlines():
        pin = _PIN.match(line)
        if pin is not None:
            current = ([line], set())
            blocks[normalise(pin.group(1))] = current
            continue
        if current is None or not line.startswith(" "):
            current = None
            continue
        current[0].append(line)
        via = _VIA_ONE.match(line) or _VIA_MANY.match(line)
        if via is not None:
            current[1].add(_requirement_name(via.group(1)))
    return blocks


def runtime_names(blocks: dict[str, tuple[list[str], set[str]]], roots: set[str]) -> set[str]:
    """The roots and everything they require, directly or through another package."""
    missing = roots - blocks.keys()
    if missing:
        raise ValueError(f"not pinned in the lock: {', '.join(sorted(missing))}")
    kept = set(roots)
    grew = True
    while grew:
        grew = False
        for name, (_, required_by) in blocks.items():
            if name not in kept and required_by & kept:
                kept.add(name)
                grew = True
    return kept


def select(lock_text: str, pyproject_text: str) -> str:
    project = tomllib.loads(pyproject_text)["project"]
    roots = {_requirement_name(item) for item in project["dependencies"]}
    blocks = parse_lock(lock_text)
    kept = runtime_names(blocks, roots)
    lines: list[str] = []
    for name in sorted(kept):
        block = blocks[name][0]
        if not any("--hash=" in line for line in block):
            raise ValueError(f"{name} has no hash in the lock")
        lines.extend(block)
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write(__doc__ or "")
        return 2
    lock = Path(argv[1]).read_text(encoding="utf-8")
    pyproject = Path(argv[2]).read_text(encoding="utf-8")
    sys.stdout.write(select(lock, pyproject))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
