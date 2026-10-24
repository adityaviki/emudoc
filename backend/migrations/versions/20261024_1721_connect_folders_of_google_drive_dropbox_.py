"""Connect folders of Google Drive, Dropbox and OneDrive.

Revision 98934f0604c4, written 2026-10-24.

A connector (app/connectors/) keeps a project in step with a folder of a member's Google Drive,
Dropbox or OneDrive, and goes with the member's membership: the member's grant, encrypted, the
folder, where its files go, how far its changes were read and when it syncs next, and for each
file of the folder its id and version at the service, its path and the document made of it, or
why none was. The audit log's actions gain a connector made, changed and removed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "98934f0604c4"
down_revision: str | None = "2aa96e183a87"
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
    "'mailbox_created', 'mailbox_changed', 'mailbox_deleted'"
)
ADDED_ACTIONS = ("connector_created", "connector_changed", "connector_deleted")
NEW_ACTIONS = OLD_ACTIONS + "".join(f", '{action}'" for action in ADDED_ACTIONS)


def upgrade() -> None:
    op.create_table(
        "connectors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("account", sa.String(length=320), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("folder_ref", sa.String(length=300), nullable=True),
        sa.Column("folder_name", sa.String(length=300), nullable=False),
        sa.Column("target_folder_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_sync_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider IN ('google', 'dropbox', 'onedrive')", name="ck_connectors_provider"
        ),
        sa.CheckConstraint(
            "status IN ('choosing', 'active', 'paused')", name="ck_connectors_status"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("connectors_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("connectors_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_folder_id"],
            ["folders.id"],
            name=op.f("connectors_target_folder_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("connectors_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["memberships.org_id", "memberships.user_id"],
            name=op.f("connectors_org_id_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("connectors_pkey")),
    )
    op.create_index(
        "ix_connectors_due",
        "connectors",
        ["next_sync_at"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(op.f("ix_connectors_org_id"), "connectors", ["org_id"], unique=False)
    op.create_index(op.f("ix_connectors_project_id"), "connectors", ["project_id"], unique=False)
    op.create_index(op.f("ix_connectors_user_id"), "connectors", ["user_id"], unique=False)
    op.create_table(
        "connector_files",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("connector_id", sa.Integer(), nullable=False),
        sa.Column("remote_id", sa.String(length=300), nullable=False),
        sa.Column("version", sa.String(length=300), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["connectors.id"],
            name=op.f("connector_files_connector_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("connector_files_document_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("connector_files_pkey")),
        sa.UniqueConstraint(
            "connector_id", "remote_id", name=op.f("connector_files_connector_id_remote_id_key")
        ),
    )
    op.create_index(
        op.f("ix_connector_files_document_id"), "connector_files", ["document_id"], unique=False
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    actions = ", ".join(f"'{action}'" for action in ADDED_ACTIONS)
    op.execute(sa.text(f"DELETE FROM audit_events WHERE action IN ({actions})"))
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_index(op.f("ix_connector_files_document_id"), table_name="connector_files")
    op.drop_table("connector_files")
    op.drop_index(op.f("ix_connectors_user_id"), table_name="connectors")
    op.drop_index(op.f("ix_connectors_project_id"), table_name="connectors")
    op.drop_index(op.f("ix_connectors_org_id"), table_name="connectors")
    op.drop_index(
        "ix_connectors_due", table_name="connectors", postgresql_where=sa.text("status = 'active'")
    )
    op.drop_table("connectors")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
