"""Offer no questions in an empty chat.

Revision 685e4c51a549, written 2026-11-11.

An empty chat no longer offers questions to ask, so the model writes none from a project's
documents: the projects lose the questions and when they were written, and the jobs their writing
(`suggest_questions`, the queued ones and those kept). The model calls already recorded as the
usage kind `suggestions` stay, being what those writings cost. A downgrade adds the columns again,
every project with no questions, the sample's too.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "685e4c51a549"
down_revision: str | None = "6d0a3f8e1c27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_JOB_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'apply_language', "
    "'export_project', 'sync_connector', 'run_workflow', 'run_automation', 'write_draft', "
    "'receive_mail', 'deliver_webhook', 'run_question_set', 'write_question_report', "
    "'send_digests', 'convert_document'"
)
OLD_JOB_KINDS = f"{NEW_JOB_KINDS}, 'suggest_questions'"


def upgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'suggest_questions'")
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", f"kind IN ({NEW_JOB_KINDS})")
    op.drop_column("projects", "suggested_at")
    op.drop_column("projects", "suggestions")


def downgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("suggestions", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
    )
    op.add_column("projects", sa.Column("suggested_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", f"kind IN ({OLD_JOB_KINDS})")
