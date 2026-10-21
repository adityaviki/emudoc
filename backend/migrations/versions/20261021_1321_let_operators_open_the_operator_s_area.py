"""Let operators open the operator's area.

Revision 676f65f8aa80, written 2026-10-21.

The users who run the deployment, granted by the `operators` maintenance command, may open the
operator's area at /operator (app/operators.py): `operators` holds them by the user's id, and a
row goes with its account. Nobody is one until the command grants it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "676f65f8aa80"
down_revision: str | None = "edada3bbf979"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operators",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("operators_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("operators_pkey")),
    )


def downgrade() -> None:
    op.drop_table("operators")
