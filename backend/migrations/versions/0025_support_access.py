"""Support access: an administrator sees one shop for a limited time, and the owner sees that.

Revision ID: 0025
Revises: 0016
"""

from pathlib import Path

from alembic import op

revision = "0025"
down_revision = "0016"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0025_support_access.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
