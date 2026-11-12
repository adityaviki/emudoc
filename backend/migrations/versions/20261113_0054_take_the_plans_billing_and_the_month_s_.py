"""Take the plans, billing and the month's quotas out.

Revision f0e6fbfb6cdc, written 2026-11-13.

An install is one team's own (S.2): nothing is refused for a plan and nothing is sold. So an
organisation keeps no plan, seats or agreed figures, no customer or subscription at Stripe and no
hold of its chats after a lower plan; the packs bought for a month, the Stripe events acted on and
the month's notices at 80 % and 100 % go with their tables, and the notices of that kind with
them, from the centres and from what the members and the workspace chose. How long the audit log
keeps a row is the workspace's own choice now, a year until its admins choose otherwise; a chat is
kept until it is deleted unless they chose fewer days, as the column already said. The spending
warnings are the install's alone, the cap being the one limit left.

Going back puts every organisation on the Free plan, with nothing bought.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f0e6fbfb6cdc"
down_revision: str | None = "5c1e9a7d3b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "'upload_indexed', 'project_export', 'checks_run', 'review_assigned', 'report_ready', "
    "'weekly_digest', 'mentioned', 'thread_replied'"
)
OLD_KINDS = (
    "'upload_indexed', 'quota', 'project_export', 'checks_run', 'review_assigned', "
    "'report_ready', 'weekly_digest', 'mentioned', 'thread_replied'"
)
PLANS = "'free', 'pro', 'team', 'enterprise'"
DROPPED = (
    "plan",
    "seats",
    "overrides",
    "stripe_customer_id",
    "stripe_subscription_id",
    "subscription_status",
    "trial_ends_at",
    "renews_at",
    "ends_at",
    "payment_failed_at",
    "retention_held_until",
    "held_chat_days",
    "held_audit_days",
)


def upgrade() -> None:
    op.drop_index(op.f("ix_billing_events_received_at"), table_name="billing_events")
    op.drop_table("billing_events")
    op.drop_index(op.f("ix_allowance_packs_org_id_month"), table_name="allowance_packs")
    op.drop_table("allowance_packs")
    op.drop_table("quota_notices")
    op.execute("DELETE FROM notifications WHERE kind = 'quota'")
    op.drop_constraint("ck_notifications_kind", "notifications", type_="check")
    op.create_check_constraint("ck_notifications_kind", "notifications", f"kind IN ({KINDS})")
    op.execute("UPDATE users SET notices = notices - 'quota' WHERE notices ? 'quota'")
    op.execute("UPDATE organizations SET notices = notices - 'quota' WHERE notices ? 'quota'")
    op.execute("DELETE FROM spend_alerts WHERE org_id IS NOT NULL")
    op.drop_index(op.f("ix_spend_alerts_org_id"), table_name="spend_alerts")
    op.drop_column("spend_alerts", "org_id")
    op.add_column(
        "organizations",
        sa.Column("audit_days", sa.Integer(), server_default="365", nullable=True),
    )
    op.create_check_constraint("ck_organizations_audit_days", "organizations", "audit_days > 0")
    for constraint in ("ck_organizations_plan", "ck_organizations_seats"):
        op.drop_constraint(constraint, "organizations", type_="check")
    op.drop_constraint("ck_organizations_overrides", "organizations", type_="check")
    op.drop_constraint(
        op.f("organizations_stripe_customer_id_key"), "organizations", type_="unique"
    )
    for column in DROPPED:
        op.drop_column("organizations", column)


def downgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("plan", sa.String(length=20), server_default="free", nullable=False),
    )
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
    op.add_column(
        "organizations", sa.Column("stripe_customer_id", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "organizations", sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "organizations", sa.Column("subscription_status", sa.String(length=30), nullable=True)
    )
    for column in ("trial_ends_at", "renews_at", "ends_at", "payment_failed_at"):
        op.add_column("organizations", sa.Column(column, sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "organizations",
        sa.Column("retention_held_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("organizations", sa.Column("held_chat_days", sa.Integer(), nullable=True))
    op.add_column("organizations", sa.Column("held_audit_days", sa.Integer(), nullable=True))
    op.create_unique_constraint(
        op.f("organizations_stripe_customer_id_key"), "organizations", ["stripe_customer_id"]
    )
    op.create_check_constraint("ck_organizations_plan", "organizations", f"plan IN ({PLANS})")
    op.create_check_constraint("ck_organizations_seats", "organizations", "seats > 0")
    op.create_check_constraint(
        "ck_organizations_overrides", "organizations", "jsonb_typeof(overrides) = 'object'"
    )
    op.drop_constraint("ck_organizations_audit_days", "organizations", type_="check")
    op.drop_column("organizations", "audit_days")
    op.add_column("spend_alerts", sa.Column("org_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("spend_alerts_org_id_fkey"),
        "spend_alerts",
        "organizations",
        ["org_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_spend_alerts_org_id"), "spend_alerts", ["org_id"], unique=False)
    op.drop_constraint("ck_notifications_kind", "notifications", type_="check")
    op.create_check_constraint("ck_notifications_kind", "notifications", f"kind IN ({OLD_KINDS})")
    op.create_table(
        "quota_notices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("month", sa.DateTime(timezone=True), nullable=False),
        sa.Column("figure", sa.String(length=40), nullable=False),
        sa.Column("share", sa.Integer(), nullable=False),
        sa.Column("allowance", sa.Integer(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "figure IN ('pages_per_month', 'questions_per_month')", name="ck_quota_notices_figure"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("quota_notices_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("quota_notices_pkey")),
        sa.UniqueConstraint(
            "org_id",
            "month",
            "figure",
            "share",
            "allowance",
            name=op.f("quota_notices_org_id_month_figure_share_allowance_key"),
        ),
    )
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
