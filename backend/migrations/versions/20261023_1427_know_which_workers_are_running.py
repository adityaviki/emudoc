"""Know which workers are running.

Revision 1b1f199ae02f, written 2026-10-23.

A deployment whose worker is not running leaves every upload queued and every question
unanswered, and nothing said so: the worker serves nothing to ask. Each worker now writes a row
at its start and with every heartbeat, and marks it at its stop (app/jobs.py), which the members'
status line and the operator read. No worker has written one yet.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1b1f199ae02f"
down_revision: str | None = "11f7c94e23c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("workers_pkey")),
    )
    op.create_index(op.f("ix_workers_last_seen_at"), "workers", ["last_seen_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_workers_last_seen_at"), table_name="workers")
    op.drop_table("workers")
