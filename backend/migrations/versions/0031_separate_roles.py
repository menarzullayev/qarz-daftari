"""Separate database roles for the application, the administrators' side and the worker (finding 11).

Revision ID: 0031
Revises: 0030
"""

from pathlib import Path

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0031_separate_roles.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
