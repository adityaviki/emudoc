"""Daily spending limits: what a day of model use may cost, the checks that hold the chat and
the indexing stages to it, and the warning the operator gets as a day draws near a limit.

There are two limits, both read against the costs the usage rows carry (`cost_micros`,
app/usage.py, from MODEL_PRICES): the organisation's, which its plan sets
(`daily_spend_micros` in app/entitlements.py; the enterprise plan has none), and the
platform's, DAILY_SPEND_CAP in app/config.py, the most the operator will see on the
providers' bills for one day whatever the organisations are entitled to. A day is the UTC
day, like every date the app keeps, and both limits start afresh at midnight UTC. A row for a
model without a price costs nothing known, so the limits count nothing without MODEL_PRICES:
production refuses the platform's limit without prices (app/config.py) and warns when the
plans' limits have none to count (`warn_without_prices`).

A question is checked twice. The ask route (app/api/chats.py) refuses it before it is saved,
with 402 and the plan's sentence for the organisation's limit and with 503 and a Retry-After
until midnight for the platform's, which is not the caller's fault. The worker checks again
as it starts the answer (app/answers.py), since MAX_ANSWERS questions may have been queued
under the limit at once, and ends the answer with the sentence as its error. An indexing stage
that calls the model checks before its first call (app/indexing/pipeline.py): parse, for the
scanned pages it would read, and enrich put the stage off until midnight UTC with the sentence
in the stage's detail, and the worker queues the document again for then (app/worker.py);
structure, which the document cannot be searched without, builds its outline from the pages
instead of asking the model and says so in its detail. A limit holds the next unit of work,
not the running ones: the check comes before a call, and an answer or a stage that has started
finishes, so a day can run past its limit by what was in flight. The kill switch for a bill
that cannot wait for midnight is DISABLED_PROVIDERS (app/llm.py).

The warning: when a day's spending reaches ALERT_SHARE of a limit, the worker's alerter thread
(app/worker.py) mails the operator at ALERT_EMAIL, once that day for each organisation and
once for the platform, in the same round as the failed jobs (`alert_spending`); a
`spend_alerts` row says it was sent. The usage command (app/maintenance.py) prints the day's
cost; the providers' consoles have the bill itself, and a budget set there is the last line
(the README says where).
"""

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import MIGRATION_LOCK, SessionLocal
from app.entitlements import MONTHS, PLANS, entitlements_of
from app.mail import Mailer
from app.models import Organization, SpendAlert, UsageEvent
from app.usage import money

log = logging.getLogger(__name__)

USAGE_COMMAND = "uv run python -m app.maintenance usage <slug>"
# The share of a limit at which the operator is warned: far enough from it to act, near enough
# that a quiet day never warns.
ALERT_SHARE = Decimal("0.8")
# The advisory lock a warning round holds, so that two workers whose rounds cross cannot both
# find a day unwarned and both mail.
ALERT_LOCK = MIGRATION_LOCK + 2


def day_of(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The start of the current UTC day, and the start of the next. A moment in another zone
    is read in UTC first: east of Greenwich the next day begins before UTC's does."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    start = datetime.combine(now.date(), time.min, UTC)
    return start, start + timedelta(days=1)


def spent_since(session: Session, start: datetime, org_id: int | None = None) -> int:
    """The known cost of everything since `start`, in millionths of PRICE_CURRENCY: one
    organisation's, or every organisation's together when none is named."""
    query = select(func.coalesce(func.sum(UsageEvent.cost_micros), 0)).where(
        UsageEvent.occurred_at >= start
    )
    if org_id is not None:
        query = query.where(UsageEvent.org_id == org_id)
    return session.scalar(query)


def check_platform_spend(session: Session) -> str | None:
    """Why the platform's daily limit (DAILY_SPEND_CAP) leaves no room for more model work
    today, or None while it does or there is no limit."""
    cap = get_settings().daily_spend_cap_micros
    if not cap:
        return None
    spent = spent_since(session, day_of()[0])
    if spent < cap:
        return None
    return (
        f"Emudoc has reached its spending limit of {money(cap)} a day on models "
        f"({money(spent)} spent today, UTC). The limit resets at midnight UTC; try again then."
    )


def check_spend(session: Session, organization: Organization) -> str | None:
    """Why the plan's daily limit leaves no room for more of the organisation's model work
    today, or None while it does or the plan has no limit."""
    cap = entitlements_of(organization).daily_spend_micros
    if cap is None:
        return None
    spent = spent_since(session, day_of()[0], organization.id)
    if spent < cap:
        return None
    return (
        f"The {organization.plan.capitalize()} plan allows {money(cap)} of model use a day, and "
        f"this organisation has used {money(spent)} today (UTC). Upgrade your plan, or try again "
        "after midnight UTC."
    )


def retry_after(now: datetime | None = None) -> int:
    """The seconds until the limits reset, for the Retry-After header of a refusal."""
    now = now or datetime.now(UTC)
    return max(1, math.ceil((day_of(now)[1] - now).total_seconds()))


def when(moment: datetime) -> str:
    """A moment as a stage's detail names it: 00:00 UTC on 14 October."""
    moment = moment.astimezone(UTC)
    return f"{moment:%H:%M} UTC on {moment.day} {MONTHS[moment.month - 1]}"


def warn_without_prices(settings: Settings) -> None:
    """Say, at a production start, that the plans' daily limits count nothing without
    MODEL_PRICES; the platform's limit without them is refused outright (app/config.py)."""
    limited = any(plan.daily_spend_micros is not None for plan in PLANS.values())
    if settings.environment == "production" and limited and not settings.model_prices:
        log.warning(
            "MODEL_PRICES is not set: no cost is recorded, so the plans' daily spending limits "
            "count nothing. List what each model costs per million tokens in and out."
        )


@dataclass(frozen=True)
class NearLimit:
    """A day's spending that has reached ALERT_SHARE of a limit: an organisation's, or the
    platform's when `organization` is None."""

    organization: Organization | None
    spent_micros: int
    cap_micros: int

    @property
    def share(self) -> int:
        """The spending as a percentage of the limit, rounded down."""
        return int(self.spent_micros * 100 // self.cap_micros)

    @property
    def who(self) -> str:
        if self.organization is None:
            return "the platform"
        return f"{self.organization.name} ({self.organization.slug})"

    @property
    def line(self) -> str:
        """The warning's line for it: who, how much of which limit, and the share."""
        who = "The platform" if self.organization is None else self.who
        limit = (
            "DAILY_SPEND_CAP"
            if self.organization is None
            else f"the {self.organization.plan.capitalize()} plan's"
        )
        return (
            f"  {who}: {money(self.spent_micros)} of {limit} {money(self.cap_micros)} "
            f"({self.share} %)"
        )


def near_limits(session: Session, start: datetime) -> list[NearLimit]:
    """What has reached ALERT_SHARE of a daily limit since `start`: the platform first, then
    the organisations by slug."""
    by_org = dict(
        session.execute(
            select(UsageEvent.org_id, func.sum(UsageEvent.cost_micros))
            .where(UsageEvent.occurred_at >= start, UsageEvent.cost_micros.is_not(None))
            .group_by(UsageEvent.org_id)
        ).all()
    )
    near: list[NearLimit] = []
    cap = get_settings().daily_spend_cap_micros
    if cap and (total := sum(by_org.values())) >= cap * ALERT_SHARE:
        near.append(NearLimit(None, total, cap))
    organizations = session.scalars(
        select(Organization).where(Organization.id.in_(by_org)).order_by(Organization.slug)
    )
    for organization in organizations:
        cap = entitlements_of(organization).daily_spend_micros
        if cap is not None and by_org[organization.id] >= cap * ALERT_SHARE:
            near.append(NearLimit(organization, by_org[organization.id], cap))
    return near


def spending_email(near: list[NearLimit]) -> tuple[str, str]:
    """The warning: the subject names who is near a limit, the body says how near and what
    happens at it."""
    share = int(ALERT_SHARE * 100)
    if len(near) == 1:
        subject = f"Emudoc: {near[0].who} has spent {near[0].share} % of its daily limit"
    else:
        subject = f"Emudoc: {len(near)} spending warnings today"
    listed = "\n".join(item.line for item in near)
    body = (
        f"Today's (UTC) spending on models has reached {share} % of a daily limit:\n\n"
        f"{listed}\n\n"
        "At its limit, an organisation's questions are refused with the plan's message and its "
        "indexing waits for midnight UTC; at the platform's, every question is refused and every "
        "indexing stage that needs a model waits. Both reset at midnight UTC.\n\n"
        f"`{USAGE_COMMAND}` in the backend directory prints an organisation's use per day; the "
        "providers' consoles have the bill itself. To stop the spending at once, set "
        "DISABLED_PROVIDERS and restart the app.\n"
    )
    return subject, body


def alert_spending(mailer: Mailer, to: str) -> int:
    """Mail the operator at `to` about what has reached ALERT_SHARE of a daily limit today and
    was not warned about yet, as one message, and note the warning in `spend_alerts`.

    The note is committed only once the message was handed over, so that a relay that is down
    leaves the warning for the next round. Returns how many were warned about. With no address
    nothing is sent or noted.
    """
    if not to:
        return 0
    with SessionLocal() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ALERT_LOCK})
        start, _ = day_of()
        warned = set(
            session.scalars(select(SpendAlert.org_id).where(SpendAlert.day == start.date()))
        )
        due = [
            item
            for item in near_limits(session, start)
            if (item.organization.id if item.organization else None) not in warned
        ]
        if not due:
            return 0
        session.add_all(
            SpendAlert(
                org_id=item.organization.id if item.organization else None,
                day=start.date(),
                spent_micros=item.spent_micros,
                cap_micros=item.cap_micros,
            )
            for item in due
        )
        subject, body = spending_email(due)
        mailer.send(to, subject, body)
        session.commit()
    return len(due)
