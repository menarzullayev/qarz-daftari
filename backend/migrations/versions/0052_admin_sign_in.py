"""A service key and a password: two more ways for an administrator to come in.

Revision ID: 0052
Revises: 0051
"""

from pathlib import Path

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0052_admin_sign_in.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
