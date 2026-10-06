"""Catalog items can be merged: a hidden alias remembers the item it stands for.

Revision ID: 0007
Revises: 0005
"""

from pathlib import Path

from alembic import op

revision = "0007"
# 0006 belongs to a parallel branch; the chain is joined when the branches are merged.
down_revision = "0005"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0007_catalog_merge.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
