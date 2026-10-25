"""Suggest questions written from a project's documents and after an answer.

Revision b55f9a8eea31, written 2026-10-25.

An empty chat offers questions the model wrote from the project's catalogue once its documents
settled (app/suggestions.py): the projects gain when they were last written, since they are
written again an hour after at the soonest, and the writing is a job kind of its own, its model
calls a usage kind of their own. An answer may suggest questions to ask next (the agent's
`suggest_follow_ups`), which the answers gain.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b55f9a8eea31"
down_revision: str | None = "b244c80cacf0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_JOB_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'apply_language', "
    "'export_project', 'sync_connector', 'run_workflow', 'run_automation', 'write_draft', "
    "'receive_mail', 'deliver_webhook'"
)
NEW_JOB_KINDS = OLD_JOB_KINDS + ", 'suggest_questions'"
OLD_USAGE_KINDS = (
    "'answer', 'upload', 'ocr', 'infer_sections', 'enrich', 'embed', 'render', 'report', 'draft'"
)
NEW_USAGE_KINDS = OLD_USAGE_KINDS + ", 'suggestions'"


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("follow_ups", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
    )
    op.add_column("projects", sa.Column("suggested_at", sa.DateTime(timezone=True), nullable=True))
    _check("jobs", "kind", NEW_JOB_KINDS)
    _check("usage_events", "kind", NEW_USAGE_KINDS)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM usage_events WHERE kind = 'suggestions'"))
    _check("usage_events", "kind", OLD_USAGE_KINDS)
    op.execute(sa.text("DELETE FROM jobs WHERE kind = 'suggest_questions'"))
    _check("jobs", "kind", OLD_JOB_KINDS)
    op.drop_column("projects", "suggested_at")
    op.drop_column("chat_messages", "follow_ups")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
