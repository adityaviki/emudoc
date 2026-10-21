"""The audit log as an organisation's owners and admins read it (app/audit.py): its rows, the
newest first, a page at a time, of the actions, the member, the project and the span of time
asked for; and the same rows as a CSV file to download, which is itself a row of the log.

The rows are read by the time they were recorded, then by id for those of one transaction, and
each filter has an index that ends in that order (`AuditEvent.__table_args__`), so that a rare
action, a quiet project or a month a year ago in a large organisation is a range of an index
rather than a walk through all of its rows. A page goes on from the row `before` names.

The file is written as it is read, a batch of rows at a time in a session of its own, so that a
long log neither holds a database connection for its whole download nor sits in memory. A cell
that a spreadsheet would take for a formula, such as a file named "=HYPERLINK(…)", is written
with a quote in front, as OWASP advises, since that is where admins open it; and the file starts
with a byte order mark, without which Excel reads its names as another encoding.
"""

import csv
import datetime as dt
import io
import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, and_, select, tuple_
from sqlalchemy.orm import Session

from app.accounts import Account
from app.api.deps import AccountDep, SessionDep
from app.audit import audit
from app.db import SessionLocal
from app.models import AUDIT_ACTIONS, AuditEvent, Project, User
from app.schemas import AuditEventOut, AuditPage
from app.storage import content_disposition

router = APIRouter(tags=["audit"])

ADMINS_ONLY = (
    "Only the owners and admins of the organisation can read its audit log. Ask one of them "
    "for what you need."
)
# The rows the file is read in at a time.
BATCH = 1000
# What a spreadsheet starts a formula with (OWASP's "CSV injection").
FORMULA = ("=", "+", "-", "@", "\t", "\r")
COLUMNS = (
    "occurred_at",
    "action",
    "user_id",
    "user_name",
    "user_email",
    "ip",
    "project_id",
    "project_name",
    "target_type",
    "target_id",
    "detail",
)

# One of AUDIT_ACTIONS, so that an action misspelt is refused rather than matching nothing.
AuditAction = Literal[AUDIT_ACTIONS]  # type: ignore[valid-type]


@dataclass(frozen=True)
class AuditFilter:
    """Which of the organisation's rows to read: of these actions (any, when none), by this
    member, about this project, recorded from `since` until before `until`."""

    actions: tuple[str, ...] = ()
    user_id: int | None = None
    project_id: int | None = None
    since: dt.datetime | None = None
    until: dt.datetime | None = None

    def given(self) -> dict[str, Any]:
        """The filters asked for, as the export's row records them."""
        return {
            name: list(value) if isinstance(value, tuple) else _iso(value)
            for name, value in asdict(self).items()
            if value not in (None, ())
        }


def audit_filter(
    action: Annotated[list[AuditAction] | None, Query()] = None,
    user_id: int | None = None,
    project_id: int | None = None,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> AuditFilter:
    """The filters of a request, a time without its zone being UTC's."""
    return AuditFilter(tuple(action or ()), user_id, project_id, _utc(since), _utc(until))


FilterDep = Annotated[AuditFilter, Depends(audit_filter)]


@router.get("/audit", response_model=AuditPage)
def list_audit(
    account: AccountDep,
    session: SessionDep,
    filters: FilterDep,
    before: int | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    """A page of the organisation's audit log, older than the row `before` when it is given."""
    _refuse_unless_admin(account)
    org_id = account.organization.id
    query = _rows(org_id, filters).limit(limit + 1)
    if before is not None:
        mark = session.execute(
            select(AuditEvent.occurred_at, AuditEvent.id).where(
                AuditEvent.id == before, AuditEvent.org_id == org_id
            )
        ).first()
        # A row pruned since the page before has nothing older to show.
        if mark is None:
            return AuditPage(events=[], next_before=None)
        query = _after(query, mark.occurred_at, mark.id)
    rows = session.execute(query).all()
    page = rows[:limit]
    return AuditPage(
        events=[_event_out(*row) for row in page],
        next_before=page[-1][0].id if len(rows) > limit else None,
    )


@router.get("/audit/export")
def export_audit(account: AccountDep, request: Request, session: SessionDep, filters: FilterDep):
    """The rows the filters pick, as a CSV file to save, the newest first; the download is
    recorded first, with the filters it asked for."""
    _refuse_unless_admin(account)
    organization = account.organization
    audit(session, account, request, "audit_exported", detail=filters.given())
    session.commit()
    filename = f"emudoc-audit-{organization.slug}-{dt.datetime.now(dt.UTC):%Y-%m-%d}.csv"
    return StreamingResponse(
        _csv(organization.id, filters),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition(filename)},
    )


def _rows(org_id: int, filters: AuditFilter) -> Select:
    """The organisation's rows the filters pick, the newest first, with the name and address of
    the member as they are now and the name of the project."""
    query = (
        select(AuditEvent, User.name, User.email, Project.name)
        .outerjoin(User, User.id == AuditEvent.user_id)
        .outerjoin(
            Project, and_(Project.id == AuditEvent.project_id, Project.org_id == AuditEvent.org_id)
        )
        .where(AuditEvent.org_id == org_id)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
    )
    if filters.actions:
        query = query.where(AuditEvent.action.in_(filters.actions))
    if filters.user_id is not None:
        query = query.where(AuditEvent.user_id == filters.user_id)
    if filters.project_id is not None:
        query = query.where(AuditEvent.project_id == filters.project_id)
    if filters.since is not None:
        query = query.where(AuditEvent.occurred_at >= filters.since)
    if filters.until is not None:
        query = query.where(AuditEvent.occurred_at < filters.until)
    return query


def _after(query: Select, occurred_at: dt.datetime, event_id: int) -> Select:
    """The rows of `query` that come after the row recorded at `occurred_at` with this id."""
    return query.where(
        tuple_(AuditEvent.occurred_at, AuditEvent.id) < tuple_(occurred_at, event_id)
    )


def _csv(org_id: int, filters: AuditFilter) -> Iterator[str]:
    """The file, a batch of rows at a time, each read in a session of its own."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    yield "﻿" + _taken(buffer)
    query = _rows(org_id, filters).limit(BATCH)
    while True:
        with SessionLocal() as session:
            rows = _batch(session, query)
        for event in rows:
            writer.writerow([_cell(event.get(column)) for column in COLUMNS])
        yield _taken(buffer)
        if len(rows) < BATCH:
            return
        last = rows[-1]
        query = _after(_rows(org_id, filters).limit(BATCH), last["occurred_at"], last["id"])


def _batch(session: Session, query: Select) -> list[dict[str, Any]]:
    """A batch of rows as the file writes them, read into plain values before the session
    closes."""
    return [_event_out(*row).model_dump() for row in session.execute(query).all()]


def _cell(value: Any) -> Any:
    """A value as the file holds it: a time in UTC, the detail as JSON, and text a spreadsheet
    would run as a formula quoted."""
    if isinstance(value, dt.datetime):
        return _iso(value)
    if isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, str) and value.startswith(FORMULA):
        return f"'{value}"
    return value


def _taken(buffer: io.StringIO) -> str:
    """What the writer wrote since last asked."""
    written = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate()
    return written


def _event_out(
    event: AuditEvent, name: str | None, email: str | None, project: str | None
) -> AuditEventOut:
    return AuditEventOut(
        id=event.id,
        occurred_at=event.occurred_at,
        action=event.action,
        user_id=event.user_id,
        user_name=name,
        user_email=email,
        ip=event.ip,
        project_id=event.project_id,
        project_name=project,
        target_type=event.target_type,
        target_id=event.target_id,
        detail=event.detail,
    )


def _refuse_unless_admin(account: Account) -> None:
    if not account.is_at_least("admin"):
        raise HTTPException(403, ADMINS_ONLY)


def _utc(moment: dt.datetime | None) -> dt.datetime | None:
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=dt.UTC)


def _iso(moment: Any) -> Any:
    """A time as ISO 8601 in UTC, "2026-10-21T09:30:00Z"; anything else as it is."""
    if not isinstance(moment, dt.datetime):
        return moment
    return moment.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
