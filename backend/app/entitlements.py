"""What each plan entitles an organisation to, and the checks that hold it to that.

An organisation is on one plan (`Organization.plan`, app/models.py), and a plan is a row of
PLANS: how many projects, documents and bytes of documents the organisation may hold, how many
pages it may index and questions it may ask in a calendar month, how many members it may have,
whether it may use the API and the connectors, how long its data is kept, which tier of model
serves it, and how much of the workers its jobs get. A limit of None is no limit. The
platform's own ceilings (MAX_UPLOAD_MB, MAX_ANSWERS and the PARSE_* limits in app/config.py)
are the same for every plan and stay settings; everything here differs by plan, so it is read
per request through the account (app/api/deps.py), never from the settings.

The checks run where the thing is about to happen: `check_project` in the route that creates
one, `check_upload` in the upload route before anything is stored and again per file,
`check_pages` in the parse stage once it knows how many pages a document has, `check_question`
in the ask route, and `check_member` by whatever invites (nothing does yet). Each returns the
sentence the user sees, naming the plan, the limit and what to do, or None when the plan
allows it; a route refuses with 402, the stage fails the document with it. The counts come
from the rows (projects, documents and their bytes, memberships), so a deleted document frees
what it took, or from the month so far in UTC (the pages indexed, by the `upload` usage
events; the questions, by the answers asked), which starts afresh on the first.

`priority` weights the organisation's share of the workers in the job claim (app/jobs.py): an
organisation's jobs count for a third as much on the team plan as on the free one, so its
backlog gets the larger share. `model_tier`, `api_access`, `connectors` and `retention_days`
are declared for the features that will read them (the organisation settings, the API keys,
the connectors, the trash) and enforced by nothing yet.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    PLANS as PLAN_NAMES,
)
from app.models import (
    Chat,
    ChatMessage,
    Document,
    Invitation,
    Membership,
    Organization,
    Project,
    UsageEvent,
)
from app.usage import UPLOAD

MB = 1_000_000
GB = 1_000 * MB
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


@dataclass(frozen=True)
class Entitlements:
    """What one plan allows. A limit of None is no limit."""

    projects: int | None
    documents: int | None
    storage_bytes: int | None
    pages_per_month: int | None
    questions_per_month: int | None
    members: int | None
    api_access: bool
    connectors: bool
    retention_days: int | None
    # standard | premium: which models answer and index for the organisation, once the
    # organisation settings choose a model by it.
    model_tier: str
    # The weight of the organisation's share of the workers in the job claim (app/jobs.py).
    priority: int


PLANS: dict[str, Entitlements] = {
    "free": Entitlements(
        projects=1,
        documents=25,
        storage_bytes=100 * MB,
        pages_per_month=200,
        questions_per_month=50,
        members=1,
        api_access=False,
        connectors=False,
        retention_days=30,
        model_tier="standard",
        priority=1,
    ),
    "pro": Entitlements(
        projects=10,
        documents=1_000,
        storage_bytes=10 * GB,
        pages_per_month=5_000,
        questions_per_month=1_000,
        members=1,
        api_access=True,
        connectors=False,
        retention_days=365,
        model_tier="standard",
        priority=2,
    ),
    "team": Entitlements(
        projects=50,
        documents=10_000,
        storage_bytes=100 * GB,
        pages_per_month=25_000,
        questions_per_month=5_000,
        members=25,
        api_access=True,
        connectors=True,
        retention_days=None,
        model_tier="premium",
        priority=3,
    ),
    # Custom terms: nothing here limits it, only the platform's ceilings do.
    "enterprise": Entitlements(
        projects=None,
        documents=None,
        storage_bytes=None,
        pages_per_month=None,
        questions_per_month=None,
        members=None,
        api_access=True,
        connectors=True,
        retention_days=None,
        model_tier="premium",
        priority=4,
    ),
}
assert set(PLANS) == set(PLAN_NAMES), "every plan the database accepts has its entitlements"


def entitlements_of(organization: Organization) -> Entitlements:
    return PLANS[organization.plan]


def share_weights() -> dict[str, int]:
    """Each plan's weight in the job claim, by plan name (app/jobs.py)."""
    return {plan: entitlements.priority for plan, entitlements in PLANS.items()}


def month_of(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The start of the current calendar month in UTC, and the start of the next."""
    now = now or datetime.now(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    following = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1, tzinfo=UTC)
    return start, following


def check_project(session: Session, organization: Organization) -> str | None:
    """Why the plan refuses another project, or None when it has room for one."""
    cap = entitlements_of(organization).projects
    if cap is None:
        return None
    count = session.scalar(
        select(func.count()).select_from(Project).where(Project.org_id == organization.id)
    )
    if count < cap:
        return None
    return (
        f"The {_label(organization)} plan allows {_count(cap, 'project')}, and this "
        f"organisation has {count}. Upgrade your plan to create another."
    )


def check_upload(
    session: Session, organization: Organization, size: int | None = None
) -> str | None:
    """Why the plan refuses another document, or None when it has room: for any at all when no
    size is given (the request as a whole, which is also when the month's pages are checked),
    or for one of `size` bytes (each file of it)."""
    plan = entitlements_of(organization)
    label = _label(organization)
    count, held = _documents(session, organization.id)
    if plan.documents is not None and count >= plan.documents:
        return (
            f"The {label} plan allows {_count(plan.documents, 'document')}, and this "
            f"organisation has {count}. Delete some, or upgrade your plan to add more."
        )
    if plan.storage_bytes is not None:
        if held >= plan.storage_bytes:
            return (
                f"The {label} plan allows {size_label(plan.storage_bytes)} of documents, and "
                f"this organisation holds {size_label(held)}. Delete some, or upgrade your "
                "plan to add more."
            )
        if size is not None and held + size > plan.storage_bytes:
            return (
                f"This file is {size_label(size)}, more than the "
                f"{size_label(plan.storage_bytes - held)} left of the {label} plan's "
                f"{size_label(plan.storage_bytes)}. Delete some documents, or upgrade your plan."
            )
    if size is None and plan.pages_per_month is not None:
        start, following = month_of()
        used = pages_this_month(session, organization.id, start)
        if used >= plan.pages_per_month:
            return (
                f"The {label} plan indexes {_count(plan.pages_per_month, 'page')} a month, and "
                f"this organisation has indexed {used:,} in {_month(start)}. Upgrade your plan, "
                f"or upload again from 1 {_month(following)}."
            )
    return None


def check_pages(session: Session, organization: Organization, pages: int) -> str | None:
    """Why the plan refuses indexing a document of `pages` pages this month, or None when the
    month has room for them. A document without pages costs none."""
    cap = entitlements_of(organization).pages_per_month
    if cap is None or not pages:
        return None
    start, following = month_of()
    used = pages_this_month(session, organization.id, start)
    if used + pages <= cap:
        return None
    return (
        f"This document has {_count(pages, 'page')}, more than the {max(cap - used, 0):,} "
        f"left of the {_label(organization)} plan's {cap:,} a month. Upgrade your plan, or "
        f"index it again from 1 {_month(following)}."
    )


def check_question(session: Session, organization: Organization) -> str | None:
    """Why the plan refuses another question this month, or None when it has room for one."""
    cap = entitlements_of(organization).questions_per_month
    if cap is None:
        return None
    start, following = month_of()
    asked = questions_this_month(session, organization.id, start)
    if asked < cap:
        return None
    return (
        f"The {_label(organization)} plan answers {_count(cap, 'question')} a month, and this "
        f"organisation has asked {asked:,} in {_month(start)}. Upgrade your plan, or ask again "
        f"from 1 {_month(following)}."
    )


def check_member(session: Session, organization: Organization) -> str | None:
    """Why the plan refuses another member, or None when it has room for one. An invitation
    that is still open holds a place."""
    cap = entitlements_of(organization).members
    if cap is None:
        return None
    taken = _members(session, organization.id)
    if taken < cap:
        return None
    return (
        f"The {_label(organization)} plan allows {_count(cap, 'member')}, and this organisation "
        f"has {taken}, open invitations counted. Upgrade your plan to invite more."
    )


def pages_this_month(session: Session, org_id: int, start: datetime) -> int:
    """The pages the organisation's uploads brought in since `start`: what the first parse of
    each document counted (app/indexing/pipeline.py)."""
    return session.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.org_id == org_id,
            UsageEvent.kind == UPLOAD,
            UsageEvent.unit == "pages",
            UsageEvent.occurred_at >= start,
        )
    )


def questions_this_month(session: Session, org_id: int, start: datetime) -> int:
    """The questions asked in the organisation's projects since `start`, answered or not."""
    return session.scalar(
        select(func.count())
        .select_from(ChatMessage)
        .join(Chat, Chat.id == ChatMessage.chat_id)
        .join(Project, Project.id == Chat.project_id)
        .where(
            Project.org_id == org_id,
            ChatMessage.role == "assistant",
            ChatMessage.created_at >= start,
        )
    )


def size_label(size: int) -> str:
    """A size in the unit that reads best: 100 MB, 2.5 GB, 12 kB."""
    for unit, scale in (("GB", GB), ("MB", MB), ("kB", 1_000)):
        if size >= scale:
            value = size / scale
            return (
                f"{value:,.0f} {unit}"
                if value >= 10 or value == int(value)
                else f"{value:.1f} {unit}"
            )
    return f"{size} bytes"


def _documents(session: Session, org_id: int) -> tuple[int, int]:
    """How many documents the organisation holds, and their bytes."""
    count, held = session.execute(
        select(func.count(Document.id), func.coalesce(func.sum(Document.size_bytes), 0))
        .join(Project, Project.id == Document.project_id)
        .where(Project.org_id == org_id)
    ).one()
    return count, held


def _members(session: Session, org_id: int) -> int:
    members = session.scalar(
        select(func.count()).select_from(Membership).where(Membership.org_id == org_id)
    )
    invited = session.scalar(
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.org_id == org_id,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > func.now(),
        )
    )
    return members + invited


def _label(organization: Organization) -> str:
    return organization.plan.capitalize()


def _count(number: int, noun: str) -> str:
    return f"{number:,} {noun}{'' if number == 1 else 's'}"


def _month(moment: datetime) -> str:
    return MONTHS[moment.month - 1]
