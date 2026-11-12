"""The user's notification centre (app/notices.py): their notices of the workspace, the newest
first, a page at a time, with how many of them are unread, which the header's bell asks every
minute; and marking them read.

The routes take the account as every page's routes do, so a member held until they sign in with
two-factor reads none of them, since they name what its projects hold (app/security.py). A
notice is found by its user and the organisation the request works in, so another's id among
those to mark read marks nothing.
"""

from fastapi import APIRouter, Query
from sqlalchemy import ColumnElement, func, select, update

from app.accounts import Account
from app.api.deps import AccountDep, SessionDep
from app.models import Notification
from app.schemas import NotificationOut, NotificationPage, NotificationsReadIn

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=NotificationPage)
def list_notifications(
    account: AccountDep,
    session: SessionDep,
    before: int | None = None,
    limit: int = Query(20, ge=1, le=50),
):
    """A page of the user's notices, older than the one `before` when it is given."""
    shown = _shown(account)
    query = select(Notification).where(*shown).order_by(Notification.id.desc()).limit(limit + 1)
    if before is not None:
        query = query.where(Notification.id < before)
    rows = session.scalars(query).all()
    page = rows[:limit]
    unread = session.scalar(
        select(func.count()).select_from(Notification).where(*shown, Notification.read_at.is_(None))
    )
    return NotificationPage(
        notifications=[
            NotificationOut(
                id=row.id,
                kind=row.kind,
                title=row.title,
                body=row.body,
                link=row.link,
                created_at=row.created_at,
                read_at=row.read_at,
            )
            for row in page
        ],
        unread=unread or 0,
        next_before=page[-1].id if len(rows) > limit else None,
    )


@router.post("/notifications/read", status_code=204)
def mark_read(body: NotificationsReadIn, account: AccountDep, session: SessionDep):
    """Mark the notices named read, or every one when none is named."""
    statement = (
        update(Notification)
        .where(*_shown(account), Notification.read_at.is_(None))
        .values(read_at=func.now())
    )
    if body.ids is not None:
        statement = statement.where(Notification.id.in_(body.ids))
    session.execute(statement)
    session.commit()


def _shown(account: Account) -> list[ColumnElement[bool]]:
    """The conditions that pick the user's notices of the organisation the request works in."""
    return [Notification.user_id == account.user.id, Notification.org_id == account.organization.id]
