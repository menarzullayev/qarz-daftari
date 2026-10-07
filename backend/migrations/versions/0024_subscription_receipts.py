"""Subscription receipts: stated months, the administrator's queue and decision, duplicates, retention.

Revision ID: 0024
Revises: 0022
"""

from pathlib import Path

from alembic import op

revision = "0024"
down_revision = "0022"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0024_subscription_receipts.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
