"""Let an organisation's owners and admins make API keys.

Revision ee389d8b7c92, written 2026-10-21.

The initial schema made `api_keys` for keys of the organisation's own, outliving the member who
made them, which nothing ever wrote. A key now acts as the member who made it (app/apikeys.py),
so it names them and goes with their membership: `user_id`, with a foreign key to the membership
that cascades, in place of `created_by_id`; one `scope`, read or write, in place of a list;
`token_hash` and `hint` in the names the other credentials use; `rolled_at`; and no
`revoked_at`, since revoking deletes the key. Any row there would be a key that acts as nobody,
so the table is emptied first. The audit log's actions gain `api_key_created`,
`api_key_rolled` and `api_key_revoked`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ee389d8b7c92"
down_revision: str | None = "269a4afb127b"
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
    "'email_changed', 'sign_in_paused', 'organization_created', 'organization_renamed', "
    "'ownership_transferred', 'member_left', 'invitation_sent', 'invitation_revoked', "
    "'invitation_accepted', 'joining_changed', 'member_joined', 'member_removed', "
    "'project_access_changed', 'seats_changed', 'entitlements_changed', 'checkout_started', "
    "'subscription_changed', 'payment_failed', 'pack_bought', 'subscription_cancelled', "
    "'cancellation_withdrawn', 'sample_added', 'project_restored', 'project_purged', "
    "'folder_restored', 'folder_purged', 'document_restored', 'document_purged', "
    "'chat_restored', 'chat_purged', 'settings_changed', 'audit_exported'"
)
NEW_ACTIONS = f"{OLD_ACTIONS}, 'api_key_created', 'api_key_rolled', 'api_key_revoked'"


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM api_keys"))
    op.drop_constraint("api_keys_key_hash_key", "api_keys", type_="unique")
    op.drop_constraint("api_keys_created_by_id_fkey", "api_keys", type_="foreignkey")
    for column in ("scopes", "revoked_at", "created_by_id", "key_prefix", "key_hash"):
        op.drop_column("api_keys", column)
    op.add_column("api_keys", sa.Column("user_id", sa.Integer(), nullable=False))
    op.add_column("api_keys", sa.Column("scope", sa.String(length=10), nullable=False))
    op.add_column("api_keys", sa.Column("token_hash", sa.String(length=64), nullable=False))
    op.add_column("api_keys", sa.Column("hint", sa.String(length=12), nullable=False))
    op.add_column("api_keys", sa.Column("rolled_at", sa.DateTime(timezone=True)))
    op.alter_column(
        "api_keys",
        "name",
        existing_type=sa.String(length=200),
        type_=sa.String(length=100),
        existing_nullable=False,
    )
    op.create_unique_constraint("api_keys_token_hash_key", "api_keys", ["token_hash"])
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_foreign_key(
        "api_keys_user_id_fkey", "api_keys", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        "api_keys_org_id_user_id_fkey",
        "api_keys",
        "memberships",
        ["org_id", "user_id"],
        ["org_id", "user_id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint("ck_api_keys_scope", "api_keys", "scope IN ('read', 'write')")
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM audit_events WHERE action IN "
            "('api_key_created', 'api_key_rolled', 'api_key_revoked')"
        )
    )
    _check("audit_events", "action", OLD_ACTIONS)
    op.execute(sa.text("DELETE FROM api_keys"))
    op.drop_constraint("ck_api_keys_scope", "api_keys", type_="check")
    op.drop_constraint("api_keys_org_id_user_id_fkey", "api_keys", type_="foreignkey")
    op.drop_constraint("api_keys_user_id_fkey", "api_keys", type_="foreignkey")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_constraint("api_keys_token_hash_key", "api_keys", type_="unique")
    op.alter_column(
        "api_keys",
        "name",
        existing_type=sa.String(length=100),
        type_=sa.String(length=200),
        existing_nullable=False,
    )
    for column in ("rolled_at", "hint", "token_hash", "scope", "user_id"):
        op.drop_column("api_keys", column)
    op.add_column("api_keys", sa.Column("key_hash", sa.String(length=64), nullable=False))
    op.add_column("api_keys", sa.Column("key_prefix", sa.String(length=12), nullable=False))
    op.add_column("api_keys", sa.Column("created_by_id", sa.Integer()))
    op.add_column("api_keys", sa.Column("revoked_at", sa.DateTime(timezone=True)))
    op.add_column(
        "api_keys",
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_foreign_key(
        "api_keys_created_by_id_fkey",
        "api_keys",
        "users",
        ["created_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("api_keys_key_hash_key", "api_keys", ["key_hash"])


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
