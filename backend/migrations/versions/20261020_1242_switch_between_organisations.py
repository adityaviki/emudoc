"""Switch between organisations.

Revision 4b8b284b8b86, written 2026-10-20.

A member of several organisations now chooses the one they work in, and the choice is the
session's: a nullable organisation on each session, cleared when the organisation goes, and
read as the first one joined while it is null, as every session from before this reads. The
audit log records an organisation's creation, renaming, handing over and a member leaving, and
its actions are a check constraint, so it gains those values.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b8b284b8b86"
down_revision: str | None = "960c8aaa9de2"
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
    "'email_changed', 'sign_in_paused'"
)
ADDED = ("organization_created", "organization_renamed", "ownership_transferred", "member_left")
NEW_ACTIONS = ", ".join([OLD_ACTIONS, *(f"'{action}'" for action in ADDED)])


def upgrade() -> None:
    op.add_column("sessions", sa.Column("org_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_sessions_org_id"), "sessions", ["org_id"], unique=False)
    op.create_foreign_key(
        op.f("sessions_org_id_fkey"),
        "sessions",
        "organizations",
        ["org_id"],
        ["id"],
        ondelete="SET NULL",
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM audit_events WHERE action = ANY(:added)").bindparams(added=list(ADDED))
    )
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_constraint(op.f("sessions_org_id_fkey"), "sessions", type_="foreignkey")
    op.drop_index(op.f("ix_sessions_org_id"), table_name="sessions")
    op.drop_column("sessions", "org_id")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
