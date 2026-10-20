"""What each plan entitles an organisation to, and the checks that hold it to that.

An organisation is on one plan (`Organization.plan`, app/models.py), and a plan is a row of
PLANS: how many projects, documents and bytes of documents the organisation may hold, how many
pages it may index and questions it may ask in a calendar month, how many members it may have,
whether it may use the API and the connectors, how long its data is kept, which tier of model
serves it, and how much of the workers its jobs get. A limit of None is no limit. A plan sold
by the seat (Team) takes its members from the seats the organisation bought
(`organizations.seats`), and any figure may be agreed with an organisation in place of its
plan's (`organizations.overrides`: an Enterprise organisation's terms); `entitlements_of` is
the one place that puts the three together, and app/billing.py the one that changes them. The
platform's own ceilings (MAX_UPLOAD_MB, MAX_ANSWERS and the PARSE_* limits in app/config.py)
are the same for every plan and stay settings; everything here differs by plan, so it is read
per request through the account (app/api/deps.py), never from the settings.

The checks run where the thing is about to happen: `check_project` in the route that creates
one, `check_upload` in the upload route before anything is stored and again per file,
`check_pages` in the parse stage once it knows how many pages a document has (a document
without pages, a Word file, a sheet or a text file, has those of its text, `text_pages`),
`check_question`
in the ask route, and `check_member` by the invitations and the joining by domain. Each
returns a `Refusal`, the sentence the user sees, naming the plan, the limit and what to do,
with the figure it is held to, or None when the plan allows it; a route raises `PlanRefused`
with it, which app/main.py answers 402 with both, so that the page can offer what lifts that
figure (a pack for the month, a larger plan, more seats); the stage fails the document with
the sentence. The counts come
from the rows (projects, documents and their bytes, memberships), so a deleted document frees
what it took, or from the month so far in UTC (the pages indexed, by the `upload` usage
events; the questions, by the `answer` events in `questions` each question writes as it is
asked, so that a chat deleted gives none back), which starts afresh on the first. Pages and
questions bought in packs for the month (`allowance_packs`, app/billing.py) add to the plan's
until it ends. `holdings` is what the organisation holds now, as the checks count it, which
`over_limits` and the usage page (app/api/usage.py) both read.

An organisation whose plan, seats or terms were lowered under what it holds (a subscription
that lapsed to Free, seats taken off) keeps it all, and is read-only until it is within its plan
again (`read_only`): what it holds can be read, asked about and deleted, members removed and
invitations revoked, but nothing is added or changed, which the role checks in app/api/deps.py
hold every change of a project to. Deleting stays, since it is how the organisation gets back
within the plan without buying one.

`priority` weights the organisation's share of the workers in the job claim (app/jobs.py): an
organisation's jobs count for a third as much on the team plan as on the free one, so its
backlog gets the larger share. `daily_spend_micros` is what a day of the organisation's model
use may cost, by the prices the usage rows are costed with; app/spending.py holds the check,
beside the platform's own daily limit, since the two are read together. `retention_days` is
how long a chat is kept after its last question, which the housekeeper holds it to
(app/retention.py), and `audit_days` how long a row of the audit log is (app/audit.py).
`model_tier`, `api_access` and `connectors` are declared for the features that will read them
(the organisation settings, the API keys, the connectors) and enforced by nothing yet.
"""

from collections.abc import Iterable
from dataclasses import dataclass, fields, replace
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    PLANS as PLAN_NAMES,
)
from app.models import (
    AllowancePack,
    Document,
    Invitation,
    Membership,
    Organization,
    Project,
    UsageEvent,
)
from app.usage import ANSWER, UPLOAD

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


# The plans whose members are the seats an organisation bought (`organizations.seats`).
BY_THE_SEAT = frozenset({"team"})
# A document without pages (a Word file, a sheet, a text file) counts against the month a page
# for every CHARS_PER_PAGE characters of its text begun, about what a page of a report holds,
# so that a file of up to PARSE_MAX_CHARS is indexed and embedded against the plan like a PDF.
CHARS_PER_PAGE = 3_000
MODEL_TIERS = ("standard", "premium")
# The figures of `Entitlements` that limit what an organisation holds at any moment, as
# against what it uses in a month; past one of them it is read-only (`read_only`).
HELD = ("projects", "documents", "storage_bytes", "members")


@dataclass(frozen=True)
class Refusal:
    """What a plan turned down: the sentence the member reads, and the figure of `Entitlements`
    it is held to, from which the page chooses what to offer."""

    limit: str
    sentence: str


class PlanRefused(Exception):
    """A request the organisation's plan turns down, which app/main.py answers 402."""

    def __init__(self, refusal: Refusal):
        super().__init__(refusal.sentence)
        self.refusal = refusal


@dataclass(frozen=True)
class Excess:
    """A figure the organisation holds past what it may: its field of `Entitlements`, and how it
    reads, as "7 members of 5"."""

    limit: str
    words: str


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
    # How many days a chat is kept after its last question (app/retention.py).
    retention_days: int | None
    # How many days the organisation's audit log keeps a row (app/audit.py): longer than a
    # chat, since an audit trail answers questions about the past.
    audit_days: int | None
    # standard | premium: which models answer and index for the organisation, once the
    # organisation settings choose a model by it.
    model_tier: str
    # The weight of the organisation's share of the workers in the job claim (app/jobs.py).
    priority: int
    # What the organisation's model use may cost in one UTC day, in millionths of
    # PRICE_CURRENCY (app/spending.py); past it, its questions are refused and its indexing
    # waits for midnight UTC. Counts nothing without MODEL_PRICES.
    daily_spend_micros: int | None


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
        audit_days=90,
        model_tier="standard",
        priority=1,
        daily_spend_micros=1_000_000,
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
        audit_days=365,
        model_tier="standard",
        priority=2,
        daily_spend_micros=10_000_000,
    ),
    "team": Entitlements(
        projects=50,
        documents=10_000,
        storage_bytes=100 * GB,
        pages_per_month=25_000,
        questions_per_month=5_000,
        # Sold by the seat: the organisation's seats replace this, what a team has before it
        # buys any.
        members=3,
        api_access=True,
        connectors=True,
        retention_days=None,
        audit_days=365,
        model_tier="premium",
        priority=3,
        daily_spend_micros=50_000_000,
    ),
    # Terms agreed per organisation (`organizations.overrides`); without any, nothing here limits
    # it, only the platform's ceilings do.
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
        audit_days=None,
        model_tier="premium",
        priority=4,
        daily_spend_micros=None,
    ),
}
assert set(PLANS) == set(PLAN_NAMES), "every plan the database accepts has its entitlements"


FIELDS = tuple(field_.name for field_ in fields(Entitlements))


def entitlements_of(organization: Organization) -> Entitlements:
    """What the organisation may keep and use: its plan's entitlements, with the seats it
    bought for the plan's members and the figures agreed with it for the plan's."""
    entitlements = PLANS[organization.plan]
    if (seats := seats_of(organization)) is not None:
        entitlements = replace(entitlements, members=seats)
    if agreed := agreed_with(organization):
        entitlements = replace(entitlements, **agreed)
    return entitlements


def seats_of(organization: Organization) -> int | None:
    """The members the organisation bought, on a plan sold by the seat; None on another plan,
    or before it bought any."""
    return organization.seats if organization.plan in BY_THE_SEAT else None


def agreed_with(organization: Organization) -> dict[str, object]:
    """The figures agreed with the organisation in place of its plan's, in the order of the
    fields (the database keeps none). A name no longer a field of `Entitlements` is passed
    over, so that a renamed field cannot stop the organisation's every request."""
    overrides = organization.overrides or {}
    return {name: overrides[name] for name in FIELDS if name in overrides}


def share_weights() -> dict[str, int]:
    """Each plan's weight in the job claim, by plan name (app/jobs.py)."""
    return {plan: entitlements.priority for plan, entitlements in PLANS.items()}


def month_of(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The start of the current calendar month in UTC, and the start of the next."""
    now = now or datetime.now(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    following = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1, tzinfo=UTC)
    return start, following


def check_project(session: Session, organization: Organization) -> Refusal | None:
    """Why the plan refuses another project, or None when it has room for one."""
    cap = entitlements_of(organization).projects
    if cap is None:
        return None
    count = _projects(session, organization.id)
    if count < cap:
        return None
    return Refusal(
        "projects",
        f"The {_label(organization)} plan allows {_count(cap, 'project')}, and this "
        f"organisation has {count}. {upgrade(organization).capitalize()} to create another.",
    )


def check_upload(
    session: Session, organization: Organization, size: int | None = None
) -> Refusal | None:
    """Why the plan refuses another document, or None when it has room: for any at all when no
    size is given (the request as a whole, which is also when the month's pages are checked),
    or for one of `size` bytes (each file of it)."""
    plan = entitlements_of(organization)
    label = _label(organization)
    count, held = _documents(session, organization.id)
    if plan.documents is not None and count >= plan.documents:
        return Refusal(
            "documents",
            f"The {label} plan allows {_count(plan.documents, 'document')}, and this "
            f"organisation has {count}. Delete some, or {upgrade(organization)} to add more.",
        )
    if plan.storage_bytes is not None:
        if held >= plan.storage_bytes:
            return Refusal(
                "storage_bytes",
                f"The {label} plan allows {size_label(plan.storage_bytes)} of documents, and "
                f"this organisation holds {size_label(held)}. Delete some, or "
                f"{upgrade(organization)} to add more.",
            )
        if size is not None and held + size > plan.storage_bytes:
            return Refusal(
                "storage_bytes",
                f"This file is {size_label(size)}, more than the "
                f"{size_label(plan.storage_bytes - held)} left of the {label} plan's "
                f"{size_label(plan.storage_bytes)}. Delete some documents, or "
                f"{upgrade(organization)}.",
            )
    if size is None and plan.pages_per_month is not None:
        start, following = month_of()
        used = pages_this_month(session, organization.id, start)
        bought = bought_this_month(session, organization.id, "pages", start)
        if used >= plan.pages_per_month + bought:
            return Refusal(
                "pages_per_month",
                f"The {label} plan indexes {_count(plan.pages_per_month, 'page')} a month"
                f"{_and_bought(bought, start)}, and this organisation has indexed {used:,} in "
                f"{_month(start)}. {upgrade(organization).capitalize()}, or upload again from "
                f"1 {_month(following)}.",
            )
    return None


def text_pages(characters: int) -> int:
    """The pages a document without pages counts for, by the characters of its text: one for
    every CHARS_PER_PAGE begun."""
    return -(-characters // CHARS_PER_PAGE)


def check_pages(
    session: Session, organization: Organization, pages: int, *, of_text: bool = False
) -> Refusal | None:
    """Why the plan refuses indexing a document of `pages` pages this month, or None when the
    month has room for them. `of_text` says the pages are a document's text (`text_pages`),
    which the sentence says, or its member would read a Word file's pages as a mistake."""
    cap = entitlements_of(organization).pages_per_month
    if cap is None or not pages:
        return None
    start, following = month_of()
    used = pages_this_month(session, organization.id, start)
    bought = bought_this_month(session, organization.id, "pages", start)
    if used + pages <= cap + bought:
        return None
    has = (
        f"about {_count(pages, 'page')} of text (a page for every {CHARS_PER_PAGE:,} characters)"
        if of_text
        else _count(pages, "page")
    )
    return Refusal(
        "pages_per_month",
        f"This document has {has}, more than the "
        f"{max(cap + bought - used, 0):,} left of the {_label(organization)} plan's {cap:,} a "
        f"month{_and_bought(bought, start)}. {upgrade(organization).capitalize()}, or index it "
        f"again from 1 {_month(following)}.",
    )


def check_question(session: Session, organization: Organization) -> Refusal | None:
    """Why the plan refuses another question this month, or None when it has room for one."""
    cap = entitlements_of(organization).questions_per_month
    if cap is None:
        return None
    start, following = month_of()
    asked = questions_this_month(session, organization.id, start)
    bought = bought_this_month(session, organization.id, "questions", start)
    if asked < cap + bought:
        return None
    return Refusal(
        "questions_per_month",
        f"The {_label(organization)} plan answers {_count(cap, 'question')} a month"
        f"{_and_bought(bought, start)}, and this organisation has asked {asked:,} in "
        f"{_month(start)}. {upgrade(organization).capitalize()}, or ask again from 1 "
        f"{_month(following)}.",
    )


def check_member(session: Session, organization: Organization) -> Refusal | None:
    """Why the plan refuses another member, or None when it has room for one. An invitation
    that is still open holds a place. An organisation with fewer seats than members keeps them
    all, and takes nobody until it has room."""
    cap = entitlements_of(organization).members
    if cap is None:
        return None
    taken = _members(session, organization.id)
    if taken < cap:
        return None
    if seats_of(organization) is not None:
        return Refusal(
            "members",
            f"This organisation has {_count(cap, 'seat')} on the {_label(organization)} plan, "
            f"and {_count(taken, 'member')}, open invitations counted. Add seats to invite more.",
        )
    return Refusal(
        "members",
        f"The {_label(organization)} plan allows {_count(cap, 'member')}, and this organisation "
        f"has {taken}, open invitations counted. {upgrade(organization).capitalize()} to invite "
        "more.",
    )


def over_limits(session: Session, organization: Organization) -> list[Excess]:
    """What the organisation holds past what it may, once its plan, seats or terms were lowered
    under it: it keeps everything, and is read-only until it is within them (`read_only`).
    Counts only the figures its plan limits, so that an organisation without limits costs no
    query."""
    entitlements = entitlements_of(organization)
    caps = {name: cap for name in HELD if (cap := getattr(entitlements, name)) is not None}
    held = holdings(session, organization.id, caps)
    return [
        Excess(name, _held_words(name, held[name], cap))
        for name, cap in caps.items()
        if held[name] > cap
    ]


def holdings(session: Session, org_id: int, figures: Iterable[str] = HELD) -> dict[str, int]:
    """How much the organisation holds now of each of `figures` (of HELD), counted as the
    checks count it: its projects, its documents and their bytes, and its members with the
    open invitations, each of which holds a member's place. A figure not asked for costs no
    query."""
    wanted = set(figures)
    held = {}
    if "projects" in wanted:
        held["projects"] = _projects(session, org_id)
    if wanted & {"documents", "storage_bytes"}:
        held["documents"], held["storage_bytes"] = _documents(session, org_id)
    if "members" in wanted:
        held["members"] = _members(session, org_id)
    return {name: held[name] for name in HELD if name in wanted}


def _held_words(name: str, held: int, cap: int) -> str:
    """How a figure held past its cap reads: "7 members of 5"."""
    if name == "storage_bytes":
        return f"{size_label(held)} of documents of {size_label(cap)}"
    return f"{_count(held, name.removesuffix('s'))} of {cap:,}"


def read_only(session: Session, organization: Organization) -> Refusal | None:
    """Why nothing can be added to or changed in the organisation now, or None when it holds no
    more than it may: past its plan, what it holds can be read, asked about and deleted and its
    members removed, which is how it gets within the plan again, and nothing else."""
    if not (excesses := over_limits(session, organization)):
        return None
    limits = [excess.limit for excess in excesses]
    ways = []
    if any(limit != "members" for limit in limits):
        ways.append("delete what it no longer needs")
    if "members" in limits:
        ways.append("remove members or revoke invitations")
    seats = limits == ["members"] and seats_of(organization) is not None
    ways.append("add seats" if seats else upgrade(organization))
    return Refusal(
        limits[0],
        f"This organisation holds more than the {_label(organization)} plan allows "
        f"({_listed([excess.words for excess in excesses])}), so until it is within the plan "
        "nothing can be added or changed: what it holds can still be read, asked about and "
        f"deleted. {', or '.join([', '.join(ways[:-1]), ways[-1]]).capitalize()}.",
    )


def pages_this_month(session: Session, org_id: int, start: datetime) -> int:
    """The pages the organisation's uploads brought in since `start`: what the first parse of
    each document counted (app/indexing/pipeline.py), its text's for a document without
    pages."""
    return session.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.org_id == org_id,
            UsageEvent.kind == UPLOAD,
            UsageEvent.unit == "pages",
            UsageEvent.occurred_at >= start,
        )
    )


def questions_this_month(session: Session, org_id: int, start: datetime) -> int:
    """The questions asked in the organisation's projects since `start`, answered or not, and
    whether or not their chats are kept: what each wrote as it was asked (app/api/chats.py)."""
    return session.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.org_id == org_id,
            UsageEvent.kind == ANSWER,
            UsageEvent.unit == "questions",
            UsageEvent.occurred_at >= start,
        )
    )


def bought_this_month(session: Session, org_id: int, unit: str, start: datetime) -> int:
    """The pages or questions the organisation bought in packs for the month that starts at
    `start` (app/billing.py)."""
    return session.scalar(
        select(func.coalesce(func.sum(AllowancePack.quantity), 0)).where(
            AllowancePack.org_id == org_id,
            AllowancePack.unit == unit,
            AllowancePack.month == start,
        )
    )


def bought_by_month(session: Session, org_id: int, since: datetime) -> dict[tuple[date, str], int]:
    """The pages and questions the organisation bought in packs for each month from the one
    that starts at `since`, by the month's first day and the unit."""
    rows = session.execute(
        select(AllowancePack.month, AllowancePack.unit, func.sum(AllowancePack.quantity))
        .where(AllowancePack.org_id == org_id, AllowancePack.month >= since)
        .group_by(AllowancePack.month, AllowancePack.unit)
    )
    return {(month.astimezone(UTC).date(), unit): quantity for month, unit, quantity in rows}


def _listed(items: list[str]) -> str:
    """The items as a sentence lists them: "a", "a and b", "a, b and c"."""
    return " and ".join([", ".join(items[:-1]), items[-1]]) if len(items) > 1 else items[0]


def _and_bought(bought: int, start: datetime) -> str:
    return f" and {bought:,} more bought for {_month(start)}" if bought else ""


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


def _projects(session: Session, org_id: int) -> int:
    return session.scalar(select(func.count()).select_from(Project).where(Project.org_id == org_id))


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
        .where(Invitation.org_id == org_id, Invitation.expires_at > func.now())
    )
    return members + invited


def upgrade(organization: Organization) -> str:
    """What a refusal offers besides deleting or waiting: a larger plan, or on the largest, a
    higher figure in the organisation's terms."""
    if organization.plan == PLAN_NAMES[-1]:
        return "ask for a higher limit"
    return "upgrade your plan"


def _label(organization: Organization) -> str:
    return organization.plan.capitalize()


def _count(number: int, noun: str) -> str:
    return f"{number:,} {noun}{'' if number == 1 else 's'}"


def _month(moment: datetime) -> str:
    return MONTHS[moment.month - 1]
