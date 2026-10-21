"""Let operators switch providers off.

Revision 4bd26bda783e, written 2026-10-21.

An operator switches a model provider off from the operator's area, and on again, without a
restart (app/switches.py): `provider_switches` holds a row for each provider switched off, by
the prefix of a `provider:model` name, with who switched it and when. DISABLED_PROVIDERS stays
the deployment's own switch, which the table cannot turn back on. The table starts empty.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4bd26bda783e"
down_revision: str | None = "676f65f8aa80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_switches",
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "switched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("provider ~ '^[a-z0-9_-]+$'", name="ck_provider_switches_provider"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("provider_switches_user_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("provider", name=op.f("provider_switches_pkey")),
    )


def downgrade() -> None:
    op.drop_table("provider_switches")
