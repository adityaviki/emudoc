"""Queue mail, keep the addresses not to mail notices to and the choice of notices.

Revision d2d7fd97b21b, written 2026-10-20.

A member's mail was sent by the request after its answer, and lost when the relay was down at
that moment. It is now a `send_email` job the worker sends and tries again (app/postbox.py),
and much of it is about no organisation (a sign-up's link, an account's address), so a job's
organisation may be null.

The mail provider says which addresses bounced for good or were marked as spam, kept by their
SHA-256 in `mail_suppressions` and sent no notices for a while, and every user chooses whether
to be mailed notices at all, on for everyone as they stand.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2d7fd97b21b"
down_revision: str | None = "90cbd173ba0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_suppressions",
        sa.Column("address_hash", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("reason IN ('bounce', 'complaint')", name="ck_mail_suppressions_reason"),
        sa.PrimaryKeyConstraint("address_hash", name=op.f("mail_suppressions_pkey")),
    )
    op.create_index(
        op.f("ix_mail_suppressions_recorded_at"), "mail_suppressions", ["recorded_at"], unique=False
    )
    op.alter_column("jobs", "org_id", existing_type=sa.INTEGER(), nullable=True)
    op.add_column(
        "users",
        sa.Column("notices_by_email", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "notices_by_email")
    # The mail no organisation's is the queue's only job without one.
    op.execute("DELETE FROM jobs WHERE org_id IS NULL")
    op.alter_column("jobs", "org_id", existing_type=sa.INTEGER(), nullable=False)
    op.drop_index(op.f("ix_mail_suppressions_recorded_at"), table_name="mail_suppressions")
    op.drop_table("mail_suppressions")
