"""Record what is done in an audit log.

Revision 7c537d5e34f4, written 2026-10-15.

Each organisation now keeps an audit log (app/audit.py): its members' sign-ins and account
changes, the uploads, downloads and deletions of its documents, the sharing of its chats, and
what the operator and the app did to it on their own, one row per action, written in the
transaction of the action. A row names the member and the project and object by id; the
project and the object are not foreign keys, since a row about a deletion outlives what it
names. Nothing is recorded for what happened before this.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c537d5e34f4"
down_revision: str | None = "b2e16e700157"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("target_type", sa.String(length=40), nullable=True),
        sa.Column("target_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('sign_up', 'sign_in', 'sign_in_failed', 'sign_out', 'session_revoked', "
            "'other_sessions_revoked', 'password_changed', 'password_reset_requested', "
            "'password_reset', 'mfa_enabled', 'mfa_disabled', 'account_exported', "
            "'account_deletion_requested', 'account_deletion_cancelled', 'account_purged', "
            "'role_changed', 'plan_changed', 'project_deleted', 'folder_deleted', "
            "'document_uploaded', 'document_downloaded', 'document_deleted', "
            "'document_quarantined', 'document_requeued', 'chat_shared', 'chat_unshared', "
            "'chat_deleted')",
            name="ck_audit_events_action",
        ),
        sa.CheckConstraint(
            "(target_type IS NULL) = (target_id IS NULL)", name="ck_audit_events_target"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("audit_events_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("audit_events_user_id_fkey"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("audit_events_pkey")),
    )
    op.create_index("ix_audit_events_occurred", "audit_events", ["occurred_at"], unique=False)
    op.create_index("ix_audit_events_org_newest", "audit_events", ["org_id", "id"], unique=False)
    op.create_index(op.f("ix_audit_events_user_id"), "audit_events", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_events_user_id"), table_name="audit_events")
    op.drop_index("ix_audit_events_org_newest", table_name="audit_events")
    op.drop_index("ix_audit_events_occurred", table_name="audit_events")
    op.drop_table("audit_events")
