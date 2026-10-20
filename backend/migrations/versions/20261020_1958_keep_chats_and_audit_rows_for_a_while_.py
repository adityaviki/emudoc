"""Keep chats and audit rows for a while after a lower plan.

Revision 90cbd173ba0d, written 2026-10-20.

How long an organisation keeps a chat and an audit row followed its plan at once, so a Team
organisation that cancelled lost every chat older than Free's 30 days within the housekeeper's
next round. A move to a plan or terms that keep them for less time now holds what was kept
before for a while (`RETENTION_HELD` in app/retention.py): the organisation records until when,
and the days it kept each for, a null among them while the hold stands keeping them for good.
Nothing is held for the organisations as they stand.

A cancellation leaves a subscription's status as it was until it ends, so the audit log said
nothing of it: it gains `subscription_cancelled` and `cancellation_withdrawn`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "90cbd173ba0d"
down_revision: str | None = "67eb6d3cb16c"
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
    "'subscription_changed', 'payment_failed', 'pack_bought'"
)
ADDED = ("subscription_cancelled", "cancellation_withdrawn")
NEW_ACTIONS = ", ".join([OLD_ACTIONS, *(f"'{action}'" for action in ADDED)])


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("retention_held_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("organizations", sa.Column("held_chat_days", sa.Integer(), nullable=True))
    op.add_column("organizations", sa.Column("held_audit_days", sa.Integer(), nullable=True))
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM audit_events WHERE action = ANY(:added)").bindparams(added=list(ADDED))
    )
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_column("organizations", "held_audit_days")
    op.drop_column("organizations", "held_chat_days")
    op.drop_column("organizations", "retention_held_until")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
