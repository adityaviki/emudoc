"""Drop support's view of an account.

Revision 8f89bdd0402c, written 2026-11-13.

A team runs its own install (S.3), so there is no Emudoc support to let into an account: the
members' leaves for it go, and so do the two columns that turned an operator's session into a
view of a member's account. The audit log's rows of a leave given or withdrawn and of a view
opened or closed stay, as their actions, which the page still words.

Going back brings the table and the columns back empty: no leave stands and no session views an
account, which is where every member stood until they chose otherwise.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8f89bdd0402c"
down_revision: str | None = "0767ba2df2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Dropping a column drops its foreign key and its partial index, and dropping the table
    # its own index and keys.
    op.drop_column("sessions", "support_ends_at")
    op.drop_column("sessions", "support_grant_id")
    op.drop_table("support_grants")


def downgrade() -> None:
    op.create_table(
        "support_grants",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["memberships.org_id", "memberships.user_id"],
            name=op.f("support_grants_org_id_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("support_grants_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("support_grants_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("support_grants_pkey")),
        sa.UniqueConstraint("org_id", "user_id", name=op.f("support_grants_org_id_user_id_key")),
    )
    op.create_index(op.f("ix_support_grants_user_id"), "support_grants", ["user_id"])
    op.add_column("sessions", sa.Column("support_grant_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "sessions",
        sa.Column("support_ends_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("sessions_support_grant_id_fkey"),
        "sessions",
        "support_grants",
        ["support_grant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_sessions_support_grant_id"),
        "sessions",
        ["support_grant_id"],
        postgresql_where=sa.text("support_grant_id IS NOT NULL"),
    )
