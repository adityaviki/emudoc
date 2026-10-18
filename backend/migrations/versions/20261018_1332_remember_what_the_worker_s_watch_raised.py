"""Remember what the worker's watch raised.

Revision c751da5d101b, written 2026-10-18.

The worker now watches the queue, the documents and answers that settle, the fallbacks, the
services it reaches and the database server (app/watch.py), and writes to the operator when a
concern begins and when it ends, not every round. `watch_alerts` holds the concerns raised and
not yet over, one row each, keyed by what the concern is about, with what the operator was sent.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c751da5d101b"
down_revision: str | None = "5a8a2980c876"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watch_alerts",
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "raised_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("watch_alerts_pkey")),
    )


def downgrade() -> None:
    op.drop_table("watch_alerts")
