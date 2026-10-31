"""Keep the version of the prompts on each usage row.

Revision 4ec681672ab5, written 2026-11-01.

A kind of model work's prompts have a version, a hash of what the model is told (3.14,
app/prompts.py), which the rows of its calls now carry, so that the `costs` command can split a
kind's cost by the prompts that spent it. The rows there are now were written before and keep
null, as do the rows of the kinds that ask no model.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4ec681672ab5"
down_revision: str | None = "f7c88e6c67de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("usage_events", sa.Column("prompt_version", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("usage_events", "prompt_version")
