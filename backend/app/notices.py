"""The notices the app tells members about their work, beside the mail their accounts,
invitations and billing need. Each is told through `tell`, in the app and by email, each as its
member chose for its kind (NOTICE_KINDS, `User.wants`), or where they did not choose, as the
organisation it is about tells its members by default (`organizations.notices`, which its owners
and admins choose on the organisation page): a row in their notification centre
(`notifications`, app/api/notifications.py), and the mail through `post_notice`
(app/postbox.py), which also leaves out an address the mail provider said to spare. Both are
made in the transaction of the change that calls for them, so that the centre and the mail never
disagree. The centre lists a member's notices of every organisation they belong to, and a
notice's mail links to its page in the organisation it is about (`in_organization`), since they
may work in another when they open it. A member who leaves an organisation, or is removed from
it, loses its notices (`forget_notifications`), and the housekeeper removes those older than
NOTIFICATION_DAYS (`prune_notifications`).

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
cross-checks once they have gone quiet.

An upload is found settled by whoever ends its last piece of work. The worker looks once a
document's index job is finished (`after_index`, which app/worker.py runs after the job's row
says so), so that of two documents whose jobs end at once, the one that looks last sees the
other's ended; the upload request looks as it makes the row, for documents settled before
that; and the housekeeper looks through the uploads not yet settled (`settle_uploads`), for one
whose last look was lost with a worker that died or a document deleted while it waited. The
statement that marks an upload settled holds its row, so it is told of once.

A month's pages and questions. An organisation's owners and admins are told when what it has
used of the month's pages or questions reaches NEAR percent of what it may use, its plan's and
the packs bought for the month together (app/entitlements.py, app/billing.py), and when it
reaches all of it, or, for pages, when what is left could not take a document. Each is posted
where the figure is counted and in its transaction (`note_quota`): the question's by the ask
route as it records the question, the pages' by the parse stage with its mark, under the job's
lease. A `quota_notices` row for the month, figure, share and allowance says it was posted, so
that each is posted once a month, and a pack bought after the limit was reached starts the
count again; the usage page warns from the same share (`NEAR` in frontend/src/allowance.ts).
"""

import logging
from datetime import datetime, timedelta, tzinfo
from typing import TYPE_CHECKING

from sqlalchemy import Connection, Row, delete, exists, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.access import visible_to
from app.accounts import Account, zone_of
from app.automations import arrived
from app.config import get_settings
from app.db import SessionLocal
from app.entitlements import (
    MONTHS,
    bought_this_month,
    entitlements_of,
    month_of,
    pages_this_month,
    questions_this_month,
)
from app.mail import Content, in_organization, quota_email, upload_indexed_email
from app.models import (
    NOTICE_CHANNELS,
    NOTICE_KINDS,
    ROLES,
    Document,
    Job,
    Membership,
    Notification,
    Organization,
    Project,
    QuotaNotice,
    Upload,
    User,
)
from app.postbox import BY_EMAIL, post_notice
from app.schemas import NoticeChoiceOut

if TYPE_CHECKING:
    # For the handler's signature alone: the pipeline posts the pages' notices through here.
    from app.indexing.pipeline import Services

log = logging.getLogger(__name__)

# The kinds of notice, and the way of telling them this module makes itself; a project's export
# is told by app/export.py, what an automation's run found by app/check_runs.py, and a document's
# review assigned by app/reviews.py.
UPLOAD_INDEXED, QUOTA, PROJECT_EXPORT, CHECKS_RUN, REVIEW_ASSIGNED = NOTICE_KINDS
IN_APP = NOTICE_CHANNELS[0]
# How long a notice stays in its member's notification centre, read or not; the privacy policy
# says so.
NOTIFICATION_DAYS = 90
# How long an upload must take to settle for its member to be told: one indexed sooner was
# most likely watched in the library.
NOTICE_AFTER = timedelta(minutes=5)
# The figures of a plan the notices follow (QUOTA_FIGURES in app/models.py), and the shares of
# them, in percent, that are told: most of the month's, and all of it.
MONTH_PAGES = "pages_per_month"
MONTH_QUESTIONS = "questions_per_month"
NEAR = 80
FULL = 100
# Who is told about the month's figures: who may change the plan or ask an owner to.
MANAGERS = ROLES[:2]
# What a document that is neither ready nor told why is said to be, which no run leaves.
NOT_INDEXED = "It could not be indexed."
# Where the links in the notices lead.
PROJECT_PAGE = "/projects/{}"
USAGE_PAGE = "/organization/usage"
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
    either way. The content's own link is the caller's, `in_organization` of the same page."""
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
    session: Session, project_id: int, user_id: int, document_ids: list[int]
) -> int | None:
    """Make the upload of a request's documents in the caller's session, once it has stored
    them all; returns its id, or None when the request kept none."""
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
    automations that watch them (app/automations.py); commits. Returns whether it was settled
    now, rather than before or not yet."""
    settled = session.execute(_SETTLE, {"id": upload_id}).first()
    if settled is not None:
        _tell(session, settled, app_url)
        arrived(session, settled.id, settled.project_id)
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
        in_organization(f"{app_url}{page}", project.org_id),
    )
    tell(session, user, project.org_id, UPLOAD_INDEXED, content, page, app_url)


def moment_words(moment: datetime, zone: tzinfo) -> str:
    """A time as a notice names it, 14:05 CEST on 20 October 2026, in the reader's zone."""
    moment = moment.astimezone(zone)
    return f"{moment:%H:%M %Z} on {moment.day} {MONTHS[moment.month - 1]} {moment.year}"


def note_quota(
    session: Session,
    organization: Organization,
    figure: str,
    app_url: str,
    *,
    adding: int = 0,
    refused: bool = False,
) -> int:
    """Tell the notice the month's `figure` calls for to the organisation's owners and admins
    in the caller's session, which counts what was used, unless it was told already; returns
    to how many. `adding` is used beyond what the usage rows say: the pages the parse stage
    counted, which its charge records after the stage. `refused` says what is left could not
    take a document."""
    cap = getattr(entitlements_of(organization), figure)
    if cap is None:
        return 0
    start, following = month_of()
    if figure == MONTH_PAGES:
        used = pages_this_month(session, organization.id, start) + adding
        allowance = cap + bought_this_month(session, organization.id, "pages", start)
    else:
        used = questions_this_month(session, organization.id, start) + adding
        allowance = cap + bought_this_month(session, organization.id, "questions", start)
    if refused or used >= allowance:
        share = FULL
    elif used * 100 >= allowance * NEAR:
        share = NEAR
    else:
        return 0
    if not _noted(session, organization.id, start, figure, share, allowance, used):
        return 0
    if share == FULL:
        # So that most of it is never told after all of it was.
        _noted(session, organization.id, start, figure, NEAR, allowance, used)
    managers = session.scalars(
        select(Membership)
        .where(Membership.org_id == organization.id, Membership.role.in_(MANAGERS))
        .order_by(Membership.user_id)
    ).all()
    told = 0
    for membership in managers:
        content = quota_email(
            membership.user.name,
            organization.name,
            figure,
            used,
            allowance,
            MONTHS[start.month - 1],
            MONTHS[following.month - 1],
            in_organization(f"{app_url}{USAGE_PAGE}", organization.id),
            refused=refused,
        )
        told += tell(session, membership.user, organization.id, QUOTA, content, USAGE_PAGE, app_url)
    return told


def _noted(
    session: Session,
    org_id: int,
    month: datetime,
    figure: str,
    share: int,
    allowance: int,
    used: int,
) -> bool:
    """Record that the share of the month's figure is told, unless it was; returns whether it
    is told now. Two requests that reach it at once insert one row: the second waits for the
    first's transaction and inserts nothing."""
    inserted = session.execute(
        insert(QuotaNotice)
        .values(
            org_id=org_id,
            month=month,
            figure=figure,
            share=share,
            allowance=allowance,
            used=used,
        )
        .on_conflict_do_nothing()
        .returning(QuotaNotice.id)
    ).first()
    return inserted is not None


def prune_quota_notices(connection: Connection) -> int:
    """Delete the records of the notices of months gone by, which no count reaches again;
    returns how many."""
    return connection.execute(
        text("DELETE FROM quota_notices WHERE month < :start"), {"start": month_of()[0]}
    ).rowcount
