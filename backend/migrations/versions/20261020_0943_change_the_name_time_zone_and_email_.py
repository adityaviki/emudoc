"""Change the name, time zone and email address of an account.

Revision acecf87de76f, written 2026-10-20.

A user may now give their account another name and a time zone, which dates the mail they are
sent, and move it to another email address through a link sent there. The zone is a column of
its own, empty for the accounts made before it, which read as UTC; a change of address is a
one-time link of its own purpose; and the audit log records the three new acts. The purposes
and the actions are check constraints, so each gains its values.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "acecf87de76f"
down_revision: str | None = "9a14e0e90cf8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_PURPOSES = "'verify_email', 'reset_password'"
NEW_PURPOSES = f"{OLD_PURPOSES}, 'change_email'"
OLD_ACTIONS = (
    "'sign_up', 'sign_in', 'sign_in_failed', 'sign_out', 'session_revoked', "
    "'other_sessions_revoked', 'password_changed', 'password_reset_requested', "
    "'password_reset', 'mfa_enabled', 'mfa_disabled', 'account_exported', 'terms_accepted', "
    "'account_deletion_requested', 'account_deletion_cancelled', 'account_purged', "
    "'role_changed', 'plan_changed', 'project_deleted', 'folder_deleted', "
    "'document_uploaded', 'document_downloaded', 'document_deleted', "
    "'document_quarantined', 'document_requeued', 'chat_shared', 'chat_unshared', "
    "'chat_deleted', 'database_restored'"
)
NEW_ACTIONS = f"{OLD_ACTIONS}, 'profile_changed', 'email_change_requested', 'email_changed'"


def upgrade() -> None:
    # The default fills the rows there are and goes, as the model writes the column itself.
    op.add_column("users", sa.Column("time_zone", sa.String(64), nullable=False, server_default=""))
    op.alter_column("users", "time_zone", server_default=None)
    _check("auth_tokens", "purpose", NEW_PURPOSES)
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM audit_events WHERE action IN "
            "('profile_changed', 'email_change_requested', 'email_changed')"
        )
    )
    op.execute(sa.text("DELETE FROM auth_tokens WHERE purpose = 'change_email'"))
    _check("audit_events", "action", OLD_ACTIONS)
    _check("auth_tokens", "purpose", OLD_PURPOSES)
    op.drop_column("users", "time_zone")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
