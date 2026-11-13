"""The notices the app tells members about their work, beside the mail their accounts and
invitations need. Each is told through `tell`, in the app and by email, each as its
member chose for its kind (NOTICE_KINDS, `User.wants`), or where they did not choose, as the
organisation it is about tells its members by default (`organizations.notices`, which its owners
and admins choose on the organisation page): a row in their notification centre
(`notifications`, app/api/notifications.py), and the mail through `post_notice`
(app/postbox.py), which also leaves out an address the mail provider said to spare. Both are
made in the transaction of the change that calls for them, so that the centre and the mail never
disagree. The centre lists a member's notices of the workspace, and a notice's mail links to
its page. A member removed from the workspace loses its notices (`forget_notifications`), and
the housekeeper removes those older than NOTIFICATION_DAYS (`prune_notifications`).

An upload. The files of one request are one upload (`uploads`, made by `record_upload` once the
request has stored them all), and the member who sent them is told once the last of them is
settled: ready, failed or set apart by the scan, with no retry waiting and no job running for
it. Most uploads are indexed while their member watches the library, which says as much, so a
notice is posted only for an upload that took NOTICE_AFTER or longer to settle, when they may
have gone; and one for the whole upload, however many files, naming those that could not be
indexed with the sentence the library shows for each. A document that fails outside an upload,
run again by "Index again", is told of by nothing else: whoever pressed it is watching.

A settled upload's documents are what the project's automations watch arrive
(app/automations.py): kept for each that watches them in the same transaction, they run its
cross-checks once they have gone quiet; and so are its question sets that run on arrivals
(app/question_sets.py), whose questions they have asked.

An upload is found settled by whoever ends its last piece of work. The worker looks once a
document's index job is finished (`after_index`, which app/worker.py runs after the job's row
says so), so that of two documents whose jobs end at once, the one that looks last sees the
other's ended; the upload request looks as it makes the row, for documents settled before
that; and the housekeeper looks through the uploads not yet settled (`settle_uploads`), for one
whose last look was lost with a worker that died or a document deleted while it waited. The
statement that marks an upload settled holds its row, so it is told of once.
"""

import logging
from datetime import datetime, timedelta, tzinfo
from typing import TYPE_CHECKING

from sqlalchemy import Connection, Row, delete, exists, select, text, update
from sqlalchemy.orm import Session

from app.access import visible_to
from app.accounts import Account, zone_of
from app.automations import arrived
from app.config import get_settings
from app.db import SessionLocal
from app.mail import Content, upload_indexed_email
from app.models import (
    NOTICE_CHANNELS,
    NOTICE_KINDS,
    Document,
    Job,
    Membership,
    Notification,
    Organization,
    Project,
    Upload,
    User,
)
from app.postbox import BY_EMAIL, post_notice
from app.question_sets import arrived as arrived_for_sets
from app.schemas import NoticeChoiceOut
from app.usage import MONTHS

if TYPE_CHECKING:
    # For the handler's signature alone: the pipeline posts the pages' notices through here.
    from app.indexing.pipeline import Services

log = logging.getLogger(__name__)

# The kinds of notice, and the way of telling them this module makes itself; a project's export
# is told by app/export.py, what an automation's run found by app/check_runs.py, a document's
# review assigned by app/reviews.py, a question set's report kept by app/question_reports.py,
# the weekly digest of what arrived by app/digests.py, and a member mentioned in a comment or a
# reply in a thread they wrote in by app/comments.py.
(
    UPLOAD_INDEXED,
    PROJECT_EXPORT,
    CHECKS_RUN,
    REVIEW_ASSIGNED,
    REPORT_READY,
    WEEKLY_DIGEST,
    MENTIONED,
    THREAD_REPLIED,
) = NOTICE_KINDS
IN_APP = NOTICE_CHANNELS[0]
# How long a notice stays in its member's notification centre, read or not.
NOTIFICATION_DAYS = 90
# How long an upload must take to settle for its member to be told: one indexed sooner was
# most likely watched in the library.
NOTICE_AFTER = timedelta(minutes=5)
# What a document that is neither ready nor told why is said to be, which no run leaves.
NOT_INDEXED = "It could not be indexed."
# Where the links in the notices lead.
PROJECT_PAGE = "/projects/{}"
# Where the links of a notice the worker writes lead in development without APP_URL, which no
# request names there: the frontend's development server (dev.sh).
DEVELOPMENT_APP_URL = "http://localhost:5173"

# The jobs are an uncorrelated list, which Postgres hashes once for the statement, rather than
# looked up per document: an upload of hundreds is looked at as each of its jobs ends. A document
# in the trash (app/trash.py) waits there unindexed, and holds nothing up.
_SETTLE = text(
    """
    UPDATE uploads AS u SET settled_at = now()
    WHERE u.id = :id AND u.settled_at IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM documents AS d
        WHERE d.upload_id = u.id AND d.deleted_at IS NULL
          AND (d.status IN ('queued', 'processing')
               OR d.id IN (SELECT (j.payload->>'document_id')::bigint FROM jobs AS j
                           WHERE j.kind = 'index_document'
                             AND j.status IN ('queued', 'running'))))
    RETURNING u.id, u.project_id, u.user_id, u.created_at, u.settled_at
    """
)


def tell(
    session: Session,
    user: User,
    org_id: int,
    kind: str,
    content: Content,
    path: str,
    app_url: str,
) -> bool:
    """Tell the user a notice of the kind about the organisation, whose page `path` it leads to,
    in the app and by email as they chose, in the caller's session; returns whether it was told
    either way. The content's own link is the caller's, to the same page."""
    told = False
    defaults = session.get(Organization, org_id).notices
    if user.wants(kind, IN_APP, defaults):
        session.add(
            Notification(
                user_id=user.id,
                org_id=org_id,
                kind=kind,
                title=content.subject,
                body=content.brief,
                link=path,
            )
        )
        told = True
    if post_notice(session, user, kind, content, app_url, org_id) is not None:
        told = True
    return told


def choices(user: User, defaults: dict | None = None) -> list[NoticeChoiceOut]:
    """How the user is told each kind of notice, where they did not choose as `defaults`, an
    organisation's, say: for the account page, in the organisation it works in, and the
    export, by the user's choices alone."""
    return [
        NoticeChoiceOut(
            kind=kind,
            app=user.wants(kind, IN_APP, defaults),
            email=user.wants(kind, BY_EMAIL, defaults),
        )
        for kind in NOTICE_KINDS
    ]


def organization_choices(organization: Organization) -> list[NoticeChoiceOut]:
    """How the organisation's members are told each kind of notice until they choose."""
    return [
        NoticeChoiceOut(
            kind=kind,
            app=organization.tells(kind, IN_APP),
            email=organization.tells(kind, BY_EMAIL),
        )
        for kind in NOTICE_KINDS
    ]


def audience_of(
    session: Session, project: Project, audience: str, maker_id: int | None
) -> list[User]:
    """Whom an automation or a question set tells (AUTOMATION_AUDIENCES), of the organisation's
    members who see the project now: its maker, everyone, or nobody."""
    if audience == "nobody" or (audience == "maker" and maker_id is None):
        return []
    query = select(Membership).where(Membership.org_id == project.org_id)
    if audience == "maker":
        query = query.where(Membership.user_id == maker_id)
    told = []
    for membership in session.scalars(query.order_by(Membership.user_id)):
        account = Account(project.organization, membership.user, membership.role)
        if session.scalar(select(exists().where(Project.id == project.id, visible_to(account)))):
            told.append(membership.user)
    return told


def forget_notifications(session: Session, org_id: int, user_id: int) -> None:
    """Delete the user's notices about the organisation, which they are no longer a member of
    and whose news is no longer theirs to read; the caller commits."""
    session.execute(
        delete(Notification).where(Notification.org_id == org_id, Notification.user_id == user_id)
    )


def prune_notifications(connection: Connection) -> int:
    """Delete the notices older than NOTIFICATION_DAYS, read or not; returns how many."""
    return connection.execute(
        text("DELETE FROM notifications WHERE created_at < now() - make_interval(days => :days)"),
        {"days": NOTIFICATION_DAYS},
    ).rowcount


def app_address() -> str:
    """Where the links of a notice the worker writes lead: APP_URL, which production requires,
    or in development the frontend's development server."""
    return get_settings().app_url.rstrip("/") or DEVELOPMENT_APP_URL


def record_upload(
    session: Session, project_id: int, user_id: int | None, document_ids: list[int]
) -> int | None:
    """Make the upload of a request's documents in the caller's session, once it has stored
    them all; returns its id, or None when the request kept none. A message from a sender
    outside the organisation (app/inbound.py) is nobody's upload, and its notice goes to no
    one."""
    if not document_ids:
        return None
    upload = Upload(project_id=project_id, user_id=user_id)
    session.add(upload)
    session.flush()
    session.execute(
        update(Document).where(Document.id.in_(document_ids)).values(upload_id=upload.id)
    )
    return upload.id


def settle_upload(session: Session, upload_id: int, app_url: str) -> bool:
    """Mark the upload settled if none of its documents is waiting or being indexed any more,
    post its member's notice if it took long enough, and keep its documents for the project's
    automations that watch them (app/automations.py) and its question sets that run on arrivals
    (app/question_sets.py); commits. Returns whether it was settled now, rather than before or
    not yet."""
    settled = session.execute(_SETTLE, {"id": upload_id}).first()
    if settled is not None:
        _tell(session, settled, app_url)
        arrived(session, settled.id, settled.project_id)
        arrived_for_sets(session, settled.id, settled.project_id)
    session.commit()
    return settled is not None


def settle_uploads(app_url: str | None = None) -> int:
    """Settle every upload that is settled and was not marked so, one at a time; returns how
    many. The housekeeper's, for the uploads whose last look was lost."""
    settled = 0
    with SessionLocal() as session:
        unsettled = session.scalars(
            select(Upload.id).where(Upload.settled_at.is_(None)).order_by(Upload.id)
        ).all()
        for upload_id in unsettled:
            settled += settle_upload(session, upload_id, app_url or app_address())
    return settled


def after_index(job: Job, services: "Services") -> None:
    """What follows an index job once its row says it ended (app/worker.py): its document's
    upload may be settled now."""
    with SessionLocal() as session:
        upload_id = session.scalar(
            select(Document.upload_id).where(Document.id == job.payload["document_id"])
        )
        if upload_id is not None:
            settle_upload(session, upload_id, app_address())


def _tell(session: Session, settled: Row, app_url: str) -> None:
    """Tell the notice of an upload just settled to the member who sent it, if it took
    NOTICE_AFTER or longer and they still see its project."""
    if settled.user_id is None or settled.settled_at - settled.created_at < NOTICE_AFTER:
        return
    documents = session.execute(
        select(Document.filename, Document.status, Document.error)
        .where(Document.upload_id == settled.id, Document.deleted_at.is_(None))
        .order_by(Document.filename, Document.id)
    ).all()
    project = session.get(Project, settled.project_id)
    if not documents or project is None:
        return
    membership = session.scalar(
        select(Membership).where(
            Membership.org_id == project.org_id, Membership.user_id == settled.user_id
        )
    )
    if membership is None:
        # They left the organisation, or were removed from it, while it was indexed.
        return
    account = Account(project.organization, membership.user, membership.role)
    if not session.scalar(select(exists().where(Project.id == project.id, visible_to(account)))):
        return
    failed = [
        (filename, error or NOT_INDEXED)
        for filename, status, error in documents
        if status != "ready"
    ]
    user = account.user
    page = PROJECT_PAGE.format(project.id)
    content = upload_indexed_email(
        user.name,
        project.organization.name,
        project.name,
        moment_words(settled.created_at, zone_of(user.time_zone)),
        len(documents) - len(failed),
        failed,
        f"{app_url}{page}",
    )
    tell(session, user, project.org_id, UPLOAD_INDEXED, content, page, app_url)


def moment_words(moment: datetime, zone: tzinfo) -> str:
    """A time as a notice names it, 14:05 CEST on 20 October 2026, in the reader's zone."""
    moment = moment.astimezone(zone)
    return f"{moment:%H:%M %Z} on {moment.day} {MONTHS[moment.month - 1]} {moment.year}"
