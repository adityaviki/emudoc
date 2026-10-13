"""Warn about spending once a day.

Revision 504c1bd96819, written 2026-10-13.

A day of model use now has limits (app/spending.py): the organisation's, set by its plan, and
the platform's, DAILY_SPEND_CAP, both measured against the costs the usage rows carry since
midnight UTC. The worker warns the operator once a day for each organisation and once for the
platform as a day nears a limit, and `spend_alerts` is what says a warning was sent: one row
per warning, with the organisation it was about (none for the platform) and the figures it
gave. The platform's limit reads every organisation's rows since midnight, which the index by
organisation and time did not serve, so `usage_events` gets an index by time alone.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "504c1bd96819"
down_revision: str | None = "9f8860b09021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spend_alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("spent_micros", sa.BigInteger(), nullable=False),
        sa.Column("cap_micros", sa.BigInteger(), nullable=False),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("spend_alerts_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("spend_alerts_pkey")),
    )
    op.create_index(op.f("ix_spend_alerts_org_id"), "spend_alerts", ["org_id"], unique=False)
    op.create_index("ix_usage_events_occurred", "usage_events", ["occurred_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_usage_events_occurred", table_name="usage_events")
    op.drop_index(op.f("ix_spend_alerts_org_id"), table_name="spend_alerts")
    op.drop_table("spend_alerts")
