"""Keep how far a member read what is new.

Revision 6157f4753b44, written 2026-10-21.

The help menu leads to what's new, the members' entries of the changelog (app/changelog.py),
and marks it while there are entries the member has not read. How far they read is the newest
day they read and how many entries it held then, both null until they first open it, when the
days after the one their account was made count as unread; so the accounts from before are
shown what changed since they joined.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6157f4753b44"
down_revision: str | None = "4cf254f44ec1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("changes_seen_on", sa.Date()))
    op.add_column("users", sa.Column("changes_seen_entries", sa.Integer()))
    op.create_check_constraint(
        "ck_users_changes_seen",
        "users",
        "(changes_seen_on IS NULL) = (changes_seen_entries IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_changes_seen", "users", type_="check")
    op.drop_column("users", "changes_seen_entries")
    op.drop_column("users", "changes_seen_on")
