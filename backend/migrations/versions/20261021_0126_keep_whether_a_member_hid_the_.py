"""Keep whether a member hid the suggestion to invite their team.

Revision a990052a728b, written 2026-10-21.

Under an answer about the organisation's own documents, the chat suggests inviting its team to
an owner or admin while nobody else is in it or invited (app/onboarding.py). Hiding the
suggestion is the user's choice for every organisation, kept as the moment it was made. The
accounts from before had their first answers before there was a suggestion to show after
them, so it is taken as hidden for them now.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a990052a728b"
down_revision: str | None = "462fa69fdf2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("invite_hidden_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE users SET invite_hidden_at = now()")


def downgrade() -> None:
    op.drop_column("users", "invite_hidden_at")
