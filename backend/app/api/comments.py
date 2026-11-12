"""A document's comments, which everyone who sees its project reads and writes (app/comments.py),
and the members a comment in a project may mention."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session

from app.accounts import Account
from app.api.auth import app_url
from app.api.deps import (
    AccountDep,
    CommentDep,
    DocumentDep,
    ProjectDep,
    SessionDep,
    ThreadDep,
)
from app.audit import audit
from app.comments import (
    NOT_YOURS,
    NOT_YOURS_TO_DELETE,
    CommentRefused,
    change_comment,
    delete_comment,
    mentionable,
    mentioned,
    reply,
    resolve,
    start_thread,
    tell_comment,
    thread_out,
    threads_of,
)
from app.models import CommentThread, Document, Project
from app.schemas import CommentIn, MentionableOut, ThreadChangeIn, ThreadIn, ThreadOut

router = APIRouter(tags=["comments"])

# What a member may not do to another's comment is a matter of who they are, not of what they
# sent.
FORBIDDEN = (NOT_YOURS, NOT_YOURS_TO_DELETE)


@router.get("/documents/{document_id}/threads", response_model=list[ThreadOut])
def list_threads(document: DocumentDep, session: SessionDep):
    """The document's threads, oldest first, each with its comments, resolved ones too."""
    return threads_of(session, document.id)


@router.post("/documents/{document_id}/threads", response_model=ThreadOut, status_code=201)
def create_thread(
    document: DocumentDep,
    body: ThreadIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Start a thread on the document, about a passage of it or the document as a whole. The
    members its comment mentions are told."""
    project = session.get(Project, document.project_id)
    try:
        thread, comment = start_thread(
            session,
            project,
            document,
            account.user.id,
            body.text,
            passage_id=body.passage_id,
            block=body.block,
            quote=body.quote,
        )
    except CommentRefused as refused:
        raise _refusal(refused) from None
    _audit(session, account, request, "comment_added", document, thread.id, reply=False)
    tell_comment(
        session,
        project,
        document,
        comment,
        account.user,
        mentioned(comment.text),
        False,
        app_url(request),
    )
    session.commit()
    return thread_out(session, thread)


@router.post("/threads/{thread_id}/comments", response_model=ThreadOut, status_code=201)
def create_comment(
    thread: ThreadDep, body: CommentIn, session: SessionDep, account: AccountDep, request: Request
):
    """Reply in the thread, resolved or not. The members the reply mentions are told, and the
    thread's earlier writers."""
    document = session.get(Document, thread.document_id)
    project = session.get(Project, document.project_id)
    try:
        comment = reply(session, project, thread, account.user.id, body.text)
    except CommentRefused as refused:
        raise _refusal(refused) from None
    _audit(session, account, request, "comment_added", document, thread.id, reply=True)
    tell_comment(
        session,
        project,
        document,
        comment,
        account.user,
        mentioned(comment.text),
        True,
        app_url(request),
    )
    session.commit()
    return thread_out(session, thread)


@router.patch("/threads/{thread_id}", response_model=ThreadOut)
def update_thread(
    thread: ThreadDep,
    body: ThreadChangeIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Resolve the thread, or open it again."""
    if resolve(thread, account.user.id, body.resolved):
        document = session.get(Document, thread.document_id)
        action = "thread_resolved" if body.resolved else "thread_reopened"
        _audit(session, account, request, action, document, thread.id)
        session.commit()
    return thread_out(session, thread)


@router.patch("/comments/{comment_id}", response_model=ThreadOut)
def update_comment(
    comment: CommentDep, body: CommentIn, session: SessionDep, account: AccountDep, request: Request
):
    """Change the words of the member's own comment. Whoever the change newly mentions is
    told."""
    thread = session.get(CommentThread, comment.thread_id)
    document = session.get(Document, thread.document_id)
    project = session.get(Project, document.project_id)
    try:
        added = change_comment(session, project, comment, account.user.id, body.text)
    except CommentRefused as refused:
        raise _refusal(refused) from None
    tell_comment(session, project, document, comment, account.user, added, False, app_url(request))
    session.commit()
    return thread_out(session, thread)


@router.delete("/comments/{comment_id}", status_code=204)
def remove_comment(comment: CommentDep, session: SessionDep, account: AccountDep, request: Request):
    """Delete the member's own comment, or as an owner or admin anyone's; a thread goes with its
    last comment."""
    thread = session.get(CommentThread, comment.thread_id)
    document = session.get(Document, thread.document_id)
    author_id = comment.author_id
    try:
        delete_comment(session, comment, account.user.id, account.is_at_least("admin"))
    except CommentRefused as refused:
        raise _refusal(refused) from None
    _audit(session, account, request, "comment_deleted", document, thread.id, author=author_id)
    session.commit()


@router.get("/projects/{project_id}/mentionable", response_model=list[MentionableOut])
def list_mentionable(project: ProjectDep, session: SessionDep):
    """The members a comment in the project may mention, by name: those who see it."""
    return [
        MentionableOut(id=user_id, name=name) for user_id, name in mentionable(session, project)
    ]


def _audit(
    session: Session,
    account: Account,
    request: Request,
    action: str,
    document: Document,
    thread_id: int,
    **detail,
) -> None:
    """The audit row of a comment or a thread, by the document's name and the thread's id: never
    the comment's words, which are a member's and may quote the document."""
    audit(
        session,
        account,
        request,
        action,
        project_id=document.project_id,
        target=("document", document.id),
        detail={"filename": document.filename, "thread": thread_id, **detail},
    )


def _refusal(refused: CommentRefused) -> HTTPException:
    sentence = str(refused)
    return HTTPException(403 if sentence in FORBIDDEN else 400, sentence)
