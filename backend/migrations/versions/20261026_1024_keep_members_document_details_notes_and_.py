"""Keep the title, kind and date members give documents, their notes, and find their copies.

Revision 1c8fe42c43c7, written 2026-10-26.

A member may set a document's title, kind and date in place of what its reading found (3.9): the
columns everything reads keep the value in force, and the new `read_*` columns what the last
reading found, which `edited_details` names the fields a member set in place of. The documents
there are now were set by no member, so what they hold is what their reading found. A document
gains a note its project's members share, and the documents an index by their bytes alone, by
which a file's copies in the organisation's other projects are found.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1c8fe42c43c7"
down_revision: str | None = "ef4d50a71594"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_notes",
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["changed_by"],
            ["users.id"],
            name=op.f("document_notes_changed_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("document_notes_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", name=op.f("document_notes_pkey")),
    )
    op.create_index(
        "ix_document_notes_changed_by",
        "document_notes",
        ["changed_by"],
        unique=False,
        postgresql_where=sa.text("changed_by IS NOT NULL"),
    )
    op.add_column("documents", sa.Column("read_title", sa.String(length=500), nullable=True))
    op.add_column("documents", sa.Column("read_doc_type", sa.String(length=100), nullable=True))
    op.add_column("documents", sa.Column("read_doc_date", sa.Date(), nullable=True))
    op.add_column(
        "documents",
        sa.Column(
            "edited_details",
            postgresql.ARRAY(sa.String(length=20)),
            server_default="{}",
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE documents SET read_title = title, read_doc_type = doc_type, "
        "read_doc_date = doc_date"
    )
    op.create_check_constraint(
        "ck_documents_edited_details",
        "documents",
        "edited_details <@ ARRAY['title', 'doc_type', 'doc_date']::varchar[]",
    )
    op.create_index("ix_documents_sha256", "documents", ["sha256"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_documents_sha256", table_name="documents")
    op.drop_constraint("ck_documents_edited_details", "documents", type_="check")
    op.drop_column("documents", "edited_details")
    op.drop_column("documents", "read_doc_date")
    op.drop_column("documents", "read_doc_type")
    op.drop_column("documents", "read_title")
    op.drop_index(
        "ix_document_notes_changed_by",
        table_name="document_notes",
        postgresql_where=sa.text("changed_by IS NOT NULL"),
    )
    op.drop_table("document_notes")
