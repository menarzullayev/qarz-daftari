"""Open debts, stored: what each unpaid debt has left and when it was promised.

Revision ID: 0026
Revises: 0023
"""

from pathlib import Path

from alembic import op

revision = "0026"
down_revision = "0023"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0026_open_debt.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
