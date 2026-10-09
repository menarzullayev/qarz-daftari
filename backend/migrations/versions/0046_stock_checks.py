"""The worker may count where the stock's kept figures differ from their ledgers (operations watch).

Revision ID: 0046
Revises: 0044
"""

from pathlib import Path

from alembic import op

revision = "0046"
down_revision = "0044"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0046_stock_checks.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
