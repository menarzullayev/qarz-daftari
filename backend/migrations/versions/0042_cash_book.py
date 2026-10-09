"""The cash book: entries of income and expense, and their categories.

Revision ID: 0042
Revises: 0041
"""

from pathlib import Path

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0042_cash_book.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
