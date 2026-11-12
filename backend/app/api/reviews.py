"""Documents' reviews (app/reviews.py): a project's documents in review, by state, which the
library's reviews view lists, the members they may be assigned to, and the change of one
document's review, which the library's row and the document's tab make.

Anyone who sees the project reads where its documents' reviews stand; whoever may change it moves
them, each change a row of the organisation's audit log.
"""

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.auth import app_url
from app.api.deps import AccountDep, EditableDocumentDep, ProjectDep, SessionDep
from app.audit import audit
from app.models import Project
from app.reviews import ReviewRefused, change_review, read_reviews, reviewers, tell_assigned
from app.schemas import DocumentOut, ReviewerOut, ReviewIn, ReviewsOut, ReviewState

router = APIRouter(tags=["reviews"])

# How many documents a page of the reviews holds unless the request asks for another number.
PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


@router.get("/projects/{project_id}/reviews", response_model=ReviewsOut)
def list_reviews(
    project: ProjectDep,
    session: SessionDep,
    account: AccountDep,
    state: ReviewState = "to_review",
    mine: bool = False,
    after: str | None = None,
    limit: int = Query(PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """A page of the project's documents in review of `state`, with `mine` those assigned to the
    asker alone: an open review oldest first, an approved one newest first; and how many there
    are of each state."""
    try:
        found = read_reviews(
            session,
            project.id,
            state,
            assignee_id=account.user.id if mine else None,
            after=after,
            limit=limit,
        )
    except ValueError as refused:
        raise HTTPException(422, str(refused)) from None
    return ReviewsOut(documents=found.documents, counts=found.counts, next_after=found.next_after)


@router.get("/projects/{project_id}/reviewers", response_model=list[ReviewerOut])
def list_reviewers(project: ProjectDep, session: SessionDep):
    """The members the project's documents may be assigned to, by name."""
    return [ReviewerOut(id=user_id, name=name) for user_id, name in reviewers(session, project)]


@router.put("/documents/{document_id}/review", response_model=DocumentOut)
def update_review(
    document: EditableDocumentDep,
    body: ReviewIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Move the document's review, assign it, or both; a field left out stays as it is. The
    member assigned is told, unless they assigned it themselves."""
    project = session.get(Project, document.project_id)
    try:
        change = change_review(
            session,
            project,
            document,
            body.model_fields_set,
            body.state,
            body.assignee_id,
            account.user.id,
        )
    except ReviewRefused as refused:
        raise HTTPException(400, str(refused)) from None
    target = ("document", document.id)
    if change.moved:
        audit(
            session,
            account,
            request,
            "document_review_changed",
            project_id=project.id,
            target=target,
            detail={
                "filename": document.filename,
                "from": change.state_from,
                "to": change.state_to,
            },
        )
    if change.assigned:
        audit(
            session,
            account,
            request,
            "document_assigned",
            project_id=project.id,
            target=target,
            detail={
                "filename": document.filename,
                "from": change.assignee_from,
                "to": change.assignee_to,
            },
        )
        if change.assignee_to not in (None, account.user.id):
            tell_assigned(
                session, project, document, change.assignee_to, account.user.name, app_url(request)
            )
    session.commit()
    # The names read with it, the assignee's and who moved it, as they are now.
    session.refresh(document)
    return document
