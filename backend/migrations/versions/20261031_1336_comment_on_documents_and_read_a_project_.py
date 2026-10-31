"""Comment on documents and read a project's activity.

Revision 7c3cbe4fc83f, written 2026-10-31.

A document's comments (app/comments.py) are threads, each about the document as a whole or a
passage of it, whose words, page and version the thread keeps once the passage goes with a new
reading; each comment is a member's words, the members it mentions written in them. The notices
gain a member mentioned and a reply in a thread they wrote in, and the audit log's actions a
comment added and deleted and a thread resolved and opened again, which the project's activity
(app/activity.py) reads with the rest of what was done in it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c3cbe4fc83f"
down_revision: str | None = "28ce2d5915d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_NOTICE_KINDS = (
    "'upload_indexed', 'quota', 'project_export', 'checks_run', 'review_assigned', "
    "'report_ready', 'weekly_digest'"
)
ADDED_NOTICE_KINDS = ("mentioned", "thread_replied")
OLD_ACTIONS = (
    "'sign_up', 'sign_in', 'sign_in_failed', 'sign_in_paused', 'sign_out', 'session_revoked', "
    "'other_sessions_revoked', 'password_changed', 'password_reset_requested', 'password_reset', "
    "'profile_changed', 'email_change_requested', 'email_changed', 'mfa_enabled', 'mfa_disabled', "
    "'account_exported', 'terms_accepted', 'account_deletion_requested', "
    "'account_deletion_cancelled', 'account_purged', 'support_access_granted', "
    "'support_access_revoked', 'support_session_started', 'support_session_ended', "
    "'role_changed', 'plan_changed', 'seats_changed', 'entitlements_changed', 'checkout_started', "
    "'subscription_changed', 'subscription_cancelled', 'cancellation_withdrawn', "
    "'payment_failed', 'pack_bought', 'organization_created', 'organization_renamed', "
    "'organization_deletion_requested', 'organization_deletion_cancelled', 'settings_changed', "
    "'flag_changed', 'audit_exported', 'api_key_created', 'api_key_rolled', 'api_key_revoked', "
    "'webhook_created', 'webhook_changed', 'webhook_rolled', 'webhook_deleted', "
    "'ownership_transferred', 'invitation_sent', 'invitation_revoked', 'invitation_accepted', "
    "'joining_changed', 'member_joined', 'member_left', 'member_removed', "
    "'project_access_changed', 'project_exported', 'template_created', 'template_changed', "
    "'template_deleted', 'fact_checked', 'fact_corrected', 'fact_check_removed', "
    "'facts_exported', 'cross_check_created', 'cross_check_changed', 'cross_check_deleted', "
    "'cross_checks_run', 'check_report_requested', 'check_run_deleted', 'automation_created', "
    "'automation_changed', 'automation_deleted', 'filing_rule_created', 'filing_rule_changed', "
    "'filing_rule_deleted', 'draft_created', 'draft_changed', 'draft_deleted', 'draft_exported', "
    "'draft_template_created', 'draft_template_changed', 'draft_template_deleted', "
    "'mailbox_created', 'mailbox_changed', 'mailbox_deleted', 'connector_created', "
    "'connector_changed', 'connector_deleted', 'project_deleted', 'project_restored', "
    "'project_purged', 'sample_added', 'folder_deleted', 'folder_restored', 'folder_purged', "
    "'document_uploaded', 'document_version_uploaded', 'document_downloaded', 'document_deleted', "
    "'document_restored', 'document_purged', 'document_quarantined', 'document_requeued', "
    "'document_review_changed', 'document_assigned', 'share_link_created', 'share_link_revoked', "
    "'share_link_viewed', 'share_link_downloaded', 'chat_shared', 'chat_unshared', "
    "'chat_exported', 'answer_exported', 'chat_deleted', 'chat_restored', 'chat_purged', "
    "'database_restored', 'question_set_created', 'question_set_changed', "
    "'question_set_deleted', 'question_set_run', 'question_report_deleted'"
)
ADDED_ACTIONS = ("comment_added", "comment_deleted", "thread_resolved", "thread_reopened")
# Each widened check constraint: its table and column, the values it held, and those it gains.
WIDENED = (
    ("notifications", "kind", OLD_NOTICE_KINDS, ADDED_NOTICE_KINDS),
    ("audit_events", "action", OLD_ACTIONS, ADDED_ACTIONS),
)


def upgrade() -> None:
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
    for table, column, old, added in WIDENED:
        _check(table, column, old + "".join(f", '{value}'" for value in added))


def downgrade() -> None:
    for table, column, old, added in WIDENED:
        op.execute(
            sa.text(f"DELETE FROM {table} WHERE {column} = ANY(:added)").bindparams(
                added=list(added)
            )
        )
        _check(table, column, old)
    op.drop_index("ix_comments_thread", table_name="comments")
    op.drop_index(
        "ix_comments_author",
        table_name="comments",
        postgresql_where=sa.text("author_id IS NOT NULL"),
    )
    op.drop_table("comments")
    op.drop_index(
        "ix_comment_threads_resolved_by",
        table_name="comment_threads",
        postgresql_where=sa.text("resolved_by IS NOT NULL"),
    )
    op.drop_index(
        "ix_comment_threads_passage",
        table_name="comment_threads",
        postgresql_where=sa.text("passage_id IS NOT NULL"),
    )
    op.drop_index("ix_comment_threads_document", table_name="comment_threads")
    op.drop_table("comment_threads")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
