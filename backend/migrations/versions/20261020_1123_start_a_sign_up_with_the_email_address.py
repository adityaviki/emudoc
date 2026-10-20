"""Start a sign-up with the email address.

Revision 2656abd623ec, written 2026-10-20.

A sign-up now asks for the address alone and mails it a link, and the account is made only
once the link is opened, so that the form answers alike whether the address has an account or
not. That link is a one-time link of its own purpose with no user yet, so a link's user may be
null, for that purpose alone, which a check constraint holds; the purposes are a check
constraint too, so they gain the value.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2656abd623ec"
down_revision: str | None = "30f8c6a34e56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_PURPOSES = "'verify_email', 'reset_password', 'change_email'"
NEW_PURPOSES = f"{OLD_PURPOSES}, 'sign_up'"
ONE_USER = "(user_id IS NULL) = (purpose = 'sign_up')"


def upgrade() -> None:
    op.alter_column("auth_tokens", "user_id", existing_type=sa.Integer(), nullable=True)
    _check("auth_tokens", "purpose", NEW_PURPOSES)
    op.create_check_constraint("ck_auth_tokens_user_id", "auth_tokens", ONE_USER)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM auth_tokens WHERE purpose = 'sign_up'"))
    op.drop_constraint("ck_auth_tokens_user_id", "auth_tokens", type_="check")
    _check("auth_tokens", "purpose", OLD_PURPOSES)
    op.alter_column("auth_tokens", "user_id", existing_type=sa.Integer(), nullable=False)


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
