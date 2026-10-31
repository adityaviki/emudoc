"""Keep the code of the language a document's words were detected in.

Revision f7c88e6c67de, written 2026-10-31.

The enrich stage reads a document's dates and numbers by the way its language writes them (3.13,
app/locales.py). `read_language` is a text search configuration, and `simple` for every language
the database cannot stem, so Polish and Czech read alike there: `read_language_code` keeps the
ISO 639 code the detection named. The documents there are now were parsed before it and keep
null until they are parsed again; their facts are read by their language in force meanwhile.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7c88e6c67de"
down_revision: str | None = "851c9990f203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("read_language_code", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "read_language_code")
