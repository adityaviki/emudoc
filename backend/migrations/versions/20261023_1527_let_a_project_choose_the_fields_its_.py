"""Let a project choose the fields its documents are read for.

Revision f2536d2e7ff7, written 2026-10-23.

An extraction template names the fields the enrich stage asks the model to find in a project's
documents of one kind (app/extraction.py): the project's own, in `extraction_templates`, or the
built-in one of its kind, which the code holds. A document keeps the kind of the template its
facts were read for, null for every document from before, read for none. The audit log's actions
gain a template made, changed and deleted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2536d2e7ff7"
down_revision: str | None = "1b1f199ae02f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = (
    "'sign_up', 'sign_in', 'sign_in_failed', 'sign_out', 'session_revoked', "
    "'other_sessions_revoked', 'password_changed', 'password_reset_requested', "
    "'password_reset', 'mfa_enabled', 'mfa_disabled', 'account_exported', 'terms_accepted', "
    "'account_deletion_requested', 'account_deletion_cancelled', 'account_purged', "
    "'role_changed', 'plan_changed', 'project_deleted', 'folder_deleted', 'document_uploaded', "
    "'document_downloaded', 'document_deleted', 'document_quarantined', 'document_requeued', "
    "'chat_shared', 'chat_unshared', 'chat_deleted', 'database_restored', 'profile_changed', "
    "'email_change_requested', 'email_changed', 'sign_in_paused', 'organization_created', "
    "'organization_renamed', 'ownership_transferred', 'member_left', 'invitation_sent', "
    "'invitation_revoked', 'invitation_accepted', 'joining_changed', 'member_joined', "
    "'member_removed', 'project_access_changed', 'seats_changed', 'entitlements_changed', "
    "'checkout_started', 'subscription_changed', 'payment_failed', 'pack_bought', "
    "'subscription_cancelled', 'cancellation_withdrawn', 'sample_added', 'project_restored', "
    "'project_purged', 'folder_restored', 'folder_purged', 'document_restored', "
    "'document_purged', 'chat_restored', 'chat_purged', 'settings_changed', 'audit_exported', "
    "'api_key_created', 'api_key_rolled', 'api_key_revoked', 'organization_deletion_requested', "
    "'organization_deletion_cancelled', 'project_exported', 'support_access_granted', "
    "'support_access_revoked', 'support_session_started', 'support_session_ended', "
    "'flag_changed'"
)
TEMPLATE_ACTIONS = ("template_created", "template_changed", "template_deleted")
NEW_ACTIONS = OLD_ACTIONS + "".join(f", '{action}'" for action in TEMPLATE_ACTIONS)


def upgrade() -> None:
    op.create_table(
        "extraction_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("doc_type", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.CheckConstraint("jsonb_typeof(fields) = 'array'", name="ck_extraction_templates_fields"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("extraction_templates_created_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("extraction_templates_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("extraction_templates_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("extraction_templates_pkey")),
        sa.UniqueConstraint(
            "project_id", "doc_type", name=op.f("extraction_templates_project_id_doc_type_key")
        ),
    )
    op.create_index(
        op.f("ix_extraction_templates_created_by"),
        "extraction_templates",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_extraction_templates_org_id"), "extraction_templates", ["org_id"], unique=False
    )
    op.add_column("documents", sa.Column("template", sa.String(length=100), nullable=True))
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM audit_events WHERE action = ANY(:actions)").bindparams(
            actions=list(TEMPLATE_ACTIONS)
        )
    )
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_column("documents", "template")
    op.drop_index(op.f("ix_extraction_templates_org_id"), table_name="extraction_templates")
    op.drop_index(op.f("ix_extraction_templates_created_by"), table_name="extraction_templates")
    op.drop_table("extraction_templates")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
