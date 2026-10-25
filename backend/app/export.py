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
organisations' documents, which belong to the organisation and go in its projects' exports.

A project's export (`make_export`, the worker's `export_project` job, asked for in
app/api/exports.py) is the organisation's part, for portability and for a member who wants
everything at once: one ZIP file with every document's original in its folders, `project.json`
listing the project, its folders, its documents and the chats the member may read (those shared
with the project and their own), and `documents/<id>.json` with what was read from each: its
outline, its passages, which answers cite as [P<id>], and its facts. What is in the trash and the
documents the malware scan set apart are left out, and so is an original the scan has not passed
yet. A project of thousands of documents is read a batch at a time and written into a temporary
file, never held whole, then put in the storage beside the project's documents (`export_key` in
app/storage.py), and its member is told (app/notices.py). It is kept EXPORT_DAYS: the row is
pruned by the housekeeping and the file goes in its walk of the storage by its age, so a file
whose row went another way goes too.
"""

import logging
import tempfile
import zipfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path, PurePosixPath

from sqlalchemy import Connection, delete, func, or_, select, update
from sqlalchemy.orm import Session, selectinload, undefer

from app.accounts import zone_of
from app.bounces import address_hash
from app.db import SessionLocal
from app.errors import summary
from app.extraction import templates_for
from app.fact_checks import check_out
from app.folders import PATH_SEPARATOR, folder_paths
from app.jobs import EXPORT_PROJECT, Lease, LeaseLost, enqueue, renew
from app.mail import in_organization, project_export_email
from app.models import (
    AuditEvent,
    Browser,
    Chat,
    ChatMessage,
    ChatPin,
    Document,
    DocumentFavorite,
    DocumentVersion,
    DocumentVisit,
    Fact,
    Identity,
    MailSuppression,
    Membership,
    Notification,
    Organization,
    Passage,
    Project,
    ProjectExport,
    ProjectMember,
    Section,
    ShareLink,
    SupportGrant,
    UsageEvent,
    User,
)
from app.models import UserSession as SessionRow
from app.notices import PROJECT_EXPORT, app_address, choices, tell
from app.retention import day
from app.scanning import scan_passed
from app.schemas import (
    AccountExport,
    ExportedAccount,
    ExportedActivity,
    ExportedBlock,
    ExportedBrowser,
    ExportedBy,
    ExportedChat,
    ExportedDocument,
    ExportedDocumentData,
    ExportedFact,
    ExportedFavorite,
    ExportedIdentity,
    ExportedMembership,
    ExportedMessage,
    ExportedNotification,
    ExportedPassage,
    ExportedPin,
    ExportedProject,
    ExportedProjectChat,
    ExportedProjectShareLink,
    ExportedSection,
    ExportedSession,
    ExportedShareLink,
    ExportedSpared,
    ExportedTag,
    ExportedTemplate,
    ExportedUsage,
    ExportedVersion,
    ExportedVersionText,
    ExportedVisit,
    ProjectExportFile,
    TemplateFieldOut,
)
from app.storage import Storage, export_key
from app.tags import project_tags

log = logging.getLogger(__name__)

ABOUT = (
    "What Emudoc keeps about you: your account and the edition of the terms you accepted, how you "
    "are told each kind of notice and whether the mail provider said not to write to your address, "
    "how you sign in, the organisations you belong to and the private projects you were chosen to "
    "see, the browsers and devices you signed in from in the last month and when each browser you "
    "signed in with did so first and last, the chats you started with every question and answer, "
    "the chats you pinned, the documents you starred and the ones you opened lately, the links you "
    "made that show a document or an answer to whoever holds them, the notices in your "
    "notification centre, the usage counted to you, and what the audit logs of your organisations "
    "recorded you doing. Answers cite passages of documents as [P<number>]. The documents "
    "themselves are not in this file: they belong to the organisation that holds them. Your "
    "password and recovery codes are kept only as hashes, and they and your authenticator secret "
    "are left out."
)

# How long a project's export is kept for the member who asked for it, once it is ready: long
# enough to come back for it after the notice, short enough that a copy of everything the project
# held does not outlive by much the documents a member deletes.
EXPORT_DAYS = 7
EXPORT_KEPT = timedelta(days=EXPORT_DAYS)
# How many documents one read takes: a project of thousands is read in pieces, a session each.
BATCH = 100
# Formats compressed already, which are stored as they are: deflating them again costs time and
# saves nothing.
COMPRESSED = frozenset(
    {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".zip"}
)

PROJECT_ABOUT = (
    "A copy of a project in Emudoc, for the member named in exported_by. originals/ holds each "
    "document as it was uploaded, in its folders, once the malware scan passed it, and versions/ "
    "the files a document had before a new version took their place. project.json lists the "
    "project, its folders, its tags, its documents with theirs and the chats about them: those "
    "shared with the project and the member's own, whose answers cite passages as [P<number>]. "
    "documents/ holds what was read from each document: its outline, its passages by number, and "
    "the facts found in it, each with the passage it came from. templates holds the project's own "
    "extraction templates, the fields its documents of each kind are read for, and share_links the "
    "links that show its documents and the answers of its chats to whoever holds them, without "
    "their addresses. What is in the trash is left out, and so are other members' private chats."
)
EXPORT_FAILED = (
    "The export could not be made. Ask for another; if that one fails too, write to support."
)
PROJECT_GONE = "The project went into the trash before its export was made."
# The projects page's query that opens a project's exports, which the notice leads to.
EXPORT_QUERY = "export"


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
    leaves = dict(
        session.execute(
            select(SupportGrant.org_id, SupportGrant.expires_at).where(
                SupportGrant.user_id == user.id, SupportGrant.expires_at > func.now()
            )
        ).all()
    )
    sessions = session.scalars(
        select(SessionRow).where(SessionRow.user_id == user.id).order_by(SessionRow.id)
    ).all()
    browsers = session.scalars(
        select(Browser).where(Browser.user_id == user.id).order_by(Browser.id)
    ).all()
    chats = session.execute(
        select(Chat, Project.name, Organization.slug, Project.deleted_at)
        .join(Project, Project.id == Chat.project_id)
        .join(Organization, Organization.id == Project.org_id)
        .where(Chat.user_id == user.id)
        .order_by(Chat.created_at, Chat.id)
        .options(selectinload(Chat.messages))
    ).all()
    pins = session.execute(
        select(ChatPin.pinned_at, Organization.slug, Project.name, Chat.title)
        .join(Chat, Chat.id == ChatPin.chat_id)
        .join(Project, Project.id == Chat.project_id)
        .join(Organization, Organization.id == Project.org_id)
        .where(ChatPin.user_id == user.id)
        .order_by(ChatPin.pinned_at, ChatPin.chat_id)
    ).all()
    favorites = session.execute(
        select(DocumentFavorite.starred_at, Organization.slug, Project.name, Document.filename)
        .join(Document, Document.id == DocumentFavorite.document_id)
        .join(Project, Project.id == Document.project_id)
        .join(Organization, Organization.id == Project.org_id)
        .where(DocumentFavorite.user_id == user.id)
        .order_by(DocumentFavorite.starred_at, DocumentFavorite.document_id)
    ).all()
    visits = session.execute(
        select(DocumentVisit.opened_at, Organization.slug, Project.name, Document.filename)
        .join(Document, Document.id == DocumentVisit.document_id)
        .join(Project, Project.id == Document.project_id)
        .join(Organization, Organization.id == Project.org_id)
        .where(DocumentVisit.user_id == user.id)
        .order_by(DocumentVisit.opened_at, DocumentVisit.document_id)
    ).all()
    share_links = session.execute(
        select(ShareLink, Organization.slug, Project.name, Document.filename)
        .join(Organization, Organization.id == ShareLink.org_id)
        .join(Project, Project.id == ShareLink.project_id)
        .outerjoin(Document, Document.id == ShareLink.document_id)
        .where(ShareLink.user_id == user.id, ShareLink.expires_at > func.now())
        .order_by(ShareLink.id)
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
            invite_hidden_at=user.invite_hidden_at,
            changes_seen_on=user.changes_seen_on,
            changes_seen_entries=user.changes_seen_entries,
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
                support_until=leaves.get(organization.id),
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
                deleted_at=chat.deleted_at or trashed,
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
            for chat, project, slug, trashed in chats
        ],
        pinned_chats=[
            ExportedPin(organization=slug, project=project, chat=title, pinned_at=pinned_at)
            for pinned_at, slug, project, title in pins
        ],
        favorite_documents=[
            ExportedFavorite(
                organization=slug, project=project, document=filename, starred_at=starred_at
            )
            for starred_at, slug, project, filename in favorites
        ],
        recent_documents=[
            ExportedVisit(organization=slug, project=project, document=filename, opened_at=opened)
            for opened, slug, project, filename in visits
        ],
        share_links=[
            ExportedShareLink(
                organization=slug,
                project=project,
                kind="document" if link.document_id is not None else "answer",
                document=filename,
                created_at=link.created_at,
                expires_at=link.expires_at,
                password=link.password_hash is not None,
                views=link.views,
                last_viewed_at=link.last_viewed_at,
            )
            for link, slug, project, filename in share_links
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


def make_export(export_id: int, storage: Storage, lease: Lease) -> str | None:
    """Make the export: write its ZIP file, put it in the storage, mark it ready and tell its
    member. Run again after a lost lease, it starts over and replaces the file. Returns the
    reason it failed, for the job's `last_error`, or None; raises `LeaseLost`."""
    with SessionLocal() as session:
        export = session.get(ProjectExport, export_id)
        if export is None or export.status != "queued":
            # Its member or project is gone, or another run finished it.
            return None
        project = session.get(Project, export.project_id)
        org_id = project.org_id
        if project.deleted_at is not None:
            # The member's own doing, or another's: nothing for the operator.
            _settle(session, export, lease, error=PROJECT_GONE)
            return None
    try:
        with tempfile.TemporaryDirectory(prefix="emudoc-export-") as directory:
            path = Path(directory) / "export.zip"
            documents = _write(path, export_id, storage)
            key = export_key(org_id, export.project_id, export_id)
            storage.put(key, path, "application/zip")
            size = path.stat().st_size
    except LeaseLost:
        raise
    except Exception as error:
        log.error(
            "The export %s of project %s failed: %s", export_id, export.project_id, summary(error)
        )
        with SessionLocal() as session:
            _settle(session, session.get(ProjectExport, export_id), lease, error=EXPORT_FAILED)
        return summary(error)
    with SessionLocal() as session:
        _settle(
            session, session.get(ProjectExport, export_id), lease, size=size, documents=documents
        )
    return None


def _write(path: Path, export_id: int, storage: Storage) -> int:
    """Write the export's ZIP file at `path`, the documents a batch at a time; returns how many
    originals it holds."""
    with SessionLocal() as session:
        export = session.get(ProjectExport, export_id)
        project = session.get(Project, export.project_id)
        user = session.get(User, export.user_id)
        organization = session.get(Organization, project.org_id)
        folders = folder_paths(session, project.id)
        ids = session.scalars(
            select(Document.id)
            .where(*_EXPORTED, Document.project_id == project.id)
            .order_by(Document.id)
        ).all()
        chats = _chats(session, project.id, user.id)
        tags = project_tags(session, project.id)
        tag_names = {tag.id: tag.name for tag in tags}
        head = {
            "organization": organization.name,
            "project": ExportedProject(
                name=project.name,
                description=project.description,
                visibility=project.visibility,
                created_at=project.created_at,
            ),
            "exported_by": ExportedBy(name=user.name, email=user.email),
            "folders": sorted(folders.values(), key=str.lower),
            "templates": _templates(session, project.id),
            "share_links": _share_links(session, project.id, user.id),
            "tags": [ExportedTag(name=tag.name, color=tag.color) for tag in tags],
        }
    names = _Names()
    listed: list[ExportedDocument] = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for start in range(0, len(ids), BATCH):
            # Read, then let the connection go: copying the originals waits on the storage.
            with SessionLocal() as session:
                batch = _read(session, ids[start : start + BATCH])
            for document, data, earlier in batch:
                folder = folders.get(document.folder_id, "") if document.folder_id else ""
                original = _original(archive, storage, names, document, folder)
                data_path = f"documents/{document.id}.json"
                archive.writestr(data_path, data.model_dump_json(indent=2))
                listed.append(
                    _listed(document, folder, original, data_path).model_copy(
                        update={
                            "versions": [
                                _version(archive, storage, names, version, folder)
                                for version in earlier
                            ],
                            "tags": [tag_names[i] for i in document.tag_ids if i in tag_names],
                        }
                    )
                )
        archive.writestr(
            "project.json",
            ProjectExportFile(
                exported_at=datetime.now(UTC),
                about=PROJECT_ABOUT,
                documents=listed,
                chats=chats,
                **head,
            ).model_dump_json(indent=2),
        )
    return sum(1 for document in listed if document.original is not None)


# The documents an export holds: out of the trash, and not set apart by the malware scan.
_EXPORTED = (Document.deleted_at.is_(None), Document.status != "quarantined")


def _read(
    session: Session, ids: Sequence[int]
) -> list[tuple[Document, ExportedDocumentData, list[DocumentVersion]]]:
    """The documents with these ids, each with what was read from it and its earlier versions,
    the latest first."""
    documents = session.scalars(
        select(Document).where(Document.id.in_(ids)).order_by(Document.id)
    ).all()
    sections, passages, facts = defaultdict(list), defaultdict(list), defaultdict(list)
    earlier: dict[int, list[DocumentVersion]] = defaultdict(list)
    for row in session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id.in_(ids))
        .options(undefer(DocumentVersion.blocks))
        .order_by(DocumentVersion.document_id, DocumentVersion.number.desc())
    ):
        earlier[row.document_id].append(row)
    for row in session.scalars(
        select(Section).where(Section.document_id.in_(ids)).order_by(Section.ordinal)
    ):
        sections[row.document_id].append(
            ExportedSection(
                id=row.id,
                parent_id=row.parent_id,
                level=row.level,
                title=row.title,
                path=row.path,
                summary=row.summary,
                page_start=row.page_start,
                page_end=row.page_end,
            )
        )
    # The columns alone: a passage's vector and text-search terms are the index's, not its own.
    for row in session.execute(
        select(
            Passage.id,
            Passage.document_id,
            Passage.section_id,
            Passage.text,
            Passage.context,
            Passage.page_start,
            Passage.page_end,
        )
        .where(Passage.document_id.in_(ids))
        .order_by(Passage.document_id, Passage.ordinal)
    ):
        passages[row.document_id].append(
            ExportedPassage(
                id=row.id,
                section_id=row.section_id,
                text=row.text,
                context=row.context,
                page_start=row.page_start,
                page_end=row.page_end,
            )
        )
    for row in session.scalars(
        select(Fact).where(Fact.document_id.in_(ids)).order_by(Fact.document_id, Fact.id)
    ):
        facts[row.document_id].append(
            ExportedFact(
                name=row.name,
                value_type=row.value_type,
                value=row.value,
                number=row.number,
                date=row.date,
                currency=row.currency,
                quote=row.quote,
                page=row.page,
                passage_id=row.passage_id,
                verified=row.verified,
                check=check_out(row.check) if row.check is not None else None,
            )
        )
    return [
        (
            document,
            ExportedDocumentData(
                id=document.id,
                filename=document.filename,
                title=document.title,
                sections=sections[document.id],
                passages=passages[document.id],
                facts=facts[document.id],
                versions=[
                    ExportedVersionText(
                        number=version.number,
                        blocks=[ExportedBlock(**block) for block in version.blocks],
                    )
                    for version in earlier[document.id]
                ],
            ),
            earlier[document.id],
        )
        for document in documents
    ]


def _chats(session: Session, project_id: int, user_id: int) -> list[ExportedProjectChat]:
    """The project's chats out of the trash that the member may read: those shared with the
    project, and their own."""
    chats = session.scalars(
        select(Chat)
        .where(
            Chat.project_id == project_id,
            Chat.deleted_at.is_(None),
            or_(Chat.visibility == "project", Chat.user_id == user_id),
        )
        .order_by(Chat.created_at, Chat.id)
        .options(selectinload(Chat.messages))
    ).all()
    return [
        ExportedProjectChat(
            title=chat.title,
            visibility=chat.visibility,
            started_by=chat.started_by,
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
        for chat in chats
    ]


def _original(
    archive: zipfile.ZipFile, storage: Storage, names: "_Names", document: Document, folder: str
) -> str | None:
    """Copy the document's original into the archive under its folder, and return where; None
    for one the malware scan has not passed, or whose file the storage lacks."""
    if not scan_passed(document):
        return None
    name = names.taken(PurePosixPath("originals", *_segments(folder), _segment(document.filename)))
    try:
        with storage.localize(document.storage_key) as local:
            archive.write(local, name, _compression(document.filename))
    except FileNotFoundError:
        log.warning("Document %s has no original in the storage to export", document.id)
        return None
    return name


def _version(
    archive: zipfile.ZipFile,
    storage: Storage,
    names: "_Names",
    version: DocumentVersion,
    folder: str,
) -> ExportedVersion:
    """An earlier version as `project.json` lists it, its file copied into the archive under
    `versions/` and its folder, named for its number; none for one the malware scan had not
    passed, or whose file the storage lacks."""
    name = PurePosixPath(_segment(version.filename))
    path = None
    if version.scan_passed:
        path = names.taken(
            PurePosixPath(
                "versions",
                *_segments(folder),
                f"{name.stem} (version {version.number}){name.suffix}",
            )
        )
        try:
            with storage.localize(version.storage_key) as local:
                archive.write(local, path, _compression(version.filename))
        except FileNotFoundError:
            log.warning(
                "A version of document %s has no file in the storage to export", version.document_id
            )
            path = None
    return ExportedVersion(
        number=version.number,
        filename=version.filename,
        original=path,
        mime_type=version.mime_type,
        size_bytes=version.size_bytes,
        sha256=version.sha256,
        page_count=version.page_count,
        uploaded_at=version.uploaded_at,
        replaced_at=version.replaced_at,
    )


def _compression(filename: str) -> int:
    """How a file goes into the archive: stored as it is when it is compressed already."""
    if PurePosixPath(filename).suffix.lower() in COMPRESSED:
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


def _segments(path: str) -> list[str]:
    return [_segment(part) for part in path.split(PATH_SEPARATOR) if part]


def _segment(name: str) -> str:
    """A folder's or file's name as one segment of a path in the archive: no separator, and
    nothing an unpacking tool would take for a step up."""
    cleaned = name.replace("/", "_").replace("\\", "_").strip()
    return "_" if cleaned in ("", ".", "..") else cleaned


class _Names:
    """The paths given out in the archive, so that two files of one folder whose names differ
    only by a character the archive cannot hold do not overwrite each other."""

    def __init__(self) -> None:
        self._taken: set[str] = set()

    def taken(self, path: PurePosixPath) -> str:
        candidate = str(path)
        for number in count(2):
            if candidate.lower() not in self._taken:
                break
            candidate = str(path.with_name(f"{path.stem} ({number}){path.suffix}"))
        self._taken.add(candidate.lower())
        return candidate


def _listed(document: Document, folder: str, original: str | None, data: str) -> ExportedDocument:
    return ExportedDocument(
        id=document.id,
        filename=document.filename,
        folder=folder,
        original=original,
        data=data,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        status=document.status,
        title=document.title,
        doc_type=document.doc_type,
        doc_date=document.doc_date,
        summary=document.summary,
        template=document.template,
        page_count=document.page_count,
        uploaded_at=document.created_at,
        review=document.review,
        assignee=document.assignee,
        review_changed_at=document.review_changed_at,
        version=document.version,
    )


def _templates(session: Session, project_id: int) -> list[ExportedTemplate]:
    """The project's own extraction templates (app/extraction.py), as its members wrote them."""
    return [
        ExportedTemplate(
            doc_type=template.doc_type,
            title=template.title,
            description=template.description,
            fields=[TemplateFieldOut(**asdict(field)) for field in template.fields],
        )
        for template in templates_for(session, project_id)
        if template.id is not None
    ]


def _share_links(session: Session, project_id: int, user_id: int) -> list[ExportedProjectShareLink]:
    """The links that have not expired to the project's documents out of the trash, and to the
    answers of the chats the export holds: those shared with the project and the member's own."""
    rows = session.execute(
        select(ShareLink, Document.filename, User.name)
        .outerjoin(Document, Document.id == ShareLink.document_id)
        .outerjoin(ChatMessage, ChatMessage.id == ShareLink.message_id)
        .outerjoin(Chat, Chat.id == ChatMessage.chat_id)
        .join(User, User.id == ShareLink.user_id)
        .where(
            ShareLink.project_id == project_id,
            ShareLink.expires_at > func.now(),
            Document.deleted_at.is_(None),
            Chat.deleted_at.is_(None),
            or_(
                ShareLink.document_id.is_not(None),
                Chat.visibility == "project",
                Chat.user_id == user_id,
            ),
        )
        .order_by(ShareLink.id)
    ).all()
    return [
        ExportedProjectShareLink(
            kind="document" if link.document_id is not None else "answer",
            document_id=link.document_id,
            document=filename,
            made_by=made_by,
            created_at=link.created_at,
            expires_at=link.expires_at,
            password=link.password_hash is not None,
            views=link.views,
        )
        for link, filename, made_by in rows
    ]


def _settle(
    session: Session,
    export: ProjectExport | None,
    lease: Lease,
    *,
    size: int = 0,
    documents: int = 0,
    error: str | None = None,
) -> None:
    """Mark the export ready, or failed with `error`, and tell its member, committed with the
    lease renewed; raises `LeaseLost`. An export gone meanwhile, with its member or project,
    has nobody to tell."""
    if export is None:
        return
    export.status = "failed" if error else "ready"
    export.error = error
    export.size_bytes, export.documents = size, documents
    if error is None:
        export.expires_at = datetime.now(UTC) + EXPORT_KEPT
    user = session.get(User, export.user_id)
    project = session.get(Project, export.project_id)
    organization = session.get(Organization, project.org_id)
    path = f"/?{EXPORT_QUERY}={project.id}"
    url = app_address()
    tell(
        session,
        user,
        organization.id,
        PROJECT_EXPORT,
        project_export_email(
            user.name,
            project.name,
            organization.name,
            in_organization(f"{url}{path}", organization.id),
            None if error else day(export.expires_at, zone_of(user.time_zone)),
        ),
        path,
        url,
    )
    if not renew(session, lease):
        session.rollback()
        raise LeaseLost(lease)
    session.commit()


def queue_export(session: Session, project: Project, user_id: int) -> ProjectExport:
    """An export of the project for the member, queued for the worker in the caller's session;
    the caller commits."""
    export = ProjectExport(project_id=project.id, user_id=user_id)
    session.add(export)
    session.flush()
    enqueue(
        session,
        EXPORT_PROJECT,
        org_id=project.org_id,
        project_id=project.id,
        payload={"export_id": export.id},
    )
    return export


def abandon_export(export_id: int) -> None:
    """Mark the export failed once every run of its job was lost, for the member to ask again."""
    with SessionLocal() as session:
        session.execute(
            update(ProjectExport)
            .where(ProjectExport.id == export_id, ProjectExport.status == "queued")
            .values(status="failed", error=EXPORT_FAILED)
        )
        session.commit()


def prune_exports(connection: Connection) -> int:
    """Forget the exports past their days, and the failed ones as long after they were asked
    for; their files go in the storage walk by age (`sweep_storage` in app/maintenance.py)."""
    return connection.execute(
        delete(ProjectExport).where(
            func.coalesce(ProjectExport.expires_at, ProjectExport.created_at + EXPORT_KEPT)
            < func.now()
        )
    ).rowcount
