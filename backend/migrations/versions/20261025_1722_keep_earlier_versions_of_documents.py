"""Keep earlier versions of documents.

Revision b244c80cacf0, written 2026-10-25.

A member gives a document a new file (app/versions.py): the document keeps its id, and so its
chats' scope, its review, its share links and its checks, and reads the new file anew, while
the file it had is kept as an earlier version with its text, to open and download. The
documents gain the number of their current version and the bytes of the earlier ones, which
the plan's storage counts; the audit log gains the action that records a new version.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b244c80cacf0"
down_revision: str | None = "5937fbc7e744"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = (
    "'sign_up', 'sign_in', 'sign_in_failed', 'sign_out', 'session_revoked', "
    "'other_sessions_revoked', 'password_changed', 'password_reset_requested', 'password_reset', "
    "'mfa_enabled', 'mfa_disabled', 'account_exported', 'terms_accepted', "
    "'account_deletion_requested', 'account_deletion_cancelled', 'account_purged', 'role_changed', "
    "'plan_changed', 'project_deleted', 'folder_deleted', 'document_uploaded', "
    "'document_downloaded', 'document_deleted', 'document_quarantined', 'document_requeued', "
    "'chat_shared', 'chat_unshared', 'chat_deleted', 'database_restored', 'profile_changed', "
    "'email_change_requested', 'email_changed', 'sign_in_paused', 'organization_created', "
    "'organization_renamed', 'ownership_transferred', 'member_left', 'invitation_sent', "
    "'invitation_revoked', 'invitation_accepted', 'joining_changed', 'member_joined', "
    "'member_removed', 'project_access_changed', 'seats_changed', 'entitlements_changed', "
    "'checkout_started', 'subscription_changed', 'payment_failed', 'pack_bought', "
    "'subscription_cancelled', 'cancellation_withdrawn', 'sample_added', 'project_restored', "
    "'project_purged', 'folder_restored', 'folder_purged', 'document_restored', 'document_purged', "
    "'chat_restored', 'chat_purged', 'settings_changed', 'audit_exported', 'api_key_created', "
    "'api_key_rolled', 'api_key_revoked', 'organization_deletion_requested', "
    "'organization_deletion_cancelled', 'project_exported', 'support_access_granted', "
    "'support_access_revoked', 'support_session_started', 'support_session_ended', 'flag_changed', "
    "'template_created', 'template_changed', 'template_deleted', 'fact_checked', 'fact_corrected', "
    "'fact_check_removed', 'facts_exported', 'cross_check_created', 'cross_check_changed', "
    "'cross_check_deleted', 'cross_checks_run', 'check_report_requested', 'check_run_deleted', "
    "'automation_created', 'automation_changed', 'automation_deleted', 'document_review_changed', "
    "'document_assigned', 'draft_created', 'draft_changed', 'draft_deleted', 'draft_exported', "
    "'draft_template_created', 'draft_template_changed', 'draft_template_deleted', "
    "'mailbox_created', 'mailbox_changed', 'mailbox_deleted', "
    "'connector_created', 'connector_changed', 'connector_deleted', 'webhook_created', "
    "'webhook_changed', 'webhook_rolled', 'webhook_deleted', 'share_link_created', "
    "'share_link_revoked', 'share_link_viewed', 'share_link_downloaded', 'chat_exported', "
    "'answer_exported'"
)
ADDED_ACTIONS = ("document_version_uploaded",)
NEW_ACTIONS = OLD_ACTIONS + "".join(f", '{action}'" for action in ADDED_ACTIONS)


def upgrade() -> None:
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=200), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("scan_passed", sa.Boolean(), nullable=False),
        sa.Column("blocks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("upload_id", sa.Integer(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "replaced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("document_versions_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["uploads.id"],
            name=op.f("document_versions_upload_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("document_versions_pkey")),
        sa.UniqueConstraint(
            "document_id", "number", name=op.f("document_versions_document_id_number_key")
        ),
    )
    op.create_index(
        op.f("ix_document_versions_upload_id"), "document_versions", ["upload_id"], unique=False
    )
    op.add_column(
        "documents", sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False)
    )
    op.add_column(
        "documents",
        sa.Column("versions_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    actions = ", ".join(f"'{action}'" for action in ADDED_ACTIONS)
    op.execute(sa.text(f"DELETE FROM audit_events WHERE action IN ({actions})"))
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_column("documents", "versions_bytes")
    op.drop_column("documents", "version")
    op.drop_index(op.f("ix_document_versions_upload_id"), table_name="document_versions")
    op.drop_table("document_versions")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
