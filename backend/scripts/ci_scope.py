"""Decide whether a pull request changes documentation only, so that CI may skip its heavy jobs.

The first job of the workflow hands this script the changed paths (NUL-separated, on standard input) and
reads one line back: `docs_only=true` or `docs_only=false`. With `true` the jobs that build, test and
start the service are skipped and only the `documents` job runs.

The rule is an allow-list, and everything else runs everything:

- a Markdown file under `docs/`, anything under `.project-alpha/` (decisions and evidence), and a Markdown
  file at the top of the repository are documentation;
- except the files of `READ_BY_TESTS`: a test or a job reads them, so changing one can turn it red;
- any other path is code. That includes every file under `deploy/` (its READMEs too: two jobs scan those
  directories), the workflow itself, and `docs/08-technical-spec/*.sql`;
- no changed path at all is not "documentation only": nothing is known, so everything runs.

`backend/tests/test_ci_scope.py` holds the rule to this and fails when a test starts to read a document
that is not listed here.

The last backend job asks the second question, `verdict`: given how the jobs before it ended, is the
backend green? A skipped job counts as green only when this script said "documentation only".

Usage:  git diff --name-only --no-renames -z BASE HEAD | python backend/scripts/ci_scope.py
        CHANGES=success DOCS_ONLY=false CHECKS=success TESTS=success python backend/scripts/ci_scope.py verdict
"""

import os
import sys
from collections.abc import Iterable
from pathlib import PurePosixPath

# Documentation that a test reads. A change to one of these runs everything.
READ_BY_TESTS = frozenset(
    {
        # backend/tests/test_sms_templates.py compares the texts sent for registration with the code's.
        "docs/10-operations/runbooks.md",
    }
)

_DOCUMENT_DIRECTORIES = ("docs",)
_RECORD_DIRECTORIES = (".project-alpha",)
_DOCUMENT_SUFFIXES = (".md",)


def is_documentation(path: str) -> bool:
    """True only for a path that no test, build or job of the workflow other than `documents` reads."""
    if not path or path in READ_BY_TESTS:
        return False
    if "\\" in path or path.startswith("/"):
        return False  # git prints repository-relative paths with forward slashes; anything else is unknown
    parts = PurePosixPath(path).parts
    if not parts or ".." in parts or "." in parts or "/".join(parts) != path:
        return False
    if parts[0] in _RECORD_DIRECTORIES:
        return len(parts) > 1
    is_document = path.endswith(_DOCUMENT_SUFFIXES)
    if parts[0] in _DOCUMENT_DIRECTORIES:
        return len(parts) > 1 and is_document
    return len(parts) == 1 and is_document


def code_paths(paths: Iterable[str]) -> list[str]:
    """The changed paths that are not documentation, in the order given."""
    return [path for path in paths if not is_documentation(path)]


def docs_only(paths: Iterable[str]) -> bool:
    changed = list(paths)
    return bool(changed) and not code_paths(changed)


def changed_paths(raw: bytes) -> list[str]:
    """The paths of `git diff --name-only -z`. Bytes that are not UTF-8 become a path that is never a document."""
    return [name.decode("utf-8", errors="replace") for name in raw.split(b"\0") if name]


def verdict_errors(*, changes: str, docs_only_output: str, checks: str, tests: str) -> list[str]:
    """Why the backend is not green, from the results GitHub reports for the jobs before; empty when it is.

    `changes`, `checks` and `tests` are job results (success, failure, cancelled, skipped); a matrix job
    reports success only when every one of its jobs succeeded. `docs_only_output` is what this script
    printed in the first job.
    """
    errors: list[str] = []
    if changes != "success":
        errors.append(f"the job that reads the changed paths ended with {changes!r}")
    if docs_only_output not in ("true", "false"):
        errors.append(f"docs_only is {docs_only_output!r}, neither 'true' nor 'false'")
    wanted = "skipped" if changes == "success" and docs_only_output == "true" else "success"
    for name, result in (("backend-checks", checks), ("backend-tests", tests)):
        if result != wanted:
            errors.append(f"{name} ended with {result!r}; {wanted!r} is the only result accepted here")
    return errors


def verdict() -> int:
    results = {name: os.environ.get(name, "") for name in ("CHANGES", "DOCS_ONLY", "CHECKS", "TESTS")}
    print(" ".join(f"{name}={value}" for name, value in results.items()))
    errors = verdict_errors(
        changes=results["CHANGES"],
        docs_only_output=results["DOCS_ONLY"],
        checks=results["CHECKS"],
        tests=results["TESTS"],
    )
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 1 if errors else 0


def main(arguments: list[str]) -> int:
    if arguments == ["verdict"]:
        return verdict()
    if arguments:
        print("usage: ci_scope.py [verdict]", file=sys.stderr)
        return 2
    paths = changed_paths(sys.stdin.buffer.read())
    not_documentation = code_paths(paths)
    print(f"{len(paths)} changed path(s), {len(not_documentation)} not documentation", file=sys.stderr)
    for path in not_documentation[:20]:
        print(f"  runs everything: {path!r}", file=sys.stderr)
    print(f"docs_only={'true' if docs_only(paths) else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
