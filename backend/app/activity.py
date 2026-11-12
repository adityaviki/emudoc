"""A project's activity: what was done in it lately, which everyone who sees the project reads.

It is a view of the project's rows of the audit log (app/audit.py), of the actions that tell a
project's members what changed in their work (FEED_ACTIONS): documents uploaded, given a new
version, deleted and restored, folders deleted and restored, reviews moved and assigned, comments
added and threads resolved, drafts written, cross-checks and question sets run, and chats shared
with the project. The audit log's other rows, a member's sign-in, a download, a share link viewed or
a setting changed, stay the owners' and admins'. An item is made from a row by names alone, a chosen
few of its detail's for each action, never the detail as it is: a document's name as it was then,
the member who acted and the one assigned by their names as they are now, an automation's name as it
was. What an item leads to (a document, a thread, a run, a report, a draft, a chat) is offered while
it is there to open: a document out of the trash, a chat still shared. It is kept as long as the
audit log is, by the organisation's `audit_days`.
"""

from collections.abc import Iterable
from typing import get_args

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    Chat,
    CommentThread,
    Document,
    Draft,
    Project,
    QuestionReport,
    User,
    WorkflowRun,
)
from app.schemas import ActivityAction, ActivityOut, ActivityPage

# The audit log's actions the activity shows, in ActivityAction's order.
FEED_ACTIONS: tuple[str, ...] = get_args(ActivityAction)
# What each kind of target an item leads to is, and whether one of the project's is there to
# open: out of the trash, and a chat still shared.
_TARGETS = {
    "workflow_run": (WorkflowRun, ()),
    "question_report": (QuestionReport, ()),
    "draft": (Draft, ()),
    "chat": (Chat, (Chat.deleted_at.is_(None), Chat.visibility == "project")),
}


def read_activity(
    session: Session, project: Project, before: int | None, limit: int
) -> ActivityPage:
    """A page of the project's activity, newest first, older than the row `before` names."""
    query = (
        select(AuditEvent, User.name)
        .outerjoin(User, User.id == AuditEvent.user_id)
        .where(
            AuditEvent.org_id == project.org_id,
            AuditEvent.project_id == project.id,
            AuditEvent.action.in_(FEED_ACTIONS),
        )
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(limit + 1)
    )
    if before is not None:
        mark = session.execute(
            select(AuditEvent.occurred_at, AuditEvent.id).where(
                AuditEvent.id == before, AuditEvent.project_id == project.id
            )
        ).first()
        # A row pruned since the page before has nothing older to show.
        if mark is None:
            return ActivityPage(events=[], next_before=None)
        query = query.where(tuple_(AuditEvent.occurred_at, AuditEvent.id) < tuple(mark))
    rows = session.execute(query).all()
    page = rows[:limit]
    events = [event for event, _ in page]
    names = _names(session, {event.detail.get("to") for event in events if _assigned(event)})
    documents = _live_documents(
        session,
        project.id,
        {event.target_id for event in events if event.target_type == "document"},
    )
    threads = _live_threads(session, {_thread(event) for event in events} - {None})
    targets = _live_targets(session, project.id, events)
    return ActivityPage(
        events=[_item(event, actor, names, documents, threads, targets) for event, actor in page],
        next_before=page[-1][0].id if len(rows) > limit else None,
    )


def _item(
    event: AuditEvent,
    actor: str | None,
    names: dict[int, str],
    documents: set[int],
    threads: set[int],
    targets: set[tuple[str, int]],
) -> ActivityOut:
    detail = event.detail
    on_document = event.target_type == "document"
    folder = event.action.startswith("folder_")
    run = event.action == "cross_checks_run"
    asked = event.action == "question_set_run"
    target = (event.target_type, event.target_id)
    return ActivityOut(
        id=event.id,
        action=event.action,
        occurred_at=event.occurred_at,
        actor=actor,
        automation=detail.get("automation"),
        via="email" if detail.get("by_email") else detail.get("by_connector"),
        filename=detail.get("filename"),
        document_id=event.target_id if on_document and event.target_id in documents else None,
        folder=detail.get("name") if folder else None,
        documents=detail.get("documents") if folder else None,
        thread_id=_thread(event) if _thread(event) in threads else None,
        reply=bool(detail.get("reply")),
        review=detail.get("to") if event.action == "document_review_changed" else None,
        assignee=names.get(detail.get("to")) if _assigned(event) else None,
        names=detail.get("names", []) if run else [],
        name=detail.get("name") if asked else None,
        questions=detail.get("questions") if asked else None,
        kind=detail.get("kind") if event.action == "draft_created" else None,
        target_id=event.target_id if target in targets else None,
    )


def _assigned(event: AuditEvent) -> bool:
    return event.action == "document_assigned" and event.detail.get("to") is not None


def _thread(event: AuditEvent) -> int | None:
    return event.detail.get("thread") if event.action.startswith(("comment_", "thread_")) else None


def _names(session: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(session.execute(select(User.id, User.name).where(User.id.in_(ids))).all())


def _live_documents(session: Session, project_id: int, ids: set[int]) -> set[int]:
    """Those of the documents still in the project, out of the trash."""
    if not ids:
        return set()
    return set(
        session.scalars(
            select(Document.id).where(
                Document.id.in_(ids),
                Document.project_id == project_id,
                Document.deleted_at.is_(None),
            )
        )
    )


def _live_threads(session: Session, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    return set(session.scalars(select(CommentThread.id).where(CommentThread.id.in_(ids))))


def _live_targets(
    session: Session, project_id: int, events: Iterable[AuditEvent]
) -> set[tuple[str, int]]:
    """The runs, reports, drafts and chats the events lead to that are still there to open."""
    wanted: dict[str, set[int]] = {}
    for event in events:
        if event.target_type in _TARGETS:
            wanted.setdefault(event.target_type, set()).add(event.target_id)
    live = set()
    for kind, ids in wanted.items():
        model, held = _TARGETS[kind]
        live |= {
            (kind, found)
            for found in session.scalars(
                select(model.id).where(model.id.in_(ids), model.project_id == project_id, *held)
            )
        }
    return live
