"""Print the weekly measurement figures as CSV (REQ-030).

Run with:  python -m qarz.interface.measure_export [--weeks 12] [--compute YYYY-MM-DD]

`--compute` first computes the week that starts on the given Monday. The output holds totals only.
"""

import argparse
import asyncio
import sys
from datetime import date

from qarz.application.measurement import MeasurementService
from qarz.infrastructure.db import Database
from qarz.infrastructure.settings import Settings


async def run(weeks: int, compute: date | None) -> str:
    settings = Settings()
    if not settings.worker_database_url:
        raise RuntimeError("QD_WORKER_DATABASE_URL is not set")
    # The weekly figures are the worker's: this command connects as the worker's role.
    database = Database(settings.worker_database_url)
    try:
        service = MeasurementService(database)
        if compute is not None:
            await service.compute_week(compute)
        return await service.export_csv(weeks)
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weeks", type=int, default=12)
    parser.add_argument("--compute", type=date.fromisoformat, default=None)
    arguments = parser.parse_args()
    sys.stdout.write(asyncio.run(run(arguments.weeks, arguments.compute)))


if __name__ == "__main__":
    main()
