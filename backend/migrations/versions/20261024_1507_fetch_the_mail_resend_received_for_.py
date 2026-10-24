"""Fetch the mail Resend received for projects' addresses.

Revision 2aa96e183a87, written 2026-10-24.

A message Resend received for a project's address is fetched from its API and taken in by a job
of its own (app/receiving.py), `receive_mail`, which the jobs' kinds gain.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2aa96e183a87"
down_revision: str | None = "1db4bf8f1b2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_JOB_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'sync_connector', "
    "'run_workflow', 'apply_language', 'export_project', 'run_automation', 'write_draft'"
)
NEW_JOB_KINDS = OLD_JOB_KINDS + ", 'receive_mail'"


def upgrade() -> None:
    _check("jobs", "kind", NEW_JOB_KINDS)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM jobs WHERE kind = 'receive_mail'"))
    _check("jobs", "kind", OLD_JOB_KINDS)


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
