"""Index the jobs by when they finished.

Revision 3dae99421cb1, written 2026-10-12.

The claim now shares the workers between organisations (app/jobs.py): for each organisation it
counts the jobs that are running and the jobs that finished within the last few minutes, and
takes the job of the organisation that has had the least. The finished ones it counts are a few
out of a month of history, which is kept for the operator; without this index every claim would
read all of it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3dae99421cb1"
down_revision: str | None = "22346a737bb1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_jobs_finished", "jobs", ["finished_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jobs_finished", table_name="jobs")
