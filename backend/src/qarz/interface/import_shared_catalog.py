"""Load the seed of the shared product catalogue (docs/09-development-plan/EXPANSION.md, "The shared
catalogue").

Run with:  python -m qarz.interface.import_shared_catalog DIRECTORY

DIRECTORY holds `source.json` (the rows), `categories.json` (which section of the seed is which of our
categories) and `img/<key>.jpg|png|webp` (a row's photo; a row without a file has no photo). The seed is
not part of the repository and must stay out of it.

Connects with `QD_ADMIN_DATABASE_URL`, the administrators' role, and writes photos to the file store the
environment names (`QD_FILE_STORE`), like the application. It can be run again safely: a row is known by
its key, so nothing is added twice and a photo already kept is not sent again. It turns no switch on:
the catalogue is offered to shops only when an administrator sets `catalog_on`. It prints counts and no
row. The exit status is 2 when the configuration or the directory is unusable.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from qarz.application.shared_catalog_import import ImportResult, import_catalog, sections_of
from qarz.domain.files import MAX_FILE_BYTES
from qarz.infrastructure.db import Database
from qarz.infrastructure.file_store import build_file_store
from qarz.infrastructure.settings import Settings

_EXTENSIONS = ("jpg", "jpeg", "png", "webp")


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"{path.name} cannot be read as JSON") from error


async def run(directory: Path, settings: Settings | None = None) -> ImportResult:
    settings = settings or Settings()
    rows = _json(directory / "source.json")
    if not isinstance(rows, list):
        raise ValueError("source.json is not a list of rows")
    sections = sections_of(_json(directory / "categories.json"))
    if not settings.admin_database_url:
        raise ValueError("QD_ADMIN_DATABASE_URL is not set")
    store = build_file_store(settings, max_object_bytes=MAX_FILE_BYTES)
    images = directory / "img"

    def read_image(key: str) -> bytes | None:
        # The key was checked to be sixteen hexadecimal digits: it cannot name a file outside `img`.
        for extension in _EXTENSIONS:
            path = images / f"{key}.{extension}"
            if path.is_file():
                return path.read_bytes()
        return None

    # Only the administrators' role may write the catalogue.
    database = Database(settings.admin_database_url)
    try:
        return await import_catalog(database, store, rows, sections, read_image)
    finally:
        await database.dispose()


def report(result: ImportResult, *, with_store: bool) -> str:
    lines = [
        f"rows read: {result.rows}",
        f"items added: {result.added}",
        f"items already known, brought up to date: {result.updated}",
        f"rows skipped (no usable key or name): {result.skipped}",
    ]
    if with_store:
        lines += [
            f"photos stored: {result.images_stored}",
            f"photos already kept: {result.images_kept}",
            f"rows without a photo file: {result.images_missing}",
            f"photo files refused (not a whole image): {result.images_refused}",
        ]
    else:
        lines.append("no file store is configured (QD_FILE_STORE): no photo was stored")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="where source.json, categories.json and img/ are")
    arguments = parser.parse_args()
    settings = Settings()
    try:
        result = asyncio.run(run(arguments.directory, settings))
    except ValueError as error:
        sys.stderr.write(f"cannot import: {error}\n")
        raise SystemExit(2) from None
    sys.stdout.write(report(result, with_store=bool(settings.file_store.strip())))


if __name__ == "__main__":
    main()
