"""Share answers by link.

Revision 3346e4cbab21, written 2026-10-25.

A share link shows a document or, now, an answer of a chat with its question and the passages it
cites (app/share_links.py): the links gain the answer, gone with it, and their document becomes
one of the two, a check holding each link to exactly one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3346e4cbab21"
down_revision: str | None = "2b20cde30ca9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("share_links", sa.Column("message_id", sa.BigInteger(), nullable=True))
    op.alter_column("share_links", "document_id", existing_type=sa.INTEGER(), nullable=True)
    op.create_index(op.f("ix_share_links_message_id"), "share_links", ["message_id"])
    op.create_foreign_key(
        op.f("share_links_message_id_fkey"),
        "share_links",
        "chat_messages",
        ["message_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_share_links_shows", "share_links", "(document_id IS NULL) <> (message_id IS NULL)"
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM share_links WHERE message_id IS NOT NULL"))
    op.drop_constraint("ck_share_links_shows", "share_links", type_="check")
    op.drop_constraint(op.f("share_links_message_id_fkey"), "share_links", type_="foreignkey")
    op.drop_index(op.f("ix_share_links_message_id"), table_name="share_links")
    op.alter_column("share_links", "document_id", existing_type=sa.INTEGER(), nullable=False)
    op.drop_column("share_links", "message_id")
