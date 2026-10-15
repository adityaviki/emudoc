"""The audit log as an organisation's admins read it (app/audit.py): its rows, the newest
first, a page at a time. The organisation settings will put a view with filters and an export
on top (2.7); until then this is the whole of it."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AccountDep, SessionDep
from app.models import AuditEvent, User
from app.schemas import AuditEventOut, AuditPage

router = APIRouter(tags=["audit"])

ADMINS_ONLY = (
    "Only the owners and admins of the organisation can read its audit log. Ask one of them "
    "for what you need."
)


@router.get("/audit", response_model=AuditPage)
def list_audit(
    account: AccountDep,
    session: SessionDep,
    before: int | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    """A page of the organisation's audit log, older than the row `before` when it is given."""
    if not account.is_at_least("admin"):
        raise HTTPException(403, ADMINS_ONLY)
    query = (
        select(AuditEvent, User.name, User.email)
        .outerjoin(User, User.id == AuditEvent.user_id)
        .where(AuditEvent.org_id == account.organization.id)
        .order_by(AuditEvent.id.desc())
        .limit(limit + 1)
    )
    if before is not None:
        query = query.where(AuditEvent.id < before)
    rows = session.execute(query).all()
    page = rows[:limit]
    return AuditPage(
        events=[
            AuditEventOut(
                id=row.id,
                occurred_at=row.occurred_at,
                action=row.action,
                user_id=row.user_id,
                user_name=name,
                user_email=email,
                ip=row.ip,
                project_id=row.project_id,
                target_type=row.target_type,
                target_id=row.target_id,
                detail=row.detail,
            )
            for row, name, email in page
        ],
        next_before=page[-1][0].id if len(rows) > limit else None,
    )
