"""Chat questions for payment notices, and indexes for notices and stored files.

Revision ID: 0012
Revises: 0020
"""

from pathlib import Path

from alembic import op

revision = "0012"
down_revision = "0020"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0012_payment_notices.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
