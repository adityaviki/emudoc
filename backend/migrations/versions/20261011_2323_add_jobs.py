"""Add the job queue.

Revision 22346a737bb1, written 2026-10-11.

Until now the `documents` table was the queue: a worker claimed rows whose `status` was `queued`,
and a process that started set every `processing` row back to `queued`, which made two replicas
run the pipeline on the same document. Background work becomes a row in `jobs` (app/jobs.py):
claimed with `FOR UPDATE SKIP LOCKED` as before, but leased to the worker for a few minutes at a
time, so a job whose worker died is claimed again when its lease runs out and nothing at start
has to guess. `documents.status` and `stages` stay what the UI shows.

Every document that is waiting gets its job here: the `queued` ones, and the `processing` ones,
which belong to the workers of the version being replaced; those are set back to `queued` one
last time. Stop the old replicas before this runs, as a release does: an old worker still running
would claim queued documents by their status while a new one claims their jobs.

The downgrade drops the table. The documents keep their `queued` status, which is all the old
worker needs; a job that was `running` leaves its document `processing`, which the old worker's
start resets.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "22346a737bb1"
down_revision: str | None = "5c1e7a9d2b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column(
            "run_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('index_document', 'render_pages', 'send_email', 'purge_trash', "
            "'sync_connector', 'run_workflow')",
            name="ck_jobs_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')", name="ck_jobs_status"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("jobs_org_id_fkey"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("jobs_project_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("jobs_pkey")),
    )
    op.create_index(op.f("ix_jobs_org_id"), "jobs", ["org_id"], unique=False)
    op.create_index(op.f("ix_jobs_project_id"), "jobs", ["project_id"], unique=False)
    op.create_index("ix_jobs_queue", "jobs", ["status", "priority", "run_at"], unique=False)

    op.execute(
        sa.text(
            "INSERT INTO jobs (org_id, project_id, kind, payload) "
            "SELECT p.org_id, d.project_id, 'index_document', "
            "jsonb_build_object('document_id', d.id) "
            "FROM documents AS d JOIN projects AS p ON p.id = d.project_id "
            "WHERE d.status IN ('queued', 'processing') ORDER BY d.id"
        )
    )
    op.execute(sa.text("UPDATE documents SET status = 'queued' WHERE status = 'processing'"))


def downgrade() -> None:
    op.drop_index("ix_jobs_queue", table_name="jobs")
    op.drop_index(op.f("ix_jobs_project_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_org_id"), table_name="jobs")
    op.drop_table("jobs")
