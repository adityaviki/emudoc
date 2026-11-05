"""Drop filing rules.

Revision ca622aa5d107, written 2026-11-05.

Filing rules are gone from the library (2026-11-05): nothing moves or tags a new document on its
own any more. The rules go with their table, and `documents.filed_at`, which said a document's
rules had been applied, with them; the documents stay where they are, with the tags they carry.
The audit log's `filing_rule_*` actions stay, so that the entries already written still read.
A downgrade makes the table again, empty, and marks every document filed, as the migration that
first made them did, so that a rule made after files only what arrives after it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ca622aa5d107"
down_revision: str | None = "45a76579a0ca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Its indexes go with it.
    op.drop_table("filing_rules")
    op.drop_column("documents", "filed_at")


def downgrade() -> None:
    op.add_column("documents", sa.Column("filed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE documents SET filed_at = now()"))
    op.create_table(
        "filing_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("condition", sa.String(length=10), nullable=False),
        sa.Column("field", sa.String(length=100), nullable=True),
        sa.Column("value", sa.String(length=200), nullable=True),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("folder_id", sa.Integer(), nullable=True),
        sa.Column("tag_id", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("(action = 'tag') = (tag_id IS NOT NULL)", name="ck_filing_rules_tag"),
        sa.CheckConstraint("(condition = 'any') = (value IS NULL)", name="ck_filing_rules_value"),
        sa.CheckConstraint(
            "(condition = 'fact') = (field IS NOT NULL)", name="ck_filing_rules_field"
        ),
        sa.CheckConstraint(
            "action <> 'folder' OR folder_id IS NOT NULL", name="ck_filing_rules_folder"
        ),
        sa.CheckConstraint(
            "action IN ('folder', 'by_year', 'by_month', 'tag')", name="ck_filing_rules_action"
        ),
        sa.CheckConstraint(
            "condition IN ('type', 'fact', 'any')", name="ck_filing_rules_condition"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("filing_rules_created_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["folder_id"],
            ["folders.id"],
            name=op.f("filing_rules_folder_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("filing_rules_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("filing_rules_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name=op.f("filing_rules_tag_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("filing_rules_pkey")),
    )
    op.create_index(
        op.f("ix_filing_rules_created_by"), "filing_rules", ["created_by"], unique=False
    )
    op.create_index(op.f("ix_filing_rules_folder_id"), "filing_rules", ["folder_id"], unique=False)
    op.create_index(op.f("ix_filing_rules_org_id"), "filing_rules", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_filing_rules_project_id"), "filing_rules", ["project_id"], unique=False
    )
    op.create_index(op.f("ix_filing_rules_tag_id"), "filing_rules", ["tag_id"], unique=False)
