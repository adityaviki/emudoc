"""Add usage events.

Revision 4d17fad8beb9, written 2026-10-12.

What an organisation uses is now recorded as it happens (app/usage.py): one row per measure
of one thing it did, naming the activity, the unit, the quantity, who did it, the model when
one was called, and what it was for. The first rows are an answer's model calls and tokens,
which the agent loop counted before but only sent in the `done` event, pruned with the other
events after a day; the indexing stages and the page renders record theirs next, and their
kinds are allowed already. The daily rollup per organisation is a query over the rows, for
which the index by organisation and time is there. A project or a user that is deleted
leaves its usage with the organisation, which is what is billed; an organisation takes its
rows with it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4d17fad8beb9"
down_revision: str | None = "d3264966f7f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("ref_type", sa.String(length=40), nullable=True),
        sa.Column("ref_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('answer', 'upload', 'ocr', 'infer_sections', 'enrich', 'embed', 'render')",
            name="ck_usage_events_kind",
        ),
        sa.CheckConstraint(
            "unit IN ('calls', 'input_tokens', 'output_tokens', 'pages', 'bytes', 'inputs')",
            name="ck_usage_events_unit",
        ),
        sa.CheckConstraint("(ref_type IS NULL) = (ref_id IS NULL)", name="ck_usage_events_ref"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("usage_events_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("usage_events_project_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("usage_events_user_id_fkey"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("usage_events_pkey")),
    )
    op.create_index(
        "ix_usage_events_org_occurred", "usage_events", ["org_id", "occurred_at"], unique=False
    )
    op.create_index(
        op.f("ix_usage_events_project_id"), "usage_events", ["project_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_usage_events_project_id"), table_name="usage_events")
    op.drop_index("ix_usage_events_org_occurred", table_name="usage_events")
    op.drop_table("usage_events")
