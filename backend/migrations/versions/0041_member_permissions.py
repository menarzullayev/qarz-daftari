"""Separate permissions per member of staff: the owner's per-member changes, kept on the membership.

Revision ID: 0041
Revises: 0037
"""

from pathlib import Path

from alembic import op

revision = "0041"
down_revision = "0037"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0041_member_permissions.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
