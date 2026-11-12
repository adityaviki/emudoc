"""Key the files an answer writes in My files by the answer.

Revision 920709903bf8, written 2026-11-12.

The agent writes Word documents into the asker's My files (app/agent/writing.py). A file it made
names the answer and which of the answer's files it is, so that a run of the answer that starts
over, or a try asked again, writes the same file again as its next version rather than a second
one. The answer's row going with its chat leaves the file as an upload is.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "920709903bf8"
down_revision: str | None = "562af5b541e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("my_files", sa.Column("answer_id", sa.BigInteger(), nullable=True))
    op.add_column("my_files", sa.Column("answer_ordinal", sa.SmallInteger(), nullable=True))
    op.create_unique_constraint(
        op.f("my_files_answer_id_answer_ordinal_key"), "my_files", ["answer_id", "answer_ordinal"]
    )
    op.create_foreign_key(
        op.f("my_files_answer_id_fkey"),
        "my_files",
        "chat_messages",
        ["answer_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("my_files_answer_id_fkey"), "my_files", type_="foreignkey")
    op.drop_constraint(op.f("my_files_answer_id_answer_ordinal_key"), "my_files", type_="unique")
    op.drop_column("my_files", "answer_ordinal")
    op.drop_column("my_files", "answer_id")
