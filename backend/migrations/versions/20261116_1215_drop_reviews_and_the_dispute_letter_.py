"""Drop reviews and the dispute letter written from one.

Revision cc45ffa7565c, written 2026-11-16.

The app is the documents and the agent with its tools (S.6, 2026-11-16): a document's review goes,
its state (in review, approved, disputed), its assignee and who moved it to its state and when,
with the reviews view, an automation's putting what it checked in review for a member, and the
notice of a document assigned. So does the dispute letter a disputed document asked the agent to
write into its member's My files outside a chat: the writings under way or failed go with their
table, and the worker's `write_draft` jobs with their kind, queued or kept. In a chat the agent
still writes a dispute letter into My files when asked, to the project's writing template.

`documents` loses `review`, `assignee_id`, `review_changed_by` and `review_changed_at` with their
checks and indexes; `automations` loses `review` and `assignee_id`; what each member and the
workspace chose for the notice of a document assigned goes. The notices of that kind already in
the centres stay, to read until NOTIFICATION_DAYS take them, so the notifications keep allowing
the kind (PAST_NOTICE_KINDS); the audit log's `document_review_changed` and `document_assigned`
stay, for the entries already written, and so do the usage rows of the kind `draft`, being what
the writings cost.

Going back makes the columns and the table again, empty: no document is in review, no automation
puts anything in review, and the members are told of a document assigned until they choose again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "cc45ffa7565c"
down_revision: str | None = "7e532b9ccb61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_JOB_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'apply_language', "
    "'export_project', 'sync_connector', 'run_workflow', 'run_automation', 'receive_mail', "
    "'deliver_webhook', 'run_question_set', 'write_question_report', 'send_digests', "
    "'convert_document', 'view_my_file'"
)
OLD_JOB_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'apply_language', "
    "'export_project', 'sync_connector', 'run_workflow', 'run_automation', 'write_draft', "
    "'receive_mail', 'deliver_webhook', 'run_question_set', 'write_question_report', "
    "'send_digests', 'convert_document', 'view_my_file'"
)
KIND = "review_assigned"


def upgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'write_draft'")
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", f"kind IN ({NEW_JOB_KINDS})")
    # Its indexes go with it.
    op.drop_table("my_file_writings")

    op.drop_constraint("ck_documents_review_changed_at", "documents", type_="check")
    op.drop_constraint("ck_documents_assignee", "documents", type_="check")
    op.drop_constraint("ck_documents_review", "documents", type_="check")
    op.drop_index("ix_documents_review_changed_by", table_name="documents")
    op.drop_index("ix_documents_review", table_name="documents")
    op.drop_index("ix_documents_assignee_id", table_name="documents")
    # Their foreign keys go with them.
    op.drop_column("documents", "review_changed_at")
    op.drop_column("documents", "review_changed_by")
    op.drop_column("documents", "assignee_id")
    op.drop_column("documents", "review")

    op.drop_constraint("ck_automations_assignee", "automations", type_="check")
    op.drop_constraint("ck_automations_review", "automations", type_="check")
    op.drop_index("ix_automations_assignee_id", table_name="automations")
    op.drop_column("automations", "assignee_id")
    op.drop_column("automations", "review")

    for table in ("users", "organizations"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET notices = notices - :kind WHERE notices ? :kind"
            ).bindparams(kind=KIND)
        )


def downgrade() -> None:
    op.add_column("automations", sa.Column("review", sa.String(length=20), nullable=True))
    op.add_column("automations", sa.Column("assignee_id", sa.Integer(), nullable=True))
    op.create_index("ix_automations_assignee_id", "automations", ["assignee_id"])
    op.create_foreign_key(
        "automations_assignee_id_fkey",
        "automations",
        "users",
        ["assignee_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_automations_review", "automations", "review IN ('differs', 'arrived')"
    )
    op.create_check_constraint(
        "ck_automations_assignee", "automations", "assignee_id IS NULL OR review IS NOT NULL"
    )

    op.add_column("documents", sa.Column("review", sa.String(length=20), nullable=True))
    op.add_column("documents", sa.Column("assignee_id", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("review_changed_by", sa.Integer(), nullable=True))
    op.add_column(
        "documents", sa.Column("review_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "documents_assignee_id_fkey",
        "documents",
        "users",
        ["assignee_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "documents_review_changed_by_fkey",
        "documents",
        "users",
        ["review_changed_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_documents_assignee_id",
        "documents",
        ["assignee_id"],
        postgresql_where=sa.text("assignee_id IS NOT NULL"),
    )
    op.create_index(
        "ix_documents_review",
        "documents",
        ["project_id", "review", "review_changed_at", "id"],
        postgresql_where=sa.text("review IS NOT NULL"),
    )
    op.create_index(
        "ix_documents_review_changed_by",
        "documents",
        ["review_changed_by"],
        postgresql_where=sa.text("review_changed_by IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_documents_review", "documents", "review IN ('to_review', 'approved', 'disputed')"
    )
    op.create_check_constraint(
        "ck_documents_assignee", "documents", "assignee_id IS NULL OR review IS NOT NULL"
    )
    op.create_check_constraint(
        "ck_documents_review_changed_at",
        "documents",
        "(review IS NULL) = (review_changed_at IS NULL)",
    )

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
            name="my_file_writings_org_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="my_file_writings_project_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="my_file_writings_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="my_file_writings_pkey"),
    )
    op.create_index("ix_my_file_writings_org_id", "my_file_writings", ["org_id"])
    op.create_index("ix_my_file_writings_owner", "my_file_writings", ["project_id", "user_id"])
    op.create_index("ix_my_file_writings_user_id", "my_file_writings", ["user_id"])
    op.create_index(
        "ix_my_file_writings_writing",
        "my_file_writings",
        ["org_id"],
        postgresql_where=sa.text("status IN ('queued', 'writing')"),
    )

    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", f"kind IN ({OLD_JOB_KINDS})")
