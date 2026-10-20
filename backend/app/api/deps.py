"""What a request may touch: the account behind it, and the objects it may see or change.

The account is the user the session cookie names, in their organisation. Without a valid
session the request is refused, unless the local account is on for development, in which case
it acts as the local owner. An account set to be deleted (app/retention.py) is refused too,
except by the three routes that take `AnyAccountDep`: the ones that show it, keep it and
export its data. So is an account whose user has not accepted the current terms of service
(app/legal/), except by those three and the two that take `LiveAccountDep`: accepting them,
and asking for the account to be deleted instead. Signing out needs no account.

Every object is reached through the account's organisation: a project must belong to it, and a
document, folder, passage or chat must lie in one of its projects. Anything else is reported as
not found, so a guessed id reveals nothing about other tenants. A chat is also hidden from
everyone but the member who started it until they share it with the project.

Reading needs any role; changing something needs the member role or better, which the
`Editable...` dependencies check after the object itself is found.

A request that changes something must also carry the header the app's own requests send.
The browser attaches the session cookie to a request from any site, so without that proof a
page elsewhere could act as the signed-in user; `refuse_cross_site` runs on every route.

The routes that cost something or guard an account are held to a rate per caller
(app/ratelimit.py): `limited(limit)` is the dependency for a route with an account, counted
per user, and `refuse_past` is what a sign-in route calls with the keys it has.
"""

from collections.abc import Callable
from typing import Annotated, Literal

import httpx
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts import Account, account_for_user, local_account, zone_of
from app.auth import SESSION_COOKIE, find_session, touch_session
from app.config import get_settings
from app.db import get_session
from app.indexing.pipeline import Services
from app.legal import terms_due, terms_pending
from app.logs import about
from app.mail import Mailer
from app.models import Chat, Document, Folder, Passage, Project, User
from app.ratelimit import Key, Limit, by_user, hit
from app.retention import deletion_pending
from app.worker import Worker

SessionDep = Annotated[Session, Depends(get_session)]
# The stage a reprocess run starts from; later stages run too, and so does an earlier one
# that is not done, which is how a quarantined document is scanned again.
Stage = Literal["scan", "parse", "structure", "enrich", "embed"]

SIGN_IN = "Sign in to continue."
# How long a request refused because the process is full (every database connection in use,
# every answer it may stream in flight) tells the client to wait, in seconds, in the
# Retry-After header of its 503 or 429.
RETRY_AFTER = 5
NO_ORGANISATION = (
    "Your account is not a member of any organisation. Ask for an invitation, or contact support."
)
READ_ONLY = (
    "Your role in this organisation is viewer, which can only read. "
    "Ask an owner or admin of the organisation to make you a member."
)
# The app sends this header with every request. A form on another site cannot add a header,
# and a script there that adds one makes the browser ask first with a CORS preflight, which
# this app never answers; so the header proves the request did not come from another site.
SAME_APP_HEADER = "X-Requested-With"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CROSS_SITE = (
    "This request would change something but did not come from the Emudoc app: it lacks the "
    f"{SAME_APP_HEADER} header. If you call the API yourself, send that header with any value."
)


def get_services(request: Request) -> Services:
    return request.app.state.services


def get_worker(request: Request) -> Worker | None:
    return request.app.state.worker


def get_mailer(request: Request) -> Mailer:
    return request.app.state.mailer


def get_http(request: Request) -> httpx.Client:
    """The client for calls the app makes to other services, such as the OAuth providers."""
    return request.app.state.http


ServicesDep = Annotated[Services, Depends(get_services)]
WorkerDep = Annotated[Worker | None, Depends(get_worker)]
MailerDep = Annotated[Mailer, Depends(get_mailer)]
HttpDep = Annotated[httpx.Client, Depends(get_http)]


async def refuse_cross_site(request: Request) -> None:
    """Refuse a change that a page on another site could have made with the user's cookie.

    Async, so that it runs on the event loop: a synchronous dependency waits for a thread, and
    the liveness route (`alive` in app/main.py) must answer while every thread is busy.
    """
    if request.method not in SAFE_METHODS and SAME_APP_HEADER not in request.headers:
        raise HTTPException(403, CROSS_SITE)


def get_account(request: Request, session: SessionDep) -> Account:
    """The organisation and user a request acts as: the session's user, or the local account.
    What the request logs from here on names them."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        user_session = find_session(session, token)
        # A session that still waits for the authenticator code grants nothing yet.
        if user_session is not None and not user_session.needs_mfa:
            user = session.get(User, user_session.user_id)
            about(user_id=user.id)
            account = account_for_user(session, user, user_session.id)
            if account is None:
                raise HTTPException(403, NO_ORGANISATION)
            about(org_id=account.organization.id)
            touch_session(session, user_session)
            return account
    if get_settings().local_account:
        account = local_account(session)
        about(user_id=account.user.id, org_id=account.organization.id)
        return account
    raise HTTPException(401, SIGN_IN)


AnyAccountDep = Annotated[Account, Depends(get_account)]


def get_live_account(account: AnyAccountDep) -> Account:
    """The account, provided it is not set to be deleted: until its user keeps it, it can do
    nothing that would add to what the purge removes, or act on what others hold."""
    if account.user.deletion_due_at is not None:
        raise HTTPException(
            403, deletion_pending(account.user.deletion_due_at, zone_of(account.user.time_zone))
        )
    return account


LiveAccountDep = Annotated[Account, Depends(get_live_account)]


def get_usable_account(account: LiveAccountDep) -> Account:
    """The account, provided its user has also accepted the current terms of service: nobody
    uses the service on terms they have not seen. Leaving it needs no acceptance."""
    if terms_due(account):
        raise HTTPException(403, terms_pending())
    return account


AccountDep = Annotated[Account, Depends(get_usable_account)]


def get_editor(account: AccountDep) -> Account:
    """The account, provided its role allows changing what the organisation holds."""
    if not account.is_at_least("member"):
        raise HTTPException(403, READ_ONLY)
    return account


EditorDep = Annotated[Account, Depends(get_editor)]


def refuse_past(session: Session, *hits: tuple[Limit, Key]) -> None:
    """Count the request under each limit for its key, and refuse it with 429 and a
    Retry-After when one of them is past its rate."""
    if refusal := hit(session, *hits):
        raise HTTPException(
            429, refusal.sentence, headers={"Retry-After": str(refusal.retry_after)}
        )


def limited(limit: Limit) -> Callable[..., None]:
    """The dependency that holds a route to `limit` per user. It goes in the route's
    `dependencies`, which run before the route's own parameters are resolved, so the request
    is counted before anything else is read or checked, the terms included: the route's own
    account parameter refuses an account that has not accepted them."""

    def dependency(session: SessionDep, account: LiveAccountDep) -> None:
        refuse_past(session, (limit, by_user(account)))

    return dependency


def get_project(project_id: int, session: SessionDep, account: AccountDep) -> Project:
    project = session.scalar(
        select(Project).where(Project.id == project_id, Project.org_id == account.organization.id)
    )
    if project is None:
        raise HTTPException(404, "Project not found")
    about(project_id=project.id)
    return project


def get_document(document_id: int, session: SessionDep, account: AccountDep) -> Document:
    document = _in_organization(session, account, Document, document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    about(document_id=document.id)
    return document


def get_folder(folder_id: int, session: SessionDep, account: AccountDep) -> Folder:
    folder = _in_organization(session, account, Folder, folder_id)
    if folder is None:
        raise HTTPException(404, "Folder not found")
    return folder


def get_passage(passage_id: int, session: SessionDep, account: AccountDep) -> Passage:
    passage = _in_organization(session, account, Passage, passage_id)
    if passage is None:
        raise HTTPException(404, "Passage not found")
    return passage


def get_chat(chat_id: int, session: SessionDep, account: AccountDep) -> Chat:
    chat = _in_organization(session, account, Chat, chat_id)
    if chat is None or (chat.visibility == "private" and chat.user_id != account.user.id):
        raise HTTPException(404, "Chat not found")
    return chat


def _in_organization[T: (Document, Folder, Passage, Chat)](
    session: Session, account: Account, model: type[T], object_id: int
) -> T | None:
    """The row with this id, provided its project belongs to the account's organisation. What
    the request logs from here on names the project, once it is known to be the account's."""
    found = session.scalar(
        select(model)
        .join(Project, Project.id == model.project_id)
        .where(model.id == object_id, Project.org_id == account.organization.id)
    )
    if found is not None:
        about(project_id=found.project_id)
    return found


ProjectDep = Annotated[Project, Depends(get_project)]
DocumentDep = Annotated[Document, Depends(get_document)]
FolderDep = Annotated[Folder, Depends(get_folder)]
PassageDep = Annotated[Passage, Depends(get_passage)]
ChatDep = Annotated[Chat, Depends(get_chat)]


def get_editable_project(project: ProjectDep, account: AccountDep) -> Project:
    get_editor(account)
    return project


def get_editable_document(document: DocumentDep, account: AccountDep) -> Document:
    get_editor(account)
    return document


def get_editable_folder(folder: FolderDep, account: AccountDep) -> Folder:
    get_editor(account)
    return folder


def get_own_chat(chat: ChatDep, account: AccountDep) -> Chat:
    """The chat, for changing it or asking in it: a shared chat is still its starter's to run."""
    if chat.user_id != account.user.id:
        raise HTTPException(
            403, "Only the member who started this chat can change it or ask in it."
        )
    return chat


EditableProjectDep = Annotated[Project, Depends(get_editable_project)]
EditableDocumentDep = Annotated[Document, Depends(get_editable_document)]
EditableFolderDep = Annotated[Folder, Depends(get_editable_folder)]
OwnChatDep = Annotated[Chat, Depends(get_own_chat)]
