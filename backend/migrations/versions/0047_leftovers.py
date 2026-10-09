"""A delivery note is compared line by line when it is received; the documents list finds a state by index.

Revision ID: 0047
Revises: 0046
"""

from pathlib import Path

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0047_leftovers.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
