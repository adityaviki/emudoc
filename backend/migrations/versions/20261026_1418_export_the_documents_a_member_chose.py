"""Export the documents a member chose.

Revision e004b305758e, written 2026-10-26.

A member may export the documents they chose together in the library rather than the whole
project (3.9): the export keeps their ids, and the exports there are now, which have none, were
each of a whole project.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e004b305758e"
down_revision: str | None = "1c8fe42c43c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project_exports", sa.Column("document_ids", postgresql.ARRAY(sa.Integer()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("project_exports", "document_ids")
