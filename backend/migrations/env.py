"""Alembic environment. Forward-only migrations, run by the owner role (QD_MIGRATION_URL)."""

import os

from alembic import context
from sqlalchemy import create_engine, pool


def _url() -> str:
    url = os.environ.get("QD_MIGRATION_URL")
    if not url:
        raise RuntimeError("QD_MIGRATION_URL is not set")
    # SQLAlchemy needs the driver named; plain postgresql:// URLs mean psycopg 3 here.
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported")
run_migrations_online()
