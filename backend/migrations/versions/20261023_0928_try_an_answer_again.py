"""Number the tries at an answer.

Revision 11f7c94e23c2, written 2026-10-23.

A stopped or failed answer can be tried again in its place: the row is emptied and a new job
writes it. The run of the earlier try may still be waiting on the model, or its job may still
be queued, so each try is numbered and its job carries the number: a run writes events and
settles the row only while the row holds its try (app/answers.py). Every answer so far is its
first try.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "11f7c94e23c2"
down_revision: str | None = "6157f4753b44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "attempt")
