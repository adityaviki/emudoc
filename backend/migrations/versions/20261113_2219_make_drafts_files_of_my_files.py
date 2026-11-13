"""Make the drafts the agent writes Word files of their asker's My files.

Revision c65165e94621, written 2026-11-13.

A draft was a Markdown text the project's members edited in the page, kept with its versions in
`drafts` and `draft_versions`, and an answer could offer one (`chat_messages.draft_offer`, with
`draft_id` the draft its member's press wrote). Now the agent writes a Word document into the
asker's own My files: in a chat with `create_document`, to the project's template of the kind,
and from a disputed document's review by a worker's `write_draft` job (app/writings.py), whose
writing is a row of `my_file_writings` while it is under way, and once failed until its member
tries it again or dismisses it.

No install holds a draft before the first public release, so the drafts are dropped, not turned
into files: a development database's are lost. The `write_draft` jobs not yet run go with them,
their payload naming a draft. The templates (`draft_templates`), the audit actions `draft_*`, the
usage kind `draft` and the job kind `write_draft` stay, for the rows from before and for the
writings.

Going back makes the empty tables and columns again; the writings under way are lost.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c65165e94621"
down_revision: str | None = "63b78931f8dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'write_draft' AND status IN ('queued', 'running')")
    op.drop_index(
        "ix_chat_messages_draft",
        table_name="chat_messages",
        postgresql_where="(draft_id IS NOT NULL)",
    )
    op.drop_constraint("chat_messages_draft_id_fkey", "chat_messages", type_="foreignkey")
    op.drop_column("chat_messages", "draft_id")
    op.drop_column("chat_messages", "draft_offer")
    op.drop_index("ix_draft_versions_user_id", table_name="draft_versions")
    op.drop_table("draft_versions")
    op.drop_index("ix_drafts_created_by", table_name="drafts")
    op.drop_index("ix_drafts_org_id", table_name="drafts")
    op.drop_index("ix_drafts_project", table_name="drafts")
    op.drop_index("ix_drafts_run_id", table_name="drafts")
    op.drop_index("ix_drafts_writing", table_name="drafts")
    op.drop_table("drafts")

    op.create_table(
        "my_file_writings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column(
            "documents",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('reply_email', 'dispute_letter', 'summary_memo', 'comparison_table', "
            "'report')",
            name="ck_my_file_writings_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'writing', 'failed')", name="ck_my_file_writings_status"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("my_file_writings_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("my_file_writings_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("my_file_writings_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("my_file_writings_pkey")),
    )
    op.create_index(op.f("ix_my_file_writings_org_id"), "my_file_writings", ["org_id"])
    op.create_index("ix_my_file_writings_owner", "my_file_writings", ["project_id", "user_id"])
    op.create_index(op.f("ix_my_file_writings_user_id"), "my_file_writings", ["user_id"])
    op.create_index(
        "ix_my_file_writings_writing",
        "my_file_writings",
        ["org_id"],
        postgresql_where=sa.text("status IN ('queued', 'writing')"),
    )


def downgrade() -> None:
    op.drop_index("ix_my_file_writings_writing", table_name="my_file_writings")
    op.drop_index(op.f("ix_my_file_writings_user_id"), table_name="my_file_writings")
    op.drop_index("ix_my_file_writings_owner", table_name="my_file_writings")
    op.drop_index(op.f("ix_my_file_writings_org_id"), table_name="my_file_writings")
    op.drop_table("my_file_writings")

    op.create_table(
        "drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("instructions", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "documents",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("notice", sa.Text(), nullable=True),
        sa.Column(
            "usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('reply_email', 'dispute_letter', 'summary_memo', 'comparison_table', "
            "'report')",
            name="ck_drafts_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'writing', 'done', 'failed')", name="ck_drafts_status"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="drafts_created_by_fkey", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="drafts_org_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="drafts_project_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["workflow_runs.id"], name="drafts_run_id_fkey", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="drafts_pkey"),
    )
    op.create_index("ix_drafts_created_by", "drafts", ["created_by"])
    op.create_index("ix_drafts_org_id", "drafts", ["org_id"])
    op.create_index("ix_drafts_project", "drafts", ["project_id", "id"])
    op.create_index("ix_drafts_run_id", "drafts", ["run_id"])
    op.create_index(
        "ix_drafts_writing",
        "drafts",
        ["org_id"],
        postgresql_where=sa.text("status IN ('queued', 'writing')"),
    )
    op.create_table(
        "draft_versions",
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column(
            "sources",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("by_model", sa.Boolean(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["drafts.id"], name="draft_versions_draft_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="draft_versions_user_id_fkey", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("draft_id", "number", name="draft_versions_pkey"),
    )
    op.create_index("ix_draft_versions_user_id", "draft_versions", ["user_id"])
    op.add_column(
        "chat_messages",
        sa.Column("draft_offer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("chat_messages", sa.Column("draft_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "chat_messages_draft_id_fkey",
        "chat_messages",
        "drafts",
        ["draft_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_chat_messages_draft",
        "chat_messages",
        ["draft_id"],
        postgresql_where=sa.text("draft_id IS NOT NULL"),
    )
