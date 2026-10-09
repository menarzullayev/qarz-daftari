"""Four more languages: Uzbek Cyrillic, Tajik, Karakalpak and English beside Uzbek and Russian.

Revision ID: 0044
Revises: 0042
"""

from pathlib import Path

from alembic import op

revision = "0044"
down_revision = "0042"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0044_languages.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
