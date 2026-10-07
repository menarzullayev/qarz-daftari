"""Ledger indexes that start with the shop, so a query costs what its shop holds (S19.1 load test).

Revision ID: 0020
Revises: 0021
"""

from pathlib import Path

from alembic import op

revision = "0020"
down_revision = "0021"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0020_ledger_shop_indexes.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
