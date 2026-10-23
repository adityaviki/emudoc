"""Keep members' checks of facts.

Revision 72089ad8c097, written 2026-10-23.

A member checks a fact the enrich stage read, or writes what the document says in its place
(app/fact_checks.py). The check is a row of its own, kept by the document, the field's name and
the model's reading, since a run writes the document's facts anew: a fact reading the same takes
it again through `facts.check_id`. The audit log's actions gain a fact checked, corrected, and
its check removed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "72089ad8c097"
down_revision: str | None = "f2536d2e7ff7"
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
    "'flag_changed', 'template_created', 'template_changed', 'template_deleted'"
)
CHECK_ACTIONS = ("fact_checked", "fact_corrected", "fact_check_removed")
NEW_ACTIONS = OLD_ACTIONS + "".join(f", '{action}'" for action in CHECK_ACTIONS)


def upgrade() -> None:
    op.create_table(
        "fact_checks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("read_value", sa.Text(), nullable=False),
        sa.Column("read_number", sa.Float(), nullable=True),
        sa.Column("read_date", sa.Date(), nullable=True),
        sa.Column("read_currency", sa.String(length=10), nullable=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("number", sa.Float(), nullable=True),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("checked_by", sa.Integer(), nullable=True),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["checked_by"],
            ["users.id"],
            name=op.f("fact_checks_checked_by_fkey"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fact_checks_document_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("fact_checks_pkey")),
    )
    op.create_index(op.f("ix_fact_checks_checked_by"), "fact_checks", ["checked_by"], unique=False)
    op.create_index(
        op.f("ix_fact_checks_document_id"), "fact_checks", ["document_id"], unique=False
    )
    op.add_column("facts", sa.Column("check_id", sa.BigInteger(), nullable=True))
    op.create_index(
        "ix_facts_check_id",
        "facts",
        ["check_id"],
        unique=False,
        postgresql_where=sa.text("check_id IS NOT NULL"),
    )
    op.create_foreign_key(
        op.f("facts_check_id_fkey"),
        "facts",
        "fact_checks",
        ["check_id"],
        ["id"],
        ondelete="SET NULL",
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM audit_events WHERE action = ANY(:actions)").bindparams(
            actions=list(CHECK_ACTIONS)
        )
    )
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_constraint(op.f("facts_check_id_fkey"), "facts", type_="foreignkey")
    op.drop_index(
        "ix_facts_check_id", table_name="facts", postgresql_where=sa.text("check_id IS NOT NULL")
    )
    op.drop_column("facts", "check_id")
    op.drop_index(op.f("ix_fact_checks_document_id"), table_name="fact_checks")
    op.drop_index(op.f("ix_fact_checks_checked_by"), table_name="fact_checks")
    op.drop_table("fact_checks")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
