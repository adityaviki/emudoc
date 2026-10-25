"""Add share links.

Revision 2b20cde30ca9, written 2026-10-25.

A member shows a document to someone outside the organisation by a link that needs no account
(app/share_links.py): a share link is the document, its maker, its token's SHA-256, its
password's hash, when it expires and how often it was opened, gone with its maker's membership,
the document, the project and the organisation; a view is one opening of it in a browser, the
SHA-256 of its cookie's token and when it ends. The organisations gain the switch their owners
and admins turn links off with, on by default, and the audit log's actions gain a link made,
revoked, opened and its original downloaded.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2b20cde30ca9"
down_revision: str | None = "bb533acac8ec"
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
    "'webhook_changed', 'webhook_rolled', 'webhook_deleted'"
)
ADDED_ACTIONS = (
    "share_link_created",
    "share_link_revoked",
    "share_link_viewed",
    "share_link_downloaded",
)
NEW_ACTIONS = OLD_ACTIONS + "".join(f", '{action}'" for action in ADDED_ACTIONS)


def upgrade() -> None:
    op.create_table(
        "share_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("views", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("share_links_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["memberships.org_id", "memberships.user_id"],
            name=op.f("share_links_org_id_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("share_links_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("share_links_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("share_links_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("share_links_pkey")),
        sa.UniqueConstraint("token_hash", name=op.f("share_links_token_hash_key")),
    )
    op.create_index(op.f("ix_share_links_document_id"), "share_links", ["document_id"])
    op.create_index("ix_share_links_expires_at", "share_links", ["expires_at"])
    op.create_index(op.f("ix_share_links_org_id"), "share_links", ["org_id"])
    op.create_index(op.f("ix_share_links_project_id"), "share_links", ["project_id"])
    op.create_index(op.f("ix_share_links_user_id"), "share_links", ["user_id"])
    op.create_table(
        "share_views",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("link_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["link_id"],
            ["share_links.id"],
            name=op.f("share_views_link_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("share_views_pkey")),
        sa.UniqueConstraint("token_hash", name=op.f("share_views_token_hash_key")),
    )
    op.create_index(op.f("ix_share_views_expires_at"), "share_views", ["expires_at"])
    op.create_index(op.f("ix_share_views_link_id"), "share_views", ["link_id"])
    op.add_column(
        "organizations",
        sa.Column("share_links", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    actions = ", ".join(f"'{action}'" for action in ADDED_ACTIONS)
    op.execute(sa.text(f"DELETE FROM audit_events WHERE action IN ({actions})"))
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_column("organizations", "share_links")
    op.drop_index(op.f("ix_share_views_link_id"), table_name="share_views")
    op.drop_index(op.f("ix_share_views_expires_at"), table_name="share_views")
    op.drop_table("share_views")
    op.drop_index(op.f("ix_share_links_user_id"), table_name="share_links")
    op.drop_index(op.f("ix_share_links_project_id"), table_name="share_links")
    op.drop_index(op.f("ix_share_links_org_id"), table_name="share_links")
    op.drop_index("ix_share_links_expires_at", table_name="share_links")
    op.drop_index(op.f("ix_share_links_document_id"), table_name="share_links")
    op.drop_table("share_links")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
