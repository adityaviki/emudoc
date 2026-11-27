"""Stop keeping whether a member hid the suggestion to invite their team.

Revision c90ee014e661, written 2026-11-27.

The chat no longer suggests inviting the team under an answer (the owner's word), so the accounts
lose whether they hid it. Owners and admins invite from the workspace's page, as before. A
downgrade adds the column again with the suggestion hidden for every account, as migration
a990052a728b took it for the accounts from before it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c90ee014e661"
down_revision: str | None = "b8d9a8dd4ff8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("users", "invite_hidden_at")


def downgrade() -> None:
    op.add_column("users", sa.Column("invite_hidden_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE users SET invite_hidden_at = now()")
