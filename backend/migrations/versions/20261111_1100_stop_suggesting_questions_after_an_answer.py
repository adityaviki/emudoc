"""Stop suggesting questions after an answer.

Revision 6d0a3f8e1c27, written 2026-11-11.

An answer no longer suggests questions to ask next (the agent's `suggest_follow_ups` is gone, and
the chat its "Ask next"), so the answers lose `follow_ups`. An empty chat's questions, written from
the project's documents, stay. A downgrade adds the column again, every answer suggesting none.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6d0a3f8e1c27"
down_revision: str | None = "4b7d2e91c0a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("chat_messages", "follow_ups")


def downgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("follow_ups", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
    )
