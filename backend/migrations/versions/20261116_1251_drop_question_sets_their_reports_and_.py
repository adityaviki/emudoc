"""Drop question sets, their reports and the weekly digest.

Revision f149fd1a2183, written 2026-11-16.

The app is the documents and the agent with its tools (S.6, 2026-11-16): a project's question
sets go, the questions saved to be asked of its documents every day, one day a week or as
documents arrived, with the reports that kept their answers, the notice of a report ready and the
webhooks' event `report.completed`; and so does the weekly digest, the Monday notice of what
arrived in the projects a member sees. A member asks the agent the same questions in a chat.

`question_sets`, `question_set_arrivals` and `question_reports` go with their keys and indexes,
`memberships` loses `digested_on`, the Monday each member was last told, and the worker's
`run_question_set`, `write_question_report` and `send_digests` jobs go with their kinds, queued
or kept. What each member and the workspace chose for the two kinds of notice goes; the notices
of those kinds already in the centres stay, to read until NOTIFICATION_DAYS take them, so the
notifications keep allowing the kinds (PAST_NOTICE_KINDS). Each webhook stops listening for
`report.completed`, and the delivery log's entries of it go with the tries still waiting, since
the event is no longer one a delivery may be of. The audit log's `question_set_created`,
`question_set_changed`, `question_set_deleted`, `question_set_run` and `question_report_deleted`
stay, for the entries already written, and so do the usage rows of the reports' answers, kind
`answer`, being what those calls cost.

Going back makes the tables and the column again, empty: no project has a question set, the
members are told of a report ready and not of the week's arrivals until they choose again, and a
webhook listens for `report.completed` once an admin chooses it again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f149fd1a2183"
down_revision: str | None = "cc45ffa7565c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_KINDS = ("run_question_set", "write_question_report", "send_digests")
NEW_JOB_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'apply_language', "
    "'export_project', 'sync_connector', 'run_workflow', 'run_automation', 'receive_mail', "
    "'deliver_webhook', 'convert_document', 'view_my_file'"
)
OLD_JOB_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'apply_language', "
    "'export_project', 'sync_connector', 'run_workflow', 'run_automation', 'receive_mail', "
    "'deliver_webhook', 'run_question_set', 'write_question_report', 'send_digests', "
    "'convert_document', 'view_my_file'"
)
EVENT = "report.completed"
NEW_EVENTS = "'document.ready', 'document.failed', 'check_run.completed'"
OLD_EVENTS = "'document.ready', 'document.failed', 'check_run.completed', 'report.completed'"
NOTICE_KINDS = ("report_ready", "weekly_digest")


def upgrade() -> None:
    op.execute(
        sa.text("DELETE FROM jobs WHERE kind = ANY(:kinds)").bindparams(kinds=list(JOB_KINDS))
    )
    _check("jobs", "kind", NEW_JOB_KINDS)

    op.execute(
        sa.text(
            "DELETE FROM jobs WHERE kind = 'deliver_webhook' AND status = 'queued' "
            "AND payload->>'event' = :event"
        ).bindparams(event=EVENT)
    )
    op.execute(
        sa.text("DELETE FROM webhook_deliveries WHERE event_type = :event").bindparams(event=EVENT)
    )
    _check("webhook_deliveries", "event_type", NEW_EVENTS)
    op.execute(
        sa.text("UPDATE webhooks SET events = events - :event WHERE events ? :event").bindparams(
            event=EVENT
        )
    )

    # Their indexes and keys go with them.
    op.drop_table("question_set_arrivals")
    op.drop_table("question_reports")
    op.drop_table("question_sets")
    op.drop_column("memberships", "digested_on")

    for table in ("users", "organizations"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET notices = notices - CAST(:kinds AS text[]) "
                "WHERE notices ?| CAST(:kinds AS text[])"
            ).bindparams(kinds=list(NOTICE_KINDS))
        )


def downgrade() -> None:
    op.add_column("memberships", sa.Column("digested_on", sa.Date(), nullable=True))
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
    op.create_index("ix_question_sets_created_by", "question_sets", ["created_by"])
    op.create_index("ix_question_sets_org_id", "question_sets", ["org_id"])
    op.create_index("ix_question_sets_project_id", "question_sets", ["project_id"])
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
    op.create_index("ix_question_reports_org_id", "question_reports", ["org_id"])
    op.create_index("ix_question_reports_project", "question_reports", ["project_id", "id"])
    op.create_index("ix_question_reports_set_id", "question_reports", ["set_id"])
    op.create_index("ix_question_reports_user_id", "question_reports", ["user_id"])
    op.create_index(
        "ix_question_reports_writing",
        "question_reports",
        ["org_id"],
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
        "ix_question_set_arrivals_document_id", "question_set_arrivals", ["document_id"]
    )

    _check("webhook_deliveries", "event_type", OLD_EVENTS)
    _check("jobs", "kind", OLD_JOB_KINDS)


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
