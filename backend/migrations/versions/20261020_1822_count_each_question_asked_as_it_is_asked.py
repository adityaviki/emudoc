"""Count each question asked as it is asked.

Revision 920efab20e8b, written 2026-10-20.

A plan's questions a month were the assistant messages of the month still in the
organisation's chats, so a chat deleted, by its member or by the retention, gave its questions
back, and no history of them outlived the chats. Each question is now a usage event of its
own, `answer` in `questions`, written as it is asked (app/api/chats.py), which the plan, the
usage page and the `usage` command count. The units are a check constraint, so it gains that
value, and the questions of the chats still kept are written as such events at the time they
were asked, so that the month counts what it counted before; those of chats already gone
cannot be.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "920efab20e8b"
down_revision: str | None = "0151920be687"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_UNITS = "'calls', 'input_tokens', 'output_tokens', 'pages', 'bytes', 'inputs'"
NEW_UNITS = f"{OLD_UNITS}, 'questions'"


def upgrade() -> None:
    _check("usage_events", "unit", NEW_UNITS)
    # An assistant message is a question's answer, made with the question (app/answers.py).
    op.execute(
        """
        INSERT INTO usage_events (org_id, project_id, user_id, kind, unit, quantity, occurred_at)
        SELECT projects.org_id, chats.project_id, chats.user_id, 'answer', 'questions', 1,
               chat_messages.created_at
        FROM chat_messages
        JOIN chats ON chats.id = chat_messages.chat_id
        JOIN projects ON projects.id = chats.project_id
        WHERE chat_messages.role = 'assistant'
        """
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM usage_events WHERE unit = 'questions'"))
    _check("usage_events", "unit", OLD_UNITS)


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
