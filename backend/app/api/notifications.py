"""The user's notification centre (app/notices.py): their notices about every organisation they
belong to, the newest first, a page at a time, with how many of all of them are unread, which
the header's bell asks every minute; and marking them read.

The routes take the usable account without checking the organisation the page shows: the centre
is the user's across their organisations, and a tab left in one by a switch in another asks it
every minute, which must not start that tab again as a change it made would. A notice is found
by its user alone, so another's id among those to mark read marks nothing. The notices of an
organisation that requires two-factor sign-in are left out until the user signs in with it,
since they name what its projects hold (app/security.py).
"""

from fastapi import APIRouter, Query
from sqlalchemy import ColumnElement, func, select, update

from app.accounts import Account
from app.api.deps import SessionDep, UsableAccountDep
from app.models import Notification, Organization
from app.schemas import NotificationOut, NotificationPage, NotificationsReadIn

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=NotificationPage)
def list_notifications(
    account: UsableAccountDep,
    session: SessionDep,
    before: int | None = None,
    limit: int = Query(20, ge=1, le=50),
):
    """A page of the user's notices, older than the one `before` when it is given."""
    shown = _shown(account)
    query = (
        select(Notification, Organization.name)
        .join(Organization, Organization.id == Notification.org_id)
        .where(*shown)
        .order_by(Notification.id.desc())
        .limit(limit + 1)
    )
    if before is not None:
        query = query.where(Notification.id < before)
    rows = session.execute(query).all()
    page = rows[:limit]
    unread = session.scalar(
        select(func.count())
        .select_from(Notification)
        .join(Organization, Organization.id == Notification.org_id)
        .where(*shown, Notification.read_at.is_(None))
    )
    return NotificationPage(
        notifications=[
            NotificationOut(
                id=row.id,
                kind=row.kind,
                title=row.title,
                body=row.body,
                link=row.link,
                org_id=row.org_id,
                organization=name,
                created_at=row.created_at,
                read_at=row.read_at,
            )
            for row, name in page
        ],
        unread=unread or 0,
        next_before=page[-1][0].id if len(rows) > limit else None,
    )


@router.post("/notifications/read", status_code=204)
def mark_read(body: NotificationsReadIn, account: UsableAccountDep, session: SessionDep):
    """Mark the notices named read, or every one when none is named."""
    statement = (
        update(Notification)
        .where(Notification.user_id == account.user.id, Notification.read_at.is_(None))
        .values(read_at=func.now())
    )
    if body.ids is not None:
        statement = statement.where(Notification.id.in_(body.ids))
    session.execute(statement)
    session.commit()


def _shown(account: Account) -> list[ColumnElement[bool]]:
    """The conditions that pick the user's notices, without those of the organisations that
    hold them until they sign in with two-factor."""
    conditions = [Notification.user_id == account.user.id]
    if account.session_id is not None and not account.user.mfa_enabled:
        conditions.append(Organization.mfa_required.is_(False))
    return conditions
