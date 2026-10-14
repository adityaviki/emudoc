"""Record the region of each organisation.

Revision 5fef4d549111, written 2026-10-14.

A deployment may now promise to keep its data in one region (DATA_REGION, app/residency.py), and
each organisation records the region it was promised (`organizations.region`), so that a
deployment in another region refuses to serve it. The database accepts the regions the app
knows. The organisations made before this have none: the first start that names a region
records it for them, once nothing contradicts it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5fef4d549111"
down_revision: str | None = "b02c5a6e01ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REGIONS = ("eu", "us")


def upgrade() -> None:
    op.add_column("organizations", sa.Column("region", sa.String(length=20), nullable=True))
    allowed = ", ".join(f"'{region}'" for region in REGIONS)
    op.create_check_constraint("ck_organizations_region", "organizations", f"region IN ({allowed})")


def downgrade() -> None:
    op.drop_constraint("ck_organizations_region", "organizations", type_="check")
    op.drop_column("organizations", "region")
