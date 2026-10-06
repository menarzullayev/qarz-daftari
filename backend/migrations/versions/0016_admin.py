"""The administrator's side: second factor, admin sessions, audit, cross-shop functions.

Revision ID: 0016
Revises: 0011
"""

from pathlib import Path

from alembic import op

revision = "0016"
down_revision = "0011"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0016_admin.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
