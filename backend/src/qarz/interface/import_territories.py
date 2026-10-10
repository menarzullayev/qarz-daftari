"""Load the seed of the territory reference (docs/09-development-plan/EXPANSION.md, "Territories and a
customer's address").

Run with:  python -m qarz.interface.import_territories DIRECTORY

DIRECTORY holds four comma-separated files in UTF-8 (a byte order mark is accepted): `regions.csv`,
`districts.csv`, `mahallas.csv` and `streets.csv`. The seed is not part of the repository and must stay
out of it. It holds places and counts of districts, and no person.

Connects with `QD_ADMIN_DATABASE_URL`, the administrators' role. It can be run again safely: every row
is known by a stable key, so nothing is added twice. A place that is in the files no longer is retired,
never deleted. It turns no switch on: a shop is offered an address only when an administrator sets
`address_on`. It prints counts and no row. The exit status is 2 when the configuration or the directory
is unusable.
"""

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from qarz.application.territories_import import ImportResult, Loaded, import_territories
from qarz.infrastructure.db import Database
from qarz.infrastructure.settings import Settings

# Each file and the columns it cannot be read without.
FILES = {
    "regions.csv": ("soato", "name_uz"),
    "districts.csv": ("soato", "region_soato", "name_uz"),
    "mahallas.csv": ("name_uz", "region_soato", "district_soato"),
    "streets.csv": ("district_soato", "mahalla", "street", "kind"),
}


def _rows(directory: Path, name: str) -> list[dict[str, str]]:
    path = directory / name
    try:
        with path.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            columns = reader.fieldnames or []
            rows = [dict(row) for row in reader]
    except (OSError, ValueError, csv.Error) as error:
        raise ValueError(f"{name} cannot be read as UTF-8 CSV") from error
    missing = [column for column in FILES[name] if column not in columns]
    if missing:
        raise ValueError(f"{name} has no column {', '.join(missing)}")
    return rows


async def run(directory: Path, settings: Settings | None = None) -> ImportResult:
    settings = settings or Settings()
    # All four are read before anything is written: each file is the whole of its table, so a missing
    # one must stop the import rather than retire everything it would have held.
    regions, districts, mahallas, streets = (_rows(directory, name) for name in FILES)
    if not regions:
        raise ValueError("regions.csv has no rows")
    if not settings.admin_database_url:
        raise ValueError("QD_ADMIN_DATABASE_URL is not set")
    # Only the administrators' role may write the reference.
    database = Database(settings.admin_database_url)
    try:
        return await import_territories(database, regions, districts, mahallas, streets)
    finally:
        await database.dispose()


def _line(name: str, part: Loaded) -> str:
    return (
        f"{name}: {part.rows} read, {part.added} added, {part.updated} already known and brought up to date, "
        f"{part.skipped} skipped, {part.retired} retired"
    )


def report(result: ImportResult) -> str:
    lines = [
        _line("regions", result.regions),
        _line("districts", result.districts),
        _line("mahallas", result.mahallas),
        f"mahallas with a region and no district (the seed does not say): {result.mahallas_without_district}",
        _line("streets", result.streets),
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="where the four CSV files are")
    arguments = parser.parse_args()
    try:
        result = asyncio.run(run(arguments.directory))
    except ValueError as error:
        sys.stderr.write(f"cannot import: {error}\n")
        raise SystemExit(2) from None
    sys.stdout.write(report(result))


if __name__ == "__main__":
    main()
