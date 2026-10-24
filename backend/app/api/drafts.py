"""Drafts (app/drafts.py): a member asks the agent for a draft of a kind about some of a project's
documents, which the worker writes, then edits it into new versions or has it written again; the
project's members read its drafts, newest first, and each one's versions.

Every route is behind the flag `drafts` (app/flags.py). Whoever may change the project asks for a
draft, saves a version and deletes one, each a row of the organisation's audit log naming the
draft by its kind and id alone, never what it says; anyone who sees the project reads them. A
writing is a run of the agent, so asking for one is held to what holds a question: the agent's
runs in flight, the month's questions, of which it counts one, and the day's spending.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import Account
from app.api.check_runs import count_question, refuse_agent_run
from app.api.deps import (
    AccountDep,
    DeletableDraftDep,
    DraftDep,
    EditableDraftDep,
    EditableProjectDep,
    ProjectDep,
    SessionDep,
    flagged,
    limited,
)
from app.audit import audit
from app.check_runs import gone_documents
from app.drafts import WRITING, latest_number, queue_draft, save_version, start_draft
from app.flags import DRAFTS
from app.models import Document, Draft, DraftVersion, User
from app.ratelimit import QUESTIONS
from app.schemas import (
    DraftDocumentOut,
    DraftIn,
    DraftOut,
    DraftRewriteIn,
    DraftsOut,
    DraftSummaryOut,
    DraftVersionIn,
    DraftVersionOut,
    DraftVersionSummaryOut,
)

router = APIRouter(tags=["drafts"], dependencies=[Depends(flagged(DRAFTS))])

WRITING_NOW = (
    "The model is writing this draft now. Wait for its version, then edit that one or ask again."
)
SAVED_MEANWHILE = (
    "A newer version of this draft was saved while you were editing it. Your text is still in "
    "the editor: save it again to keep it as the newest version, or cancel to read the other."
)
NO_SUCH_VERSION = "This draft has no such version."

# Drafts a page of the project's list.
DRAFTS_SHOWN = 20
MAX_DRAFTS_SHOWN = 100


@router.post(
    "/projects/{project_id}/drafts",
    response_model=DraftOut,
    status_code=201,
    dependencies=[Depends(limited(QUESTIONS))],
)
def ask_draft(
    project: EditableProjectDep,
    body: DraftIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Have the agent write a draft of the kind about the documents named, or about what it finds
    when none are; the worker writes it, which the page follows."""
    asked = list(dict.fromkeys(body.document_ids))
    found = {
        document.id: document
        for document in session.scalars(
            select(Document).where(
                Document.id.in_(asked),
                Document.project_id == project.id,
                Document.deleted_at.is_(None),
            )
        )
    }
    if len(found) < len(asked):
        raise HTTPException(404, "Document not found")
    refuse_agent_run(session, account, f"A draft in project {project.id}")
    draft = start_draft(
        session,
        project,
        account.user.id,
        body.kind,
        body.instructions.strip(),
        [found[document_id] for document_id in asked],
    )
    count_question(session, account, project.id, request)
    _audit(session, account, request, "draft_created", draft, documents=len(asked))
    session.commit()
    return _out(session, draft)


@router.get("/projects/{project_id}/drafts", response_model=DraftsOut)
def list_drafts(
    project: ProjectDep,
    session: SessionDep,
    before: int | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_DRAFTS_SHOWN)] = DRAFTS_SHOWN,
):
    """The project's drafts, newest first, a page at a time before the draft `before` names."""
    query = (
        select(Draft, User.name, _versions_of(Draft.id))
        .outerjoin(User, User.id == Draft.created_by)
        .where(Draft.project_id == project.id)
    )
    if before is not None:
        query = query.where(Draft.id < before)
    rows = session.execute(query.order_by(Draft.id.desc()).limit(limit + 1)).all()
    shown = rows[:limit]
    return DraftsOut(
        drafts=[_summary(draft, name, versions) for draft, name, versions in shown],
        next_before=shown[-1][0].id if len(rows) > limit else None,
    )


@router.get("/drafts/{draft_id}", response_model=DraftOut)
def get_draft(draft: DraftDep, session: SessionDep):
    """The draft whole: what was asked of it, its versions, and the newest one's text."""
    return _out(session, draft)


@router.get("/drafts/{draft_id}/versions/{number}", response_model=DraftVersionOut)
def get_version(draft: DraftDep, number: Annotated[int, Path(ge=1)], session: SessionDep):
    """One of the draft's versions, an older one to read or to start from."""
    row = session.execute(
        select(DraftVersion, User.name)
        .outerjoin(User, User.id == DraftVersion.user_id)
        .where(DraftVersion.draft_id == draft.id, DraftVersion.number == number)
    ).first()
    if row is None:
        raise HTTPException(404, NO_SUCH_VERSION)
    return _version_out(*row)


@router.post("/drafts/{draft_id}/versions", response_model=DraftOut, status_code=201)
def save(
    draft: EditableDraftDep,
    body: DraftVersionIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Keep the member's text as the draft's newest version, provided no other was kept since
    the one they edited, and the model is not writing one."""
    _hold(session, draft)
    if draft.status in WRITING:
        raise HTTPException(409, WRITING_NOW)
    if body.base != latest_number(session, draft.id):
        raise HTTPException(409, SAVED_MEANWHILE)
    version = save_version(session, draft, body.text, account.user.id)
    _audit(session, account, request, "draft_changed", draft, version=version.number)
    session.commit()
    return _out(session, draft)


@router.post(
    "/drafts/{draft_id}/rewrite",
    response_model=DraftOut,
    dependencies=[Depends(limited(QUESTIONS))],
)
def rewrite(
    draft: EditableDraftDep,
    body: DraftRewriteIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Have the agent write the draft again as a new version, from the documents as they read now
    and what the member asks of it now; the worker writes it, which the page follows."""
    _hold(session, draft)
    if draft.status in WRITING:
        raise HTTPException(409, WRITING_NOW)
    refuse_agent_run(session, account, f"Draft {draft.id}")
    if body.instructions is not None:
        draft.instructions = body.instructions.strip()
    queue_draft(session, draft, account.user.id)
    count_question(session, account, draft.project_id, request)
    _audit(session, account, request, "draft_changed", draft, rewritten=True)
    session.commit()
    return _out(session, draft)


@router.delete("/drafts/{draft_id}", status_code=204)
def delete_draft(
    draft: DeletableDraftDep, session: SessionDep, account: AccountDep, request: Request
):
    """Delete the draft for good, with every version of it."""
    _audit(session, account, request, "draft_deleted", draft)
    session.delete(draft)
    session.commit()


def _hold(session: Session, draft: Draft) -> None:
    """Lock the draft's row, which orders a save or a writing with another at once and with the
    worker settling one, and read it again."""
    session.execute(select(Draft.id).where(Draft.id == draft.id).with_for_update())
    session.refresh(draft)


def _audit(
    session: Session, account: Account, request: Request, action: str, draft: Draft, **detail
) -> None:
    """The audit row of a draft, named by its kind and id alone: what it says, its name among it,
    is what documents say."""
    audit(
        session,
        account,
        request,
        action,
        project_id=draft.project_id,
        target=("draft", draft.id),
        detail={"kind": draft.kind, **detail},
    )


def _versions_of(draft_id: Any) -> Any:
    """How many versions the draft has, as a column of the list's query."""
    return (
        select(func.count())
        .where(DraftVersion.draft_id == draft_id)
        .correlate(Draft)
        .scalar_subquery()
    )


def _summary(draft: Draft, created_by: str | None, versions: int) -> DraftSummaryOut:
    return DraftSummaryOut(
        id=draft.id,
        project_id=draft.project_id,
        kind=draft.kind,
        title=draft.title,
        status=draft.status,
        created_by=created_by,
        versions=versions,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


def _version_out(version: DraftVersion, author: str | None) -> DraftVersionOut:
    return DraftVersionOut(
        number=version.number,
        by_model=version.by_model,
        author=author,
        created_at=version.created_at,
        text=version.text,
        sources=version.sources,
    )


def _out(session: Session, draft: Draft) -> DraftOut:
    session.refresh(draft)
    # The versions' texts stay in the database but the newest one's.
    history = [
        DraftVersionSummaryOut(number=number, by_model=by_model, author=author, created_at=at)
        for number, by_model, at, author in session.execute(
            select(DraftVersion.number, DraftVersion.by_model, DraftVersion.created_at, User.name)
            .outerjoin(User, User.id == DraftVersion.user_id)
            .where(DraftVersion.draft_id == draft.id)
            .order_by(DraftVersion.number.desc())
        )
    ]
    latest = (
        session.execute(
            select(DraftVersion, User.name)
            .outerjoin(User, User.id == DraftVersion.user_id)
            .where(DraftVersion.draft_id == draft.id, DraftVersion.number == history[0].number)
        ).one()
        if history
        else None
    )
    created_by = session.scalar(select(User.name).where(User.id == draft.created_by))
    gone = set(
        gone_documents(session, draft.project_id, [named["id"] for named in draft.documents])
    )
    return DraftOut(
        **_summary(draft, created_by, len(history)).model_dump(),
        instructions=draft.instructions,
        documents=[
            DraftDocumentOut(id=named["id"], title=named["title"], gone=named["id"] in gone)
            for named in draft.documents
        ],
        run_id=draft.run_id,
        error=draft.error,
        notice=draft.notice,
        usage=draft.usage,
        history=history,
        latest=_version_out(*latest) if latest else None,
    )
