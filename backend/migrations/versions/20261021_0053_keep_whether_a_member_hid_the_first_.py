"""Keep whether a member hid the first steps and saw the tour.

Revision 462fa69fdf2b, written 2026-10-21.

The projects page lists an organisation's first steps (upload, ask, invite) until they are
taken or its member hides the list, and a project's workspace shows a short tour the first time
a member opens one (app/onboarding.py). Each is the user's choice for every organisation, kept
as the moment it was made. The accounts from before know their way about, so both are taken as
made for them now; the user menu offers the tour again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "462fa69fdf2b"
down_revision: str | None = "40ecd8a542b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("checklist_hidden_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("tour_seen_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE users SET checklist_hidden_at = now(), tour_seen_at = now()")


def downgrade() -> None:
    op.drop_column("users", "tour_seen_at")
    op.drop_column("users", "checklist_hidden_at")
