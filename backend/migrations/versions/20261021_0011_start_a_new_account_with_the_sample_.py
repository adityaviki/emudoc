"""Start a new account with the sample project.

Revision 40ecd8a542b4, written 2026-10-21.

The organisation an account signs up with starts with a copy of Emudoc's sample documents,
indexed already (app/sample). A project says whether it is that sample, which counts against
nothing the plan holds and takes no uploads, and which questions an empty chat in it offers.
The audit log records the copy as the app's own act, so its actions, a check constraint, gain
`sample_added`. Every project from before is no sample and offers the page's own questions.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "40ecd8a542b4"
down_revision: str | None = "1f4f88ff7d9d"
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
    "'cancellation_withdrawn'"
)
ADDED = ("sample_added",)
NEW_ACTIONS = ", ".join([OLD_ACTIONS, *(f"'{action}'" for action in ADDED)])


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("sample", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "projects",
        sa.Column("suggestions", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM audit_events WHERE action = ANY(:added)").bindparams(added=list(ADDED))
    )
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_column("projects", "suggestions")
    op.drop_column("projects", "sample")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
