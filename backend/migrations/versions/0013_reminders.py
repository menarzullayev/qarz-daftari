"""Scheduled work and the reminder schedule.

Revision ID: 0013
Revises: 0010
"""

from pathlib import Path

from alembic import op

revision = "0013"
down_revision = "0010"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0013_reminders.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
