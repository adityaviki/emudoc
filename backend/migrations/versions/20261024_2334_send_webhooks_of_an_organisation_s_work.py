"""Send webhooks of an organisation's work.

Revision bb533acac8ec, written 2026-10-24.

An organisation's owners and admins give its programs addresses to hear of its work at
(app/webhooks.py): a webhook is the address, the events it is told of and its signing secret,
encrypted, and each event posted to one is a delivery, kept for the log with how the address
answered its last try. Each try is a job of its own, `deliver_webhook`, which the jobs' kinds
gain; the audit log's actions gain a webhook added, changed, given a new secret and removed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bb533acac8ec"
down_revision: str | None = "dcb8fb991c33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_JOB_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'sync_connector', "
    "'run_workflow', 'apply_language', 'export_project', 'run_automation', 'write_draft', "
    "'receive_mail'"
)
NEW_JOB_KINDS = OLD_JOB_KINDS + ", 'deliver_webhook'"
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
    "'connector_created', 'connector_changed', 'connector_deleted'"
)
ADDED_ACTIONS = ("webhook_created", "webhook_changed", "webhook_rolled", "webhook_deleted")
NEW_ACTIONS = OLD_ACTIONS + "".join(f", '{action}'" for action in ADDED_ACTIONS)


def upgrade() -> None:
    op.create_table(
        "webhooks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("events", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("rolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("webhooks_created_by_fkey"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("webhooks_org_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("webhooks_pkey")),
    )
    op.create_index(op.f("ix_webhooks_org_id"), "webhooks", ["org_id"], unique=False)
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("webhook_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=40), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "event_type IN ('document.ready', 'document.failed', 'check_run.completed')",
            name="ck_webhook_deliveries_event_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed')", name="ck_webhook_deliveries_status"
        ),
        sa.ForeignKeyConstraint(
            ["webhook_id"],
            ["webhooks.id"],
            name=op.f("webhook_deliveries_webhook_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("webhook_deliveries_pkey")),
        sa.UniqueConstraint(
            "webhook_id", "job_id", name=op.f("webhook_deliveries_webhook_id_job_id_key")
        ),
    )
    op.create_index(
        op.f("ix_webhook_deliveries_created_at"), "webhook_deliveries", ["created_at"], unique=False
    )
    op.create_index(
        "ix_webhook_deliveries_hook", "webhook_deliveries", ["webhook_id", "id"], unique=False
    )
    _check("jobs", "kind", NEW_JOB_KINDS)
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    actions = ", ".join(f"'{action}'" for action in ADDED_ACTIONS)
    op.execute(sa.text(f"DELETE FROM audit_events WHERE action IN ({actions})"))
    _check("audit_events", "action", OLD_ACTIONS)
    op.execute(sa.text("DELETE FROM jobs WHERE kind = 'deliver_webhook'"))
    _check("jobs", "kind", OLD_JOB_KINDS)
    op.drop_index("ix_webhook_deliveries_hook", table_name="webhook_deliveries")
    op.drop_index(op.f("ix_webhook_deliveries_created_at"), table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index(op.f("ix_webhooks_org_id"), table_name="webhooks")
    op.drop_table("webhooks")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
