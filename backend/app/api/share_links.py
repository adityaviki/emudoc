"""Share links' routes (app/share_links.py): the members', who make a document's links, list
them and revoke them, and the visitor's, who opens one and reads what it shows.

The members' are behind the flag, and take the document as any route of it does, so another
organisation's, or a private project's the member is not chosen for, is not found; making one
takes the role that may change the project and a plan that includes links, and its owners and
admins list every link of the organisation to revoke any. The visitor's take no account and no
flag of their own: the link is checked as its maker's at each request (`shown`), and a page or
the file needs the cookie of a view the opening made.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import visible_to
from app.api.auth import app_url
from app.api.deps import (
    AccountDep,
    DocumentDep,
    EditableDocumentDep,
    ServicesDep,
    SessionDep,
    flagged,
    refuse_past,
)
from app.api.documents import original_response, page_response
from app.audit import audit, audit_event
from app.auth import now, verify_password
from app.entitlements import PlanRefused, check_share_links
from app.flags import SHARE_LINKS
from app.models import Block, Document, Project, ShareLink, User
from app.ratelimit import SHARED_LINKS, SHARED_PASSWORDS, by_address, by_link, held, tally
from app.schemas import (
    DocumentContent,
    OrganizationShareLinkOut,
    SharedDocumentOut,
    SharedOpenIn,
    ShareLinkIn,
    ShareLinkMadeOut,
    ShareLinkOut,
)
from app.share_links import (
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
    find_link,
    issue,
    link_address,
    open_view,
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
    rows = session.execute(
        select(ShareLink, User.name)
        .join(User, User.id == ShareLink.user_id)
        .where(ShareLink.document_id == document.id, ShareLink.expires_at > now())
        .order_by(ShareLink.id.desc())
    ).all()
    return [_link_out(link, name) for link, name in rows]


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
    organization = account.organization
    if refusal := check_share_links(organization):
        raise PlanRefused(refusal)
    if not organization.share_links:
        raise HTTPException(403, LINKS_OFF)
    if not ready(document):
        raise HTTPException(409, NOT_READY)
    # Held, so that two links made at once count each other.
    session.execute(select(Document.id).where(Document.id == document.id).with_for_update())
    if running(session, document.id) >= MAX_LINKS:
        raise HTTPException(409, TOO_MANY_LINKS)
    link, token = issue(session, document, account, body.days, body.password)
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
    return ShareLinkMadeOut(
        **_link_out(link, account.user.name).model_dump(),
        url=link_address(app_url(request), token),
    )


@router.delete("/share-links/{link_id}", status_code=204)
def revoke_link(link_id: int, account: AccountDep, request: Request, session: SessionDep):
    """End the link at once: its maker, or an owner or admin, may; its document may be in the
    trash."""
    found = session.execute(
        select(ShareLink, Document.filename)
        .join(Document, Document.id == ShareLink.document_id)
        .join(Project, Project.id == ShareLink.project_id)
        .where(ShareLink.id == link_id, visible_to(account))
    ).first()
    if found is None:
        raise HTTPException(404, "Share link not found")
    link, filename = found
    if link.user_id != account.user.id and not account.is_at_least("admin"):
        raise HTTPException(403, NOT_YOURS)
    audit(
        session,
        account,
        request,
        "share_link_revoked",
        project_id=link.project_id,
        target=("document", link.document_id),
        detail={"filename": filename, "link": link.id},
    )
    session.delete(link)
    session.commit()


@router.get("/organization/share-links", response_model=list[OrganizationShareLinkOut])
def organization_links(account: AccountDep, session: SessionDep):
    """Every link of the organisation that has not expired, the newest LISTED first, for its
    owners and admins."""
    if not account.is_at_least("admin"):
        raise HTTPException(403, ADMINS_ONLY)
    rows = session.execute(
        select(ShareLink, User.name, Document.filename, Project.name)
        .join(User, User.id == ShareLink.user_id)
        .join(Document, Document.id == ShareLink.document_id)
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


def _link_out(link: ShareLink, made_by: str) -> ShareLinkOut:
    return ShareLinkOut(
        id=link.id,
        document_id=link.document_id,
        user_id=link.user_id,
        made_by=made_by,
        created_at=link.created_at,
        expires_at=link.expires_at,
        password=link.password_hash is not None,
        views=link.views,
        last_viewed_at=link.last_viewed_at,
    )


@public.post("/shared", response_model=SharedDocumentOut)
def open_link(body: SharedOpenIn, request: Request, response: Response, session: SessionDep):
    """Open a link: what it shows, and a view's cookie for its pages and file. A link with a
    password is refused with 401 until it is given, and paused for an hour after
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
    token, ends = open_view(session, link)
    document = shared.document
    _audit(session, shared, request, "share_link_viewed")
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
        content=DocumentContent(blocks=blocks, page_sizes=document.page_sizes or []),
    )
    session.commit()
    set_view_cookie(response, request, link, token, ends)
    return out


def get_viewing(link_id: int, request: Request, session: SessionDep) -> Shared:
    """What the link shows to the browser whose view's cookie is the link's, provided the link
    still works; 401 for a view that ended or was never made, 404 for a link that stopped."""
    link = viewed(session, link_id, request.cookies.get(VIEW_COOKIE))
    if link is None:
        raise HTTPException(401, VIEW_OVER)
    shared = shown(session, link)
    if shared is None:
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
    audit_event(
        session,
        action,
        org_id=shared.organization.id,
        request=request,
        project_id=shared.document.project_id,
        target=("document", shared.document.id),
        detail={"filename": shared.document.filename, "link": shared.link.id},
    )
