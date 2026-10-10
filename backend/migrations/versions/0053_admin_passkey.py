"""An administrator's passkeys: the third further way in.

Revision ID: 0053
Revises: 0052
"""

from pathlib import Path

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0053_admin_passkey.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
