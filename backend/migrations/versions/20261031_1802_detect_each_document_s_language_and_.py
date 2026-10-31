"""Detect each document's language and search it in that language.

Revision 851c9990f203, written 2026-10-31.

A document's language joins its details (3.13): `read_language` is the text search
configuration the parse stage detected in its text, `language` the one in force, a member's
where one set it, which `edited_details` may now name. The documents there are now were read
before any detection, so both stay null and their passages keep following their organisation's
language until they are parsed again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "851c9990f203"
down_revision: str | None = "7c3cbe4fc83f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("language", sa.String(length=63), nullable=True))
    op.add_column("documents", sa.Column("read_language", sa.String(length=63), nullable=True))
    op.drop_constraint("ck_documents_edited_details", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_edited_details",
        "documents",
        "edited_details <@ ARRAY['title', 'doc_type', 'doc_date', 'language']::varchar[]",
    )


def downgrade() -> None:
    op.drop_constraint("ck_documents_edited_details", "documents", type_="check")
    op.execute("UPDATE documents SET edited_details = array_remove(edited_details, 'language')")
    op.create_check_constraint(
        "ck_documents_edited_details",
        "documents",
        "edited_details <@ ARRAY['title', 'doc_type', 'doc_date']::varchar[]",
    )
    op.drop_column("documents", "read_language")
    op.drop_column("documents", "language")
