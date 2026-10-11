"""Store the text search language on each passage.

Revision 27bd56b4a29e, written 2026-10-11.

Until now `passages.tsv` was generated with the configuration FTS_LANGUAGE named when the
initial migration ran, so the setting was part of the schema and every passage shared it. Now
each passage carries its configuration in `language` (a `regconfig`, since a generated column's
expression must be immutable and casting text to one is not) and `tsv` is generated from that.
Rows that exist get the configured language, which is the one their `tsv` was built with; the
column's default is `english` from then on, and the indexing stage writes the setting itself.
Per-document language detection will write what it finds into the same column.

A search lists the languages a project holds before parsing the query once per language, so the
index on `project_id` becomes one on `(project_id, language)`, which serves both.

Generated columns cannot be altered in place: `tsv` is dropped and added again, which rebuilds
its index and rewrites the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.config import get_settings

revision: str = "27bd56b4a29e"
down_revision: str | None = "bedb624406a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    configured = get_settings().fts_language
    op.drop_index("ix_passages_tsv", table_name="passages")
    op.drop_column("passages", "tsv")
    op.add_column(
        "passages",
        sa.Column("language", postgresql.REGCONFIG(), server_default=configured, nullable=False),
    )
    op.alter_column("passages", "language", server_default="english")
    op.add_column(
        "passages",
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector(language, coalesce(context, '')), 'B') || "
                "setweight(to_tsvector(language, text), 'A')",
                persisted=True,
            ),
            nullable=False,
        ),
    )
    op.create_index("ix_passages_tsv", "passages", ["tsv"], unique=False, postgresql_using="gin")
    op.drop_index(op.f("ix_passages_project_id"), table_name="passages")
    op.create_index(
        "ix_passages_project_language", "passages", ["project_id", "language"], unique=False
    )


def downgrade() -> None:
    configured = get_settings().fts_language
    op.drop_index("ix_passages_project_language", table_name="passages")
    op.create_index(op.f("ix_passages_project_id"), "passages", ["project_id"], unique=False)
    op.drop_index("ix_passages_tsv", table_name="passages")
    op.drop_column("passages", "tsv")
    op.drop_column("passages", "language")
    op.add_column(
        "passages",
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed(
                f"setweight(to_tsvector('{configured}', coalesce(context, '')), 'B') || "
                f"setweight(to_tsvector('{configured}', text), 'A')",
                persisted=True,
            ),
            nullable=False,
        ),
    )
    op.create_index("ix_passages_tsv", "passages", ["tsv"], unique=False, postgresql_using="gin")
