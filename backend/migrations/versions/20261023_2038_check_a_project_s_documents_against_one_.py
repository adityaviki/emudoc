"""Check a project's documents against one another by saved rules.

Revision 3aa3ef0d299c, written 2026-10-23.

A cross-check compares a field of a project's documents of one kind with a field of the same
document, of the documents of another kind that state the same key, or of every document of
another kind (app/cross_checks.py): an invoice's prices with the price list's, its quantities
with its delivery notes'. The rules are kept in `cross_checks`; what they find is worked out from
the facts when it is asked for, so nothing else is kept. The audit log's actions gain a rule
made, changed and deleted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3aa3ef0d299c"
down_revision: str | None = "e0b100aad97e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = (
    "'sign_up', 'sign_in', 'sign_in_failed', 'sign_out', 'session_revoked', "
    "'other_sessions_revoked', 'password_changed', 'password_reset_requested', "
    "'password_reset', 'mfa_enabled', 'mfa_disabled', 'account_exported', 'terms_accepted', "
    "'account_deletion_requested', 'account_deletion_cancelled', 'account_purged', "
    "'role_changed', 'plan_changed', 'project_deleted', 'folder_deleted', 'document_uploaded', "
    "'document_downloaded', 'document_deleted', 'document_quarantined', 'document_requeued', "
    "'chat_shared', 'chat_unshared', 'chat_deleted', 'database_restored', 'profile_changed', "
    "'email_change_requested', 'email_changed', 'sign_in_paused', 'organization_created', "
    "'organization_renamed', 'ownership_transferred', 'member_left', 'invitation_sent', "
    "'invitation_revoked', 'invitation_accepted', 'joining_changed', 'member_joined', "
    "'member_removed', 'project_access_changed', 'seats_changed', 'entitlements_changed', "
    "'checkout_started', 'subscription_changed', 'payment_failed', 'pack_bought', "
    "'subscription_cancelled', 'cancellation_withdrawn', 'sample_added', 'project_restored', "
    "'project_purged', 'folder_restored', 'folder_purged', 'document_restored', "
    "'document_purged', 'chat_restored', 'chat_purged', 'settings_changed', 'audit_exported', "
    "'api_key_created', 'api_key_rolled', 'api_key_revoked', 'organization_deletion_requested', "
    "'organization_deletion_cancelled', 'project_exported', 'support_access_granted', "
    "'support_access_revoked', 'support_session_started', 'support_session_ended', "
    "'flag_changed', 'template_created', 'template_changed', 'template_deleted', "
    "'fact_checked', 'fact_corrected', 'fact_check_removed', 'facts_exported'"
)
RULE_ACTIONS = ("cross_check_created", "cross_check_changed", "cross_check_deleted")
NEW_ACTIONS = OLD_ACTIONS + "".join(f", '{action}'" for action in RULE_ACTIONS)


def upgrade() -> None:
    op.create_table(
        "cross_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("left_kind", sa.String(length=100), nullable=False),
        sa.Column("left_field", sa.String(length=100), nullable=False),
        sa.Column("right_kind", sa.String(length=100), nullable=False),
        sa.Column("right_field", sa.String(length=100), nullable=False),
        sa.Column("pairing", sa.String(length=20), nullable=False),
        sa.Column("left_key", sa.String(length=100), nullable=True),
        sa.Column("right_key", sa.String(length=100), nullable=True),
        sa.Column("by_item", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("comparison", sa.String(length=20), nullable=False),
        sa.Column("margin", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("summed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(pairing = 'key' AND left_key IS NOT NULL AND right_key IS NOT NULL) "
            "OR (pairing <> 'key' AND left_key IS NULL AND right_key IS NULL)",
            name="ck_cross_checks_keys",
        ),
        sa.CheckConstraint(
            "comparison IN ('equal', 'at_most', 'at_least', 'same_words', 'same_numbers')",
            name="ck_cross_checks_comparison",
        ),
        sa.CheckConstraint("pairing IN ('same', 'key', 'every')", name="ck_cross_checks_pairing"),
        sa.CheckConstraint("margin >= 0", name="ck_cross_checks_margin"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("cross_checks_created_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("cross_checks_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("cross_checks_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("cross_checks_pkey")),
    )
    op.create_index(
        op.f("ix_cross_checks_created_by"), "cross_checks", ["created_by"], unique=False
    )
    op.create_index(op.f("ix_cross_checks_org_id"), "cross_checks", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_cross_checks_project_id"), "cross_checks", ["project_id"], unique=False
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.drop_index(op.f("ix_cross_checks_project_id"), table_name="cross_checks")
    op.drop_index(op.f("ix_cross_checks_org_id"), table_name="cross_checks")
    op.drop_index(op.f("ix_cross_checks_created_by"), table_name="cross_checks")
    op.drop_table("cross_checks")
    actions = ", ".join(f"'{action}'" for action in RULE_ACTIONS)
    op.execute(sa.text(f"DELETE FROM audit_events WHERE action IN ({actions})"))
    _check("audit_events", "action", OLD_ACTIONS)


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
