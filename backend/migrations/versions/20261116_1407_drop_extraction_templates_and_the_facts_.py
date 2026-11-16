"""Drop extraction templates and the facts table, with members' checks of facts and what a
reading changed.

Revision b8d9a8dd4ff8, written 2026-11-16.

The app is the documents and the agent with its tools (S.6, 2026-11-16): a project's extraction
templates go, the fields its documents of each kind were read for, with the built-in ones, which
were the code's, and with them the facts table that showed a kind's documents a row each, the
members' checks and corrections of a fact, and what a document's last reading changed of its
template's fields. The enrich stage reads every document's facts without a template, and the
agent's `query_facts` reads them across a project's documents.

`extraction_templates` goes with the projects' own templates in it, `fact_checks` with the
members' checks, `fact_changes` with what the readings changed, `documents.template` with the
kind each document was read for, and `facts.check_id` with its index. The facts stay, each with
the value the model read: a corrected fact kept the member's value in its own columns until its
document is read again. The audit log's `template_created`, `template_changed`,
`template_deleted`, `fact_checked`, `fact_corrected`, `fact_check_removed` and `facts_exported`
stay, for the entries already written.

Going back makes the tables and columns again, empty: no project has a template of its own, no
fact a check, and no document a kind it was read for until the enrich stage reads it again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8d9a8dd4ff8"
down_revision: str | None = "162cbce435ce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The facts' key to the checks first, since it names `fact_checks`; its index goes with it.
    op.drop_column("facts", "check_id")
    op.drop_column("documents", "template")
    # Their indexes go with them.
    op.drop_table("fact_checks")
    op.drop_table("fact_changes")
    op.drop_table("extraction_templates")


def downgrade() -> None:
    op.create_table(
        "extraction_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("doc_type", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("jsonb_typeof(fields) = 'array'", name="ck_extraction_templates_fields"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("extraction_templates_created_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("extraction_templates_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("extraction_templates_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("extraction_templates_pkey")),
        sa.UniqueConstraint(
            "project_id", "doc_type", name=op.f("extraction_templates_project_id_doc_type_key")
        ),
    )
    op.create_index(
        op.f("ix_extraction_templates_created_by"), "extraction_templates", ["created_by"]
    )
    op.create_index(op.f("ix_extraction_templates_org_id"), "extraction_templates", ["org_id"])

    op.create_table(
        "fact_changes",
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column(
            "read_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("template_before", sa.String(length=100), nullable=True),
        sa.Column("template_after", sa.String(length=100), nullable=True),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("changed", sa.Boolean(), nullable=False),
        sa.Column("checks_unheld", sa.Integer(), nullable=False),
        sa.CheckConstraint("jsonb_typeof(changes) = 'array'", name="ck_fact_changes_changes"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fact_changes_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fact_changes_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", name=op.f("fact_changes_pkey")),
    )
    op.create_index("ix_fact_changes_project_read", "fact_changes", ["project_id", "read_at"])

    op.create_table(
        "fact_checks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("read_value", sa.Text(), nullable=False),
        sa.Column("read_number", sa.Float(), nullable=True),
        sa.Column("read_date", sa.Date(), nullable=True),
        sa.Column("read_currency", sa.String(length=10), nullable=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("number", sa.Float(), nullable=True),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("checked_by", sa.Integer(), nullable=True),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["checked_by"],
            ["users.id"],
            name=op.f("fact_checks_checked_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fact_checks_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("fact_checks_pkey")),
    )
    op.create_index(op.f("ix_fact_checks_checked_by"), "fact_checks", ["checked_by"])
    op.create_index(op.f("ix_fact_checks_document_id"), "fact_checks", ["document_id"])

    op.add_column("documents", sa.Column("template", sa.String(length=100), nullable=True))
    op.add_column("facts", sa.Column("check_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("facts_check_id_fkey"),
        "facts",
        "fact_checks",
        ["check_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_facts_check_id",
        "facts",
        ["check_id"],
        postgresql_where=sa.text("check_id IS NOT NULL"),
    )
