"""Sell the plans through Stripe.

Revision c4c984c6bd76, written 2026-10-20.

An organisation that buys a plan becomes a customer at Stripe, and its subscription sets its
plan (app/billing.py): the organisation keeps its customer's id, its last subscription's id and
what Stripe last said of it, the dates the page shows and since when a payment has failed. The
Stripe events the app acted on are kept by id, so that one delivered again is not acted on
twice. The audit log records a checkout begun, a subscription's status changing and a payment
that failed, and its actions are a check constraint, so it gains those values.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4c984c6bd76"
down_revision: str | None = "f5c912a5eee6"
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
    "'project_access_changed', 'seats_changed', 'entitlements_changed'"
)
ADDED = ("checkout_started", "subscription_changed", "payment_failed")
NEW_ACTIONS = ", ".join([OLD_ACTIONS, *(f"'{action}'" for action in ADDED)])
COLUMNS = (
    ("stripe_customer_id", sa.String(length=255)),
    ("stripe_subscription_id", sa.String(length=255)),
    ("subscription_status", sa.String(length=30)),
    ("trial_ends_at", sa.DateTime(timezone=True)),
    ("renews_at", sa.DateTime(timezone=True)),
    ("ends_at", sa.DateTime(timezone=True)),
    ("payment_failed_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.create_table(
        "billing_events",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("billing_events_pkey")),
    )
    op.create_index(
        op.f("ix_billing_events_received_at"), "billing_events", ["received_at"], unique=False
    )
    for name, kind in COLUMNS:
        op.add_column("organizations", sa.Column(name, kind, nullable=True))
    op.create_unique_constraint(
        op.f("organizations_stripe_customer_id_key"), "organizations", ["stripe_customer_id"]
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM audit_events WHERE action = ANY(:added)").bindparams(added=list(ADDED))
    )
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_constraint(
        op.f("organizations_stripe_customer_id_key"), "organizations", type_="unique"
    )
    for name, _ in reversed(COLUMNS):
        op.drop_column("organizations", name)
    op.drop_index(op.f("ix_billing_events_received_at"), table_name="billing_events")
    op.drop_table("billing_events")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
