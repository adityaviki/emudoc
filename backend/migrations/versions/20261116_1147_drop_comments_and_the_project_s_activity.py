"""Drop comments and the project's activity.

Revision 7e532b9ccb61, written 2026-11-16.

The app is the documents and the agent with its tools (S.6, 2026-11-16): a document's threads of
comments go, with the mentions written in them, the notices of a member mentioned and of a reply
in a thread, and the project's activity, which was a view of its audit log and kept nothing of
its own. The threads go with their table and the comments with theirs, and what each member and
the workspace chose for the two kinds of notice with them. The notices of those kinds already in
the centres stay, to read until NOTIFICATION_DAYS take them, so the notifications keep allowing
the kinds (PAST_NOTICE_KINDS); the audit log's `comment_added`, `comment_deleted`,
`thread_resolved` and `thread_reopened` stay, for the entries already written.

Going back makes the two tables again, empty; the members' choices for the two kinds are lost,
so they are told both until they choose again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7e532b9ccb61"
down_revision: str | None = "c65165e94621"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("mentioned", "thread_replied")


def upgrade() -> None:
    # Their indexes go with them.
    op.drop_table("comments")
    op.drop_table("comment_threads")
    for table in ("users", "organizations"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET notices = notices - CAST(:kinds AS text[]) "
                "WHERE notices ?| CAST(:kinds AS text[])"
            ).bindparams(kinds=list(KINDS))
        )


def downgrade() -> None:
    op.create_table(
        "comment_threads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("passage_id", sa.BigInteger(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "passage_id IS NULL OR quote IS NOT NULL", name="ck_comment_threads_quote"
        ),
        sa.CheckConstraint(
            "resolved_by IS NULL OR resolved_at IS NOT NULL", name="ck_comment_threads_resolved"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("comment_threads_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["passage_id"],
            ["passages.id"],
            name=op.f("comment_threads_passage_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"],
            ["users.id"],
            name=op.f("comment_threads_resolved_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("comment_threads_pkey")),
    )
    op.create_index("ix_comment_threads_document", "comment_threads", ["document_id", "id"])
    op.create_index(
        "ix_comment_threads_passage",
        "comment_threads",
        ["passage_id"],
        postgresql_where=sa.text("passage_id IS NOT NULL"),
    )
    op.create_index(
        "ix_comment_threads_resolved_by",
        "comment_threads",
        ["resolved_by"],
        postgresql_where=sa.text("resolved_by IS NOT NULL"),
    )
    op.create_table(
        "comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name=op.f("comments_author_id_fkey"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["comment_threads.id"],
            name=op.f("comments_thread_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("comments_pkey")),
    )
    op.create_index(
        "ix_comments_author",
        "comments",
        ["author_id"],
        postgresql_where=sa.text("author_id IS NOT NULL"),
    )
    op.create_index("ix_comments_thread", "comments", ["thread_id", "id"])
