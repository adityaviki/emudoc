"""Take the feature flags out.

Revision 0767ba2df2a3, written 2026-11-13.

Every feature is on for every install (S.2): no feature is rolled out to a share of the
organisations any more, so an organisation keeps no operator's choice of which it has, and the
column of those choices goes with its check. The audit log's rows of a choice made before stay,
as `flag_changed` rows, which the page still words.

Going back gives every organisation no choice of its own, which leaves each flag as its share in
the code takes it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0767ba2df2a3"
down_revision: str | None = "f0e6fbfb6cdc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Dropping the column drops its check, which names no other column.
    op.drop_column("organizations", "flags")


def downgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_organizations_flags", "organizations", "jsonb_typeof(flags) = 'object'"
    )
