"""Initial schema.

Revision bedb624406a6, written 2026-10-11.

Every table as the models stood when migrations were introduced, right after organisations,
users and sign-in arrived: accounts and sessions, projects and folders, documents with their
blocks, sections, passages and facts, and chats. Two columns depend on settings, as they did when
`create_all` made them: `passages.tsv` bakes in FTS_LANGUAGE (the next migration moves it into
a column), and `passages.embedding` is sized by EMBEDDING_DIM (`rebuild_embeddings` in
app/maintenance.py remakes it when that changes). The `vector` extension is created first and
is left in place by the downgrade.

A database that already has tables but no version was made by `create_all` before there were
migrations. Those made right before this migration were stamped by the app at the time; any
other cannot be upgraded, and the migration says so instead of failing on the first table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.util import CommandError
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from app.config import get_settings

revision: str = "bedb624406a6"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_settings = get_settings()
_fts = _settings.fts_language


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names()) - {"alembic_version"}
    if existing:
        raise CommandError(
            "The database already has tables but no migration version, so it was made by a "
            "version of Emudoc from before there were migrations and cannot be upgraded. Reset "
            "it with `docker compose down -v` and run this command again."
        )
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("organizations_pkey")),
        sa.UniqueConstraint("slug", name=op.f("organizations_slug_key")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("totp_secret", sa.String(length=64), nullable=True),
        sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("totp_last_step", sa.BigInteger(), nullable=True),
        sa.Column("recovery_code_hashes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("users_pkey")),
        sa.UniqueConstraint("email", name=op.f("users_email_key")),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("key_prefix", sa.String(length=12), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("api_keys_created_by_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("api_keys_org_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("api_keys_pkey")),
        sa.UniqueConstraint("key_hash", name=op.f("api_keys_key_hash_key")),
    )
    op.create_index(op.f("ix_api_keys_org_id"), "api_keys", ["org_id"], unique=False)
    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('verify_email', 'reset_password')", name="ck_auth_tokens_purpose"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("auth_tokens_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("auth_tokens_pkey")),
        sa.UniqueConstraint("token_hash", name=op.f("auth_tokens_token_hash_key")),
    )
    op.create_index(op.f("ix_auth_tokens_user_id"), "auth_tokens", ["user_id"], unique=False)
    op.create_table(
        "identities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("identities_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("identities_pkey")),
        sa.UniqueConstraint("provider", "subject", name=op.f("identities_provider_subject_key")),
    )
    op.create_index(op.f("ix_identities_user_id"), "identities", ["user_id"], unique=False)
    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'viewer')", name="ck_invitations_role"
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_id"],
            ["users.id"],
            name=op.f("invitations_invited_by_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("invitations_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("invitations_pkey")),
        sa.UniqueConstraint("token_hash", name=op.f("invitations_token_hash_key")),
    )
    op.create_index(op.f("ix_invitations_email"), "invitations", ["email"], unique=False)
    op.create_index(op.f("ix_invitations_org_id"), "invitations", ["org_id"], unique=False)
    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'viewer')", name="ck_memberships_role"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("memberships_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("memberships_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("memberships_pkey")),
        sa.UniqueConstraint("org_id", "user_id", name=op.f("memberships_org_id_user_id_key")),
    )
    op.create_index(op.f("ix_memberships_org_id"), "memberships", ["org_id"], unique=False)
    op.create_index(op.f("ix_memberships_user_id"), "memberships", ["user_id"], unique=False)
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("projects_org_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("projects_pkey")),
    )
    op.create_index(op.f("ix_projects_org_id"), "projects", ["org_id"], unique=False)
    op.create_table(
        "sessions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("needs_mfa", sa.Boolean(), nullable=False),
        sa.Column("user_agent", sa.String(length=500), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("sessions_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("sessions_pkey")),
        sa.UniqueConstraint("token_hash", name=op.f("sessions_token_hash_key")),
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)
    op.create_table(
        "chats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("visibility IN ('private', 'project')", name="ck_chats_visibility"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("chats_project_id_fkey"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("chats_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("chats_pkey")),
    )
    op.create_index(op.f("ix_chats_project_id"), "chats", ["project_id"], unique=False)
    op.create_index(op.f("ix_chats_user_id"), "chats", ["user_id"], unique=False)
    op.create_table(
        "folders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["folders.id"], name=op.f("folders_parent_id_fkey"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("folders_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("folders_pkey")),
    )
    op.create_index(op.f("ix_folders_parent_id"), "folders", ["parent_id"], unique=False)
    op.create_index(op.f("ix_folders_project_id"), "folders", ["project_id"], unique=False)
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notice", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name=op.f("chat_messages_chat_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("chat_messages_pkey")),
    )
    op.create_index(op.f("ix_chat_messages_chat_id"), "chat_messages", ["chat_id"], unique=False)
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("folder_id", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=200), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("stages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("page_sizes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("doc_type", sa.String(length=100), nullable=True),
        sa.Column("doc_date", sa.Date(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["folder_id"], ["folders.id"], name=op.f("documents_folder_id_fkey"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("documents_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("documents_pkey")),
        sa.UniqueConstraint("project_id", "sha256", name=op.f("documents_project_id_sha256_key")),
    )
    op.create_index(op.f("ix_documents_doc_type"), "documents", ["doc_type"], unique=False)
    op.create_index(op.f("ix_documents_folder_id"), "documents", ["folder_id"], unique=False)
    op.create_index(op.f("ix_documents_project_id"), "documents", ["project_id"], unique=False)
    op.create_index(op.f("ix_documents_status"), "documents", ["status"], unique=False)
    op.create_table(
        "blocks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("bbox", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("blocks_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("blocks_pkey")),
        sa.UniqueConstraint("document_id", "ordinal", name=op.f("blocks_document_id_ordinal_key")),
    )
    op.create_index(op.f("ix_blocks_document_id"), "blocks", ["document_id"], unique=False)
    op.create_table(
        "sections",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("block_start", sa.Integer(), nullable=False),
        sa.Column("block_end", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("sections_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["sections.id"], name=op.f("sections_parent_id_fkey"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("sections_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("sections_pkey")),
    )
    op.create_index(op.f("ix_sections_document_id"), "sections", ["document_id"], unique=False)
    op.create_index(op.f("ix_sections_project_id"), "sections", ["project_id"], unique=False)
    op.create_table(
        "passages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("block_start", sa.Integer(), nullable=False),
        sa.Column("block_end", sa.Integer(), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed(
                f"setweight(to_tsvector('{_fts}', coalesce(context, '')), 'B') || "
                f"setweight(to_tsvector('{_fts}', text), 'A')",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("embedding", Vector(_settings.embedding_dim), nullable=True),
        sa.Column("embedding_model", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("passages_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("passages_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["section_id"],
            ["sections.id"],
            name=op.f("passages_section_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("passages_pkey")),
    )
    op.create_index(op.f("ix_passages_document_id"), "passages", ["document_id"], unique=False)
    op.create_index(
        "ix_passages_embedding",
        "passages",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(op.f("ix_passages_project_id"), "passages", ["project_id"], unique=False)
    op.create_index(op.f("ix_passages_section_id"), "passages", ["section_id"], unique=False)
    op.create_index("ix_passages_tsv", "passages", ["tsv"], unique=False, postgresql_using="gin")
    op.create_table(
        "facts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("value_type", sa.String(length=20), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("number", sa.Float(), nullable=True),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("block_ordinal", sa.Integer(), nullable=True),
        sa.Column("passage_id", sa.BigInteger(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("facts_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["passage_id"], ["passages.id"], name=op.f("facts_passage_id_fkey"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("facts_project_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("facts_pkey")),
    )
    op.create_index(op.f("ix_facts_document_id"), "facts", ["document_id"], unique=False)
    op.create_index(op.f("ix_facts_name"), "facts", ["name"], unique=False)
    op.create_index(op.f("ix_facts_project_id"), "facts", ["project_id"], unique=False)


def downgrade() -> None:
    # Dropping a table drops its indexes; the order respects the foreign keys.
    for table in (
        "facts",
        "passages",
        "sections",
        "blocks",
        "documents",
        "chat_messages",
        "folders",
        "chats",
        "sessions",
        "projects",
        "memberships",
        "invitations",
        "identities",
        "auth_tokens",
        "api_keys",
        "users",
        "organizations",
    ):
        op.drop_table(table)
