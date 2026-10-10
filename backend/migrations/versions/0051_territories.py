"""The territory reference (region, district, mahalla, street) and a customer's address.

Revision ID: 0051
Revises: 0050
"""

from pathlib import Path

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0051_territories.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
