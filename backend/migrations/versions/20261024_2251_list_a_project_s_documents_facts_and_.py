"""List a project's documents, facts and chats in id order.

Revision dcb8fb991c33, written 2026-10-24.

The versioned API (app/api/v1/) lists a project's documents and chats the newest first and its
facts the oldest first, a page at a time by id: an index on the project and the id makes each
page a short scan whatever the project holds, where one on the project alone left the plan to
the project's size. The facts' and the chats' index on the project alone gives way to it,
whose leading column serves every lookup by project as well.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "dcb8fb991c33"
down_revision: str | None = "b613bb7c52ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_documents_project_id_id", "documents", ["project_id", "id"])
    op.create_index("ix_facts_project_id_id", "facts", ["project_id", "id"])
    op.drop_index("ix_facts_project_id", table_name="facts")
    op.create_index("ix_chats_project_id_id", "chats", ["project_id", "id"])
    op.drop_index("ix_chats_project_id", table_name="chats")


def downgrade() -> None:
    op.create_index("ix_chats_project_id", "chats", ["project_id"])
    op.drop_index("ix_chats_project_id_id", table_name="chats")
    op.create_index("ix_facts_project_id", "facts", ["project_id"])
    op.drop_index("ix_facts_project_id_id", table_name="facts")
    op.drop_index("ix_documents_project_id_id", table_name="documents")
