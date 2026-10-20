"""Pause sign-ins after many wrong passwords, except from the browsers an account signed in with.

Revision 30f8c6a34e56, written 2026-10-20.

Wrong passwords and codes are now counted over an hour as well as a minute, and past a number
the sign-ins they come from are paused (app/ratelimit.py). So that a stranger who knows an
address cannot keep its owner out that way, the browsers that signed in to an account are
counted apart, each by itself: a table of them, one row per account and browser, holding a
hash of the browser's cookie. The audit log records the pause, and its actions are a check
constraint, so it gains the value.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "30f8c6a34e56"
down_revision: str | None = "acecf87de76f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = (
    "'sign_up', 'sign_in', 'sign_in_failed', 'sign_out', 'session_revoked', "
    "'other_sessions_revoked', 'password_changed', 'password_reset_requested', "
    "'password_reset', 'mfa_enabled', 'mfa_disabled', 'account_exported', 'terms_accepted', "
    "'account_deletion_requested', 'account_deletion_cancelled', 'account_purged', "
    "'role_changed', 'plan_changed', 'project_deleted', 'folder_deleted', "
    "'document_uploaded', 'document_downloaded', 'document_deleted', "
    "'document_quarantined', 'document_requeued', 'chat_shared', 'chat_unshared', "
    "'chat_deleted', 'database_restored', 'profile_changed', 'email_change_requested', "
    "'email_changed'"
)
NEW_ACTIONS = f"{OLD_ACTIONS}, 'sign_in_paused'"


def upgrade() -> None:
    op.create_table(
        "browsers",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "signed_in_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("browsers_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("browsers_pkey")),
        sa.UniqueConstraint("user_id", "token_hash", name=op.f("browsers_user_id_token_hash_key")),
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM audit_events WHERE action = 'sign_in_paused'"))
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_table("browsers")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
