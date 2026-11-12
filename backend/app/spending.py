"""The daily spending cap: what a day of model use may cost, the checks that hold the chat and
the indexing stages to it, and the warning the admin gets as a day draws near it.

The cap is DAILY_SPEND_CAP in app/config.py, read against the costs the usage rows carry
(`cost_micros`, app/usage.py, from MODEL_PRICES): the most the providers' bills show for one
day. A day is the UTC day, like every date the app keeps, and the cap starts afresh at midnight
UTC. A row for a model without a price costs nothing known, so the cap counts nothing without
MODEL_PRICES, which production refuses (app/config.py).

A question is checked twice. The ask route (app/api/chats.py) refuses it before it is saved,
with 503 and a Retry-After until midnight, since it is not the caller's fault. The worker checks
again as it starts the answer (app/answers.py), since MAX_ANSWERS questions may have been
queued under the cap at once, and ends the answer with the sentence as its error. An indexing
stage that calls the model checks before its first call (app/indexing/pipeline.py): parse, for
the scanned pages it would read, and enrich put the stage off until midnight UTC with the
sentence in the stage's detail, and the worker queues the document again for then
(app/worker.py); structure, which the document cannot be searched without, builds its outline
from the pages instead of asking the model and says so in its detail. The cap holds the next
unit of work, not the running ones: the check comes before a call, and an answer or a stage
that has started finishes, so a day can run past the cap by what was in flight. The kill switch
for a bill that cannot wait for midnight is DISABLED_PROVIDERS (app/llm.py).

The warning: when a day's spending reaches ALERT_SHARE of the cap, the worker's alerter thread
(app/worker.py) mails ALERT_EMAIL, once that day, in the same round as the failed jobs
(`alert_spending`); a `spend_alerts` row says it was sent. The costs command
(app/maintenance.py) prints the day's cost, and the usage command the workspace's by kind; the
providers' consoles have the bill itself, and a budget set there is the last line (the README
says where).
"""

import math
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import MIGRATION_LOCK, SessionLocal
from app.mail import Mailer, Message
from app.models import SpendAlert, UsageEvent
from app.usage import MONTHS, money

USAGE_COMMAND = "uv run python -m app.maintenance usage <slug>"
COSTS_COMMAND = "uv run python -m app.maintenance costs --days 1"
# The share of the cap at which the admin is warned: far enough from it to act, near enough
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


def spent_since(session: Session, start: datetime) -> int:
    """The known cost of everything since `start`, in millionths of PRICE_CURRENCY."""
    return session.scalar(
        select(func.coalesce(func.sum(UsageEvent.cost_micros), 0)).where(
            UsageEvent.occurred_at >= start
        )
    )


def check_spend(session: Session) -> str | None:
    """Why the daily cap (DAILY_SPEND_CAP) leaves no room for more model work today, or None
    while it does or there is no cap."""
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


def retry_after(now: datetime | None = None) -> int:
    """The seconds until the cap resets, for the Retry-After header of a refusal."""
    now = now or datetime.now(UTC)
    return max(1, math.ceil((day_of(now)[1] - now).total_seconds()))


def when(moment: datetime) -> str:
    """A moment as a stage's detail names it: 00:00 UTC on 14 October."""
    moment = moment.astimezone(UTC)
    return f"{moment:%H:%M} UTC on {moment.day} {MONTHS[moment.month - 1]}"


def spending_email(spent: int, cap: int) -> tuple[str, str]:
    """The warning: the subject says how much of the cap is spent, the body what happens at it
    and where to look."""
    share = spent * 100 // cap
    subject = f"Emudoc has spent {share} % of its daily limit on models"
    body = (
        f"Today's (UTC) spending on models is {money(spent)}, {share} % of DAILY_SPEND_CAP, "
        f"{money(cap)}.\n\n"
        "At the cap, every question is refused and every indexing stage that needs a model waits; "
        "it resets at midnight UTC.\n\n"
        f"In the backend directory, `{COSTS_COMMAND}` prints what today's work cost, and "
        f"`{USAGE_COMMAND}` the workspace's use per day; the providers' consoles have the bill "
        "itself. To stop the spending at once, set DISABLED_PROVIDERS and restart the app.\n"
    )
    return subject, body


def alert_spending(mailer: Mailer, to: str) -> int:
    """Mail `to` once today's spending reaches ALERT_SHARE of the daily cap, when it was not
    warned about today yet, and note the warning in `spend_alerts`.

    The note is committed only once the message was handed over, so that a relay that is down
    leaves the warning for the next round. Returns how many were sent, 0 or 1. With no address
    or no cap nothing is sent or noted.
    """
    cap = get_settings().daily_spend_cap_micros
    if not (to and cap):
        return 0
    with SessionLocal() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ALERT_LOCK})
        start, _ = day_of()
        if session.scalar(select(SpendAlert.id).where(SpendAlert.day == start.date())):
            return 0
        spent = spent_since(session, start)
        if spent < cap * ALERT_SHARE:
            return 0
        session.add(SpendAlert(day=start.date(), spent_micros=spent, cap_micros=cap))
        subject, body = spending_email(spent, cap)
        mailer.send(Message(to, subject, body))
        session.commit()
    return 1
