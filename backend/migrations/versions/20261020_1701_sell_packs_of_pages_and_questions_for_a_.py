"""Sell packs of pages and questions for a month.

Revision 0151920be687, written 2026-10-20.

An organisation may buy pages to index or questions to ask on top of its plan's for the
calendar month it pays in (app/billing.py): each pack is a row, by the Checkout session that
paid for it, which is recorded once. The audit log records a pack bought, and its actions are
a check constraint, so it gains that value.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0151920be687"
down_revision: str | None = "c4c984c6bd76"
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
    "'subscription_changed', 'payment_failed'"
)
ADDED = ("pack_bought",)
NEW_ACTIONS = ", ".join([OLD_ACTIONS, *(f"'{action}'" for action in ADDED)])


def upgrade() -> None:
    op.create_table(
        "allowance_packs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("month", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checkout_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("unit IN ('pages', 'questions')", name="ck_allowance_packs_unit"),
        sa.CheckConstraint("quantity > 0", name="ck_allowance_packs_quantity"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("allowance_packs_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("allowance_packs_pkey")),
        sa.UniqueConstraint("checkout_id", name=op.f("allowance_packs_checkout_id_key")),
    )
    op.create_index(
        "ix_allowance_packs_org_id_month", "allowance_packs", ["org_id", "month"], unique=False
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM audit_events WHERE action = ANY(:added)").bindparams(added=list(ADDED))
    )
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_index("ix_allowance_packs_org_id_month", table_name="allowance_packs")
    op.drop_table("allowance_packs")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
