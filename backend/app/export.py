"""Copies of what is kept, for the people it is about.

`account_export` is the personal part of a subject access request: everything the app keeps
about a user as a person, as one document the account page offers for download (`GET
/api/auth/account/export`). That is the account, the providers it signs in through, its
memberships with the private projects it was chosen to see (app/access.py), its sessions (as
long as they are kept, app/auth.py) and the browsers it signed
in with (each one's first and last sign-in, never its cookie), the chats it started with
every message, its notices in the notification centre and how it is told each kind
(app/notices.py), the usage counted to it, and the rows of the audit logs that name it
(app/audit.py). It leaves out the password hash, the authenticator
secret and the recovery codes, which guard the data rather than describe the person, and the
organisations' documents, which belong to the organisation and go in its project export (3.6).
"""

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.bounces import address_hash
from app.models import (
    AuditEvent,
    Browser,
    Chat,
    Identity,
    MailSuppression,
    Membership,
    Notification,
    Organization,
    Project,
    ProjectMember,
    UsageEvent,
    User,
)
from app.models import UserSession as SessionRow
from app.notices import choices
from app.schemas import (
    AccountExport,
    ExportedAccount,
    ExportedActivity,
    ExportedBrowser,
    ExportedChat,
    ExportedIdentity,
    ExportedMembership,
    ExportedMessage,
    ExportedNotification,
    ExportedSession,
    ExportedSpared,
    ExportedUsage,
)

ABOUT = (
    "What Emudoc keeps about you: your account and the edition of the terms you accepted, "
    "how you are told each kind of notice and whether the mail provider said not to write to "
    "your address, how you sign in, the organisations you belong to and the private projects "
    "you were chosen to see, the browsers and devices you signed in from in the last month and "
    "when each browser you signed in with did so first and last, the chats you started with "
    "every question and answer, the notices in your notification centre, the usage counted to "
    "you, and what the audit logs of your "
    "organisations recorded you doing. Answers cite passages of documents as [P<number>]. "
    "The documents themselves are not in this file: they belong to the organisation that holds "
    "them. Your password and recovery codes are kept only as hashes, and they and your "
    "authenticator secret are left out."
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
    private_projects: dict[int, list[str]] = defaultdict(list)
    for org_id, name in session.execute(
        select(Project.org_id, Project.name)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id)
        .order_by(Project.name, Project.id)
    ):
        private_projects[org_id].append(name)
    sessions = session.scalars(
        select(SessionRow).where(SessionRow.user_id == user.id).order_by(SessionRow.id)
    ).all()
    browsers = session.scalars(
        select(Browser).where(Browser.user_id == user.id).order_by(Browser.id)
    ).all()
    chats = session.execute(
        select(Chat, Project.name, Organization.slug)
        .join(Project, Project.id == Chat.project_id)
        .join(Organization, Organization.id == Project.org_id)
        .where(Chat.user_id == user.id)
        .order_by(Chat.created_at, Chat.id)
        .options(selectinload(Chat.messages))
    ).all()
    notifications = session.execute(
        select(Notification, Organization.slug)
        .join(Organization, Organization.id == Notification.org_id)
        .where(Notification.user_id == user.id)
        .order_by(Notification.id)
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
            time_zone=user.time_zone,
            created_at=user.created_at,
            email_verified_at=user.email_verified_at,
            two_factor_enabled_at=user.totp_enabled_at,
            terms_version=user.terms_version,
            terms_accepted_at=user.terms_accepted_at,
            deletion_due_at=user.deletion_due_at,
            sign_in_providers=[
                ExportedIdentity(provider=row.provider, email=row.email, linked_at=row.created_at)
                for row in identities
            ],
            notices=choices(user),
            checklist_hidden_at=user.checklist_hidden_at,
            tour_seen_at=user.tour_seen_at,
            mail_spared=(
                ExportedSpared(reason=spared.reason, recorded_at=spared.recorded_at)
                if (spared := session.get(MailSuppression, address_hash(user.email)))
                else None
            ),
        ),
        organizations=[
            ExportedMembership(
                organization=organization.name,
                slug=organization.slug,
                role=membership.role,
                joined_at=membership.created_at,
                private_projects=private_projects[organization.id],
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
        browsers=[
            ExportedBrowser(first_signed_in_at=row.created_at, last_signed_in_at=row.signed_in_at)
            for row in browsers
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
        notifications=[
            ExportedNotification(
                organization=slug,
                kind=row.kind,
                title=row.title,
                body=row.body,
                link=row.link,
                created_at=row.created_at,
                read_at=row.read_at,
            )
            for row, slug in notifications
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
