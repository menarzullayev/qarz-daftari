"""An administrator reassigns the ownership of a shop whose owner lost the Telegram account.

Revision ID: 0028
Revises: 0027
"""

from pathlib import Path

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0028_admin_owner_reassignment.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
