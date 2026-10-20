"""The organisation's usage page: every figure its plan sets beside what it holds or has used
of it, counted as the checks count it (app/entitlements.py), so that the page and a refusal
agree, and the pages and questions of its last DAYS days and MONTHS months (app/usage.py), in
UTC as the plan counts them. Every member reads it, as every member reads the plan's figures
on the organisation page; it names no member and nothing that was asked."""

from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter

from app.api.deps import AccountDep, SessionDep
from app.entitlements import (
    CHARS_PER_PAGE,
    HELD,
    bought_by_month,
    entitlements_of,
    holdings,
    month_of,
    sample_documents,
)
from app.schemas import FigureOut, MeteredMonthOut, MeteredOut, UsagePageOut
from app.usage import metered

router = APIRouter(tags=["usage"])

# How far back the page looks: the days of about a month, and the months of a year.
DAYS = 30
MONTHS = 12


@router.get("/organization/usage", response_model=UsagePageOut)
def organization_usage(account: AccountDep, session: SessionDep):
    """What the organisation holds and has used this month of what its plan allows, and its
    pages and questions by day and by month."""
    organization = account.organization
    entitlements = entitlements_of(organization)
    now = datetime.now(UTC)
    start, following = month_of(now)
    today = now.date()
    first = _months_before(start.date(), MONTHS - 1)
    bought = bought_by_month(session, organization.id, datetime.combine(first, time.min, UTC))
    months = metered(session, organization.id, first, today, "month")
    held = holdings(session, organization.id)
    figures = {
        name: FigureOut(used=held[name], allowed=getattr(entitlements, name)) for name in HELD
    }
    # The month so far is the last of the months, as the checks count it.
    this = months[-1]
    return UsagePageOut(
        month=start.date(),
        next_month=following.date(),
        **figures,
        pages_per_month=FigureOut(
            used=this.pages,
            allowed=entitlements.pages_per_month,
            bought=bought.get((this.start, "pages"), 0),
        ),
        questions_per_month=FigureOut(
            used=this.questions,
            allowed=entitlements.questions_per_month,
            bought=bought.get((this.start, "questions"), 0),
        ),
        chars_per_page=CHARS_PER_PAGE,
        sample_documents=sample_documents(session, organization.id),
        days=[
            MeteredOut(start=day.start, pages=day.pages, questions=day.questions)
            for day in metered(
                session, organization.id, today - timedelta(days=DAYS - 1), today, "day"
            )
        ],
        months=[
            MeteredMonthOut(
                start=month.start,
                pages=month.pages,
                questions=month.questions,
                pages_bought=bought.get((month.start, "pages"), 0),
                questions_bought=bought.get((month.start, "questions"), 0),
            )
            for month in months
        ],
    )


def _months_before(first: date, count: int) -> date:
    """The first day of the month `count` months before the one starting on `first`."""
    year, month = divmod(first.year * 12 + first.month - 1 - count, 12)
    return date(year, month + 1, 1)
