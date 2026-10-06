"""Date change requests: a decline reason, the chat question for the date, an index of open requests.

Applied after 0013 and 0014, which reached the main branch first: the numbers name the stories' order, the
revisions below the order in which a database is built.

Revision ID: 0011
Revises: 0014
"""

from pathlib import Path

from alembic import op

revision = "0011"
down_revision = "0014"
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0011_date_requests.sql"


def upgrade() -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cursor:
        cursor.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only")
