"""Ask a project's question sets on a schedule or as documents arrive.

Revision 28ce2d5915d8, written 2026-10-31.

A project's question sets (app/question_sets.py) are questions saved to be asked of its
documents on their own, every day or one day a week at an hour of the maker's zone, as documents
arrive, or both; the documents that arrived wait for the set's next report as arrivals. Each run
is a report (app/question_reports.py), its questions with their answers. The jobs' kinds gain the
set's waking and the report's answering, the notices a report kept, the webhooks' events
`report.completed`, and the audit log's actions a set made, changed, deleted and run, and a
report deleted.

The weekly digest of what arrived (app/digests.py) is a kind of notice a member turns on, told
on Monday morning of their zone by an hourly job; a membership keeps the Monday it last told.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "28ce2d5915d8"
down_revision: str | None = "cc9c6837752e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_JOB_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'apply_language', "
    "'export_project', 'sync_connector', 'run_workflow', 'run_automation', 'write_draft', "
    "'receive_mail', 'deliver_webhook', 'suggest_questions'"
)
ADDED_JOB_KINDS = ("run_question_set", "write_question_report", "send_digests")
OLD_NOTICE_KINDS = "'upload_indexed', 'quota', 'project_export', 'checks_run', 'review_assigned'"
ADDED_NOTICE_KINDS = ("report_ready", "weekly_digest")
OLD_EVENTS = "'document.ready', 'document.failed', 'check_run.completed'"
ADDED_EVENTS = ("report.completed",)
OLD_ACTIONS = (
    "'sign_up', 'sign_in', 'sign_in_failed', 'sign_in_paused', 'sign_out', 'session_revoked', "
    "'other_sessions_revoked', 'password_changed', 'password_reset_requested', 'password_reset', "
    "'profile_changed', 'email_change_requested', 'email_changed', 'mfa_enabled', 'mfa_disabled', "
    "'account_exported', 'terms_accepted', 'account_deletion_requested', "
    "'account_deletion_cancelled', 'account_purged', 'support_access_granted', "
    "'support_access_revoked', 'support_session_started', 'support_session_ended', "
    "'role_changed', 'plan_changed', 'seats_changed', 'entitlements_changed', 'checkout_started', "
    "'subscription_changed', 'subscription_cancelled', 'cancellation_withdrawn', "
    "'payment_failed', 'pack_bought', 'organization_created', 'organization_renamed', "
    "'organization_deletion_requested', 'organization_deletion_cancelled', 'settings_changed', "
    "'flag_changed', 'audit_exported', 'api_key_created', 'api_key_rolled', 'api_key_revoked', "
    "'webhook_created', 'webhook_changed', 'webhook_rolled', 'webhook_deleted', "
    "'ownership_transferred', 'invitation_sent', 'invitation_revoked', 'invitation_accepted', "
    "'joining_changed', 'member_joined', 'member_left', 'member_removed', "
    "'project_access_changed', 'project_exported', 'template_created', 'template_changed', "
    "'template_deleted', 'fact_checked', 'fact_corrected', 'fact_check_removed', "
    "'facts_exported', 'cross_check_created', 'cross_check_changed', 'cross_check_deleted', "
    "'cross_checks_run', 'check_report_requested', 'check_run_deleted', 'automation_created', "
    "'automation_changed', 'automation_deleted', 'filing_rule_created', 'filing_rule_changed', "
    "'filing_rule_deleted', 'draft_created', 'draft_changed', 'draft_deleted', 'draft_exported', "
    "'draft_template_created', 'draft_template_changed', 'draft_template_deleted', "
    "'mailbox_created', 'mailbox_changed', 'mailbox_deleted', 'connector_created', "
    "'connector_changed', 'connector_deleted', 'project_deleted', 'project_restored', "
    "'project_purged', 'sample_added', 'folder_deleted', 'folder_restored', 'folder_purged', "
    "'document_uploaded', 'document_version_uploaded', 'document_downloaded', 'document_deleted', "
    "'document_restored', 'document_purged', 'document_quarantined', 'document_requeued', "
    "'document_review_changed', 'document_assigned', 'share_link_created', 'share_link_revoked', "
    "'share_link_viewed', 'share_link_downloaded', 'chat_shared', 'chat_unshared', "
    "'chat_exported', 'answer_exported', 'chat_deleted', 'chat_restored', 'chat_purged', "
    "'database_restored'"
)
ADDED_ACTIONS = (
    "question_set_created",
    "question_set_changed",
    "question_set_deleted",
    "question_set_run",
    "question_report_deleted",
)
# Each widened check constraint: its table and column, the values it held, and those it gains.
WIDENED = (
    ("jobs", "kind", OLD_JOB_KINDS, ADDED_JOB_KINDS),
    ("notifications", "kind", OLD_NOTICE_KINDS, ADDED_NOTICE_KINDS),
    ("webhook_deliveries", "event_type", OLD_EVENTS, ADDED_EVENTS),
    ("audit_events", "action", OLD_ACTIONS, ADDED_ACTIONS),
)


def upgrade() -> None:
    op.create_table(
        "question_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("questions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schedule", sa.String(length=20), nullable=True),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("hour", sa.Integer(), server_default=sa.text("8"), nullable=False),
        sa.Column("on_arrivals", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("audience", sa.String(length=20), server_default="maker", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "(schedule = 'weekly') = (weekday IS NOT NULL)", name="ck_question_sets_weekday"
        ),
        sa.CheckConstraint(
            "audience IN ('maker', 'project', 'nobody')", name="ck_question_sets_audience"
        ),
        sa.CheckConstraint("schedule IN ('daily', 'weekly')", name="ck_question_sets_schedule"),
        sa.CheckConstraint("hour BETWEEN 0 AND 23", name="ck_question_sets_hour"),
        sa.CheckConstraint("schedule IS NOT NULL OR on_arrivals", name="ck_question_sets_runs"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_question_sets_weekday_range"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("question_sets_created_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("question_sets_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("question_sets_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("question_sets_pkey")),
    )
    op.create_index(
        op.f("ix_question_sets_created_by"), "question_sets", ["created_by"], unique=False
    )
    op.create_index(op.f("ix_question_sets_org_id"), "question_sets", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_question_sets_project_id"), "question_sets", ["project_id"], unique=False
    )
    op.create_table(
        "question_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("audience", sa.String(length=20), server_default="maker", nullable=False),
        sa.Column("arrived", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("told_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "audience IN ('maker', 'project', 'nobody')", name="ck_question_reports_audience"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')", name="ck_question_reports_status"
        ),
        sa.CheckConstraint(
            "trigger IN ('schedule', 'arrivals', 'member')", name="ck_question_reports_trigger"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("question_reports_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("question_reports_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["question_sets.id"],
            name=op.f("question_reports_set_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("question_reports_user_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("question_reports_pkey")),
    )
    op.create_index(
        op.f("ix_question_reports_org_id"), "question_reports", ["org_id"], unique=False
    )
    op.create_index(
        "ix_question_reports_project", "question_reports", ["project_id", "id"], unique=False
    )
    op.create_index(
        op.f("ix_question_reports_set_id"), "question_reports", ["set_id"], unique=False
    )
    op.create_index(
        op.f("ix_question_reports_user_id"), "question_reports", ["user_id"], unique=False
    )
    op.create_index(
        "ix_question_reports_writing",
        "question_reports",
        ["org_id"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "question_set_arrivals",
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column(
            "arrived_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("question_set_arrivals_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["question_sets.id"],
            name=op.f("question_set_arrivals_set_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("set_id", "document_id", name=op.f("question_set_arrivals_pkey")),
    )
    op.create_index(
        op.f("ix_question_set_arrivals_document_id"),
        "question_set_arrivals",
        ["document_id"],
        unique=False,
    )
    op.add_column("memberships", sa.Column("digested_on", sa.Date(), nullable=True))
    for table, column, old, added in WIDENED:
        _check(table, column, old + "".join(f", '{value}'" for value in added))


def downgrade() -> None:
    for table, column, old, added in WIDENED:
        op.execute(
            sa.text(f"DELETE FROM {table} WHERE {column} = ANY(:added)").bindparams(
                added=list(added)
            )
        )
        _check(table, column, old)
    op.drop_column("memberships", "digested_on")
    op.drop_index(op.f("ix_question_set_arrivals_document_id"), table_name="question_set_arrivals")
    op.drop_table("question_set_arrivals")
    op.drop_index(
        "ix_question_reports_writing",
        table_name="question_reports",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_index(op.f("ix_question_reports_user_id"), table_name="question_reports")
    op.drop_index(op.f("ix_question_reports_set_id"), table_name="question_reports")
    op.drop_index("ix_question_reports_project", table_name="question_reports")
    op.drop_index(op.f("ix_question_reports_org_id"), table_name="question_reports")
    op.drop_table("question_reports")
    op.drop_index(op.f("ix_question_sets_project_id"), table_name="question_sets")
    op.drop_index(op.f("ix_question_sets_org_id"), table_name="question_sets")
    op.drop_index(op.f("ix_question_sets_created_by"), table_name="question_sets")
    op.drop_table("question_sets")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
