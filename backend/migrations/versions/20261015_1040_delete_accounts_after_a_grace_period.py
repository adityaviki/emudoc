"""Delete accounts after a grace period.

Revision b2e16e700157, written 2026-10-15.

A user may now ask for their account to be deleted (app/retention.py). The account is not
purged at once: `users.deletion_due_at` says when it will be, and until then signing in and
keeping it undoes the request. Accounts made before this have nothing due.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2e16e700157"
down_revision: str | None = "5fef4d549111"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deletion_due_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "deletion_due_at")
