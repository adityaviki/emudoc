"""Drop smart folders and saved searches.

Revision 45a76579a0ca, written 2026-11-03.

Smart folders, and the saved searches kept as smart folders with words, are gone from the library
(2026-11-03): its search keeps its filter, which nothing saves any more. The smart folders there
are go with their table; their documents stay where they are. A downgrade makes the table again,
empty.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "45a76579a0ca"
down_revision: str | None = "4ec681672ab5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Its indexes go with it.
    op.drop_table("smart_folders")


def downgrade() -> None:
    op.create_table(
        "smart_folders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("filter", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("query", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("smart_folders_created_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("smart_folders_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("smart_folders_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("smart_folders_pkey")),
    )
    op.create_index(
        op.f("ix_smart_folders_created_by"), "smart_folders", ["created_by"], unique=False
    )
    op.create_index(op.f("ix_smart_folders_org_id"), "smart_folders", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_smart_folders_project_id"), "smart_folders", ["project_id"], unique=False
    )
