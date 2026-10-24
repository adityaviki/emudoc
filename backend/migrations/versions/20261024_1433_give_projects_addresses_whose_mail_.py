"""Give projects addresses whose mail becomes their documents.

Revision 1db4bf8f1b2b, written 2026-10-24.

A project's address (app/inbound.py) is a token of its own at INBOUND_DOMAIN, with the senders it
takes mail from and the folder what arrives goes into, one a project; what became of each message
sent to it, its sender and subject, the documents kept and the files rejected, or why it was
turned away, is kept a month for the members to read, once per delivery. The audit log's actions
gain an address made, changed and taken away.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1db4bf8f1b2b"
down_revision: str | None = "1dcf4155983a"
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
    "'draft_template_created', 'draft_template_changed', 'draft_template_deleted'"
)
ADDED_ACTIONS = ("mailbox_created", "mailbox_changed", "mailbox_deleted")
NEW_ACTIONS = OLD_ACTIONS + "".join(f", '{action}'" for action in ADDED_ACTIONS)


def upgrade() -> None:
    op.create_table(
        "inbound_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("delivery", sa.String(length=200), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sender", sa.String(length=320), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("kept", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "rejected",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("upload_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('kept', 'refused', 'empty')", name="ck_inbound_messages_outcome"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("inbound_messages_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("inbound_messages_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["uploads.id"],
            name=op.f("inbound_messages_upload_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("inbound_messages_user_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("inbound_messages_pkey")),
        sa.UniqueConstraint(
            "project_id", "delivery", name=op.f("inbound_messages_project_id_delivery_key")
        ),
    )
    op.create_index(
        op.f("ix_inbound_messages_org_id"), "inbound_messages", ["org_id"], unique=False
    )
    op.create_index(
        "ix_inbound_messages_project_received",
        "inbound_messages",
        ["project_id", "received_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inbound_messages_received_at"), "inbound_messages", ["received_at"], unique=False
    )
    op.create_index(
        op.f("ix_inbound_messages_user_id"), "inbound_messages", ["user_id"], unique=False
    )
    op.create_table(
        "project_mailboxes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("senders", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("folder_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("project_mailboxes_created_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["folder_id"],
            ["folders.id"],
            name=op.f("project_mailboxes_folder_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("project_mailboxes_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("project_mailboxes_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("project_mailboxes_pkey")),
        sa.UniqueConstraint("project_id", name=op.f("project_mailboxes_project_id_key")),
        sa.UniqueConstraint("token", name=op.f("project_mailboxes_token_key")),
    )
    op.create_index(
        op.f("ix_project_mailboxes_org_id"), "project_mailboxes", ["org_id"], unique=False
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    actions = ", ".join(f"'{action}'" for action in ADDED_ACTIONS)
    op.execute(sa.text(f"DELETE FROM audit_events WHERE action IN ({actions})"))
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_index(op.f("ix_project_mailboxes_org_id"), table_name="project_mailboxes")
    op.drop_table("project_mailboxes")
    op.drop_index(op.f("ix_inbound_messages_user_id"), table_name="inbound_messages")
    op.drop_index(op.f("ix_inbound_messages_received_at"), table_name="inbound_messages")
    op.drop_index("ix_inbound_messages_project_received", table_name="inbound_messages")
    op.drop_index(op.f("ix_inbound_messages_org_id"), table_name="inbound_messages")
    op.drop_table("inbound_messages")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
