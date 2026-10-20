"""Keep each upload with who sent it, and the notices sent about a month's pages and questions.

Revision f7e94b5276eb, written 2026-10-20.

A member whose upload takes a while to index is told when it is done (app/notices.py), which
needs to know which documents one request brought in and who sent them: an `uploads` row per
request, named by its documents. The documents from before have none, and are never told about.
The owners and admins of an organisation are told when a month's pages or questions reach 80 %
and 100 % of what it may use, once for each, which `quota_notices` records.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7e94b5276eb"
down_revision: str | None = "d2d7fd97b21b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quota_notices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("month", sa.DateTime(timezone=True), nullable=False),
        sa.Column("figure", sa.String(length=40), nullable=False),
        sa.Column("share", sa.Integer(), nullable=False),
        sa.Column("allowance", sa.Integer(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "figure IN ('pages_per_month', 'questions_per_month')", name="ck_quota_notices_figure"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("quota_notices_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("quota_notices_pkey")),
        sa.UniqueConstraint(
            "org_id",
            "month",
            "figure",
            "share",
            "allowance",
            name=op.f("quota_notices_org_id_month_figure_share_allowance_key"),
        ),
    )
    op.create_table(
        "uploads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("uploads_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("uploads_user_id_fkey"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("uploads_pkey")),
    )
    op.create_index(op.f("ix_uploads_project_id"), "uploads", ["project_id"], unique=False)
    op.create_index(
        "ix_uploads_unsettled",
        "uploads",
        ["id"],
        unique=False,
        postgresql_where=sa.text("settled_at IS NULL"),
    )
    op.create_index(op.f("ix_uploads_user_id"), "uploads", ["user_id"], unique=False)
    op.add_column("documents", sa.Column("upload_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_documents_upload_id"), "documents", ["upload_id"], unique=False)
    op.create_foreign_key(
        op.f("documents_upload_id_fkey"),
        "documents",
        "uploads",
        ["upload_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("documents_upload_id_fkey"), "documents", type_="foreignkey")
    op.drop_index(op.f("ix_documents_upload_id"), table_name="documents")
    op.drop_column("documents", "upload_id")
    op.drop_index(op.f("ix_uploads_user_id"), table_name="uploads")
    op.drop_index(
        "ix_uploads_unsettled", table_name="uploads", postgresql_where=sa.text("settled_at IS NULL")
    )
    op.drop_index(op.f("ix_uploads_project_id"), table_name="uploads")
    op.drop_table("uploads")
    op.drop_table("quota_notices")
