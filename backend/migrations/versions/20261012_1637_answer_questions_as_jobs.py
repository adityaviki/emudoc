"""Answer questions as jobs.

Revision d3264966f7f4, written 2026-10-12.

Until now an answer was written in the request that asked the question: the API ran the agent
and streamed its events to the client, so an API restart or a dropped connection lost the
answer, and every answer in flight held a request thread while the model thought. Now the
question is saved with an empty answer whose `status` is `streaming` and an `answer_question`
job (app/answers.py): the worker runs the agent and appends each event to `chat_events`, and
the API streams them from there, from wherever a client left off. The answer's row holds the
finished answer once its status is anything else.

Every message written so far is `done`, which the column's default gives them. The downgrade
drops the events and the status; an answer still being written when it runs stays as its row
is, which is empty, and the jobs of the new kind go with the constraint that allowed them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d3264966f7f4"
down_revision: str | None = "e5c15aa5e982"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = (
    "'index_document', 'render_pages', 'send_email', 'purge_trash', 'sync_connector', "
    "'run_workflow'"
)
NEW_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'purge_trash', "
    "'sync_connector', 'run_workflow'"
)


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("status", sa.String(length=20), server_default="done", nullable=False),
    )
    op.create_check_constraint(
        "ck_chat_messages_status",
        "chat_messages",
        "status IN ('streaming', 'done', 'stopped', 'failed')",
    )
    op.create_index(
        "ix_chat_messages_streaming",
        "chat_messages",
        ["chat_id"],
        unique=False,
        postgresql_where=sa.text("status = 'streaming'"),
    )
    op.create_table(
        "chat_events",
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["chat_messages.id"],
            name=op.f("chat_events_message_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("message_id", "seq", name=op.f("chat_events_pkey")),
    )
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", f"kind IN ({NEW_KINDS})")


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM jobs WHERE kind = 'answer_question'"))
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", f"kind IN ({OLD_KINDS})")
    op.drop_table("chat_events")
    op.drop_index(
        "ix_chat_messages_streaming",
        table_name="chat_messages",
        postgresql_where=sa.text("status = 'streaming'"),
    )
    op.drop_constraint("ck_chat_messages_status", "chat_messages", type_="check")
    op.drop_column("chat_messages", "status")
