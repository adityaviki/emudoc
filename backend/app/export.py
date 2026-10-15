"""Copies of what is kept, for the people it is about.

`account_export` is the personal part of a subject access request: everything the app keeps
about a user as a person, as one document the account page offers for download (`GET
/api/auth/account/export`). That is the account, the providers it signs in through, its
memberships, its sessions (as long as they are kept, app/auth.py), the chats it started with
every message, the usage counted to it, and the rows of the audit logs that name it
(app/audit.py). It leaves out the password hash, the authenticator
secret and the recovery codes, which guard the data rather than describe the person, and the
organisations' documents, which belong to the organisation and go in its project export (3.6).
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    AuditEvent,
    Chat,
    Identity,
    Membership,
    Organization,
    Project,
    UsageEvent,
    User,
)
from app.models import UserSession as SessionRow
from app.schemas import (
    AccountExport,
    ExportedAccount,
    ExportedActivity,
    ExportedChat,
    ExportedIdentity,
    ExportedMembership,
    ExportedMessage,
    ExportedSession,
    ExportedUsage,
)

ABOUT = (
    "What Emudoc keeps about you: your account, how you sign in, the organisations you belong "
    "to, the browsers and devices you signed in from in the last month, the chats you started "
    "with every question and answer, the usage counted to you, and what the audit logs of "
    "your organisations recorded you doing. Answers cite passages of documents as "
    "[P<number>]. The documents themselves are not in this file: they belong to the "
    "organisation that holds them. Your password and recovery codes are kept only as hashes, "
    "and they and your authenticator secret are left out."
)


def account_export(session: Session, user: User) -> AccountExport:
    identities = session.scalars(
        select(Identity).where(Identity.user_id == user.id).order_by(Identity.id)
    ).all()
    memberships = session.execute(
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.org_id)
        .where(Membership.user_id == user.id)
        .order_by(Membership.created_at, Membership.id)
    ).all()
    sessions = session.scalars(
        select(SessionRow).where(SessionRow.user_id == user.id).order_by(SessionRow.id)
    ).all()
    chats = session.execute(
        select(Chat, Project.name, Organization.slug)
        .join(Project, Project.id == Chat.project_id)
        .join(Organization, Organization.id == Project.org_id)
        .where(Chat.user_id == user.id)
        .order_by(Chat.created_at, Chat.id)
        .options(selectinload(Chat.messages))
    ).all()
    usage = session.execute(
        select(UsageEvent, Organization.slug)
        .join(Organization, Organization.id == UsageEvent.org_id)
        .where(UsageEvent.user_id == user.id)
        .order_by(UsageEvent.occurred_at, UsageEvent.id)
    ).all()
    activity = session.execute(
        select(AuditEvent, Organization.slug)
        .join(Organization, Organization.id == AuditEvent.org_id)
        .where(AuditEvent.user_id == user.id)
        .order_by(AuditEvent.id)
    ).all()
    return AccountExport(
        exported_at=datetime.now(UTC),
        about=ABOUT,
        account=ExportedAccount(
            email=user.email,
            name=user.name,
            created_at=user.created_at,
            email_verified_at=user.email_verified_at,
            two_factor_enabled_at=user.totp_enabled_at,
            deletion_due_at=user.deletion_due_at,
            sign_in_providers=[
                ExportedIdentity(provider=row.provider, email=row.email, linked_at=row.created_at)
                for row in identities
            ],
        ),
        organizations=[
            ExportedMembership(
                organization=organization.name,
                slug=organization.slug,
                role=membership.role,
                joined_at=membership.created_at,
            )
            for membership, organization in memberships
        ],
        sessions=[
            ExportedSession(
                user_agent=row.user_agent,
                ip=row.ip,
                created_at=row.created_at,
                last_seen_at=row.last_seen_at,
                expires_at=row.expires_at,
                revoked_at=row.revoked_at,
            )
            for row in sessions
        ],
        chats=[
            ExportedChat(
                organization=slug,
                project=project,
                title=chat.title,
                visibility=chat.visibility,
                created_at=chat.created_at,
                last_question_at=chat.updated_at,
                messages=[
                    ExportedMessage(
                        role=message.role,
                        content=message.content,
                        status=message.status,
                        notice=message.notice,
                        error=message.error,
                        created_at=message.created_at,
                    )
                    for message in chat.messages
                ],
            )
            for chat, project, slug in chats
        ],
        usage=[
            ExportedUsage(
                organization=slug,
                kind=row.kind,
                unit=row.unit,
                quantity=row.quantity,
                model=row.model,
                occurred_at=row.occurred_at,
            )
            for row, slug in usage
        ],
        activity=[
            ExportedActivity(
                organization=slug,
                action=row.action,
                ip=row.ip,
                detail=row.detail,
                occurred_at=row.occurred_at,
            )
            for row, slug in activity
        ],
    )
