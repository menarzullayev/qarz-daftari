"""Import: the states and claim of worker steps, indexes, and import files in the retention job.

Revision ID: 0023
Revises: 0024
"""

from pathlib import Path

from alembic import op

revision = "0023"
down_revision = "0024"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0023_import.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
