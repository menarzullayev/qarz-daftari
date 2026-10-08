"""The deployment files stay in step with the code.

A setting the application reads must be named in `deploy/production/.env.example` and handed to the
service that reads it in `deploy/production/compose.yml`; otherwise a server silently runs on a default.
The image's dependency list must be the runtime part of the lock and nothing else.
"""

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

from qarz.infrastructure.settings import Settings

REPO = Path(__file__).resolve().parents[2]
PRODUCTION = REPO / "deploy" / "production"
BACKEND = REPO / "backend"

OWNER_URL = "QD_MIGRATION_URL"
# The SMS provider's account: the worker alone sends, so the API is never handed it.
ESKIZ = {"QD_ESKIZ_EMAIL", "QD_ESKIZ_PASSWORD", "QD_ESKIZ_SENDER"}
WORKER_ONLY = {"QD_WORKER_STATEMENT_TIMEOUT_MS", "QD_WORKER_DATABASE_URL"} | ESKIZ
# The connection of each part's own database role (migration 0031): which service may hold which.
CONNECTIONS = {
    "QD_DATABASE_URL": {"api"},
    "QD_ADMIN_DATABASE_URL": {"api"},
    "QD_WORKER_DATABASE_URL": {"worker"},
    OWNER_URL: {"migrate"},
}


def setting_names() -> set[str]:
    return {f"QD_{name.upper()}" for name in Settings.model_fields}


def assigned_names(env_text: str) -> set[str]:
    """Names assigned in an env file; a commented-out line assigns nothing."""
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", env_text, flags=re.MULTILINE))


def missing_from(env_text: str, names: set[str]) -> set[str]:
    return names - assigned_names(env_text)


def block(compose_text: str, header: str) -> str:
    """The lines of one top-level key (`x-shared-env:`) or one service (`  api:`), without the header."""
    lines = compose_text.splitlines()
    start = lines.index(header)
    indent = len(header) - len(header.lstrip())
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.lstrip().startswith("#") and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line)
    return "\n".join(body)


def handed(compose_text: str, service: str) -> set[str]:
    """The QD_ names in a service's environment, the shared group included when it is merged in."""
    own = block(compose_text, f"  {service}:")
    names = set(re.findall(r"^\s+(QD_[A-Z0-9_]+):", own, flags=re.MULTILINE))
    if "*shared-env" in own:
        shared = block(compose_text, "x-shared-env: &shared-env")
        names |= set(re.findall(r"^\s+(QD_[A-Z0-9_]+):", shared, flags=re.MULTILINE))
    return names


@pytest.fixture(scope="module")
def production_env() -> str:
    return (PRODUCTION / ".env.example").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose() -> str:
    return (PRODUCTION / "compose.yml").read_text(encoding="utf-8")


def test_every_setting_is_named_in_the_production_env_example(production_env: str) -> None:
    assert missing_from(production_env, setting_names() | {OWNER_URL}) == set()


def test_a_setting_missing_from_the_env_example_is_reported(production_env: str) -> None:
    without = production_env.replace("QD_SECRETS_KEY=", "# QD_SECRETS_KEY=")
    assert missing_from(without, setting_names()) == {"QD_SECRETS_KEY"}


def test_the_production_env_example_names_nothing_the_root_one_does_not(production_env: str) -> None:
    root = assigned_names((REPO / ".env.example").read_text(encoding="utf-8"))
    ours = {name for name in assigned_names(production_env) if name.startswith("QD_")}
    assert ours - root == set()
    assert setting_names() - root == set()


def test_no_example_carries_a_value(production_env: str) -> None:
    assert re.findall(r"^[A-Z][A-Z0-9_]*=.+$", production_env, flags=re.MULTILINE) == []


def test_every_setting_reaches_the_service_that_reads_it(compose: str) -> None:
    api, worker = handed(compose, "api"), handed(compose, "worker")
    assert setting_names() - WORKER_ONLY - api == set()
    assert WORKER_ONLY - worker == set()
    # The worker's bot and file store are the API's. Its database connection is not: see below.
    shared = handed(compose, "worker") - WORKER_ONLY
    assert shared <= api
    assert {"QD_BOT_TOKEN", "QD_FILE_STORE", "QD_S3_SECRET_KEY"} <= shared


def holders(compose_text: str) -> dict[str, set[str]]:
    """For each database connection, the services that are handed it."""
    services = ("api", "worker", "migrate", "proxy")
    return {name: {service for service in services if name in handed(compose_text, service)} for name in CONNECTIONS}


def test_each_part_is_handed_the_connection_of_its_own_role_and_no_other(compose: str) -> None:
    """The worker never holds the ordinary or the administrators' connection, nor the API the worker's."""
    assert holders(compose) == CONNECTIONS
    assert {name for name in setting_names() if name.endswith("DATABASE_URL")} == set(CONNECTIONS) - {OWNER_URL}


def test_a_connection_handed_to_the_wrong_service_is_reported(compose: str) -> None:
    """The counterpart: the old arrangement, one connection shared by the API and the worker, fails."""
    shared_again = compose.replace(
        "  QD_BOT_TOKEN: ${QD_BOT_TOKEN:-}\n", "  QD_BOT_TOKEN: ${QD_BOT_TOKEN:-}\n  QD_DATABASE_URL: x\n", 1
    )
    assert shared_again != compose
    assert holders(shared_again)["QD_DATABASE_URL"] == {"api", "worker"}
    to_the_api = compose.replace(
        "      QD_WEBHOOK_SECRET: ${QD_WEBHOOK_SECRET:-}\n",
        "      QD_WEBHOOK_SECRET: ${QD_WEBHOOK_SECRET:-}\n      QD_WORKER_DATABASE_URL: x\n",
        1,
    )
    assert holders(to_the_api)["QD_WORKER_DATABASE_URL"] == {"api", "worker"}


def test_a_setting_not_handed_to_the_api_is_reported(compose: str) -> None:
    without = compose.replace("      QD_METRICS_TOKEN: ${QD_METRICS_TOKEN:-}\n", "")
    assert setting_names() - WORKER_ONLY - handed(without, "api") == {"QD_METRICS_TOKEN"}


def test_the_sms_account_goes_to_the_worker_alone(compose: str) -> None:
    assert setting_names() >= ESKIZ and handed(compose, "worker") >= ESKIZ
    for service in ("api", "migrate", "proxy"):
        assert not ESKIZ & handed(compose, service), service


def test_the_sms_account_handed_to_the_api_is_reported(compose: str) -> None:
    to_the_api = compose.replace(
        "      QD_WEBHOOK_SECRET: ${QD_WEBHOOK_SECRET:-}\n",
        "      QD_WEBHOOK_SECRET: ${QD_WEBHOOK_SECRET:-}\n      QD_ESKIZ_PASSWORD: x\n",
        1,
    )
    assert to_the_api != compose
    assert ESKIZ & handed(to_the_api, "api") == {"QD_ESKIZ_PASSWORD"}


def test_the_owner_connection_goes_to_the_migration_alone(compose: str) -> None:
    assert handed(compose, "migrate") == {OWNER_URL}
    assert OWNER_URL not in handed(compose, "api") | handed(compose, "worker") | handed(compose, "proxy")


def test_compose_asks_for_no_name_the_env_example_lacks(compose: str, production_env: str) -> None:
    asked = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", compose))
    # The release is the commit being deployed: the scripts set it, the env file does not.
    assert asked - assigned_names(production_env) == {"DEPLOY_RELEASE"}


def test_only_the_proxy_publishes_ports(compose: str) -> None:
    for service in ("api", "worker", "migrate"):
        assert "ports:" not in block(compose, f"  {service}:")
    assert "ports:" in block(compose, "  proxy:")


def _runtime_requirements() -> ModuleType:
    path = BACKEND / "scripts" / "runtime_requirements.py"
    spec = importlib.util.spec_from_file_location("runtime_requirements", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_image_installs_the_runtime_part_of_the_lock_and_no_tools() -> None:
    module = _runtime_requirements()
    selected = module.select(
        (BACKEND / "requirements.lock").read_text(encoding="utf-8"),
        (BACKEND / "pyproject.toml").read_text(encoding="utf-8"),
    )
    names = {module.normalise(name) for name in re.findall(r"^([A-Za-z0-9._-]+)==", selected, flags=re.MULTILINE)}
    assert {"fastapi", "uvicorn", "aiogram", "sqlalchemy", "asyncpg", "alembic", "psycopg", "cryptography"} <= names
    assert {"starlette", "pydantic-core", "greenlet", "psycopg-binary"} <= names  # reached through others
    assert names & {"pytest", "ruff", "mypy", "pip-audit", "import-linter", "httpx", "pip", "setuptools"} == set()
    assert selected.count("==") == len(names)
    for entry in re.split(r"\n(?=\S)", selected.strip()):
        assert "--hash=sha256:" in entry


def test_a_runtime_dependency_absent_from_the_lock_is_refused() -> None:
    module = _runtime_requirements()
    lock = "fastapi==1.0 \\\n    --hash=sha256:00\n    # via qarz-daftari-backend (pyproject.toml)\n"
    pyproject = '[project]\ndependencies = ["fastapi>=0.1", "left-out>=1"]\n'
    with pytest.raises(ValueError, match="left-out"):
        module.select(lock, pyproject)
