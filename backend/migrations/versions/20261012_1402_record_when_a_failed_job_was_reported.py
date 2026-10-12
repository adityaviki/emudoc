"""Record when a failed job was reported to the operator.

Revision e5c15aa5e982, written 2026-10-12.

The jobs the worker gives up on are its dead letters: they stay `failed` for a month, and the
worker mails the new ones to ALERT_EMAIL as one digest every quarter of an hour
(app/deadletters.py). The digest takes the failed jobs with no `reported_at` and stamps them in
the same statement, so that a failure is mailed once however many workers run, and a worker
that restarts does not mail last week's again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5c15aa5e982"
down_revision: str | None = "3dae99421cb1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "reported_at")
