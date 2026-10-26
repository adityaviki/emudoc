"""Keep a saved search's words on its smart folder.

Revision cc9c6837752e, written 2026-10-26.

A smart folder may keep the words of a search (3.10): the library then opens it as the search of
them within the documents it matches. The smart folders there are now keep none, and list their
documents as before.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cc9c6837752e"
down_revision: str | None = "e004b305758e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("smart_folders", sa.Column("query", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("smart_folders", "query")
