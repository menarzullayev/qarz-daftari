"""What CI skips for a change of documentation only, and the workflow that asks.

`scripts/ci_scope.py` decides from the changed paths whether a pull request is documentation only; the
workflow then skips every job but `documents`. A wrong "yes" is a change merged without its tests, so the
rule is an allow-list, and three things are held here: the rule itself, with the paths that must run
everything; that no test of this suite reads a document the rule would let through; and that the workflow
asks the question of every heavy job, never cancels a run on main, and runs the shards it proves.
"""

import ast
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
SCRIPT = BACKEND / "scripts" / "ci_scope.py"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_scope", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scope = _load()

DOCUMENTATION = [
    "README.md",
    "GOAL-PROMPT.md",
    "docs/10-operations/OUTPUT.md",
    "docs/10-operations/load-test.md",
    "docs/10-operations/rehearsals/2026-09-01.md",
    "docs/evidence/EVID-079.md",
    "docs/09-development-plan/PROGRESS.md",
    ".project-alpha/decisions/DEC-070.json",
    ".project-alpha/state.json",
]

RUNS_EVERYTHING = [
    # documents that a test reads
    "docs/10-operations/runbooks.md",
    "docs/08-technical-spec/schema.sql",
    "docs/08-technical-spec/tests/schema_checks.sql",
    # code, wherever a Markdown file may lie in it
    "backend/src/qarz/domain/ledger.py",
    "backend/tests/test_ledger.py",
    "backend/README.md",
    "backend/requirements.lock",
    "backend/openapi.json",
    "frontend/src/shared/api.ts",
    "frontend/README.md",
    "e2e/README.md",
    "e2e/tests/staff.spec.ts",
    "deploy/production/README.md",
    "deploy/production/SINGLE-HOST.md",
    "deploy/backup/README.md",
    "deploy/monitoring/alerts.yml",
    ".github/workflows/ci.yml",
    ".github/pull_request_template.md",
    ".env.example",
    ".gitattributes",
    ".dockerignore",
    "docker-compose.dev.yml",
    # not what the allow-list names
    "docs/10-operations/diagram.png",
    "docs/notes.txt",
    "docs/Makefile",
    "docs",
    ".project-alpha",
    "docs.md/x.py",
    "docsx/a.md",
    "notes.MD",
    "README.md.py",
    "README.md ",
    "README",
    # not a path git prints for this repository
    "",
    "/README.md",
    "docs/../backend/src/x.md",
    "./README.md",
    "docs//a.md",
    "docs\\a.md",
    "..\\README.md",
    "README\ufffd.md/../x",
]


# --- the rule ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", DOCUMENTATION)
def test_documentation_is_what_the_allow_list_names(path: str) -> None:
    assert scope.is_documentation(path) is True
    assert scope.docs_only([path]) is True


@pytest.mark.parametrize("path", RUNS_EVERYTHING)
def test_any_other_path_runs_everything(path: str) -> None:
    assert scope.is_documentation(path) is False
    assert scope.docs_only([path]) is False
    # One such path among any number of documents is enough.
    assert scope.docs_only([*DOCUMENTATION, path]) is False
    assert scope.docs_only([path, *DOCUMENTATION]) is False


def test_documents_alone_are_documentation_only() -> None:
    assert scope.docs_only(DOCUMENTATION) is True
    assert scope.code_paths(DOCUMENTATION) == []


def test_no_changed_path_at_all_runs_everything() -> None:
    assert scope.docs_only([]) is False


def test_a_document_that_a_test_reads_is_named_and_runs_everything() -> None:
    # The neighbouring document differs only in not being read by a test: the list is what decides.
    assert scope.is_documentation("docs/10-operations/OUTPUT.md") is True
    assert "docs/10-operations/runbooks.md" in scope.READ_BY_TESTS
    assert scope.code_paths(["docs/10-operations/OUTPUT.md", "docs/10-operations/runbooks.md"]) == [
        "docs/10-operations/runbooks.md"
    ]


def test_every_document_named_as_read_by_a_test_exists() -> None:
    # A file renamed in the repository and not in the list would leave the list guarding nothing.
    assert [path for path in sorted(scope.READ_BY_TESTS) if not (REPO / path).is_file()] == []


def test_the_changed_paths_are_read_from_what_git_prints() -> None:
    raw = "docs/a.md\0docs/бир икки.md\0deploy/x y.sh\0".encode() + b"docs/\xff.md\0"
    paths = scope.changed_paths(raw)
    assert paths == ["docs/a.md", "docs/бир икки.md", "deploy/x y.sh", "docs/\ufffd.md"]
    assert scope.code_paths(paths) == ["deploy/x y.sh"]
    assert scope.changed_paths(b"") == []


def _run(*arguments: str, stdin: bytes = b"", env: dict[str, str] | None = None) -> tuple[int, str]:
    command = [sys.executable, str(SCRIPT), *arguments]
    result = subprocess.run(command, input=stdin, capture_output=True, env=env, check=False)  # noqa: S603
    return result.returncode, result.stdout.decode().replace("\r\n", "\n")  # Windows writes its own line ends


@pytest.mark.parametrize(
    ("stdin", "answer"),
    [
        (b"docs/04-prd/OUTPUT.md\0README.md\0.project-alpha/state.json\0", "docs_only=true\n"),
        (b"docs/04-prd/OUTPUT.md\0backend/src/qarz/domain/ledger.py\0", "docs_only=false\n"),
        (b"docs/10-operations/runbooks.md\0", "docs_only=false\n"),
        (b"", "docs_only=false\n"),
        # Newline-separated input is not what the workflow sends: it is one path, and not a document.
        (b"docs/a.md\nbackend/x.py\n", "docs_only=false\n"),
    ],
)
def test_the_script_answers_the_workflow_with_one_line(stdin: bytes, answer: str) -> None:
    assert _run(stdin=stdin) == (0, answer)


def test_the_script_refuses_an_argument_it_does_not_know() -> None:
    code, output = _run("--verdict")
    assert code == 2
    assert output == ""


# --- no test reads a document that the rule lets through ----------------------------------------------

# The test modules that name a path under docs/ or .project-alpha/, or a Markdown file, with what they read.
DOCUMENT_READERS = {
    # Names documents only to leave them out of its search for the product's name: it opens none.
    "tests/test_brand.py": [],
    "tests/db/test_schema_rules.py": ["docs/08-technical-spec/schema.sql"],
    "tests/test_sms_templates.py": ["docs/10-operations/runbooks.md"],
}
_THIS = "tests/test_ci_scope.py"
# "docs" as a part of a path, but not the address "/docs" that test_healthz.py asks the application for.
_DOCUMENT_WORD = re.compile(r"(^|[^/\\][/\\])(docs|\.project-alpha)($|[/\\])|\.md$", re.IGNORECASE)


def names_a_document(source: str) -> list[str]:
    """The string constants of a module, other than docstrings, that look like the path of a document."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and _DOCUMENT_WORD.search(node.value.strip())
    ]


def test_the_test_modules_that_read_documents_are_the_listed_ones() -> None:
    # A new test that reads a document must be added to DOCUMENT_READERS, and the document to
    # READ_BY_TESTS in scripts/ci_scope.py unless the rule already runs everything for it. Otherwise a
    # pull request that changes only that document would skip the test that reads it.
    readers = {
        path.relative_to(BACKEND).as_posix()
        for path in sorted((BACKEND / "tests").rglob("*.py"))
        if names_a_document(path.read_text(encoding="utf-8"))
    }
    assert readers - {_THIS} == set(DOCUMENT_READERS)


@pytest.mark.parametrize(("module", "documents"), sorted(DOCUMENT_READERS.items()))
def test_a_document_that_a_test_reads_is_never_documentation_only(module: str, documents: list[str]) -> None:
    source = (BACKEND / module).read_text(encoding="utf-8")
    for document in documents:
        assert (REPO / document).is_file(), document
        assert Path(document).name in source, f"{module} no longer names {document}"
        assert scope.is_documentation(document) is False, document


@pytest.mark.parametrize(
    "source",
    [
        'RUNBOOKS = ROOT / "docs" / "10-operations" / "runbooks.md"',
        'text = (REPO / "docs/04-prd/OUTPUT.md").read_text()',
        'def test_x():\n    """Reads nothing."""\n    return open(REPO / "README.md").read()',
        'DECISIONS = REPO / ".project-alpha" / "decisions"',
        'path = "..\\\\docs\\\\x.txt"',
    ],
)
def test_a_module_that_names_a_document_is_found(source: str) -> None:
    assert names_a_document(source) != []


@pytest.mark.parametrize(
    "source",
    [
        '"""Compared with docs/10-operations/runbooks.md by hand."""\nX = 1',
        'def test_x():\n    """See README.md and docs/04-prd."""\n    assert "markdown" != "md"',
        'MIGRATION = BACKEND / "migrations" / "sql" / "0001_initial.sql"\n# docs/04-prd/OUTPUT.md',
        'message = "see the docs for details"',
        'answers = [client.get(path) for path in ("/docs", "/redoc")]',
    ],
)
def test_a_module_that_only_mentions_documents_in_prose_is_not(source: str) -> None:
    assert names_a_document(source) == []


# --- the verdict of the last backend job ---------------------------------------------------------------


def _verdict(changes: str, docs_only_output: str, checks: str, tests: str) -> list[str]:
    errors: list[str] = scope.verdict_errors(
        changes=changes, docs_only_output=docs_only_output, checks=checks, tests=tests
    )
    return errors


def test_the_backend_is_green_when_everything_ran_and_succeeded() -> None:
    assert _verdict("success", "false", "success", "success") == []


def test_the_backend_is_green_when_documentation_only_skipped_it() -> None:
    assert _verdict("success", "true", "skipped", "skipped") == []


@pytest.mark.parametrize(
    ("changes", "docs_only_output", "checks", "tests"),
    [
        # a check or a shard failed, was cancelled, or did not run although code changed
        ("success", "false", "failure", "success"),
        ("success", "false", "success", "failure"),
        ("success", "false", "cancelled", "success"),
        ("success", "false", "success", "cancelled"),
        ("success", "false", "skipped", "success"),
        ("success", "false", "success", "skipped"),
        ("success", "false", "skipped", "skipped"),
        ("success", "false", "", ""),
        # the first job did not answer, or did not end well: skipped jobs are then not excused
        ("failure", "", "success", "success"),
        ("failure", "false", "success", "success"),
        ("cancelled", "false", "success", "success"),
        ("failure", "", "skipped", "skipped"),
        ("cancelled", "", "skipped", "skipped"),
        ("skipped", "", "skipped", "skipped"),
        ("failure", "true", "skipped", "skipped"),
        ("success", "", "skipped", "skipped"),
        ("success", "", "success", "success"),
        ("success", "True", "skipped", "skipped"),
        ("success", "yes", "skipped", "skipped"),
        # documentation only, and yet something ran or failed: the two disagree
        ("success", "true", "success", "skipped"),
        ("success", "true", "skipped", "failure"),
        ("success", "true", "failure", "failure"),
    ],
)
def test_the_backend_is_red_otherwise(changes: str, docs_only_output: str, checks: str, tests: str) -> None:
    assert _verdict(changes, docs_only_output, checks, tests) != []


def test_the_verdict_is_the_scripts_exit_code() -> None:
    base = {"SYSTEMROOT": "C:\\Windows"} if sys.platform == "win32" else {}
    green = {"CHANGES": "success", "DOCS_ONLY": "false", "CHECKS": "success", "TESTS": "success"}
    assert _run("verdict", env={**base, **green})[0] == 0
    assert _run("verdict", env={**base, **green, "TESTS": "failure"})[0] == 1
    assert _run("verdict", env={**base, **green, "CHECKS": "skipped"})[0] == 1
    # Nothing handed over at all (a renamed variable in the workflow) is red, not green.
    assert _run("verdict", env=base)[0] == 1


# --- the workflow --------------------------------------------------------------------------------------

SKIPPED_FOR_DOCUMENTATION = [
    "backend-checks",
    "backend-tests",
    "frontend",
    "deploy-files",
    "e2e",
    "backup-files",
    "single-host",
]
CONDITION = "    if: ${{ !cancelled() && needs.changes.outputs.docs_only != 'true' }}"
SHARDS = 4


def jobs(workflow: str) -> dict[str, str]:
    """Each job of the workflow with its lines, comments left out."""
    body = workflow.split("\njobs:\n", 1)[1]
    found: dict[str, list[str]] = {}
    name = ""
    for line in body.splitlines():
        started = re.fullmatch(r"  ([a-z][a-z0-9-]*):", line)
        if started:
            name = started.group(1)
            found[name] = []
        elif name and not line.lstrip().startswith("#"):
            found[name].append(line)
    return {job: "\n".join(lines) for job, lines in found.items()}


def missing_condition(workflow: str) -> list[str]:
    """The jobs that must be skipped for documentation only and are not asked the question."""
    blocks = jobs(workflow)
    return [
        job
        for job in SKIPPED_FOR_DOCUMENTATION
        if "    needs: changes" not in blocks[job].splitlines() or CONDITION not in blocks[job].splitlines()
    ]


@pytest.fixture(scope="module")
def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_the_workflow_has_the_jobs_the_documents_name(workflow: str) -> None:
    assert sorted(jobs(workflow)) == sorted(["changes", "backend", "documents", *SKIPPED_FOR_DOCUMENTATION])


def test_every_heavy_job_is_skipped_for_documentation_only_and_runs_if_the_question_failed(workflow: str) -> None:
    assert missing_condition(workflow) == []


def test_a_heavy_job_without_the_condition_is_found(workflow: str) -> None:
    assert missing_condition(workflow.replace("  e2e:\n    needs: changes\n" + CONDITION + "\n", "  e2e:\n")) == ["e2e"]
    weaker = workflow.replace(CONDITION, "    if: ${{ needs.changes.outputs.docs_only == 'false' }}", 1)
    assert missing_condition(weaker) == ["backend-checks"]


def test_the_documents_job_and_the_question_itself_always_run(workflow: str) -> None:
    blocks = jobs(workflow)
    for job in ("documents", "changes"):
        assert "needs:" not in blocks[job], job
        assert not re.search(r"^    if:", blocks[job], flags=re.MULTILINE), job
    # No path filter on the workflow: a commit of documentation only still gets a run of its own.
    trigger = workflow.split("\njobs:\n", 1)[0]
    assert "paths" not in trigger
    assert "on:\n  push:\n    branches: [main]\n  pull_request:\n" in trigger


def test_only_a_pull_request_is_ever_documentation_only(workflow: str) -> None:
    block = jobs(workflow)["changes"]
    before_the_script = block.split("python3 backend/scripts/ci_scope.py", 1)[0]
    assert 'if [ "$EVENT" != "pull_request" ]; then' in before_the_script
    assert "EVENT: ${{ github.event_name }}" in block
    assert block.count('echo "docs_only=false" >> "$GITHUB_OUTPUT"') == 2
    assert "git diff --name-only --no-renames -z 'HEAD^1' HEAD" in block
    assert "fetch-depth: 2" in block


def test_the_backend_result_needs_every_backend_job_and_always_runs(workflow: str) -> None:
    block = jobs(workflow)["backend"]
    assert "    needs: [changes, backend-checks, backend-tests]" in block.splitlines()
    assert "    if: ${{ always() }}" in block.splitlines()
    for line in (
        "CHANGES: ${{ needs.changes.result }}",
        "DOCS_ONLY: ${{ needs.changes.outputs.docs_only }}",
        "CHECKS: ${{ needs.backend-checks.result }}",
        "TESTS: ${{ needs.backend-tests.result }}",
        "run: python3 backend/scripts/ci_scope.py verdict",
    ):
        assert line in block, line
    assert "continue-on-error" not in workflow


def test_the_shards_that_run_are_the_shards_that_are_proven(workflow: str) -> None:
    blocks = jobs(workflow)
    numbers = ", ".join(str(number) for number in range(1, SHARDS + 1))
    assert f"        shard: [{numbers}]" in blocks["backend-tests"].splitlines()
    assert f"        run: pytest -q --shard=${{{{ matrix.shard }}}}/{SHARDS}" in blocks["backend-tests"].splitlines()
    assert "      fail-fast: false" in blocks["backend-tests"].splitlines()
    assert f"        run: python scripts/check_shards.py {SHARDS}" in blocks["backend-checks"].splitlines()
    # The whole suite is run nowhere else, and the checks that need no shard are run once.
    commands = "\n".join(blocks.values())
    assert commands.count("run: pytest") == 1
    for once in ("ruff format --check .", "ruff check .", "run: mypy", "lint-imports", "pip-audit", "alembic upgrade"):
        assert commands.count(once) == 1, once
        assert once in blocks["backend-checks"], once


def test_a_run_on_main_is_never_cancelled_and_a_pull_requests_older_run_is(workflow: str) -> None:
    head = workflow.split("\njobs:\n", 1)[0]
    block = head.split("\nconcurrency:\n", 1)[1]
    assert "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n" in block + "\n"
    group = " ".join(
        line.strip() for line in block.split("  group: >-\n", 1)[1].split("  cancel-in-progress", 1)[0].splitlines()
    )
    assert group == (
        "ci-${{ github.workflow }}-${{ github.event_name == 'pull_request' "
        "&& format('pr-{0}', github.event.pull_request.number) || format('run-{0}', github.run_id) }}"
    )
    assert workflow.count("concurrency:") == 1 and workflow.count("cancel-in-progress") == 1
