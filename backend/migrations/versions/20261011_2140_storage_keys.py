"""Name stored files by key, not by path.

Revision 5c1e7a9d2b40, written 2026-10-11.

`documents.storage_path` held the absolute path of the uploaded file under the data directory,
so the directory could not move and nothing but a filesystem could hold the files. It becomes
`storage_key`: a key in the layout `app/storage.py` describes, which a local directory and an
S3 bucket both serve,

    orgs/{org}/projects/{project}/documents/{document}/original{suffix}

Rows that exist get their key from the project's organisation, the two ids and the old path's
extension. The files themselves are not moved here: a migration runs in a transaction and the
filesystem does not, so `python -m app.maintenance relocate-files` moves them afterwards (the
README says how), and until it has run the originals are reported missing.

The downgrade puts the old paths back for a local storage, under the directory STORAGE_URL
names, and leaves the keys as they are for a bucket; moving files back is by hand.
"""

from collections.abc import Sequence
from urllib.parse import unquote, urlsplit

import sqlalchemy as sa
from alembic import op

from app.config import get_settings

revision: str = "5c1e7a9d2b40"
down_revision: str | None = "27bd56b4a29e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The extension of the old path, with its dot, or nothing when it has none.
SUFFIX = r"coalesce(substring(d.storage_key from '(\.[^./]*)$'), '')"


def upgrade() -> None:
    op.alter_column("documents", "storage_path", new_column_name="storage_key")
    op.execute(
        sa.text(
            "UPDATE documents AS d SET storage_key = "
            "'orgs/' || p.org_id || '/projects/' || d.project_id || '/documents/' || d.id "
            f"|| '/original' || {SUFFIX} "
            "FROM projects AS p WHERE p.id = d.project_id"
        )
    )


def downgrade() -> None:
    parts = urlsplit(get_settings().storage_url)
    if parts.scheme == "file":
        op.execute(
            sa.text(
                "UPDATE documents AS d SET storage_key = "
                ":root || '/files/' || d.project_id || '/' || d.id || '/original' "
                f"|| {SUFFIX}"
            ).bindparams(root=unquote(parts.path).rstrip("/"))
        )
    op.alter_column("documents", "storage_key", new_column_name="storage_path")
