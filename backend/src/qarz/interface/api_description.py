"""The API description (OpenAPI) as a file, written without a server or a database (ADR-012, ADR-013).

The front end's types are generated from this file, so a changed request or response shape reaches the
front end's type check instead of a user. The running application still serves no description.

    python -m qarz.interface.api_description            # writes backend/openapi.json
    python -m qarz.interface.api_description --check    # fails when the committed file is out of date
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from qarz.infrastructure.settings import Settings
from qarz.interface.asgi import build

# backend/openapi.json, beside pyproject.toml.
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "openapi.json"

_UNUSED_DB = "postgresql://unused:unused@127.0.0.1:1/unused"  # never connected to


def described_settings() -> Settings:
    """Settings under which every part of the API is served: the ordinary side and the administrators'.

    The values are placeholders that only switch the parts on. Nothing is read from an environment file.
    """
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=_UNUSED_DB,
        admin_database_url=_UNUSED_DB,
        bot_token="0:description-only",  # noqa: S106 - a placeholder, not a credential
        webhook_secret="description-only-placeholder",  # noqa: S106
        secrets_key="description-only-placeholder-0123456789",
        admin_tg_ids="1",
    )


def describe() -> dict[str, Any]:
    """The description of the application as production wires it. Routes kept out of the schema stay out."""
    return build(described_settings()).openapi()


def render(document: dict[str, Any]) -> str:
    """The file's exact text: sorted keys, two spaces, a final line feed, the same on every platform."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def is_current(path: Path, text: str) -> bool:
    """Whether the file at `path` holds exactly `text`, byte for byte."""
    return path.is_file() and path.read_bytes() == text.encode("utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--check", action="store_true", help="write nothing; fail when the file is out of date")
    args = parser.parse_args(argv)
    text = render(describe())
    if args.check:
        if is_current(args.path, text):
            return 0
        print(
            f"{args.path} is not the description of this code. Regenerate it:\n"
            "  cd backend && python -m qarz.interface.api_description\n"
            "  cd frontend && npm run api:types",
            file=sys.stderr,
        )
        return 1
    # Bytes, so that no platform turns the line feeds into anything else.
    args.path.write_bytes(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
