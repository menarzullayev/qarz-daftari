"""The network between shops: links, orders, delivery notes and payments that both sides confirm.

Revision ID: 0045
Revises: 0043
"""

from pathlib import Path

from alembic import op

revision = "0045"
down_revision = "0043"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0045_network.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
