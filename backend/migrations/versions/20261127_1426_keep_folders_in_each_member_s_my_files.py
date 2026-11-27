"""Keep folders in each member's My files.

Revision 6f3c6407860a, written 2026-11-27.

A member's My files held a flat list of their Word and Excel files; the owner asked on 2026-11-27
for folders there as the project's files have: a new folder, files uploaded into one and a whole
folder uploaded with its subfolders. `my_folders` holds them, each its member's alone in one
project and nested by `parent_id`, and `my_files.folder_id` says which a file is in, null for the
top. Deleting a folder deletes what it holds, for good, as a file of My files is deleted. The log's
actions, a check constraint, gain `my_folder_created`, `my_folder_renamed`, `my_folder_deleted`
and `my_file_moved`.

Going back deletes those rows, and every file in a folder with the folders, since the older
schema has nowhere to put them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6f3c6407860a"
down_revision: str | None = "c90ee014e661"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EARLIER_ACTIONS = (
    "'sign_up', 'sign_in', 'sign_in_failed', 'sign_in_paused', 'sign_out', 'session_revoked', "
    "'other_sessions_revoked', 'password_changed', 'password_reset_requested', "
    "'password_reset', 'profile_changed', 'email_change_requested', 'email_changed', "
    "'mfa_enabled', 'mfa_disabled', 'account_exported', 'terms_accepted', "
    "'account_deletion_requested', 'account_deletion_cancelled', 'account_purged', "
    "'support_access_granted', 'support_access_revoked', 'support_session_started', "
    "'support_session_ended', 'role_changed', 'plan_changed', 'seats_changed', "
    "'entitlements_changed', 'checkout_started', 'subscription_changed', "
    "'subscription_cancelled', 'cancellation_withdrawn', 'payment_failed', 'pack_bought', "
    "'organization_created', 'organization_renamed', 'organization_deletion_requested', "
    "'organization_deletion_cancelled', 'settings_changed', 'flag_changed', 'audit_exported', "
    "'api_key_created', 'api_key_rolled', 'api_key_revoked', 'webhook_created', "
    "'webhook_changed', 'webhook_rolled', 'webhook_deleted', 'ownership_transferred', "
    "'invitation_sent', 'invitation_revoked', 'invitation_accepted', 'joining_changed', "
    "'member_joined', 'member_left', 'member_removed', 'project_access_changed', "
    "'project_exported', 'template_created', 'template_changed', 'template_deleted', "
    "'fact_checked', 'fact_corrected', 'fact_check_removed', 'facts_exported', "
    "'cross_check_created', 'cross_check_changed', 'cross_check_deleted', 'cross_checks_run', "
    "'check_report_requested', 'check_run_deleted', 'automation_created', "
    "'automation_changed', 'automation_deleted', 'question_set_created', "
    "'question_set_changed', 'question_set_deleted', 'question_set_run', "
    "'question_report_deleted', 'filing_rule_created', 'filing_rule_changed', "
    "'filing_rule_deleted', 'draft_created', 'draft_changed', 'draft_deleted', "
    "'draft_exported', 'draft_template_created', 'draft_template_changed', "
    "'draft_template_deleted', 'mailbox_created', 'mailbox_changed', 'mailbox_deleted', "
    "'connector_created', 'connector_changed', 'connector_deleted', 'project_deleted', "
    "'project_restored', 'project_purged', 'sample_added', 'folder_deleted', "
    "'folder_restored', 'folder_purged', 'document_uploaded', 'document_version_uploaded', "
    "'document_downloaded', 'document_deleted', 'document_restored', 'document_purged', "
    "'document_quarantined', 'document_requeued', 'document_review_changed', "
    "'document_assigned', 'share_link_created', 'share_link_revoked', 'share_link_viewed', "
    "'share_link_downloaded', 'chat_shared', 'chat_unshared', 'chat_exported', "
    "'answer_exported', 'chat_deleted', 'chat_restored', 'chat_purged', 'comment_added', "
    "'comment_deleted', 'thread_resolved', 'thread_reopened', 'database_restored', "
    "'my_file_added', 'my_file_version_added', 'my_file_renamed', 'my_file_deleted', "
    "'my_file_downloaded', 'my_file_copied', 'reset_link_made'"
)
OLD_ACTIONS = f"{EARLIER_ACTIONS}, 'provider_switched', 'email_link_made'"
NEW_ACTIONS = (
    f"{OLD_ACTIONS}, 'my_folder_created', 'my_folder_renamed', 'my_folder_deleted', 'my_file_moved'"
)


def upgrade() -> None:
    op.create_table(
        "my_folders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("my_folders_org_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["my_folders.id"],
            name=op.f("my_folders_parent_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("my_folders_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("my_folders_user_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("my_folders_pkey")),
    )
    op.create_index(op.f("ix_my_folders_org_id"), "my_folders", ["org_id"], unique=False)
    op.create_index("ix_my_folders_owner", "my_folders", ["project_id", "user_id"], unique=False)
    op.create_index(op.f("ix_my_folders_parent_id"), "my_folders", ["parent_id"], unique=False)
    op.create_index(op.f("ix_my_folders_user_id"), "my_folders", ["user_id"], unique=False)
    op.add_column("my_files", sa.Column("folder_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_my_files_folder_id"), "my_files", ["folder_id"], unique=False)
    op.create_foreign_key(
        op.f("my_files_folder_id_fkey"),
        "my_files",
        "my_folders",
        ["folder_id"],
        ["id"],
        ondelete="CASCADE",
    )
    _allow(NEW_ACTIONS)


def downgrade() -> None:
    op.execute(
        "DELETE FROM audit_events WHERE action IN "
        "('my_folder_created', 'my_folder_renamed', 'my_folder_deleted', 'my_file_moved')"
    )
    _allow(OLD_ACTIONS)
    op.execute("DELETE FROM my_files WHERE folder_id IS NOT NULL")
    op.drop_constraint(op.f("my_files_folder_id_fkey"), "my_files", type_="foreignkey")
    op.drop_index(op.f("ix_my_files_folder_id"), table_name="my_files")
    op.drop_column("my_files", "folder_id")
    op.drop_index(op.f("ix_my_folders_user_id"), table_name="my_folders")
    op.drop_index(op.f("ix_my_folders_parent_id"), table_name="my_folders")
    op.drop_index("ix_my_folders_owner", table_name="my_folders")
    op.drop_index(op.f("ix_my_folders_org_id"), table_name="my_folders")
    op.drop_table("my_folders")


def _allow(values: str) -> None:
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.create_check_constraint("ck_audit_events_action", "audit_events", f"action IN ({values})")
