"""Ask a chat about a folder or chosen documents.

Revision 0c39e089edf9, written 2026-10-25.

A chat is about the whole project, as every chat was, or about one of its folders with the
subfolders, or about documents chosen from it (app/scopes.py), and its answers read only those.
The chats gain the kind and the folder; the documents are rows of their own, which go with the
chat or the document. The kind stays when the folder or the documents are deleted for good, so
such a chat reads nothing rather than the whole project.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0c39e089edf9"
down_revision: str | None = "b55f9a8eea31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_scope_documents",
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["chats.id"],
            name=op.f("chat_scope_documents_chat_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("chat_scope_documents_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chat_id", "document_id", name=op.f("chat_scope_documents_pkey")),
    )
    op.create_index(
        op.f("ix_chat_scope_documents_document_id"),
        "chat_scope_documents",
        ["document_id"],
        unique=False,
    )
    op.add_column(
        "chats",
        sa.Column("scope_kind", sa.String(length=20), server_default="project", nullable=False),
    )
    op.create_check_constraint(
        "ck_chats_scope_kind", "chats", "scope_kind IN ('project', 'folder', 'documents')"
    )
    op.add_column("chats", sa.Column("scope_folder_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_chats_scope_folder_id",
        "chats",
        ["scope_folder_id"],
        unique=False,
        postgresql_where=sa.text("scope_folder_id IS NOT NULL"),
    )
    op.create_foreign_key(
        op.f("chats_scope_folder_id_fkey"),
        "chats",
        "folders",
        ["scope_folder_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("chats_scope_folder_id_fkey"), "chats", type_="foreignkey")
    op.drop_index(
        "ix_chats_scope_folder_id",
        table_name="chats",
        postgresql_where=sa.text("scope_folder_id IS NOT NULL"),
    )
    op.drop_column("chats", "scope_folder_id")
    op.drop_constraint("ck_chats_scope_kind", "chats", type_="check")
    op.drop_column("chats", "scope_kind")
    op.drop_index(op.f("ix_chat_scope_documents_document_id"), table_name="chat_scope_documents")
    op.drop_table("chat_scope_documents")
