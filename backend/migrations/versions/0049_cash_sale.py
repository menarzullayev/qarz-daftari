"""A sale for cash without a customer: a sixth kind of stock document, and its income in the cash book.

Revision ID: 0049
Revises: 0048
"""

from pathlib import Path

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0049_cash_sale.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
