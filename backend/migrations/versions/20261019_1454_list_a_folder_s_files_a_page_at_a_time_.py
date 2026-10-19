"""List a folder's files a page at a time in natural order.

Revision 9a14e0e90cf8, written 2026-10-19.

The library asks for one folder's files at a time, a page at a time, in the order it shows
them: by name, with the numbers in a name compared as numbers and case and accents set aside.
The page sorted them itself while it held every document of the project; now the database
does, so the order is a collation on `documents.filename`, `natural_order` (ICU's root locale
with numeric ordering at the primary strength, deterministic, so that names equal but for
case still have an order a page can end between), and an index walks it within a folder. Its
leading column serves every lookup by project, which the index on `project_id` alone did.

Alembic compares no collations, so a later autogenerate does not see this one either way.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a14e0e90cf8"
down_revision: str | None = "47fd8969314d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE COLLATION natural_order (provider = icu, locale = 'und-u-kn-ks-level1')")
    op.alter_column(
        "documents",
        "filename",
        type_=sa.String(500, collation="natural_order"),
        existing_type=sa.String(500),
        existing_nullable=False,
    )
    op.drop_index("ix_documents_project_id", table_name="documents")
    op.create_index(
        "ix_documents_listing", "documents", ["project_id", "folder_id", "filename", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_documents_listing", table_name="documents")
    op.create_index("ix_documents_project_id", "documents", ["project_id"])
    op.alter_column(
        "documents",
        "filename",
        type_=sa.String(500),
        existing_type=sa.String(500, collation="natural_order"),
        existing_nullable=False,
    )
    op.execute("DROP COLLATION natural_order")
