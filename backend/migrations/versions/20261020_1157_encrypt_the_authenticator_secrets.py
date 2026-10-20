"""Encrypt the authenticator secrets.

Revision 960c8aaa9de2, written 2026-10-20.

An authenticator's secret is now kept encrypted with ENCRYPTION_KEYS (app/encrypted.py), which
is longer than the 32 base32 characters the column was sized for, so it becomes text. The
secrets already there stay as they were: a migration has no key and must not take one, so
`python -m app.maintenance encrypt-secrets` encrypts them, and the app reads them meanwhile.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "960c8aaa9de2"
down_revision: str | None = "2656abd623ec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "totp_secret",
        existing_type=sa.VARCHAR(length=64),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # The code before this revision would take an encrypted secret for the secret itself, and a
    # migration has no key to decrypt it with, so those accounts' two-factor sign-in is turned
    # off, as a downgrade drops what the earlier revision cannot hold; they set it up again.
    op.execute(
        sa.text(
            "UPDATE users SET totp_secret = NULL, totp_enabled_at = NULL, "
            "totp_last_step = NULL, recovery_code_hashes = '[]' WHERE totp_secret LIKE 'v1.%'"
        )
    )
    op.alter_column(
        "users",
        "totp_secret",
        existing_type=sa.Text(),
        type_=sa.VARCHAR(length=64),
        existing_nullable=True,
    )
