"""Keep an organisation's security settings, and find its audit log's rows by what they are.

Revision 269a4afb127b, written 2026-10-21.

An organisation's owners and admins may require two-factor sign-in of everyone who works in it,
and sign its members in again sooner than the deployment's SESSION_DAYS (app/security.py): two
columns of `organizations`, off and null for every organisation from before. The audit log's
actions gain `audit_exported`, the log downloaded as a file.

The audit route (app/api/audit.py) now reads an organisation's rows by time, of one action,
project or member or a span, so each of those gets an index that ends in that order, and the two
it replaces go: the organisation's rows by id, and a member's rows by user alone, which the new
member's index serves too.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "269a4afb127b"
down_revision: str | None = "516af346e1f1"
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
    "'chat_restored', 'chat_purged', 'settings_changed'"
)
NEW_ACTIONS = f"{OLD_ACTIONS}, 'audit_exported'"


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("mfa_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("organizations", sa.Column("session_days", sa.Integer()))
    op.create_check_constraint("ck_organizations_session_days", "organizations", "session_days > 0")
    _check("audit_events", "action", NEW_ACTIONS)
    op.drop_index("ix_audit_events_org_newest", table_name="audit_events")
    op.drop_index("ix_audit_events_user_id", table_name="audit_events")
    op.create_index("ix_audit_events_org_time", "audit_events", ["org_id", "occurred_at", "id"])
    op.create_index(
        "ix_audit_events_org_action", "audit_events", ["org_id", "action", "occurred_at", "id"]
    )
    op.create_index(
        "ix_audit_events_org_project",
        "audit_events",
        ["org_id", "project_id", "occurred_at", "id"],
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )
    op.create_index("ix_audit_events_user", "audit_events", ["user_id", "occurred_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_user", table_name="audit_events")
    op.drop_index("ix_audit_events_org_project", table_name="audit_events")
    op.drop_index("ix_audit_events_org_action", table_name="audit_events")
    op.drop_index("ix_audit_events_org_time", table_name="audit_events")
    op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"])
    op.create_index("ix_audit_events_org_newest", "audit_events", ["org_id", "id"])
    op.execute(sa.text("DELETE FROM audit_events WHERE action = 'audit_exported'"))
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_constraint("ck_organizations_session_days", "organizations", type_="check")
    op.drop_column("organizations", "session_days")
    op.drop_column("organizations", "mfa_required")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
