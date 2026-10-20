"""Keep an organisation's settings.

Revision 516af346e1f1, written 2026-10-21.

An organisation's owners and admins choose the language its documents are searched in, how
long it keeps a chat when that is less than its plan keeps one, and how its members are told
each kind of notice until they choose for themselves (app/api/organizations.py). The three are
columns of `organizations`, null or empty for the deployment's and the plan's, which is what
every organisation from before has. The audit log's actions gain `settings_changed`, and the
jobs `apply_language`, which moves an organisation's passages to the language it chose
(app/languages.py).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "516af346e1f1"
down_revision: str | None = "9def24510a24"
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
    "'chat_restored', 'chat_purged'"
)
NEW_ACTIONS = f"{OLD_ACTIONS}, 'settings_changed'"
OLD_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'sync_connector', "
    "'run_workflow'"
)
NEW_KINDS = f"{OLD_KINDS}, 'apply_language'"


def upgrade() -> None:
    op.add_column("organizations", sa.Column("language", sa.String(length=63)))
    op.add_column("organizations", sa.Column("chat_days", sa.Integer()))
    op.add_column(
        "organizations",
        sa.Column(
            "notices",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint("ck_organizations_chat_days", "organizations", "chat_days > 0")
    op.create_check_constraint(
        "ck_organizations_notices", "organizations", "jsonb_typeof(notices) = 'object'"
    )
    _check("audit_events", "action", NEW_ACTIONS)
    _check("jobs", "kind", NEW_KINDS)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM jobs WHERE kind = 'apply_language'"))
    _check("jobs", "kind", OLD_KINDS)
    op.execute(sa.text("DELETE FROM audit_events WHERE action = 'settings_changed'"))
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_constraint("ck_organizations_notices", "organizations", type_="check")
    op.drop_constraint("ck_organizations_chat_days", "organizations", type_="check")
    op.drop_column("organizations", "notices")
    op.drop_column("organizations", "chat_days")
    op.drop_column("organizations", "language")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
