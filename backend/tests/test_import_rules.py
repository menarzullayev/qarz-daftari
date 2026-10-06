"""The layering contracts in pyproject.toml must pass on the real code and must catch a violation."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def _lint_imports(project: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(project / "src"))
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            "import sys; from importlinter import cli; sys.exit(cli.lint_imports(config_filename=sys.argv[1]))",
            str(project / "pyproject.toml"),
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def project_copy(tmp_path: Path) -> Path:
    shutil.copy(BACKEND / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copytree(BACKEND / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    return tmp_path


def test_real_code_respects_the_layers(project_copy: Path) -> None:
    result = _lint_imports(project_copy)
    assert result.returncode == 0, result.stdout + result.stderr


def test_domain_importing_the_interface_is_caught(project_copy: Path) -> None:
    (project_copy / "src/qarz/domain/bad.py").write_text("import qarz.interface.http  # noqa\n", encoding="utf-8")
    result = _lint_imports(project_copy)
    assert result.returncode != 0
    assert "Layers" in result.stdout


def test_domain_importing_a_framework_is_caught(project_copy: Path) -> None:
    (project_copy / "src/qarz/domain/bad.py").write_text("import sqlalchemy  # noqa\n", encoding="utf-8")
    result = _lint_imports(project_copy)
    assert result.returncode != 0
    assert "no framework" in result.stdout


def test_application_importing_infrastructure_is_caught(project_copy: Path) -> None:
    (project_copy / "src/qarz/application/bad.py").write_text(
        "import qarz.infrastructure.settings  # noqa\n", encoding="utf-8"
    )
    result = _lint_imports(project_copy)
    assert result.returncode != 0
