"""Export a project.

Revision edada3bbf979, written 2026-10-21.

A member asks for a copy of a project, which the worker makes as one ZIP file (app/export.py):
`project_exports` holds each, its member's alone and going with them, until it expires. The
jobs gain the kind `export_project`, the notices `project_export` (that it is ready, or could not
be made), and the audit log the action `project_exported`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "edada3bbf979"
down_revision: str | None = "d3bf731e93db"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = (
    "'sign_up', 'sign_in', 'sign_in_failed', 'sign_out', 'session_revoked', "
    "'other_sessions_revoked', 'password_changed', 'password_reset_requested', "
    "'password_reset', 'mfa_enabled', 'mfa_disabled', 'account_exported', 'terms_accepted', "
    "'account_deletion_requested', 'account_deletion_cancelled', 'account_purged', "
    "'role_changed', 'plan_changed', 'project_deleted', 'folder_deleted', "
    "'document_uploaded', 'document_downloaded', 'document_deleted', "
    "'document_quarantined', 'document_requeued', 'chat_shared', 'chat_unshared', "
    "'chat_deleted', 'database_restored', 'profile_changed', 'email_change_requested', "
    "'email_changed', 'sign_in_paused', 'organization_created', 'organization_renamed', "
    "'ownership_transferred', 'member_left', 'invitation_sent', 'invitation_revoked', "
    "'invitation_accepted', 'joining_changed', 'member_joined', 'member_removed', "
    "'project_access_changed', 'seats_changed', 'entitlements_changed', 'checkout_started', "
    "'subscription_changed', 'payment_failed', 'pack_bought', 'subscription_cancelled', "
    "'cancellation_withdrawn', 'sample_added', 'project_restored', 'project_purged', "
    "'folder_restored', 'folder_purged', 'document_restored', 'document_purged', "
    "'chat_restored', 'chat_purged', 'settings_changed', 'audit_exported', "
    "'api_key_created', 'api_key_rolled', 'api_key_revoked', "
    "'organization_deletion_requested', 'organization_deletion_cancelled'"
)
NEW_ACTIONS = f"{OLD_ACTIONS}, 'project_exported'"
OLD_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'sync_connector', "
    "'run_workflow', 'apply_language'"
)
NEW_KINDS = f"{OLD_KINDS}, 'export_project'"
OLD_NOTICES = "'upload_indexed', 'quota'"
NEW_NOTICES = f"{OLD_NOTICES}, 'project_export'"


def upgrade() -> None:
    op.create_table(
        "project_exports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("documents", sa.Integer(), server_default="0", nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'ready', 'failed')", name="ck_project_exports_status"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="project_exports_project_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="project_exports_user_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="project_exports_pkey"),
    )
    op.create_index("ix_project_exports_project_user", "project_exports", ["project_id", "user_id"])
    op.create_index("ix_project_exports_user_id", "project_exports", ["user_id"])
    _check("audit_events", "action", NEW_ACTIONS)
    _check("jobs", "kind", NEW_KINDS)
    _check("notifications", "kind", NEW_NOTICES)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM notifications WHERE kind = 'project_export'"))
    _check("notifications", "kind", OLD_NOTICES)
    op.execute(sa.text("DELETE FROM jobs WHERE kind = 'export_project'"))
    _check("jobs", "kind", OLD_KINDS)
    op.execute(sa.text("DELETE FROM audit_events WHERE action = 'project_exported'"))
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_index("ix_project_exports_user_id", table_name="project_exports")
    op.drop_index("ix_project_exports_project_user", table_name="project_exports")
    op.drop_table("project_exports")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
