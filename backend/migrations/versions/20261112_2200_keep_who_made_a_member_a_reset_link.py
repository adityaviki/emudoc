"""Keep in the audit log who made a member a link to reset their password.

Revision b7d4e1f20c93, written 2026-11-12.

An install may mail nothing (app/signups.py), so an owner or admin makes a member who forgot
their password a link that sets it, to copy and hand on (app/api/organizations.py). Whoever
opens the link chooses the member's password, so the log keeps who made it. The log's actions
are a check constraint, so it gains this one.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7d4e1f20c93"
down_revision: str | None = "920709903bf8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = (
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
    "'my_file_downloaded', 'my_file_copied'"
)
NEW_ACTIONS = f"{OLD_ACTIONS}, 'reset_link_made'"


def upgrade() -> None:
    _allow(NEW_ACTIONS)


def downgrade() -> None:
    op.execute("DELETE FROM audit_events WHERE action = 'reset_link_made'")
    _allow(OLD_ACTIONS)


def _allow(values: str) -> None:
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.create_check_constraint("ck_audit_events_action", "audit_events", f"action IN ({values})")
