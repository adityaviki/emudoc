"""Sell the Team plan by the seat and agree an organisation's figures.

Revision f5c912a5eee6, written 2026-10-20.

An organisation on the Team plan has the members it bought, its seats, in place of the plan's
own number, and any organisation may have figures agreed with it in place of its plan's, by
the name of an entitlement (app/entitlements.py), an Enterprise organisation's terms. The Team
organisations from before keep the 25 members the plan allowed them, as 25 seats. The audit log
records a change to either, and its actions are a check constraint, so it gains those values.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f5c912a5eee6"
down_revision: str | None = "ba2e143b8d08"
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
    "'project_access_changed'"
)
ADDED = ("seats_changed", "entitlements_changed")
NEW_ACTIONS = ", ".join([OLD_ACTIONS, *(f"'{action}'" for action in ADDED)])


def upgrade() -> None:
    op.add_column("organizations", sa.Column("seats", sa.Integer(), nullable=True))
    op.add_column(
        "organizations",
        sa.Column(
            "overrides",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint("ck_organizations_seats", "organizations", "seats > 0")
    op.create_check_constraint(
        "ck_organizations_overrides", "organizations", "jsonb_typeof(overrides) = 'object'"
    )
    op.execute("UPDATE organizations SET seats = 25 WHERE plan = 'team'")
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM audit_events WHERE action = ANY(:added)").bindparams(added=list(ADDED))
    )
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_column("organizations", "overrides")
    op.drop_column("organizations", "seats")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
