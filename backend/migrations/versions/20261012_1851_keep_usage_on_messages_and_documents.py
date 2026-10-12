"""Keep usage on messages and documents.

Revision 29c97f1a6f4f, written 2026-10-12.

An answer and a document now carry what they used, for the page that shows them: a `usage`
column on `chat_messages` and on `documents`, keyed by the kind of work, holding each unit's
quantity summed over every run for the row, the model when one was called, and the cost when
the models have prices (app/usage.py keeps it in step as the events are recorded). The
events recorded before the column are summed into it here, so no answer or document shows
less than it used.
"""

from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "29c97f1a6f4f"
down_revision: str | None = "4d17fad8beb9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The table each kind of reference names, as app/usage.py names them.
TABLES = {"chat_message": "chat_messages", "document": "documents"}


def upgrade() -> None:
    for table in TABLES.values():
        op.add_column(
            table,
            sa.Column(
                "usage",
                postgresql.JSONB(astext_type=sa.Text()),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
        )
    _sum_recorded_usage()


def downgrade() -> None:
    for table in TABLES.values():
        op.drop_column(table, "usage")


def _sum_recorded_usage() -> None:
    """Fill the new columns from the events already recorded, the way app/usage.py adds to
    them from then on: each unit summed by kind, the model the latest row named, the cost
    summed when any row has one."""
    connection = op.get_bind()
    events = connection.execute(
        sa.text(
            "SELECT ref_type, ref_id, kind, unit, quantity, model, cost_micros "
            "FROM usage_events WHERE ref_type IS NOT NULL ORDER BY id"
        )
    )
    usage: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    for ref_type, ref_id, kind, unit, quantity, model, cost_micros in events:
        spent = usage[(ref_type, ref_id)].setdefault(kind, {})
        spent[unit] = spent.get(unit, 0) + quantity
        if model is not None:
            spent["model"] = model
        if cost_micros is not None:
            spent["cost_micros"] = spent.get("cost_micros", 0) + cost_micros
    for (ref_type, ref_id), spent in usage.items():
        connection.execute(
            sa.text(f"UPDATE {TABLES[ref_type]} SET usage = :usage WHERE id = :id").bindparams(
                sa.bindparam("usage", type_=postgresql.JSONB)
            ),
            {"usage": spent, "id": ref_id},
        )
