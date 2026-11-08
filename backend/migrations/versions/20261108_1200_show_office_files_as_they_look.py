"""Show Word, PowerPoint and Excel files as they look.

Revision 4b7d2e91c0a6, written 2026-11-08.

A Word, PowerPoint, Excel, RTF or HTML document gets a view (app/conversion.py): the PDF the
converter makes of its file, whose pages the document pane shows it by. A document keeps where
its view stands (`view_state`: queued, ready or failed; null for none), the size of each of its
pages and, while it failed, the sentence why; the jobs' kinds gain its conversion. The documents
from before have none, and get one the first time a member opens them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4b7d2e91c0a6"
down_revision: str | None = "9e1493ee558b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_JOB_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'apply_language', "
    "'export_project', 'sync_connector', 'run_workflow', 'run_automation', 'write_draft', "
    "'receive_mail', 'deliver_webhook', 'suggest_questions', 'run_question_set', "
    "'write_question_report', 'send_digests'"
)
NEW_JOB_KINDS = f"{OLD_JOB_KINDS}, 'convert_document'"


def upgrade() -> None:
    op.add_column("documents", sa.Column("view_state", sa.String(length=20), nullable=True))
    op.add_column(
        "documents",
        sa.Column(
            "view_page_sizes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.add_column("documents", sa.Column("view_error", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_documents_view_state", "documents", "view_state IN ('queued', 'ready', 'failed')"
    )
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", f"kind IN ({NEW_JOB_KINDS})")


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'convert_document'")
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", f"kind IN ({OLD_JOB_KINDS})")
    op.drop_constraint("ck_documents_view_state", "documents", type_="check")
    op.drop_column("documents", "view_error")
    op.drop_column("documents", "view_page_sizes")
    op.drop_column("documents", "view_state")
