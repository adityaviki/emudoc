"""Let operators turn feature flags on or off per organisation.

Revision 4cf254f44ec1, written 2026-10-21.

A feature being rolled out is behind a flag (app/flags.py), on for the share of organisations the
code names and for those an operator turns it on for: `organizations.flags` holds the operators'
choices by the flag's name, empty for every organisation from before, which follows each flag's
share. The audit log's actions gain a flag's choice changed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4cf254f44ec1"
down_revision: str | None = "f6d05e27c762"
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
    "'organization_deletion_cancelled', 'project_exported', 'support_access_granted', "
    "'support_access_revoked', 'support_session_started', 'support_session_ended'"
)
NEW_ACTIONS = f"{OLD_ACTIONS}, 'flag_changed'"


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_organizations_flags", "organizations", "jsonb_typeof(flags) = 'object'"
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM audit_events WHERE action = 'flag_changed'"))
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_constraint("ck_organizations_flags", "organizations", type_="check")
    op.drop_column("organizations", "flags")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
