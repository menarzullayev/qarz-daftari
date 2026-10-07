"""Fixes from the security review: definer functions keep temporary tables out; the waiting period
before a shop is erased is held by the database.

Revision ID: 0021
Revises: 0011
"""

from pathlib import Path

from alembic import op

revision = "0021"
down_revision = "0011"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0021_security_review.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
