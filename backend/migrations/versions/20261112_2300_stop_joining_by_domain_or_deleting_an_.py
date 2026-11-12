"""Stop letting a member join an organisation by their address's domain, or delete one.

Revision 5c1e9a7d3b20, written 2026-11-12.

An install holds one workspace (S.1): nobody starts a second one, switches, leaves, joins by
their domain, or deletes the workspace, which would be the install itself. So the domains that
let an address join, the role they joined with, and the day an organisation was due to be
deleted go. A deletion that was due is dropped with its day: the organisation stays.

The audit log's actions of those (`joining_changed`, `member_joined`, `member_left`,
`organization_deletion_requested`, `organization_deletion_cancelled`) stay in its check
constraint, and their rows in the log: they are the team's record of what was done.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5c1e9a7d3b20"
down_revision: str | None = "b7d4e1f20c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_organizations_join_domains", table_name="organizations")
    op.drop_constraint("ck_organizations_join_role", "organizations", type_="check")
    op.drop_column("organizations", "join_domains")
    op.drop_column("organizations", "join_role")
    op.drop_column("organizations", "deletion_due_at")


def downgrade() -> None:
    op.add_column(
        "organizations", sa.Column("deletion_due_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "organizations",
        sa.Column("join_role", sa.String(length=20), server_default="member", nullable=False),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "join_domains",
            postgresql.ARRAY(sa.String(length=253)),
            server_default="{}",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_organizations_join_role", "organizations", "join_role IN ('member', 'viewer')"
    )
    op.create_index(
        "ix_organizations_join_domains",
        "organizations",
        ["join_domains"],
        postgresql_using="gin",
    )
