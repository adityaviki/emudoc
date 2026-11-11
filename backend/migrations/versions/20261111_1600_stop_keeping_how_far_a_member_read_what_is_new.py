"""Stop keeping how far a member read what is new.

Revision 9e41c7b2d5a8, written 2026-11-11.

The page of what's new is gone, with the help menu's way to it and its dot (the owner's word), so
the accounts lose how far they read it. The changelog stays, read by people alone. A downgrade
adds the columns again, every account as if it had never opened the page.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9e41c7b2d5a8"
down_revision: str | None = "685e4c51a549"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_users_changes_seen", "users", type_="check")
    op.drop_column("users", "changes_seen_entries")
    op.drop_column("users", "changes_seen_on")


def downgrade() -> None:
    op.add_column("users", sa.Column("changes_seen_on", sa.Date()))
    op.add_column("users", sa.Column("changes_seen_entries", sa.Integer()))
    op.create_check_constraint(
        "ck_users_changes_seen",
        "users",
        "(changes_seen_on IS NULL) = (changes_seen_entries IS NULL)",
    )
