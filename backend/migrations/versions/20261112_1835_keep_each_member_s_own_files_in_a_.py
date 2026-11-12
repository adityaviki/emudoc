"""Keep each member's own files in a project.

Revision 562af5b541e8, written 2026-11-12.

A member's My files (app/my_files.py): the Word documents and Excel workbooks they keep in a
project, seen by them alone, each with its versions, never changed after, every version with its
malware scan and its view, the PDF its pages are drawn from. The jobs' kinds gain the scan and the
view of a version, and the audit log's actions a file added, given a version, renamed, deleted,
downloaded and copied into the project's library.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "562af5b541e8"
down_revision: str | None = "9e41c7b2d5a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_JOB_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'apply_language', "
    "'export_project', 'sync_connector', 'run_workflow', 'run_automation', 'write_draft', "
    "'receive_mail', 'deliver_webhook', 'run_question_set', 'write_question_report', "
    "'send_digests', 'convert_document'"
)
ADDED_JOB_KINDS = ("view_my_file",)
OLD_ACTIONS = (
    "'sign_up', 'sign_in', 'sign_in_failed', 'sign_in_paused', 'sign_out', 'session_revoked', "
    "'other_sessions_revoked', 'password_changed', 'password_reset_requested', "
    "'password_reset', 'profile_changed', 'email_change_requested', 'email_changed', "
    "'mfa_enabled', 'mfa_disabled', 'account_exported', 'terms_accepted', "
    "'account_deletion_requested', 'account_deletion_cancelled', 'account_purged', "
    "'support_access_granted', 'support_access_revoked', 'support_session_started', "
    "'support_session_ended', 'role_changed', 'plan_changed', 'seats_changed', "
    "'entitlements_changed', 'checkout_started', 'subscription_changed', "
    "'subscription_cancelled', 'cancellation_withdrawn', 'payment_failed', 'pack_bought', "
    "'organization_created', 'organization_renamed', 'organization_deletion_requested', "
    "'organization_deletion_cancelled', 'settings_changed', 'flag_changed', 'audit_exported', "
    "'api_key_created', 'api_key_rolled', 'api_key_revoked', 'webhook_created', "
    "'webhook_changed', 'webhook_rolled', 'webhook_deleted', 'ownership_transferred', "
    "'invitation_sent', 'invitation_revoked', 'invitation_accepted', 'joining_changed', "
    "'member_joined', 'member_left', 'member_removed', 'project_access_changed', "
    "'project_exported', 'template_created', 'template_changed', 'template_deleted', "
    "'fact_checked', 'fact_corrected', 'fact_check_removed', 'facts_exported', "
    "'cross_check_created', 'cross_check_changed', 'cross_check_deleted', 'cross_checks_run', "
    "'check_report_requested', 'check_run_deleted', 'automation_created', 'automation_changed', "
    "'automation_deleted', 'question_set_created', 'question_set_changed', "
    "'question_set_deleted', 'question_set_run', 'question_report_deleted', "
    "'filing_rule_created', 'filing_rule_changed', 'filing_rule_deleted', 'draft_created', "
    "'draft_changed', 'draft_deleted', 'draft_exported', 'draft_template_created', "
    "'draft_template_changed', 'draft_template_deleted', 'mailbox_created', 'mailbox_changed', "
    "'mailbox_deleted', 'connector_created', 'connector_changed', 'connector_deleted', "
    "'project_deleted', 'project_restored', 'project_purged', 'sample_added', 'folder_deleted', "
    "'folder_restored', 'folder_purged', 'document_uploaded', 'document_version_uploaded', "
    "'document_downloaded', 'document_deleted', 'document_restored', 'document_purged', "
    "'document_quarantined', 'document_requeued', 'document_review_changed', "
    "'document_assigned', 'share_link_created', 'share_link_revoked', 'share_link_viewed', "
    "'share_link_downloaded', 'chat_shared', 'chat_unshared', 'chat_exported', "
    "'answer_exported', 'chat_deleted', 'chat_restored', 'chat_purged', 'comment_added', "
    "'comment_deleted', 'thread_resolved', 'thread_reopened', 'database_restored'"
)
ADDED_ACTIONS = (
    "my_file_added",
    "my_file_version_added",
    "my_file_renamed",
    "my_file_deleted",
    "my_file_downloaded",
    "my_file_copied",
)
# Each widened check constraint: its table and column, the values it held, and those it gains.
WIDENED = (
    ("jobs", "kind", OLD_JOB_KINDS, ADDED_JOB_KINDS),
    ("audit_events", "action", OLD_ACTIONS, ADDED_ACTIONS),
)


def upgrade() -> None:
    op.create_table(
        "my_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("origin_document_id", sa.Integer(), nullable=True),
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
        sa.CheckConstraint("kind IN ('docx', 'xlsx')", name="ck_my_files_kind"),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("my_files_org_id_fkey"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["origin_document_id"],
            ["documents.id"],
            name=op.f("my_files_origin_document_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("my_files_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("my_files_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("my_files_pkey")),
    )
    op.create_index(op.f("ix_my_files_org_id"), "my_files", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_my_files_origin_document_id"), "my_files", ["origin_document_id"], unique=False
    )
    op.create_index(
        "ix_my_files_owner", "my_files", ["project_id", "user_id", "updated_at"], unique=False
    )
    op.create_index(op.f("ix_my_files_user_id"), "my_files", ["user_id"], unique=False)
    op.create_table(
        "my_file_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("by_model", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=500), server_default="", nullable=False),
        sa.Column("scan", sa.String(length=20), nullable=False),
        sa.Column("view_state", sa.String(length=20), nullable=True),
        sa.Column(
            "view_page_sizes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("view_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scan IN ('pending', 'passed', 'skipped', 'flagged')", name="ck_my_file_versions_scan"
        ),
        sa.CheckConstraint(
            "view_state IN ('queued', 'ready', 'failed')", name="ck_my_file_versions_view_state"
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["my_files.id"],
            name=op.f("my_file_versions_file_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("my_file_versions_user_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("my_file_versions_pkey")),
        sa.UniqueConstraint("file_id", "number", name=op.f("my_file_versions_file_id_number_key")),
    )
    op.create_index(
        op.f("ix_my_file_versions_user_id"), "my_file_versions", ["user_id"], unique=False
    )
    for table, column, old, added in WIDENED:
        _allow(table, column, old + ", " + ", ".join(f"'{value}'" for value in added))


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'view_my_file'")
    op.execute(
        "DELETE FROM audit_events WHERE action IN ("
        + ", ".join(f"'{action}'" for action in ADDED_ACTIONS)
        + ")"
    )
    for table, column, old, _ in WIDENED:
        _allow(table, column, old)
    op.drop_index(op.f("ix_my_file_versions_user_id"), table_name="my_file_versions")
    op.drop_table("my_file_versions")
    op.drop_index(op.f("ix_my_files_user_id"), table_name="my_files")
    op.drop_index("ix_my_files_owner", table_name="my_files")
    op.drop_index(op.f("ix_my_files_origin_document_id"), table_name="my_files")
    op.drop_index(op.f("ix_my_files_org_id"), table_name="my_files")
    op.drop_table("my_files")


def _allow(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
