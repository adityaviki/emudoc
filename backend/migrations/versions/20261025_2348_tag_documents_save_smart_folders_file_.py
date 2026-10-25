"""Tag documents, save smart folders, file new documents by rules, and keep favourites and recents.

Revision ef4d50a71594, written 2026-10-25.

The library grows (3.9): a project's tags, whose ids each document holds on its own row so that
tagging it changes the row the library's pages are tagged by; smart folders, a project's saved
filters of its documents; filing rules, which move or tag a new document once it is read for its
type and facts, and whose making, changing and deleting the audit log records; and each member's
favourite documents and the ones they opened lately. The documents gain the indexes their
folder's other orders are paged by, by date added, by size and by their own date. The documents
there are now are marked filed, so that a rule made later files only what arrives after it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ef4d50a71594"
down_revision: str | None = "8fc77c1e35a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = (
    "'sign_up', 'sign_in', 'sign_in_failed', 'sign_out', 'session_revoked', "
    "'other_sessions_revoked', 'password_changed', 'password_reset_requested', 'password_reset', "
    "'mfa_enabled', 'mfa_disabled', 'account_exported', 'terms_accepted', "
    "'account_deletion_requested', 'account_deletion_cancelled', 'account_purged', 'role_changed', "
    "'plan_changed', 'project_deleted', 'folder_deleted', 'document_uploaded', "
    "'document_downloaded', 'document_deleted', 'document_quarantined', 'document_requeued', "
    "'chat_shared', 'chat_unshared', 'chat_deleted', 'database_restored', 'profile_changed', "
    "'email_change_requested', 'email_changed', 'sign_in_paused', 'organization_created', "
    "'organization_renamed', 'ownership_transferred', 'member_left', 'invitation_sent', "
    "'invitation_revoked', 'invitation_accepted', 'joining_changed', 'member_joined', "
    "'member_removed', 'project_access_changed', 'seats_changed', 'entitlements_changed', "
    "'checkout_started', 'subscription_changed', 'payment_failed', 'pack_bought', "
    "'subscription_cancelled', 'cancellation_withdrawn', 'sample_added', 'project_restored', "
    "'project_purged', 'folder_restored', 'folder_purged', 'document_restored', 'document_purged', "
    "'chat_restored', 'chat_purged', 'settings_changed', 'audit_exported', 'api_key_created', "
    "'api_key_rolled', 'api_key_revoked', 'organization_deletion_requested', "
    "'organization_deletion_cancelled', 'project_exported', 'support_access_granted', "
    "'support_access_revoked', 'support_session_started', 'support_session_ended', 'flag_changed', "
    "'template_created', 'template_changed', 'template_deleted', 'fact_checked', 'fact_corrected', "
    "'fact_check_removed', 'facts_exported', 'cross_check_created', 'cross_check_changed', "
    "'cross_check_deleted', 'cross_checks_run', 'check_report_requested', 'check_run_deleted', "
    "'automation_created', 'automation_changed', 'automation_deleted', 'document_review_changed', "
    "'document_assigned', 'draft_created', 'draft_changed', 'draft_deleted', 'draft_exported', "
    "'draft_template_created', 'draft_template_changed', 'draft_template_deleted', "
    "'mailbox_created', 'mailbox_changed', 'mailbox_deleted', "
    "'connector_created', 'connector_changed', 'connector_deleted', 'webhook_created', "
    "'webhook_changed', 'webhook_rolled', 'webhook_deleted', 'share_link_created', "
    "'share_link_revoked', 'share_link_viewed', 'share_link_downloaded', 'chat_exported', "
    "'answer_exported', 'document_version_uploaded'"
)
ADDED_ACTIONS = ("filing_rule_created", "filing_rule_changed", "filing_rule_deleted")
NEW_ACTIONS = OLD_ACTIONS + "".join(f", '{action}'" for action in ADDED_ACTIONS)


def upgrade() -> None:
    op.create_table(
        "smart_folders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("filter", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
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
            ["created_by"],
            ["users.id"],
            name=op.f("smart_folders_created_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("smart_folders_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("smart_folders_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("smart_folders_pkey")),
    )
    op.create_index(
        op.f("ix_smart_folders_created_by"), "smart_folders", ["created_by"], unique=False
    )
    op.create_index(op.f("ix_smart_folders_org_id"), "smart_folders", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_smart_folders_project_id"), "smart_folders", ["project_id"], unique=False
    )
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("color", sa.String(length=20), server_default="grey", nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "color IN ('grey', 'red', 'orange', 'yellow', 'green', 'teal', 'blue', 'purple', "
            "'pink')",
            name="ck_tags_color",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("tags_created_by_fkey"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("tags_org_id_fkey"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("tags_project_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("tags_pkey")),
        sa.UniqueConstraint("project_id", "name", name=op.f("tags_project_id_name_key")),
    )
    op.create_index(op.f("ix_tags_created_by"), "tags", ["created_by"], unique=False)
    op.create_index(op.f("ix_tags_org_id"), "tags", ["org_id"], unique=False)
    op.create_table(
        "filing_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("condition", sa.String(length=10), nullable=False),
        sa.Column("field", sa.String(length=100), nullable=True),
        sa.Column("value", sa.String(length=200), nullable=True),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("folder_id", sa.Integer(), nullable=True),
        sa.Column("tag_id", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("(action = 'tag') = (tag_id IS NOT NULL)", name="ck_filing_rules_tag"),
        sa.CheckConstraint("(condition = 'any') = (value IS NULL)", name="ck_filing_rules_value"),
        sa.CheckConstraint(
            "(condition = 'fact') = (field IS NOT NULL)", name="ck_filing_rules_field"
        ),
        sa.CheckConstraint(
            "action <> 'folder' OR folder_id IS NOT NULL", name="ck_filing_rules_folder"
        ),
        sa.CheckConstraint(
            "action IN ('folder', 'by_year', 'by_month', 'tag')", name="ck_filing_rules_action"
        ),
        sa.CheckConstraint(
            "condition IN ('type', 'fact', 'any')", name="ck_filing_rules_condition"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("filing_rules_created_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["folder_id"],
            ["folders.id"],
            name=op.f("filing_rules_folder_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("filing_rules_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("filing_rules_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name=op.f("filing_rules_tag_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("filing_rules_pkey")),
    )
    op.create_index(
        op.f("ix_filing_rules_created_by"), "filing_rules", ["created_by"], unique=False
    )
    op.create_index(op.f("ix_filing_rules_folder_id"), "filing_rules", ["folder_id"], unique=False)
    op.create_index(op.f("ix_filing_rules_org_id"), "filing_rules", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_filing_rules_project_id"), "filing_rules", ["project_id"], unique=False
    )
    op.create_index(op.f("ix_filing_rules_tag_id"), "filing_rules", ["tag_id"], unique=False)
    op.create_table(
        "document_favorites",
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "starred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("document_favorites_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("document_favorites_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", "user_id", name=op.f("document_favorites_pkey")),
    )
    op.create_index(
        op.f("ix_document_favorites_user_id"), "document_favorites", ["user_id"], unique=False
    )
    op.create_table(
        "document_visits",
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("document_visits_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("document_visits_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("document_id", "user_id", name=op.f("document_visits_pkey")),
    )
    op.create_index(
        op.f("ix_document_visits_user_id"), "document_visits", ["user_id"], unique=False
    )
    op.add_column(
        "documents",
        sa.Column(
            "tag_ids",
            postgresql.ARRAY(sa.Integer()),
            server_default=sa.text("'{}'::integer[]"),
            nullable=False,
        ),
    )
    op.add_column("documents", sa.Column("filed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_documents_listing_added", "documents", ["project_id", "folder_id", "id"], unique=False
    )
    op.create_index(
        "ix_documents_listing_date",
        "documents",
        ["project_id", "folder_id", "doc_date", "id"],
        unique=False,
    )
    op.create_index(
        "ix_documents_listing_size",
        "documents",
        ["project_id", "folder_id", "size_bytes", "id"],
        unique=False,
    )
    op.create_index(
        "ix_documents_tag_ids", "documents", ["tag_ids"], unique=False, postgresql_using="gin"
    )
    op.execute(sa.text("UPDATE documents SET filed_at = now()"))
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    actions = ", ".join(f"'{action}'" for action in ADDED_ACTIONS)
    op.execute(sa.text(f"DELETE FROM audit_events WHERE action IN ({actions})"))
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_index("ix_documents_tag_ids", table_name="documents", postgresql_using="gin")
    op.drop_index("ix_documents_listing_size", table_name="documents")
    op.drop_index("ix_documents_listing_date", table_name="documents")
    op.drop_index("ix_documents_listing_added", table_name="documents")
    op.drop_column("documents", "filed_at")
    op.drop_column("documents", "tag_ids")
    op.drop_index(op.f("ix_document_visits_user_id"), table_name="document_visits")
    op.drop_table("document_visits")
    op.drop_index(op.f("ix_document_favorites_user_id"), table_name="document_favorites")
    op.drop_table("document_favorites")
    op.drop_index(op.f("ix_filing_rules_tag_id"), table_name="filing_rules")
    op.drop_index(op.f("ix_filing_rules_project_id"), table_name="filing_rules")
    op.drop_index(op.f("ix_filing_rules_org_id"), table_name="filing_rules")
    op.drop_index(op.f("ix_filing_rules_folder_id"), table_name="filing_rules")
    op.drop_index(op.f("ix_filing_rules_created_by"), table_name="filing_rules")
    op.drop_table("filing_rules")
    op.drop_index(op.f("ix_tags_org_id"), table_name="tags")
    op.drop_index(op.f("ix_tags_created_by"), table_name="tags")
    op.drop_table("tags")
    op.drop_index(op.f("ix_smart_folders_project_id"), table_name="smart_folders")
    op.drop_index(op.f("ix_smart_folders_org_id"), table_name="smart_folders")
    op.drop_index(op.f("ix_smart_folders_created_by"), table_name="smart_folders")
    op.drop_table("smart_folders")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
