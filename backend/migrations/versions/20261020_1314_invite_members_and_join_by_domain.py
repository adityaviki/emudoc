"""Invite members and join by domain.

Revision 2520d37473ef, written 2026-10-20.

Invitations are sent at last. Accepting or revoking one deletes it, so a row is an invitation
still open or one that expired, and the time it was accepted is no longer kept: the membership
and the audit log record that. An address has at most one invitation to an organisation, which
is sent again rather than doubled. An organisation may let everyone whose confirmed address is
at one of its domains join it, with a role of member or viewer; the domains are an array that
a GIN index serves, since every `/me` asks which organisations an address's domain may join.
The audit log records invitations sent, revoked and accepted, a change to who may join by
domain and a member joining that way, and its actions are a check constraint, so it gains
those values.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "2520d37473ef"
down_revision: str | None = "4b8b284b8b86"
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
    "'ownership_transferred', 'member_left'"
)
ADDED = (
    "invitation_sent",
    "invitation_revoked",
    "invitation_accepted",
    "joining_changed",
    "member_joined",
)
NEW_ACTIONS = ", ".join([OLD_ACTIONS, *(f"'{action}'" for action in ADDED)])


def upgrade() -> None:
    # An accepted invitation is kept by nothing from now on.
    op.execute("DELETE FROM invitations WHERE accepted_at IS NOT NULL")
    op.drop_column("invitations", "accepted_at")
    op.create_unique_constraint(
        op.f("invitations_org_id_email_key"), "invitations", ["org_id", "email"]
    )
    op.add_column(
        "organizations",
        sa.Column(
            "join_domains",
            postgresql.ARRAY(sa.String(length=253)),
            server_default="{}",
            nullable=False,
        ),
    )
    op.add_column(
        "organizations",
        sa.Column("join_role", sa.String(length=20), server_default="member", nullable=False),
    )
    op.create_check_constraint(
        "ck_organizations_join_role", "organizations", "join_role IN ('member', 'viewer')"
    )
    op.create_index(
        "ix_organizations_join_domains",
        "organizations",
        ["join_domains"],
        unique=False,
        postgresql_using="gin",
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM audit_events WHERE action = ANY(:added)").bindparams(added=list(ADDED))
    )
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_index(
        "ix_organizations_join_domains", table_name="organizations", postgresql_using="gin"
    )
    op.drop_constraint("ck_organizations_join_role", "organizations", type_="check")
    op.drop_column("organizations", "join_role")
    op.drop_column("organizations", "join_domains")
    op.drop_constraint(op.f("invitations_org_id_email_key"), "invitations", type_="unique")
    op.add_column(
        "invitations",
        sa.Column("accepted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
