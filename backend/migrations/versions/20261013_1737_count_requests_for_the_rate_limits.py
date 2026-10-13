"""Count requests for the rate limits.

Revision 133a863e3662, written 2026-10-13.

The routes that cost something or guard an account are held to a rate per caller
(app/ratelimit.py): so many uploads, questions and searches a minute per user, and so many
sign-in requests per address and per email address. The counts live in the database so that
every API replica sees the same ones: one row per scope, key and minute, raised by one
statement per request and removed by the housekeeper once the minute is long past.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "133a863e3662"
down_revision: str | None = "504c1bd96819"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limits",
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=300), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "key", "window_start", name=op.f("rate_limits_pkey")),
    )


def downgrade() -> None:
    op.drop_table("rate_limits")
