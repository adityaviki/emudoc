"""Keep programs' idempotency keys.

Revision b613bb7c52ef, written 2026-10-24.

A program that makes something through the versioned API (app/api/v1/) may name the attempt in
an Idempotency-Key header (app/idempotency.py): the key is claimed for the API key that sent it,
with a fingerprint of the request and, once it is carried out, the answer, which the request
sent again is given in its place. It goes with its API key, and the housekeeper removes it a
day after.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b613bb7c52ef"
down_revision: str | None = "98934f0604c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["api_key_id"],
            ["api_keys.id"],
            name=op.f("idempotency_keys_api_key_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("idempotency_keys_pkey")),
        sa.UniqueConstraint("api_key_id", "key", name=op.f("idempotency_keys_api_key_id_key_key")),
    )
    op.create_index(
        op.f("ix_idempotency_keys_created_at"), "idempotency_keys", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_idempotency_keys_created_at"), table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
