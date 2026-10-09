"""The free plan: nothing changes in the database; the revision keeps the agreed numbering.

Revision ID: 0038
Revises: 0037
"""

from pathlib import Path

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0038_free_plan.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
