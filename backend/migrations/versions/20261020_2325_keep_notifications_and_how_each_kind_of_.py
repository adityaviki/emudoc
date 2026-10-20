"""Keep notifications and how each kind of notice is told.

Revision 1f4f88ff7d9d, written 2026-10-20.

A notice is told in the app as well as by email now (app/notices.py): a `notifications` row per
member told, read or not. And a member chooses each way of each kind instead of one switch for
every notice by email: `users.notices` holds what they chose, a kind or a way not named being
on. A member who had turned the notices off by email has every kind's email off; going back, a
member who had turned any kind's email off has them all off, since the one switch cannot say
which.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1f4f88ff7d9d"
down_revision: str | None = "f7e94b5276eb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("link", sa.String(length=400), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('upload_indexed', 'quota')", name="ck_notifications_kind"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("notifications_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("notifications_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("notifications_pkey")),
    )
    op.create_index(op.f("ix_notifications_org_id"), "notifications", ["org_id"], unique=False)
    op.create_index(
        "ix_notifications_unread",
        "notifications",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index("ix_notifications_user_id_id", "notifications", ["user_id", "id"], unique=False)
    op.add_column(
        "users",
        sa.Column(
            "notices",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE users
        SET notices = '{"upload_indexed": {"email": false}, "quota": {"email": false}}'::jsonb
        WHERE NOT notices_by_email
        """
    )
    op.drop_column("users", "notices_by_email")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "notices_by_email",
            sa.BOOLEAN(),
            server_default=sa.text("true"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE users SET notices_by_email = false
        WHERE EXISTS (SELECT 1 FROM jsonb_each(notices) AS kind(name, ways)
                      WHERE jsonb_typeof(ways) = 'object' AND ways->'email' = 'false'::jsonb)
        """
    )
    op.drop_column("users", "notices")
    op.drop_index("ix_notifications_user_id_id", table_name="notifications")
    op.drop_index(
        "ix_notifications_unread",
        table_name="notifications",
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.drop_index(op.f("ix_notifications_org_id"), table_name="notifications")
    op.drop_table("notifications")
