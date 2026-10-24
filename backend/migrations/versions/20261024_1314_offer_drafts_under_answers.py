"""Offer drafts under answers.

Revision 1dcf4155983a, written 2026-10-24.

An answer to a member who may have a draft written can offer one (`create_draft`,
app/agent/tools.py), which the member has written with a press: the answer keeps the offer, its
kind, instructions and documents, and whether it was written, and the draft written from it until
that is deleted for good, which leaves the answer and its offer as they were.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1dcf4155983a"
down_revision: str | None = "900090aa146e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("draft_offer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("chat_messages", sa.Column("draft_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_chat_messages_draft",
        "chat_messages",
        ["draft_id"],
        unique=False,
        postgresql_where=sa.text("draft_id IS NOT NULL"),
    )
    op.create_foreign_key(
        op.f("chat_messages_draft_id_fkey"),
        "chat_messages",
        "drafts",
        ["draft_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("chat_messages_draft_id_fkey"), "chat_messages", type_="foreignkey")
    op.drop_index(
        "ix_chat_messages_draft",
        table_name="chat_messages",
        postgresql_where=sa.text("draft_id IS NOT NULL"),
    )
    op.drop_column("chat_messages", "draft_id")
    op.drop_column("chat_messages", "draft_offer")
