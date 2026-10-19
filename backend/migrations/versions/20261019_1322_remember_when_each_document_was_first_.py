"""Remember when each document was first ready.

Revision 47fd8969314d, written 2026-10-19.

The time from an upload to its document being ready is one of the service levels
(deploying.md, "Service levels"), measured once per document as the worker makes it ready
(`DOCUMENT_READY` in app/metrics.py). `ready_at` says that it was: a later run, after "Index
again" or a new embedding model, finds it set and measures nothing, or every document in a
deployment would count months of waiting the day its vectors are made again. The documents
ready now take their last change as the moment, near enough for a column only that reads;
the others get theirs when they are first ready.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "47fd8969314d"
down_revision: str | None = "c751da5d101b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE documents SET ready_at = updated_at WHERE status = 'ready'")


def downgrade() -> None:
    op.drop_column("documents", "ready_at")
