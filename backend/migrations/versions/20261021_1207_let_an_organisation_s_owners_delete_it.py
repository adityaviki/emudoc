"""Let an organisation's owners delete it.

Revision d3bf731e93db, written 2026-10-21.

An owner may ask for the organisation to be deleted with everything in it, which happens once
the grace period is over unless an owner keeps it (app/retention.py): `deletion_due_at` holds
the day, null when nobody asked. The audit log's actions gain
`organization_deletion_requested` and `organization_deletion_cancelled`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3bf731e93db"
down_revision: str | None = "ee389d8b7c92"
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
    "'api_key_created', 'api_key_rolled', 'api_key_revoked'"
)
NEW_ACTIONS = f"{OLD_ACTIONS}, 'organization_deletion_requested', 'organization_deletion_cancelled'"


def upgrade() -> None:
    op.add_column("organizations", sa.Column("deletion_due_at", sa.DateTime(timezone=True)))
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM audit_events WHERE action IN "
            "('organization_deletion_requested', 'organization_deletion_cancelled')"
        )
    )
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_column("organizations", "deletion_due_at")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
