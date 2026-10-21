"""Let a member let Emudoc's support see their account, and an operator view it.

Revision f6d05e27c762, written 2026-10-21.

A member may let Emudoc's support see their account in one of their organisations until a time
(app/support.py): a row of `support_grants`, which goes with the membership. An operator's
session viewing such an account holds the leave it rests on and when the view ends, two columns
of `sessions`, null for every session from before. The audit log's actions gain the leave, its
withdrawal, and a view's start and end.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6d05e27c762"
down_revision: str | None = "4bd26bda783e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = (
    "'sign_up', 'sign_in', 'sign_in_failed', 'sign_out', 'session_revoked', "
    "'other_sessions_revoked', 'password_changed', 'password_reset_requested', "
    "'password_reset', 'mfa_enabled', 'mfa_disabled', 'account_exported', 'terms_accepted', "
    "'account_deletion_requested', 'account_deletion_cancelled', 'account_purged', "
    "'role_changed', 'plan_changed', 'project_deleted', 'folder_deleted', 'document_uploaded', "
    "'document_downloaded', 'document_deleted', 'document_quarantined', 'document_requeued', "
    "'chat_shared', 'chat_unshared', 'chat_deleted', 'database_restored', 'profile_changed', "
    "'email_change_requested', 'email_changed', 'sign_in_paused', 'organization_created', "
    "'organization_renamed', 'ownership_transferred', 'member_left', 'invitation_sent', "
    "'invitation_revoked', 'invitation_accepted', 'joining_changed', 'member_joined', "
    "'member_removed', 'project_access_changed', 'seats_changed', 'entitlements_changed', "
    "'checkout_started', 'subscription_changed', 'payment_failed', 'pack_bought', "
    "'subscription_cancelled', 'cancellation_withdrawn', 'sample_added', 'project_restored', "
    "'project_purged', 'folder_restored', 'folder_purged', 'document_restored', "
    "'document_purged', 'chat_restored', 'chat_purged', 'settings_changed', 'audit_exported', "
    "'api_key_created', 'api_key_rolled', 'api_key_revoked', 'organization_deletion_requested', "
    "'organization_deletion_cancelled', 'project_exported'"
)
SUPPORT_ACTIONS = (
    "'support_access_granted', 'support_access_revoked', 'support_session_started', "
    "'support_session_ended'"
)
NEW_ACTIONS = f"{OLD_ACTIONS}, {SUPPORT_ACTIONS}"


def upgrade() -> None:
    op.create_table(
        "support_grants",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["memberships.org_id", "memberships.user_id"],
            name=op.f("support_grants_org_id_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("support_grants_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("support_grants_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("support_grants_pkey")),
        sa.UniqueConstraint("org_id", "user_id", name=op.f("support_grants_org_id_user_id_key")),
    )
    op.create_index(op.f("ix_support_grants_user_id"), "support_grants", ["user_id"])
    op.add_column("sessions", sa.Column("support_grant_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "sessions", sa.Column("support_ends_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_sessions_support_grant_id",
        "sessions",
        ["support_grant_id"],
        postgresql_where=sa.text("support_grant_id IS NOT NULL"),
    )
    op.create_foreign_key(
        op.f("sessions_support_grant_id_fkey"),
        "sessions",
        "support_grants",
        ["support_grant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(sa.text(f"DELETE FROM audit_events WHERE action IN ({SUPPORT_ACTIONS})"))
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_constraint(op.f("sessions_support_grant_id_fkey"), "sessions", type_="foreignkey")
    op.drop_index("ix_sessions_support_grant_id", table_name="sessions")
    op.drop_column("sessions", "support_ends_at")
    op.drop_column("sessions", "support_grant_id")
    op.drop_index(op.f("ix_support_grants_user_id"), table_name="support_grants")
    op.drop_table("support_grants")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
