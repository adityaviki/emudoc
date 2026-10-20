"""Keep what members delete in a trash.

Revision 9def24510a24, written 2026-10-21.

Deleting a project, a folder, a document or a chat puts it in the trash (app/trash.py) rather
than removing it: the row says when, and the first three who, and the housekeeper purges it
TRASH_DAYS later unless a member restores it or deletes it for good first. Each table gets a
partial index of its rows in the trash, which are few beside the live ones, and one on who
deleted them for the account purge's foreign key. The audit log's actions, a check constraint,
gain the restore and the purge of each. The jobs lose `purge_trash`, reserved for the trash and
never queued, since the purge is part of the housekeeping round.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9def24510a24"
down_revision: str | None = "a990052a728b"
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
    "'cancellation_withdrawn', 'sample_added'"
)
ADDED = tuple(
    f"{kind}_{act}"
    for kind in ("project", "folder", "document", "chat")
    for act in ("restored", "purged")
)
NEW_ACTIONS = ", ".join([OLD_ACTIONS, *(f"'{action}'" for action in ADDED)])
OLD_KINDS = (
    "'index_document', 'answer_question', 'render_pages', 'send_email', 'purge_trash', "
    "'sync_connector', 'run_workflow'"
)
NEW_KINDS = OLD_KINDS.replace("'purge_trash', ", "")
# The tables whose rows go into the trash, the organisation's or the project's column their
# trash is listed by, and whether they say who deleted them (a chat is always its starter's).
TRASHED = (
    ("projects", "org_id", True),
    ("folders", "project_id", True),
    ("documents", "project_id", True),
    ("chats", "project_id", False),
)


def upgrade() -> None:
    for table, scope, by in TRASHED:
        op.add_column(table, sa.Column("deleted_at", sa.DateTime(timezone=True)))
        op.create_index(
            f"ix_{table}_trashed",
            table,
            [scope, "deleted_at"],
            postgresql_where=sa.text("deleted_at IS NOT NULL"),
        )
        if by:
            op.add_column(table, sa.Column("deleted_by", sa.Integer()))
            op.create_foreign_key(
                f"{table}_deleted_by_fkey",
                table,
                "users",
                ["deleted_by"],
                ["id"],
                ondelete="SET NULL",
            )
            op.create_index(
                f"ix_{table}_deleted_by",
                table,
                ["deleted_by"],
                postgresql_where=sa.text("deleted_by IS NOT NULL"),
            )
    _check("audit_events", "action", NEW_ACTIONS)
    _check("jobs", "kind", NEW_KINDS)


def downgrade() -> None:
    _check("jobs", "kind", OLD_KINDS)
    op.execute(
        sa.text("DELETE FROM audit_events WHERE action = ANY(:added)").bindparams(added=list(ADDED))
    )
    _check("audit_events", "action", OLD_ACTIONS)
    # What was in the trash goes for good, as a delete went before it, its files left for the
    # orphan sweep (app/maintenance.py). A project first, then the folders, which take their
    # subtrees, then what is left of the documents and the chats.
    for table, _, _ in TRASHED:
        op.execute(sa.text(f"DELETE FROM {table} WHERE deleted_at IS NOT NULL"))
    for table, _, by in reversed(TRASHED):
        if by:
            op.drop_index(f"ix_{table}_deleted_by", table_name=table)
            op.drop_constraint(f"{table}_deleted_by_fkey", table, type_="foreignkey")
            op.drop_column(table, "deleted_by")
        op.drop_index(f"ix_{table}_trashed", table_name=table)
        op.drop_column(table, "deleted_at")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
