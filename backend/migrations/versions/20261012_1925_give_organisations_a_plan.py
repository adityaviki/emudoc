"""Give organisations a plan.

Revision 9f8860b09021, written 2026-10-12.

Each organisation is now on a plan (`organizations.plan`), which names the entitlements it is
held to (app/entitlements.py): how many projects, documents, pages and questions it gets, and
the rest. The database accepts the plans the app knows. Every organisation made before this
starts on the free plan, as a new one does, except the local development account, which has
no limits: it stands for one developer on their own machine.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f8860b09021"
down_revision: str | None = "29c97f1a6f4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLANS = ("free", "pro", "team", "enterprise")
LOCAL_ORG_SLUG = "local"


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("plan", sa.String(length=20), server_default="free", nullable=False),
    )
    allowed = ", ".join(f"'{plan}'" for plan in PLANS)
    op.create_check_constraint("ck_organizations_plan", "organizations", f"plan IN ({allowed})")
    op.execute(
        sa.text("UPDATE organizations SET plan = 'enterprise' WHERE slug = :slug").bindparams(
            slug=LOCAL_ORG_SLUG
        )
    )


def downgrade() -> None:
    op.drop_constraint("ck_organizations_plan", "organizations", type_="check")
    op.drop_column("organizations", "plan")
