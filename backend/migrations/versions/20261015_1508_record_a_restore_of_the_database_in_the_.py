"""Record a restore of the database in the audit log.

Revision 5a8a2980c876, written 2026-10-15.

After the database is restored to an earlier point, `after-restore` (app/restore.py) writes a
row to every organisation's audit log saying so and to when, since what was done after that
moment is gone from the log. The log's actions are a check constraint, so it gains this one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5a8a2980c876"
down_revision: str | None = "64e51ecfe26c"
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
    "'chat_deleted'"
)
NEW_ACTIONS = f"{OLD_ACTIONS}, 'database_restored'"


def upgrade() -> None:
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_action", "audit_events", f"action IN ({NEW_ACTIONS})"
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM audit_events WHERE action = 'database_restored'"))
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_action", "audit_events", f"action IN ({OLD_ACTIONS})"
    )
