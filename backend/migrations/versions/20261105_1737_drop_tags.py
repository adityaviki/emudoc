"""Drop tags.

Revision 9e1493ee558b, written 2026-11-05.

Tags are gone from the library (2026-11-05): a document carries none, the library lists no tag's
documents and the search's filter narrows by none. The tags go with their table, and
`documents.tag_ids` with its index; the documents stay where they are. A downgrade makes them
again, the table empty and every document carrying none.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9e1493ee558b"
down_revision: str | None = "ca622aa5d107"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_documents_tag_ids", table_name="documents", postgresql_using="gin")
    op.drop_column("documents", "tag_ids")
    # Its indexes go with it.
    op.drop_table("tags")


def downgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("color", sa.String(length=20), server_default="grey", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "color IN ('grey', 'red', 'orange', 'yellow', 'green', 'teal', 'blue', 'purple', "
            "'pink')",
            name="ck_tags_color",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("tags_created_by_fkey"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("tags_org_id_fkey"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("tags_project_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("tags_pkey")),
        sa.UniqueConstraint("project_id", "name", name=op.f("tags_project_id_name_key")),
    )
    op.create_index(op.f("ix_tags_created_by"), "tags", ["created_by"], unique=False)
    op.create_index(op.f("ix_tags_org_id"), "tags", ["org_id"], unique=False)
    op.add_column(
        "documents",
        sa.Column(
            "tag_ids",
            postgresql.ARRAY(sa.Integer()),
            server_default=sa.text("'{}'::integer[]"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_documents_tag_ids", "documents", ["tag_ids"], unique=False, postgresql_using="gin"
    )
