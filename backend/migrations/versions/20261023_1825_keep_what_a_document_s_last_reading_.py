"""Keep what a document's last reading changed.

Revision 084763110f0b, written 2026-10-23.

A template changed and its documents read again show what the change did: each reading of a
document by the enrich stage that had one before it keeps, in a row of its own per document, the
values of its templates' fields that it no longer reads and those it reads anew, and whether a
member's check is among those gone (app/fact_changes.py).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "084763110f0b"
down_revision: str | None = "72089ad8c097"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fact_changes",
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column(
            "read_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("template_before", sa.String(length=100), nullable=True),
        sa.Column("template_after", sa.String(length=100), nullable=True),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("changed", sa.Boolean(), nullable=False),
        sa.Column("checks_unheld", sa.Integer(), nullable=False),
        sa.CheckConstraint("jsonb_typeof(changes) = 'array'", name="ck_fact_changes_changes"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fact_changes_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fact_changes_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", name=op.f("fact_changes_pkey")),
    )
    op.create_index(
        "ix_fact_changes_project_read", "fact_changes", ["project_id", "read_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_fact_changes_project_read", table_name="fact_changes")
    op.drop_table("fact_changes")
