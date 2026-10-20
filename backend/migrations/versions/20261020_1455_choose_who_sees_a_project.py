"""Choose who sees a project.

Revision ba2e143b8d08, written 2026-10-20.

A project is seen by everyone in its organisation, as every project was until now, or it is
private, seen only by the members chosen for it and the organisation's owners and admins. The
visibility is a check constraint, and the chosen members a table of their own, whose rows go
with the project or the user; a member's every request asks which private projects they were
chosen for, hence the index by user. The audit log records a change to who sees a project, and
its actions are a check constraint, so it gains that value.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ba2e143b8d08"
down_revision: str | None = "df7a67e610b4"
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
    "'invitation_accepted', 'joining_changed', 'member_joined', 'member_removed'"
)
ADDED = ("project_access_changed",)
NEW_ACTIONS = ", ".join([OLD_ACTIONS, *(f"'{action}'" for action in ADDED)])


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "visibility", sa.String(length=20), server_default="organization", nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_projects_visibility", "projects", "visibility IN ('organization', 'private')"
    )
    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("project_members_project_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("project_members_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("project_members_pkey")),
        sa.UniqueConstraint(
            "project_id", "user_id", name=op.f("project_members_project_id_user_id_key")
        ),
    )
    op.create_index(
        op.f("ix_project_members_user_id"), "project_members", ["user_id"], unique=False
    )
    _check("audit_events", "action", NEW_ACTIONS)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM audit_events WHERE action = ANY(:added)").bindparams(added=list(ADDED))
    )
    _check("audit_events", "action", OLD_ACTIONS)
    op.drop_index(op.f("ix_project_members_user_id"), table_name="project_members")
    op.drop_table("project_members")
    op.drop_constraint("ck_projects_visibility", "projects", type_="check")
    op.drop_column("projects", "visibility")


def _check(table: str, column: str, values: str) -> None:
    name = f"ck_{table}_{column}"
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")
