"""Drop cross-checks, their runs and automations.

Revision b8ee7b962950, written 2026-11-16.

The app is the documents and the agent with its tools (S.6, 2026-11-16): a project's
cross-checks go, the rules that compared a field of one kind's documents with another's, with
the runs that kept what they found and the discrepancy reports the agent wrote from them, and the
automations that ran them as documents arrived, with their notice and the webhooks' event
`check_run.completed`. A member asks the agent in a chat to check documents against each other,
and it cites where each figure is written.

`cross_checks`, `workflow_runs`, `workflow_findings`, `automations`, `automation_checks` and
`automation_arrivals` go with their keys and indexes, and the worker's `run_workflow` and
`run_automation` jobs go with their kinds, queued or kept. What each member and the workspace
chose for the notice of an automation's run goes; the notices of that kind already in the
centres stay, to read until NOTIFICATION_DAYS take them, so the notifications keep allowing the
kind (PAST_NOTICE_KINDS). Each webhook stops listening for `check_run.completed`, and the
delivery log's entries of it go with the tries still waiting, since the event is no longer one a
delivery may be of; after it a webhook hears of the documents alone. The audit log's
`cross_check_created`, `cross_check_changed`, `cross_check_deleted`, `cross_checks_run`,
`check_report_requested`, `check_run_deleted`, `automation_created`, `automation_changed` and
`automation_deleted` stay, for the entries already written, and so do the usage rows of the
reports, kind `report` with the run they were for as `workflow_run`, and the questions their
asking counted, being what those calls cost.

Going back makes the tables again, empty: no project has a cross-check, a run or an automation,
the members are told of an automation's run until they choose again, and a webhook listens for
`check_run.completed` once an admin chooses it again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8ee7b962950"
down_revision: str | None = "f149fd1a2183"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_KINDS = ("run_workflow", "run_automation")
NEW_JOB_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'apply_language', "
    "'export_project', 'sync_connector', 'receive_mail', 'deliver_webhook', "
    "'convert_document', 'view_my_file'"
)
OLD_JOB_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'apply_language', "
    "'export_project', 'sync_connector', 'run_workflow', 'run_automation', 'receive_mail', "
    "'deliver_webhook', 'convert_document', 'view_my_file'"
)
EVENT = "check_run.completed"
NEW_EVENTS = "'document.ready', 'document.failed'"
OLD_EVENTS = "'document.ready', 'document.failed', 'check_run.completed'"
NOTICE_KIND = "checks_run"


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

    # Their indexes and keys go with them; what names another table goes first.
    op.drop_table("automation_arrivals")
    op.drop_table("automation_checks")
    op.drop_table("workflow_findings")
    op.drop_table("workflow_runs")
    op.drop_table("automations")
    op.drop_table("cross_checks")

    for table in ("users", "organizations"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET notices = notices - :kind WHERE notices ? :kind"
            ).bindparams(kind=NOTICE_KIND)
        )


def downgrade() -> None:
    op.create_table(
        "cross_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("left_kind", sa.String(length=100), nullable=False),
        sa.Column("left_field", sa.String(length=100), nullable=False),
        sa.Column("right_kind", sa.String(length=100), nullable=False),
        sa.Column("right_field", sa.String(length=100), nullable=False),
        sa.Column("pairing", sa.String(length=20), nullable=False),
        sa.Column("left_key", sa.String(length=100), nullable=True),
        sa.Column("right_key", sa.String(length=100), nullable=True),
        sa.Column("by_item", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("comparison", sa.String(length=20), nullable=False),
        sa.Column("margin", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("summed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
            "comparison IN ('equal', 'at_most', 'at_least', 'same_words', 'same_numbers')",
            name="ck_cross_checks_comparison",
        ),
        sa.CheckConstraint(
            "(pairing = 'key' AND left_key IS NOT NULL AND right_key IS NOT NULL) "
            "OR (pairing <> 'key' AND left_key IS NULL AND right_key IS NULL)",
            name="ck_cross_checks_keys",
        ),
        sa.CheckConstraint("margin >= 0", name="ck_cross_checks_margin"),
        sa.CheckConstraint("pairing IN ('same', 'key', 'every')", name="ck_cross_checks_pairing"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("cross_checks_created_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("cross_checks_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("cross_checks_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("cross_checks_pkey")),
    )
    op.create_index("ix_cross_checks_created_by", "cross_checks", ["created_by"])
    op.create_index("ix_cross_checks_org_id", "cross_checks", ["org_id"])
    op.create_index("ix_cross_checks_project_id", "cross_checks", ["project_id"])

    op.create_table(
        "automations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=100), nullable=True),
        sa.Column("folder_id", sa.Integer(), nullable=True),
        sa.Column("every_check", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("report", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("audience", sa.String(length=20), server_default="maker", nullable=False),
        sa.Column("recheck", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
            "audience IN ('maker', 'project', 'nobody')", name="ck_automations_audience"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("automations_created_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["folder_id"],
            ["folders.id"],
            name=op.f("automations_folder_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("automations_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("automations_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("automations_pkey")),
    )
    op.create_index("ix_automations_created_by", "automations", ["created_by"])
    op.create_index("ix_automations_folder_id", "automations", ["folder_id"])
    op.create_index("ix_automations_org_id", "automations", ["org_id"])
    op.create_index("ix_automations_project_id", "automations", ["project_id"])

    op.create_table(
        "automation_checks",
        sa.Column("automation_id", sa.Integer(), nullable=False),
        sa.Column("cross_check_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["automation_id"],
            ["automations.id"],
            name=op.f("automation_checks_automation_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cross_check_id"],
            ["cross_checks.id"],
            name=op.f("automation_checks_cross_check_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "automation_id", "cross_check_id", name=op.f("automation_checks_pkey")
        ),
    )
    op.create_index("ix_automation_checks_cross_check_id", "automation_checks", ["cross_check_id"])

    op.create_table(
        "automation_arrivals",
        sa.Column("automation_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("recheck", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "arrived_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["automation_id"],
            ["automations.id"],
            name=op.f("automation_arrivals_automation_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("automation_arrivals_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "automation_id", "document_id", name=op.f("automation_arrivals_pkey")
        ),
    )
    op.create_index("ix_automation_arrivals_document_id", "automation_arrivals", ["document_id"])

    # The columns in the order the migrations that made the table and added to it left them.
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("report_status", sa.String(length=20), nullable=True),
        sa.Column("report", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "report_sources",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("report_notice", sa.Text(), nullable=True),
        sa.Column("report_error", sa.Text(), nullable=True),
        sa.Column(
            "usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("automation_id", sa.Integer(), nullable=True),
        sa.Column("trigger", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("told_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "report_status IN ('queued', 'writing', 'done', 'failed')",
            name="ck_workflow_runs_report_status",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')", name="ck_workflow_runs_status"
        ),
        sa.ForeignKeyConstraint(
            ["automation_id"],
            ["automations.id"],
            name=op.f("workflow_runs_automation_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("workflow_runs_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("workflow_runs_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("workflow_runs_user_id_fkey"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("workflow_runs_pkey")),
    )
    op.create_index("ix_workflow_runs_automation_id", "workflow_runs", ["automation_id"])
    op.create_index("ix_workflow_runs_org_id", "workflow_runs", ["org_id"])
    op.create_index("ix_workflow_runs_project", "workflow_runs", ["project_id", "id"])
    op.create_index(
        "ix_workflow_runs_reporting",
        "workflow_runs",
        ["org_id"],
        postgresql_where=sa.text("report_status IN ('queued', 'writing')"),
    )
    op.create_index("ix_workflow_runs_user_id", "workflow_runs", ["user_id"])

    op.create_table(
        "workflow_findings",
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("rule", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("left", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("right", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("partners", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("difference", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('differs', 'missing', 'holds')", name="ck_workflow_findings_outcome"
        ),
        sa.CheckConstraint(
            "reason IN ('holds', 'differs', 'currency', 'no_value', 'no_key', 'no_partner', "
            "'no_partner_value', 'no_item', 'untyped')",
            name="ck_workflow_findings_reason",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["workflow_runs.id"],
            name=op.f("workflow_findings_run_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "rule", "seq", name=op.f("workflow_findings_pkey")),
    )

    _check("webhook_deliveries", "event_type", OLD_EVENTS)
    _check("jobs", "kind", OLD_JOB_KINDS)


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
