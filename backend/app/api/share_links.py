"""Share links' routes (app/share_links.py): the members', who make a document's or an answer's
links, list them and revoke them, and the visitor's, who opens one and reads what it shows.

The members' are behind the flag, and take the document or the chat as any route of it does, so
another organisation's, a private project's the member is not chosen for, or another member's
private chat, is not found; making one takes the role that may change the project and a plan
that includes links, and its owners and admins list every link of the organisation to revoke
any. The visitor's take no account and no flag of their own: the link is checked as its maker's
at each request (`shown`), and a document's page or file needs the cookie of a view the opening
made.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from app.access import visible_to
from app.accounts import Account
from app.api.auth import app_url
from app.api.chats import answer_in
from app.api.deps import (
    AccountDep,
    ChatDep,
    DocumentDep,
    EditableDocumentDep,
    ServicesDep,
    SessionDep,
    flagged,
    get_editor,
    refuse_past,
)
from app.api.documents import original_response, page_response
from app.audit import audit, audit_event
from app.auth import now, verify_password
from app.entitlements import PlanRefused, check_share_links
from app.flags import SHARE_LINKS
from app.models import Block, ChatMessage, Document, Project, ShareLink, User
from app.ratelimit import SHARED_LINKS, SHARED_PASSWORDS, by_address, by_link, held, tally
from app.schemas import (
    DocumentContent,
    OrganizationShareLinkOut,
    SharedAnswerOut,
    SharedDocumentOut,
    SharedOpenIn,
    SharedOut,
    SharedPassageOut,
    ShareLinkIn,
    ShareLinkMadeOut,
    ShareLinkOut,
)
from app.share_links import (
    ANSWER_NOT_READY,
    LINK_GONE,
    LINKS_OFF,
    MAX_LINKS,
    NOT_READY,
    NOT_YOURS,
    PASSWORD_NEEDED,
    TOO_MANY_LINKS,
    VIEW_COOKIE,
    VIEW_OVER,
    WRONG_PASSWORD,
    Shared,
    answered,
    cited_passages,
    counted,
    find_link,
    issue,
    link_address,
    open_view,
    question_of,
    ready,
    running,
    set_view_cookie,
    shown,
    viewed,
)

router = APIRouter(tags=["share links"], dependencies=[Depends(flagged(SHARE_LINKS))])
public = APIRouter(tags=["share links"])

ADMINS_ONLY = "Only the organisation's owners and admins can see all of its share links."
# How many of the organisation's links its owners and admins are shown, the newest first.
LISTED = 200
PAUSED = (
    "This link was given the wrong password too often in the last hour, so it opens with none "
    "for now. Try again in {minutes} minutes."
)


@router.get("/documents/{document_id}/share-links", response_model=list[ShareLinkOut])
def list_links(document: DocumentDep, session: SessionDep):
    """The document's links that have not expired, the newest first."""
    return _links(session, ShareLink.document_id == document.id)


@router.post(
    "/documents/{document_id}/share-links", response_model=ShareLinkMadeOut, status_code=201
)
def make_link(
    document: EditableDocumentDep,
    body: ShareLinkIn,
    account: AccountDep,
    request: Request,
    session: SessionDep,
):
    """A link that shows the document to whoever holds it for `days`, with a password or not;
    its address is in the answer alone."""
    _refuse_links(account)
    if not ready(document):
        raise HTTPException(409, NOT_READY)
    # Held, so that two links made at once count each other.
    session.execute(select(Document.id).where(Document.id == document.id).with_for_update())
    if running(session, document_id=document.id) >= MAX_LINKS:
        raise HTTPException(409, TOO_MANY_LINKS)
    link, token = issue(
        session, account, document.project_id, body.days, body.password, document_id=document.id
    )
    audit(
        session,
        account,
        request,
        "share_link_created",
        project_id=document.project_id,
        target=("document", document.id),
        detail={
            "filename": document.filename,
            "link": link.id,
            "days": body.days,
            "password": body.password is not None,
        },
    )
    session.commit()
    return _made(link, account, request, token)


@router.get("/chats/{chat_id}/messages/{message_id}/share-links", response_model=list[ShareLinkOut])
def list_answer_links(chat: ChatDep, message_id: int, session: SessionDep):
    """The answer's links that have not expired, the newest first."""
    message = answer_in(chat, message_id, session)
    return _links(session, ShareLink.message_id == message.id)


@router.post(
    "/chats/{chat_id}/messages/{message_id}/share-links",
    response_model=ShareLinkMadeOut,
    status_code=201,
)
def make_answer_link(
    chat: ChatDep,
    message_id: int,
    body: ShareLinkIn,
    account: AccountDep,
    request: Request,
    session: SessionDep,
):
    """A link that shows the answer, its question and the passages it cites to whoever holds it
    for `days`, with a password or not: whoever sees the chat and may change the project makes
    one, as of a document."""
    message = answer_in(chat, message_id, session)
    get_editor(account, session)
    _refuse_links(account)
    if not answered(message):
        raise HTTPException(409, ANSWER_NOT_READY)
    session.execute(select(ChatMessage.id).where(ChatMessage.id == message.id).with_for_update())
    if running(session, message_id=message.id) >= MAX_LINKS:
        raise HTTPException(409, TOO_MANY_LINKS)
    link, token = issue(
        session, account, chat.project_id, body.days, body.password, message_id=message.id
    )
    audit(
        session,
        account,
        request,
        "share_link_created",
        project_id=chat.project_id,
        target=("chat", chat.id),
        detail={
            "answer": message.id,
            "link": link.id,
            "days": body.days,
            "password": body.password is not None,
        },
    )
    session.commit()
    return _made(link, account, request, token)


def _refuse_links(account: Account) -> None:
    """Refuse a link the organisation's plan or its owners and admins do not allow."""
    if refusal := check_share_links(account.organization):
        raise PlanRefused(refusal)
    if not account.organization.share_links:
        raise HTTPException(403, LINKS_OFF)


@router.delete("/share-links/{link_id}", status_code=204)
def revoke_link(link_id: int, account: AccountDep, request: Request, session: SessionDep):
    """End the link at once: its maker, or an owner or admin, may; its document or chat may be in
    the trash."""
    found = session.execute(
        select(ShareLink, Document.filename, ChatMessage.chat_id)
        .outerjoin(Document, Document.id == ShareLink.document_id)
        .outerjoin(ChatMessage, ChatMessage.id == ShareLink.message_id)
        .join(Project, Project.id == ShareLink.project_id)
        .where(ShareLink.id == link_id, visible_to(account))
    ).first()
    if found is None:
        raise HTTPException(404, "Share link not found")
    link, filename, chat_id = found
    if link.user_id != account.user.id and not account.is_at_least("admin"):
        raise HTTPException(403, NOT_YOURS)
    audit(
        session,
        account,
        request,
        "share_link_revoked",
        project_id=link.project_id,
        target=("document", link.document_id) if filename is not None else ("chat", chat_id),
        detail=(
            {"filename": filename, "link": link.id}
            if filename is not None
            else {"answer": link.message_id, "link": link.id}
        ),
    )
    session.delete(link)
    session.commit()


@router.get("/organization/share-links", response_model=list[OrganizationShareLinkOut])
def organization_links(account: AccountDep, session: SessionDep):
    """Every link of the organisation that has not expired, the newest LISTED first, for its
    owners and admins: a document's by its name, an answer's by none."""
    if not account.is_at_least("admin"):
        raise HTTPException(403, ADMINS_ONLY)
    rows = session.execute(
        select(ShareLink, User.name, Document.filename, Project.name)
        .join(User, User.id == ShareLink.user_id)
        .outerjoin(Document, Document.id == ShareLink.document_id)
        .join(Project, Project.id == ShareLink.project_id)
        .where(ShareLink.org_id == account.organization.id, ShareLink.expires_at > now())
        .order_by(ShareLink.id.desc())
        .limit(LISTED)
    ).all()
    return [
        OrganizationShareLinkOut(
            **_link_out(link, made_by).model_dump(),
            filename=filename,
            project_id=link.project_id,
            project_name=project,
        )
        for link, made_by, filename, project in rows
    ]


def _links(session: Session, shows: ColumnElement[bool]) -> list[ShareLinkOut]:
    rows = session.execute(
        select(ShareLink, User.name)
        .join(User, User.id == ShareLink.user_id)
        .where(shows, ShareLink.expires_at > now())
        .order_by(ShareLink.id.desc())
    ).all()
    return [_link_out(link, name) for link, name in rows]


def _made(link: ShareLink, account: Account, request: Request, token: str) -> ShareLinkMadeOut:
    return ShareLinkMadeOut(
        **_link_out(link, account.user.name).model_dump(),
        url=link_address(app_url(request), token),
    )


def _link_out(link: ShareLink, made_by: str) -> ShareLinkOut:
    return ShareLinkOut(
        id=link.id,
        kind="document" if link.document_id is not None else "answer",
        document_id=link.document_id,
        message_id=link.message_id,
        user_id=link.user_id,
        made_by=made_by,
        created_at=link.created_at,
        expires_at=link.expires_at,
        password=link.password_hash is not None,
        views=link.views,
        last_viewed_at=link.last_viewed_at,
    )


@public.post("/shared", response_model=SharedOut)
def open_link(body: SharedOpenIn, request: Request, response: Response, session: SessionDep):
    """Open a link: what it shows, and for a document a view's cookie for its pages and file. A
    link with a password is refused with 401 until it is given, and paused for an hour after
    RATE_SHARED_PASSWORDS wrong ones."""
    address = by_address(request)
    refuse_past(session, (SHARED_LINKS, address))
    link = find_link(session, body.token)
    shared = shown(session, link) if link is not None else None
    if shared is None:
        raise HTTPException(404, LINK_GONE)
    if link.password_hash is not None:
        if not body.password:
            raise HTTPException(401, PASSWORD_NEEDED)
        for key in (by_link(link.id), address):
            if paused := held(session, SHARED_PASSWORDS, key):
                minutes = max(1, -(-paused[1] // 60))
                raise HTTPException(
                    429, PAUSED.format(minutes=minutes), headers={"Retry-After": str(paused[1])}
                )
        if not verify_password(body.password, link.password_hash):
            tally(session, SHARED_PASSWORDS, by_link(link.id))
            tally(session, SHARED_PASSWORDS, address)
            session.commit()
            raise HTTPException(401, WRONG_PASSWORD)
    counted(link)
    _audit(session, shared, request, "share_link_viewed")
    if shared.message is not None:
        out = _answer_out(session, shared)
        session.commit()
        return out
    token, ends = open_view(session, link)
    document = shared.document
    blocks = session.scalars(
        select(Block).where(Block.document_id == document.id).order_by(Block.ordinal)
    ).all()
    out = SharedDocumentOut(
        link_id=link.id,
        filename=document.filename,
        title=document.title,
        doc_type=document.doc_type,
        doc_date=document.doc_date,
        mime_type=document.mime_type,
        page_count=document.page_count,
        shared_by=shared.maker.name,
        organization=shared.organization.name,
        expires_at=link.expires_at,
        view_ends_at=ends,
        content=DocumentContent(
            blocks=blocks,
            page_sizes=document.page_sizes or [],
            view_page_sizes=document.view_page_sizes if document.view_state == "ready" else [],
        ),
    )
    session.commit()
    set_view_cookie(response, request, link, token, ends)
    return out


def _answer_out(session: Session, shared: Shared) -> SharedAnswerOut:
    """The answer the link shows, with its question and the passages it cites."""
    message = shared.message
    question = question_of(session, message)
    return SharedAnswerOut(
        link_id=shared.link.id,
        question=question.content if question is not None else "",
        answer=message.content,
        answered_at=message.created_at,
        passages=[
            SharedPassageOut(
                id=passage.id,
                document_title=document.display_title,
                context=passage.context,
                text=passage.text,
                page_start=passage.page_start,
                page_end=passage.page_end,
            )
            for passage, document in cited_passages(session, shared.chat, message)
        ],
        shared_by=shared.maker.name,
        organization=shared.organization.name,
        expires_at=shared.link.expires_at,
    )


def get_viewing(link_id: int, request: Request, session: SessionDep) -> Shared:
    """What a document's link shows to the browser whose view's cookie is the link's, provided
    the link still works; 401 for a view that ended or was never made, 404 for a link that
    stopped."""
    link = viewed(session, link_id, request.cookies.get(VIEW_COOKIE))
    if link is None:
        raise HTTPException(401, VIEW_OVER)
    shared = shown(session, link)
    if shared is None or shared.document is None:
        raise HTTPException(404, LINK_GONE)
    return shared


ViewingDep = Annotated[Shared, Depends(get_viewing)]


@public.get("/shared/{link_id}/pages/{page}.png")
async def shared_page(
    page: int, shared: ViewingDep, request: Request, services: ServicesDep, session: SessionDep
) -> Response:
    """A page of the document the link shows, drawn as the members' are, a new one charged to
    the link's maker."""
    return await page_response(
        request,
        session,
        services.storage,
        shared.document,
        page,
        shared.organization.id,
        shared.maker.id,
    )


@public.get("/shared/{link_id}/file")
def shared_file(shared: ViewingDep, request: Request, services: ServicesDep, session: SessionDep):
    """The original of the document the link shows, as `download_file` sends it, each time a
    download in the organisation's audit log."""
    response = original_response(services.storage, shared.document)
    _audit(session, shared, request, "share_link_downloaded")
    session.commit()
    return response


def _audit(session: Session, shared: Shared, request: Request, action: str) -> None:
    """A visitor's act through the link, by nobody the app knows; the caller commits."""
    if shared.document is not None:
        project_id, target = shared.document.project_id, ("document", shared.document.id)
        detail = {"filename": shared.document.filename, "link": shared.link.id}
    else:
        project_id, target = shared.chat.project_id, ("chat", shared.chat.id)
        detail = {"answer": shared.message.id, "link": shared.link.id}
    audit_event(
        session,
        action,
        org_id=shared.organization.id,
        request=request,
        project_id=project_id,
        target=target,
        detail=detail,
    )
