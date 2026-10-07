"""Export jobs, the export purpose of stored files, and their place in erasure and cleanup.

Revision ID: 0022
Revises: 0016
"""

from pathlib import Path

from alembic import op

revision = "0022"
down_revision = "0016"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0022_exports.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
