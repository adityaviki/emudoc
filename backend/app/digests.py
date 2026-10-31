"""The weekly digest of what arrived: a notice a member turns on (WEEKLY_DIGEST, off until they
or their organisation choose it, CHOSEN_NOTICES in app/models.py), told on Monday morning of
their zone, which names for each project of the organisation they see the documents that arrived
in it over the week before, by their names and counts, never what one says. A week nothing
arrived in tells nothing, and no model is asked.

Nothing runs on a clock in the app: the digests' job wakes at the top of every hour, tells each
member whose Monday has reached DIGEST_HOUR in their zone and who was not told that Monday's yet
(`memberships.digested_on`, under the membership's row, so that a week is told once), and queues
itself for the next hour as it ends; the housekeeper queues it at once when none is queued or
running (`queue_digests`), for one lost with its job. A member is told one digest for each
organisation they belong to, as each notice is about one, and the week is theirs: from DIGEST_HOUR
on the Monday before to DIGEST_HOUR that Monday, by their clock.
"""

import logging
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.access import visible_to
from app.accounts import Account, zone_of
from app.db import SessionLocal
from app.jobs import LIVE, SEND_DIGESTS, Lease, LeaseLost, enqueue, renew
from app.mail import Arrivals, in_organization, weekly_digest_email
from app.models import Document, Membership, Organization, Project, User
from app.notices import IN_APP, WEEKLY_DIGEST, app_address, tell
from app.postbox import BY_EMAIL

log = logging.getLogger(__name__)

# The hour of a member's Monday, in their zone, from which their digest is told.
DIGEST_HOUR = 8
# The projects a digest lists, the most documents first, and the documents it names of each.
PROJECTS_LISTED = 20
NAMED = 5
# Where a digest leads: the projects page.
PROJECTS_PAGE = "/"

_WAITING = text("SELECT 1 FROM jobs WHERE kind = :kind AND status IN ('queued', 'running') LIMIT 1")
_QUEUED = text("SELECT 1 FROM jobs WHERE kind = :kind AND status = 'queued' LIMIT 1")


def queue_digests(session: Session) -> bool:
    """Queue the digests' job to run at once when none is queued or running, for one lost with
    its job; returns whether it did, committed. The housekeeper's."""
    if session.scalar(_WAITING, {"kind": SEND_DIGESTS}) is not None:
        return False
    enqueue(session, SEND_DIGESTS, org_id=None, priority=LIVE)
    session.commit()
    return True


def send_digests(lease: Lease | None = None, now: datetime | None = None) -> int:
    """The worker's handler of the digests' job: tell each member whose Monday morning has come
    the week's digest of each organisation, each in a transaction of its own under the lease,
    then queue the job for the top of the next hour, unless another is queued; returns to how
    many members it was told. A run again after a lost lease tells nobody twice."""
    now = now or datetime.now(UTC)
    with SessionLocal() as session:
        # Those who chose the kind, or whose organisation did: the few for whom it is on.
        memberships = session.scalars(
            select(Membership.id)
            .join(User, User.id == Membership.user_id)
            .join(Organization, Organization.id == Membership.org_id)
            .where(
                or_(
                    User.notices.has_key(WEEKLY_DIGEST),
                    Organization.notices.has_key(WEEKLY_DIGEST),
                )
            )
            .order_by(Membership.id)
        ).all()
    told = 0
    for membership_id in memberships:
        with SessionLocal() as session:
            try:
                told += _digest(session, membership_id, now)
            except Exception:
                # One member's digest that breaks holds nobody else's up; it is tried next hour.
                log.exception("The weekly digest of membership %s failed", membership_id)
                session.rollback()
                continue
            _commit(session, lease)
    with SessionLocal() as session:
        if session.scalar(_QUEUED, {"kind": SEND_DIGESTS}) is None:
            following = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            enqueue(session, SEND_DIGESTS, org_id=None, priority=LIVE, run_at=following)
        _commit(session, lease)
    if told:
        log.info("Told %s members the weekly digest of what arrived", told)
    return told


def due_monday(now: datetime, zone: tzinfo) -> date | None:
    """The Monday whose digest is due at `now` in the zone: today there, once it is Monday past
    DIGEST_HOUR; None on any other day or earlier."""
    local = now.astimezone(zone)
    if local.weekday() != 0 or local.hour < DIGEST_HOUR:
        return None
    return local.date()


def arrivals_of(
    session: Session, account: Account, start: datetime, end: datetime
) -> list[Arrivals]:
    """What arrived from `start` to `end` in the projects of the organisation the account sees,
    out of the trash: each project's documents counted, the most first, and the first few of
    those listed named in the order they came."""
    counted = session.execute(
        select(Project.id, Project.name, func.count(Document.id))
        .join(Document, Document.project_id == Project.id)
        .where(
            visible_to(account),
            Document.deleted_at.is_(None),
            Document.created_at >= start,
            Document.created_at < end,
        )
        .group_by(Project.id, Project.name)
        .order_by(func.count(Document.id).desc(), Project.name, Project.id)
    ).all()
    week = []
    for place, (project_id, name, count) in enumerate(counted):
        names = (
            session.scalars(
                select(Document.filename)
                .where(
                    Document.project_id == project_id,
                    Document.deleted_at.is_(None),
                    Document.created_at >= start,
                    Document.created_at < end,
                )
                .order_by(Document.created_at, Document.id)
                .limit(NAMED)
            ).all()
            if place < PROJECTS_LISTED
            else []
        )
        week.append(Arrivals(name, count, list(names)))
    return week


def _digest(session: Session, membership_id: int, now: datetime) -> int:
    """Tell the member the week's digest of the organisation, if it is on for them, due and not
    told yet, in the caller's session; returns 1 when it was told either way, else 0."""
    membership = session.scalar(
        select(Membership).where(Membership.id == membership_id).with_for_update()
    )
    if membership is None:
        return 0
    user, organization = membership.user, membership.organization
    defaults = organization.notices
    if not (
        user.wants(WEEKLY_DIGEST, IN_APP, defaults) or user.wants(WEEKLY_DIGEST, BY_EMAIL, defaults)
    ):
        return 0
    zone = zone_of(user.time_zone or "")
    monday = due_monday(now, zone)
    if monday is None or membership.digested_on == monday:
        return 0
    membership.digested_on = monday
    # A week by the member's clock, which a change of the clocks within it leaves at its hour.
    end = datetime.combine(monday, time(DIGEST_HOUR), tzinfo=zone)
    start = datetime.combine(monday - timedelta(days=7), time(DIGEST_HOUR), tzinfo=zone)
    account = Account(organization, user, membership.role)
    week = arrivals_of(session, account, start.astimezone(UTC), end.astimezone(UTC))
    if not week:
        return 0
    url = app_address()
    content = weekly_digest_email(
        user.name,
        organization.name,
        week,
        PROJECTS_LISTED,
        in_organization(f"{url}{PROJECTS_PAGE}", organization.id),
    )
    return int(tell(session, user, organization.id, WEEKLY_DIGEST, content, PROJECTS_PAGE, url))


def _commit(session: Session, lease: Lease | None) -> None:
    if lease is not None and not renew(session, lease):
        session.rollback()
        raise LeaseLost(lease)
    session.commit()
