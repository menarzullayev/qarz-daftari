"""Create a throwaway database and fill it with generated shops.

    python -m loadtest.load --database qd_load_full --profile full --seed 1

The database is created on the server named by QD_LOAD_ADMIN_URL (a superuser connection string), built
by the real migrations, and filled with COPY as the migration owner, a few hundred shops per transaction.
Every constraint, foreign key and trigger of the schema checks the rows as they go in, with one exception:

`goods_line_guard` refuses goods lines once the day after the sale has ended, measured by the clock at
the time of the insert. History cannot be loaded through it, so it is switched off for the load and
switched on again afterwards. The rules it guards are checked on the loaded data instead
(`loadtest.invariants.database_problems`), together with the ledger rules the schema leaves to the
application; the load fails if any of them is broken.

Safety: the database name must start with `qd_load_`, so this cannot be pointed at a real database by a
typing mistake. Generated staff sessions have predictable tokens; such a database is for load tests only.
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql

from loadtest.dataset import COLUMNS, PROFILES, TABLES, Profile, ShopData, generate
from loadtest.invariants import database_problems

BACKEND = Path(__file__).resolve().parents[1]
DATABASE_PREFIX = "qd_load_"
ADMIN_URL_VARIABLE = "QD_LOAD_ADMIN_URL"
# `qd_app` is one role for the whole server. Where the test suite uses the same server, both must set the
# same password or they lock each other out (see tests/conftest.py).
APP_PASSWORD = os.environ.get("QD_TEST_APP_PASSWORD", "test-only-qd-app-password")
SHOPS_PER_TRANSACTION = 200


def require_throwaway(name: str) -> None:
    rest = name.removeprefix(DATABASE_PREFIX)
    if rest == name or not rest.replace("_", "").isalnum() or not name.isascii() or name != name.lower():
        raise ValueError(f"a load test database must be named {DATABASE_PREFIX}<lowercase letters, digits, _>")


def database_url(admin_url: str, name: str) -> str:
    require_throwaway(name)
    parts = urlsplit(admin_url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, "", ""))


def app_url(admin_url: str, name: str) -> str:
    """Connection string of the restricted application role for this database."""
    require_throwaway(name)
    parts = urlsplit(admin_url)
    netloc = f"qd_app:{APP_PASSWORD}@{parts.hostname or '127.0.0.1'}:{parts.port or 5432}"
    return urlunsplit((parts.scheme, netloc, "/" + name, "", ""))


def create_database(admin_url: str, name: str, *, drop_existing: bool) -> str:
    """Create the database and build its schema with the migrations. Returns the owner's connection string."""
    require_throwaway(name)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        if drop_existing:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = database_url(admin_url, name)
    previous = os.environ.get("QD_MIGRATION_URL")
    os.environ["QD_MIGRATION_URL"] = url
    try:
        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "migrations"))
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("QD_MIGRATION_URL", None)
        else:
            os.environ["QD_MIGRATION_URL"] = previous
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("ALTER ROLE qd_app LOGIN PASSWORD {}").format(sql.Literal(APP_PASSWORD)))
    return url


def drop_database(admin_url: str, name: str) -> None:
    require_throwaway(name)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def _batches(shops: Iterable[ShopData], size: int) -> Iterator[list[ShopData]]:
    batch: list[ShopData] = []
    for shop in shops:
        batch.append(shop)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def copy_shops(conn: psycopg.Connection[Any], shops: Iterable[ShopData]) -> dict[str, int]:
    """Copy the shops in, one transaction per batch. Returns the number of rows written per table."""
    written = dict.fromkeys(TABLES, 0)
    conn.execute("ALTER TABLE goods_line DISABLE TRIGGER goods_line_guard")
    conn.commit()
    try:
        for batch in _batches(shops, SHOPS_PER_TRANSACTION):
            with conn.cursor() as cursor:
                for table in TABLES:
                    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
                        sql.Identifier(table), sql.SQL(", ").join(sql.Identifier(column) for column in COLUMNS[table])
                    )
                    with cursor.copy(statement) as copy:
                        for shop in batch:
                            for row in shop.rows[table]:
                                copy.write_row(row)
                            written[table] += len(shop.rows[table])
            # The deferred check that goods lines sum to their entry runs here, for every line.
            conn.commit()
    finally:
        conn.rollback()
        conn.execute("ALTER TABLE goods_line ENABLE TRIGGER goods_line_guard")
        conn.commit()
    return written


def write_manifest(conn: psycopg.Connection[Any], name: str, manifest: dict[str, Any]) -> None:
    """Keep what was generated with the database itself, as its comment, so the schema stays untouched."""
    require_throwaway(name)
    conn.execute(
        sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(name), sql.Literal(json.dumps(manifest)))
    )
    conn.commit()


def read_manifest(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    row = conn.execute(
        "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = current_database()"
    ).fetchone()
    if row is None or not row[0]:
        raise ValueError("this database was not filled by loadtest.load")
    manifest = json.loads(row[0])
    if not isinstance(manifest, dict) or "seed" not in manifest:
        raise ValueError("this database was not filled by loadtest.load")
    return manifest


def fill(
    admin_url: str, name: str, profile: Profile, seed: int, now: datetime, *, drop_existing: bool
) -> dict[str, Any]:
    started = time.perf_counter()
    url = create_database(admin_url, name, drop_existing=drop_existing)
    with psycopg.connect(url) as conn:
        written = copy_shops(conn, generate(profile, seed, now))
        copied = time.perf_counter()
        conn.autocommit = True
        # Sets the planner statistics and the visibility map, as autovacuum would have on a database
        # that grew to this size over months.
        conn.execute("VACUUM ANALYZE")
        conn.autocommit = False
        broken = database_problems(conn)
        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()
        manifest: dict[str, Any] = {
            "seed": seed,
            "now": now.isoformat(),
            "profile": {key: getattr(profile, key) for key in profile.__dataclass_fields__},
            "rows": written,
            "size_bytes": int(size[0]) if size else 0,
            "seconds": {"copy": round(copied - started, 1), "total": round(time.perf_counter() - started, 1)},
        }
        if broken:
            raise RuntimeError("the loaded data breaks the ledger rules: " + "; ".join(broken))
        write_manifest(conn, name, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--database", required=True, help=f"name of the database to create; starts with {DATABASE_PREFIX}"
    )
    parser.add_argument("--profile", choices=sorted(PROFILES), default="full")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--now", help="anchor instant in ISO form; default: the current time")
    parser.add_argument("--drop-existing", action="store_true", help="replace a database of that name")
    arguments = parser.parse_args(argv)

    admin_url = os.environ.get(ADMIN_URL_VARIABLE)
    if not admin_url:
        parser.error(f"set {ADMIN_URL_VARIABLE} to a superuser connection string of the load test server")
    now = datetime.fromisoformat(arguments.now) if arguments.now else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    manifest = fill(
        admin_url,
        arguments.database,
        PROFILES[arguments.profile],
        arguments.seed,
        now,
        drop_existing=arguments.drop_existing,
    )
    json.dump(manifest, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
