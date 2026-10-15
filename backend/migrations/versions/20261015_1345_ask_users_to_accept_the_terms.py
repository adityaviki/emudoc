"""Ask users to accept the terms.

Revision 64e51ecfe26c, written 2026-10-15.

The deployment now publishes terms of service and a privacy policy (app/legal/), and a user
records the version they accepted and when. Every account from before this has accepted none,
so its next sign-in asks for it before anything else; the audit log gains the action that
records an acceptance.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "64e51ecfe26c"
down_revision: str | None = "7c537d5e34f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = (
    "'sign_up', 'sign_in', 'sign_in_failed', 'sign_out', 'session_revoked', "
    "'other_sessions_revoked', 'password_changed', 'password_reset_requested', "
    "'password_reset', 'mfa_enabled', 'mfa_disabled', 'account_exported', "
    "'account_deletion_requested', 'account_deletion_cancelled', 'account_purged', "
    "'role_changed', 'plan_changed', 'project_deleted', 'folder_deleted', "
    "'document_uploaded', 'document_downloaded', 'document_deleted', "
    "'document_quarantined', 'document_requeued', 'chat_shared', 'chat_unshared', "
    "'chat_deleted'"
)
NEW_ACTIONS = (
    "'sign_up', 'sign_in', 'sign_in_failed', 'sign_out', 'session_revoked', "
    "'other_sessions_revoked', 'password_changed', 'password_reset_requested', "
    "'password_reset', 'mfa_enabled', 'mfa_disabled', 'account_exported', 'terms_accepted', "
    "'account_deletion_requested', 'account_deletion_cancelled', 'account_purged', "
    "'role_changed', 'plan_changed', 'project_deleted', 'folder_deleted', "
    "'document_uploaded', 'document_downloaded', 'document_deleted', "
    "'document_quarantined', 'document_requeued', 'chat_shared', 'chat_unshared', "
    "'chat_deleted'"
)


def upgrade() -> None:
    op.add_column("users", sa.Column("terms_version", sa.String(length=40), nullable=True))
    op.add_column(
        "users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_action", "audit_events", f"action IN ({NEW_ACTIONS})"
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM audit_events WHERE action = 'terms_accepted'"))
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_action", "audit_events", f"action IN ({OLD_ACTIONS})"
    )
    op.drop_column("users", "terms_accepted_at")
    op.drop_column("users", "terms_version")
