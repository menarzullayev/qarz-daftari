"""Erasing a shop after its waiting period.

Revision ID: 0017
Revises: 0014
"""

from pathlib import Path

from alembic import op

revision = "0017"
down_revision = "0014"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0017_shop_erasure.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
