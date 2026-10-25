"""Share links: a link that shows one document to whoever holds it, without an account.

A member who may change the project makes one from the document (app/api/share_links.py), on a
plan that includes them (`check_share_links`, app/entitlements.py), in an organisation whose
owners and admins allow them (`organizations.share_links`), while the feature is rolled out to it
(SHARE_LINKS, app/flags.py). It runs one of DAYS, DEFAULT_DAYS unless its maker chooses, never
longer, and may have a password, hashed as an account's is. The link carries a random token in
its fragment, which the browser never sends, so it lands in no server's or proxy's log and in no
Referer; only the token's SHA-256 is stored, as an API key's is, so a copy of the database opens
nothing, and the link is shown to its maker once.

A link works as its maker's (`shown`): only while they are a member who may change the project,
see the document and are not leaving Emudoc, so a member removed, made a viewer, or left out of a
project made private stops their links, and a link goes with its maker's membership by the
database's cascade, as an API key does; and only while the organisation's plan includes links, its
owners and admins allow them, the flag is on for it, and the document is out of the trash, indexed
and passed by the malware scan. Every check is made again at every request the link makes, so
any of those changes stops it at once, and the visitor is told the same sentence for each
(LINK_GONE): who holds a link learns nothing of the organisation from why it stopped. Revoking a
link deletes it, as does the housekeeper once it expired (`prune_share_links`), and a database
restore ends every link (app/restore.py), since one revoked after the moment restored to would
otherwise work again.

Opening a link (`POST /api/shared`, the page `/shared`) checks the token and the password and
makes a view (`ShareView`): a random token of its own in a cookie the browser sends to that
link's pages and file alone (VIEW_COOKIE, its path the link's), for VIEW_FOR or until the link
ends, so the password is checked once rather than at every page and the page's images load as
any image does. Each opening is a `share_link_viewed` row in the organisation's audit log, and
each original sent a `share_link_downloaded`, both with nobody as the member and the visitor's
address; the wrong passwords are counted for the link and for the address (SHARED_PASSWORDS,
app/ratelimit.py), and the openings for the address (SHARED_LINKS).
"""

import datetime as dt
from dataclasses import dataclass

from fastapi import Request, Response
from sqlalchemy import Connection, delete, func, select
from sqlalchemy.orm import Session

from app.access import visible_to
from app.accounts import Account
from app.auth import cookie_secure, hash_password, new_token, now, token_hash
from app.entitlements import check_share_links
from app.flags import SHARE_LINKS, flag_on
from app.models import Document, Membership, Organization, Project, ShareLink, ShareView, User
from app.scanning import scan_passed

# How many days a link may run, of which its maker chooses one; never longer, so that a link
# forgotten does not show the document for good.
DAYS = (1, 7, 30)
DEFAULT_DAYS = 7
# How long one opening of a link lets its browser read the document's pages and file before it
# opens the link again, which asks for the password again where it has one.
VIEW_FOR = dt.timedelta(hours=1)
# How many links one document may have running at once.
MAX_LINKS = 20
# The page a link opens (frontend/src/shared/SharedPage.tsx), and the cookie a view keeps.
SHARED_PAGE = "/shared"
VIEW_COOKIE = "emudoc_shared"

LINK_GONE = (
    "This link does not work any more: it expired or was revoked, or what it showed is no longer "
    "shared. Ask whoever sent it for a new one."
)
PASSWORD_NEEDED = "This link has a password. Enter the one whoever sent the link gave you."
WRONG_PASSWORD = "That is not this link's password. Check it with whoever sent the link."
VIEW_OVER = "This page was opened too long ago. Open the link again to go on reading."
LINKS_OFF = (
    "Your organisation's owners and admins turned share links off. Ask one of them to turn them "
    "on from the organisation page."
)
TOO_MANY_LINKS = (
    f"This document has {MAX_LINKS} links running already, as many as one may have. Revoke one "
    "before making another."
)
NOT_READY = "This document is not indexed yet. Share it once it is ready."
NOT_YOURS = "Only the member who made this link, or an owner or admin, can revoke it."


@dataclass(frozen=True)
class Shared:
    """A link that works, with what it shows and as whom."""

    link: ShareLink
    document: Document
    organization: Organization
    maker: User


def issue(
    session: Session, document: Document, account: Account, days: int, password: str | None
) -> tuple[ShareLink, str]:
    """A new link to the document, made by the account's member, and the token its address
    carries, which nothing keeps; the caller commits."""
    token = new_token()
    link = ShareLink(
        org_id=account.organization.id,
        project_id=document.project_id,
        document_id=document.id,
        user_id=account.user.id,
        token_hash=token_hash(token),
        password_hash=hash_password(password) if password else None,
        expires_at=now() + dt.timedelta(days=days),
    )
    session.add(link)
    session.flush()
    return link, token


def link_address(app_url: str, token: str) -> str:
    """The link as its maker sends it, the token in the fragment."""
    return f"{app_url}{SHARED_PAGE}#token={token}"


def running(session: Session, document_id: int) -> int:
    """How many links of the document have not expired."""
    return session.scalar(
        select(func.count())
        .select_from(ShareLink)
        .where(ShareLink.document_id == document_id, ShareLink.expires_at > now())
    )


def ready(document: Document) -> bool:
    """Whether the document can be shown: indexed, and its file passed by the malware scan."""
    return (
        document.status != "quarantined"
        and scan_passed(document)
        and document.stages.get("structure", {}).get("status") == "done"
    )


def find_link(session: Session, token: str) -> ShareLink | None:
    """The link whose address carries the token; None for one unknown, revoked or expired."""
    link = session.scalar(select(ShareLink).where(ShareLink.token_hash == token_hash(token)))
    if link is None or link.expires_at <= now():
        return None
    return link


def shown(session: Session, link: ShareLink) -> Shared | None:
    """What the link shows, provided it still works: as its maker, in the organisation, on its
    plan and with its leave; None when anything stops it."""
    if link.expires_at <= now():
        return None
    organization = session.get(Organization, link.org_id)
    if (
        not organization.share_links
        or check_share_links(organization) is not None
        or not flag_on(organization, SHARE_LINKS)
    ):
        return None
    found = session.execute(
        select(User, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == link.org_id, Membership.user_id == link.user_id)
    ).first()
    if found is None:
        return None
    maker, role = found
    account = Account(organization, maker, role)
    if not account.is_at_least("member") or maker.deletion_due_at is not None:
        return None
    document = session.scalar(
        select(Document)
        .join(Project, Project.id == Document.project_id)
        .where(
            Document.id == link.document_id,
            Document.deleted_at.is_(None),
            visible_to(account),
        )
    )
    if document is None or not ready(document):
        return None
    return Shared(link, document, organization, maker)


def open_view(session: Session, link: ShareLink) -> tuple[str, dt.datetime]:
    """A new view of the link for one browser, counted as an opening: the token its cookie
    keeps, and when it ends. The caller commits."""
    token = new_token()
    ends = min(now() + VIEW_FOR, link.expires_at)
    session.add(ShareView(link_id=link.id, token_hash=token_hash(token), expires_at=ends))
    link.views += 1
    link.last_viewed_at = now()
    return token, ends


def set_view_cookie(
    response: Response, request: Request, link: ShareLink, token: str, ends: dt.datetime
) -> None:
    """Keep the view's token where the browser sends it to this link's pages and file alone."""
    response.set_cookie(
        VIEW_COOKIE,
        token,
        max_age=max(1, int((ends - now()).total_seconds())),
        httponly=True,
        samesite="strict",
        secure=cookie_secure(request),
        path=view_path(link.id),
    )


def view_path(link_id: int) -> str:
    return f"/api/shared/{link_id}"


def viewed(session: Session, link_id: int, token: str | None) -> ShareLink | None:
    """The link a browser's view is of, provided the view is the link's and has not ended."""
    if not token:
        return None
    return session.scalar(
        select(ShareLink)
        .join(ShareView, ShareView.link_id == ShareLink.id)
        .where(
            ShareLink.id == link_id,
            ShareView.token_hash == token_hash(token),
            ShareView.expires_at > now(),
        )
    )


def prune_share_links(connection: Connection) -> int:
    """Delete the links that expired, with their views, and the views that ended; returns how
    many links."""
    connection.execute(delete(ShareView).where(ShareView.expires_at <= func.now()))
    return connection.execute(delete(ShareLink).where(ShareLink.expires_at <= func.now())).rowcount
