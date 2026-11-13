"""Stop asking users to accept terms, and drop the organisations' region.

Revision 027580d54585, written 2026-11-13.

A team runs its own install (S.3), so the app publishes no terms of service or privacy policy
of its own for its users to accept, and no region it promised to keep their documents in: the
edition each user accepted, and when, goes from `users`, and the region each organisation
recorded goes from `organizations`, its check with it. The audit log's rows of terms accepted
stay, as their action, which the page still words.

Going back brings the columns back empty: nobody has accepted an edition, which the terms'
page then asked of everyone, and no organisation names a region, which a start that names one
recorded for them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "027580d54585"
down_revision: str | None = "8f89bdd0402c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Dropping the column drops its check, ck_organizations_region.
    op.drop_column("organizations", "region")
    op.drop_column("users", "terms_accepted_at")
    op.drop_column("users", "terms_version")


def downgrade() -> None:
    op.add_column("users", sa.Column("terms_version", sa.String(length=40), nullable=True))
    op.add_column(
        "users",
        sa.Column("terms_accepted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("organizations", sa.Column("region", sa.String(length=20), nullable=True))
    op.create_check_constraint("ck_organizations_region", "organizations", "region IN ('eu', 'us')")
