"""Pin chats to the top of the chat list.

Revision 8fc77c1e35a0, written 2026-10-25.

A member pins a chat they read, their own or one shared with the project, and their pinned chats
lead their chat list, newest pin first. A pin is the member's alone, so a chat shared with the
project is pinned by each of its readers for themselves: a row per chat and member, which goes
with either.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8fc77c1e35a0"
down_revision: str | None = "0c39e089edf9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_pins",
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "pinned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name=op.f("chat_pins_chat_id_fkey"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("chat_pins_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("chat_id", "user_id", name=op.f("chat_pins_pkey")),
    )
    op.create_index(op.f("ix_chat_pins_user_id"), "chat_pins", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_pins_user_id"), table_name="chat_pins")
    op.drop_table("chat_pins")
