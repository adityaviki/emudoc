"""Drop the writing templates.

Revision 162cbce435ce, written 2026-11-16.

The app is the documents and the agent with its tools (S.6, 2026-11-16): a project's writing
templates go, how the documents of each kind the agent wrote into a member's My files read (a
reply email, a dispute letter, a summary memo, a comparison table, a report), with the built-in
ones, which were the code's, and the agent's `get_template`, which read the one in force. The
agent writes a letter or a report to whatever outline the member gives in the chat.

`draft_templates` goes with its keys and indexes, and the projects' own templates in it. The
audit log's `draft_template_created`, `draft_template_changed` and `draft_template_deleted` stay,
for the entries already written.

Going back makes the table again, empty: every project writes to the built-in templates.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "162cbce435ce"
down_revision: str | None = "b8ee7b962950"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = "'reply_email', 'dispute_letter', 'summary_memo', 'comparison_table', 'report'"


def upgrade() -> None:
    # Its indexes go with it.
    op.drop_table("draft_templates")


def downgrade() -> None:
    op.create_table(
        "draft_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("tone", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "sections",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("guidance", sa.Text(), server_default="", nullable=False),
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
        sa.CheckConstraint(f"kind IN ({KINDS})", name="ck_draft_templates_kind"),
        sa.CheckConstraint("jsonb_typeof(sections) = 'array'", name="ck_draft_templates_sections"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("draft_templates_created_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("draft_templates_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("draft_templates_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("draft_templates_pkey")),
        sa.UniqueConstraint("project_id", "kind", name=op.f("draft_templates_project_id_kind_key")),
    )
    op.create_index(op.f("ix_draft_templates_created_by"), "draft_templates", ["created_by"])
    op.create_index(op.f("ix_draft_templates_org_id"), "draft_templates", ["org_id"])
