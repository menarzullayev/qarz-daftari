"""The state of the operations watch: what is firing, and the samples it compares (DEC-078).

Revision ID: 0035
Revises: 0034
"""

from pathlib import Path

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0035_ops_alerts.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
