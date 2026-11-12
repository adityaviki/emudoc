"""Share links: a link that shows one document, or one answer with the passages it cites, to
whoever holds it, without an account.

A member who may change the project makes one from the document or the answer
(app/api/share_links.py), in an organisation whose owners and admins allow them
(`organizations.share_links`), while the feature is rolled out to it (SHARE_LINKS,
app/flags.py). It runs one of DAYS, DEFAULT_DAYS unless its maker chooses, never longer, and may
have a password, hashed as an account's is. The link carries a random token in its fragment,
which the browser never sends, so it lands in no server's or proxy's log and in no Referer; only
the token's SHA-256 is stored, as an API key's is, so a copy of the database opens nothing, and
the link is shown to its maker once.

A link works as its maker's (`shown`): only while they are a member who may change the project,
see what it shows and are not leaving Emudoc, so a member removed, made a viewer, or left out of
a project made private stops their links, and a link goes with its maker's membership by the
database's cascade, as an API key does; and only while the organisation's owners and admins allow
them, the flag is on for it, and what it shows is out of the trash: a
document indexed and passed by the malware scan, or an answer finished in a chat its maker sees,
their own or one shared with the project. Every check is made again at every request the link
makes, so any of those changes stops it at once, and the visitor is told the same sentence for
each (LINK_GONE): who holds a link learns nothing of the organisation from why it stopped.
Revoking a link deletes it, as does the housekeeper once it expired (`prune_share_links`), and a
database restore ends every link (app/restore.py), since one revoked after the moment restored to
would otherwise work again.

An answer's link shows its question, its text and the passages it cites (`cited_passages`), as
they read now, from documents still out of the trash; nothing else of the documents, neither a
file nor a page.

Opening a link (`POST /api/shared`, the page `/shared`) checks the token and the password. A
document's opening makes a view (`ShareView`): a random token of its own in a cookie the browser
sends to that link's pages and file alone (VIEW_COOKIE, its path the link's), for VIEW_FOR or
until the link ends, so the password is checked once rather than at every page and the page's
images load as any image does; an answer's needs none, since the opening's answer holds all it
shows. Each opening is a `share_link_viewed` row in the organisation's audit log, and each
original sent a `share_link_downloaded`, both with nobody as the member and the visitor's
address; the wrong passwords are counted for the link and for the address (SHARED_PASSWORDS,
app/ratelimit.py), and the openings for the address (SHARED_LINKS).
"""

import datetime as dt
import re
from dataclasses import dataclass

from fastapi import Request, Response
from sqlalchemy import Connection, delete, func, select
from sqlalchemy.orm import Session

from app.access import visible_to
from app.accounts import Account
from app.auth import cookie_secure, hash_password, new_token, now, token_hash
from app.flags import SHARE_LINKS, flag_on
from app.models import (
    Chat,
    ChatMessage,
    Document,
    Membership,
    Organization,
    Passage,
    Project,
    ShareLink,
    ShareView,
    User,
)
from app.scanning import scan_passed

# How many days a link may run, of which its maker chooses one; never longer, so that a link
# forgotten does not show the document for good.
DAYS = (1, 7, 30)
DEFAULT_DAYS = 7
# How long one opening of a link lets its browser read the document's pages and file before it
# opens the link again, which asks for the password again where it has one.
VIEW_FOR = dt.timedelta(hours=1)
# How many links one document or answer may have running at once.
MAX_LINKS = 20
# The page a link opens (frontend/src/shared/SharedPage.tsx), and the cookie a view keeps.
SHARED_PAGE = "/shared"
VIEW_COOKIE = "emudoc_shared"
# A citation in an answer, as the agent writes it (app/agent/).
_CITATION = re.compile(r"\[P(\d+)\]")

LINK_GONE = (
    "This link does not work any more: it expired or was revoked, or what it showed is no longer "
    "shared. Ask whoever sent it for a new one."
)
PASSWORD_NEEDED = "This link has a password. Enter the one whoever sent the link gave you."
WRONG_PASSWORD = "That is not this link's password. Check it with whoever sent the link."
VIEW_OVER = "This page was opened too long ago. Open the link again to go on reading."
LINKS_OFF = (
    "Your workspace's owners and admins turned share links off. Ask one of them to turn them "
    "on from the workspace page."
)
TOO_MANY_LINKS = (
    f"This has {MAX_LINKS} links running already, as many as one document or answer may have. "
    "Revoke one before making another."
)
NOT_READY = "This document is not indexed yet. Share it once it is ready."
ANSWER_NOT_READY = "Only a finished answer can be shared. Share it once it is written."
NOT_YOURS = "Only the member who made this link, or an owner or admin, can revoke it."


@dataclass(frozen=True)
class Shared:
    """A link that works, with what it shows (a document, or an answer of a chat) and as
    whom."""

    link: ShareLink
    organization: Organization
    maker: User
    document: Document | None = None
    chat: Chat | None = None
    message: ChatMessage | None = None


def issue(
    session: Session,
    account: Account,
    project_id: int,
    days: int,
    password: str | None,
    *,
    document_id: int | None = None,
    message_id: int | None = None,
) -> tuple[ShareLink, str]:
    """A new link to the document or the answer, made by the account's member, and the token its
    address carries, which nothing keeps; the caller commits."""
    token = new_token()
    link = ShareLink(
        org_id=account.organization.id,
        project_id=project_id,
        document_id=document_id,
        message_id=message_id,
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


def running(
    session: Session, *, document_id: int | None = None, message_id: int | None = None
) -> int:
    """How many links of the document, or of the answer, have not expired."""
    shows = (
        ShareLink.document_id == document_id
        if document_id is not None
        else ShareLink.message_id == message_id
    )
    return session.scalar(
        select(func.count()).select_from(ShareLink).where(shows, ShareLink.expires_at > now())
    )


def ready(document: Document) -> bool:
    """Whether the document can be shown: indexed, and its file passed by the malware scan."""
    return (
        document.status != "quarantined"
        and scan_passed(document)
        and document.stages.get("structure", {}).get("status") == "done"
    )


def answered(message: ChatMessage) -> bool:
    """Whether the message is an answer that can be shown: written to its end."""
    return message.role == "assistant" and message.status == "done" and bool(message.content)


def find_link(session: Session, token: str) -> ShareLink | None:
    """The link whose address carries the token; None for one unknown, revoked or expired."""
    link = session.scalar(select(ShareLink).where(ShareLink.token_hash == token_hash(token)))
    if link is None or link.expires_at <= now():
        return None
    return link


def shown(session: Session, link: ShareLink) -> Shared | None:
    """What the link shows, provided it still works: as its maker, in the organisation, with its
    leave; None when anything stops it."""
    if link.expires_at <= now():
        return None
    organization = session.get(Organization, link.org_id)
    if not organization.share_links or not flag_on(organization, SHARE_LINKS):
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
    if link.document_id is not None:
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
        return Shared(link, organization, maker, document=document)
    found = session.execute(
        select(Chat, ChatMessage)
        .join(ChatMessage, ChatMessage.chat_id == Chat.id)
        .join(Project, Project.id == Chat.project_id)
        .where(
            ChatMessage.id == link.message_id,
            Chat.deleted_at.is_(None),
            # The chat as `get_chat` shows it to the maker: their own, or one shared.
            (Chat.visibility == "project") | (Chat.user_id == maker.id),
            visible_to(account),
        )
    ).first()
    if found is None or not answered(found[1]):
        return None
    return Shared(link, organization, maker, chat=found[0], message=found[1])


def question_of(session: Session, message: ChatMessage) -> ChatMessage | None:
    """The question the answer answers: the chat's message just before it."""
    return session.scalar(
        select(ChatMessage)
        .where(ChatMessage.chat_id == message.chat_id, ChatMessage.id < message.id)
        .order_by(ChatMessage.id.desc())
        .limit(1)
    )


def cited_passages(
    session: Session, chat: Chat, message: ChatMessage
) -> list[tuple[Passage, Document]]:
    """The passages the answer cites, in the order it first cites them: those among the passages
    it was shown, as they read now, of the chat's project's documents out of the trash."""
    shown_ids = {source.get("id") for source in message.sources or ()}
    order = list(dict.fromkeys(int(match) for match in _CITATION.findall(message.content)))
    ids = [passage_id for passage_id in order if passage_id in shown_ids]
    if not ids:
        return []
    rows = session.execute(
        select(Passage, Document)
        .join(Document, Document.id == Passage.document_id)
        .where(
            Passage.id.in_(ids),
            Document.project_id == chat.project_id,
            Document.deleted_at.is_(None),
        )
    ).all()
    found = {passage.id: (passage, document) for passage, document in rows}
    return [found[passage_id] for passage_id in ids if passage_id in found]


def counted(link: ShareLink) -> None:
    """Count an opening of the link, for its maker to see; the caller commits."""
    link.views += 1
    link.last_viewed_at = now()


def open_view(session: Session, link: ShareLink) -> tuple[str, dt.datetime]:
    """A new view of a document's link for one browser: the token its cookie keeps, and when it
    ends. The caller commits."""
    token = new_token()
    ends = min(now() + VIEW_FOR, link.expires_at)
    session.add(ShareView(link_id=link.id, token_hash=token_hash(token), expires_at=ends))
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
