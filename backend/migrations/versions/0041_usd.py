"""US dollars beside Uzbek so'm: a currency on every amount of a customer's debt.

Revision ID: 0041
Revises: 0040
"""

from pathlib import Path

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0041_usd.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
