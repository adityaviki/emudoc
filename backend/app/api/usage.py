"""The workspace's usage page: what it holds now, and the pages it indexed, the questions it
asked and what its work cost over its last DAYS days and MONTHS months (app/usage.py), in UTC.
Every member reads it; the costs are its owners' and admins' alone (app/costs.py), and it
names no member and nothing that was asked."""

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.api.deps import AccountDep, SessionDep
from app.models import Organization
from app.schemas import MeteredOut, UsagePageOut
from app.usage import CHARS_PER_PAGE, holdings, metered, month_of, sample_documents

router = APIRouter(tags=["usage"])

# How far back the page looks: the days of about a month, and the months of a year.
DAYS = 30
MONTHS = 12


@router.get("/organization/usage", response_model=UsagePageOut)
def organization_usage(account: AccountDep, session: SessionDep):
    """What the workspace holds, and what it used and what that cost by day and by month."""
    return usage_page(session, account.organization)


def usage_page(session: Session, organization: Organization) -> UsagePageOut:
    """The usage page of the organisation, which the operator's area shows as its members read
    it (app/api/operator.py)."""
    now = datetime.now(UTC)
    start, following = month_of(now)
    today = now.date()
    first = _months_before(start.date(), MONTHS - 1)
    return UsagePageOut(
        month=start.date(),
        next_month=following.date(),
        **holdings(session, organization.id),
        chars_per_page=CHARS_PER_PAGE,
        sample_documents=sample_documents(session, organization.id),
        days=[
            MeteredOut.model_validate(day, from_attributes=True)
            for day in metered(
                session, organization.id, today - timedelta(days=DAYS - 1), today, "day"
            )
        ],
        months=[
            MeteredOut.model_validate(month, from_attributes=True)
            for month in metered(session, organization.id, first, today, "month")
        ],
    )


def _months_before(first: date, count: int) -> date:
    """The first day of the month `count` months before the one starting on `first`."""
    year, month = divmod(first.year * 12 + first.month - 1 - count, 12)
    return date(year, month + 1, 1)
