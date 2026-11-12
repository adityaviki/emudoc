"""What a request may touch: the account behind it, and the objects it may see or change.

The account is the user the session cookie names, in the organisation the session works in
(`account_for_user` in app/accounts.py), the install's one workspace. Without a valid session the
request is refused, unless the local account is on for development, in which case it acts as the
local owner. A signed-in user who belongs to no workspace, since an admin removed them, is
refused everything with 403 (`NoWorkspace`) but the two routes that take `PersonDep`: showing who
is signed in, which says so, and accepting an invitation, which brings them back. An account
set to be deleted (app/retention.py) is refused too, except by the three routes that take
`AnyAccountDep`: the ones that show it, keep it and export its data. So is an account whose
user has not accepted the current terms of service (app/legal/), except by those three and the
two that take `LiveAccountDep`: accepting them, and asking for the account to be deleted
instead. Signing out needs no account.

The workspace's security settings (app/security.py) are held here too. A session that signed
in longer ago than it allows ends at its next request, as an expired one; and where it requires
two-factor sign-in, an account without it is refused everything that takes `AccountDep`, the
routes it needs to set it up or sign out taking the dependencies before it.

Every object is reached through the account's organisation: a project must belong to it and be
one the account sees (a private project only its chosen members and the owners and admins,
app/access.py), and a document, folder, passage or chat must lie in such a project. Anything
else is reported as not found, so a guessed id reveals nothing about other tenants or about a
private project. A chat is also hidden from everyone but the member who started it until they
share it with the project. What is in the trash (app/trash.py) is found only by the `Trashed...`
dependencies, the trash's own routes, and a document or passage asked for as if it were live is
told where it went.

Reading needs any role; changing something needs the member role or better, which the
`Editable...` dependencies check after the object itself is found. They also hold the change
to the organisation's plan: one that holds more than its plan allows, since the plan, its seats
or its terms were lowered, is read-only until it is within it again (`read_only` in
app/entitlements.py), and the change is refused with 402. Deleting is what such an
organisation may still do, to get within its plan, so the `Deletable...` dependencies check
the role alone, as the `Trashed...` ones do for deleting for good from the trash; a restore
from it adds to what the organisation holds, so its routes take `EditorDep` as well.

A request that changes something must also carry the header the app's own requests send.
The browser attaches the session cookie to a request from any site, so without that proof a
page elsewhere could act as the signed-in user; `refuse_cross_site` runs on every route.

A program calls the API with an API key instead of a session (app/apikeys.py): a request that
names one in its `Authorization: Bearer` header acts as the member who made the key, in the
organisation it was made in, and is judged by the key alone. It needs no header of the app's,
since a browser sends a Bearer token with no request by itself. The key reaches only the routes
that read and change what the projects hold (KEYED in app/main.py), only by the methods that
change nothing unless its scope is `write`, only while the organisation's plan includes the API,
and it is held to a rate of its own, besides its member's; past that it is its member's account,
held to everything above as they are.

An operator's session may view a member's account as Emudoc's support, with the member's leave
(app/support.py): it acts as the member, in the organisation the leave is for, and only by the
methods that change nothing; past that it is the member's account too. A view that may not go
on ends at the request that finds it so, which is refused with 409 and the organisation the
operator's session works in again (ORGANIZATION_HEADER), which the page takes to start again.

The routes that cost something or guard an account are held to a rate per caller
(app/ratelimit.py): `limited(limit)` is the dependency for a route with an account, counted
per user, and `refuse_past` is what a sign-in route calls with the keys it has.

A feature being rolled out is behind a flag (app/flags.py): `flagged(name)` is the dependency of
its routes, which answers them as if they were not there for an organisation the flag is off for.
"""

from collections.abc import Callable
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import sees, visible_to
from app.accounts import Account, Outsider, account_for_user, local_account, zone_of
from app.apikeys import (
    CHALLENGE,
    ELSEWHERE,
    KEY_UNKNOWN,
    READ_ONLY_KEY,
    bearer,
    expired,
    find_key,
    key_expired,
    touch_key,
)
from app.auth import SESSION_COOKIE, find_session, now, touch_session
from app.config import get_settings
from app.costs import show_costs
from app.db import get_session
from app.entitlements import PlanRefused, check_api, read_only
from app.fact_checks import CHECK_GONE, FACT_GONE
from app.flags import flag, flag_on
from app.indexing.pipeline import Services
from app.legal import terms_due, terms_pending
from app.logs import about
from app.models import (
    Automation,
    Chat,
    Comment,
    CommentThread,
    CrossCheck,
    Document,
    Draft,
    ExtractionTemplate,
    Fact,
    FactCheck,
    Folder,
    MyFile,
    Passage,
    Project,
    QuestionReport,
    QuestionSet,
    User,
    UserSession,
    WorkflowRun,
)
from app.ratelimit import API_REQUESTS, Key, Limit, by_key, by_user, hit
from app.retention import deletion_pending
from app.security import mfa_due, mfa_pending, sign_in_again, signed_in_too_long
from app.support import READ_ONLY_VIEW, VIEW_OVER, stop, viewed
from app.trash import DOCUMENT_IN_TRASH, NOT_IN_TRASH, PASSAGE_IN_TRASH
from app.worker import Worker

SessionDep = Annotated[Session, Depends(get_session)]

SIGN_IN = "Sign in to continue."
# What a route nobody may find is answered: FastAPI's own words for a path no route takes, so
# that the two cannot be told apart.
NOT_FOUND = "Not Found"
# How long a request refused because the process is full (every database connection in use,
# every answer it may stream in flight) tells the client to wait, in seconds, in the
# Retry-After header of its 503 or 429.
RETRY_AFTER = 5
NO_WORKSPACE = (
    "You no longer belong to this server's workspace. Ask its admin to invite you again: the "
    "invitation's link brings you back."
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
    f"{SAME_APP_HEADER} header. If you call the API yourself, call it with an API key, which "
    "needs no such header."
)
# The organisation a session works in again once a view by support ends, which the page takes to
# start again there (`send` in frontend/src/api.ts).
ORGANIZATION_HEADER = "X-Organization"


def get_services(request: Request) -> Services:
    return request.app.state.services


def get_worker(request: Request) -> Worker | None:
    return request.app.state.worker


def get_http(request: Request) -> httpx.Client:
    """The client for calls the app makes to other services, such as the OAuth providers."""
    return request.app.state.http


ServicesDep = Annotated[Services, Depends(get_services)]
WorkerDep = Annotated[Worker | None, Depends(get_worker)]
HttpDep = Annotated[httpx.Client, Depends(get_http)]


async def body_within(request: Request, limit: int, too_large: str) -> bytes:
    """A webhook's body as it came, which its signature is over, refused with 413 and
    `too_large` past `limit` bytes, whatever the request says its length is."""
    declared = request.headers.get("content-length", "")
    if declared.isdecimal() and int(declared) > limit:
        raise HTTPException(413, too_large)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(413, too_large)
    return bytes(body)


async def refuse_cross_site(request: Request) -> None:
    """Refuse a change that a page on another site could have made with the user's cookie.

    Async, so that it runs on the event loop: a synchronous dependency waits for a thread, and
    the liveness route (`alive` in app/main.py) must answer while every thread is busy.
    """
    if (
        request.method not in SAFE_METHODS
        and SAME_APP_HEADER not in request.headers
        and bearer(request) is None
    ):
        raise HTTPException(403, CROSS_SITE)


def get_person(request: Request, session: SessionDep) -> Account | Outsider:
    """The organisation and user a request acts as: the API key's member, the session's user,
    the member an operator's session views as support, or the local account; or the session's
    user alone, when they belong to no workspace. What the request logs from here on names
    them."""
    if (secret := bearer(request)) is not None:
        return _key_account(request, session, secret)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        user_session = find_session(session, token)
        # A session that still waits for the authenticator code grants nothing yet.
        if user_session is not None and not user_session.needs_mfa:
            if user_session.support_ends_at is not None:
                return _viewed_account(request, session, user_session)
            user = session.get(User, user_session.user_id)
            about(user_id=user.id)
            account = account_for_user(session, user, user_session)
            if account is None:
                return Outsider(user, user_session.id)
            about(org_id=account.organization.id)
            if signed_in_too_long(account.organization, user_session):
                user_session.revoked_at = now()
                session.commit()
                raise HTTPException(401, sign_in_again(account.organization))
            touch_session(session, user_session)
            return account
    if get_settings().local_account:
        account = local_account(session)
        about(user_id=account.user.id, org_id=account.organization.id)
        return account
    raise HTTPException(401, SIGN_IN)


def _viewed_account(request: Request, session: Session, user_session: UserSession) -> Account:
    """The member's account an operator views as support from their own session, provided the
    request changes nothing. A view that may no longer go on ends, and the request is refused
    with the organisation the session works in again, so that the page starts again as the
    operator's own; the organisation's own limit on a session's age is the operator's view's
    hour instead."""
    operator = session.get(User, user_session.user_id)
    about(operator_id=operator.id)
    account = viewed(session, request, user_session, operator)
    if account is None:
        stop(user_session)
        session.commit()
        own = account_for_user(session, operator, user_session)
        raise HTTPException(
            409, VIEW_OVER, headers={ORGANIZATION_HEADER: str(own.organization.id) if own else ""}
        )
    about(user_id=account.user.id, org_id=account.organization.id)
    if request.method not in SAFE_METHODS:
        raise HTTPException(403, READ_ONLY_VIEW)
    touch_session(session, user_session)
    return account


def _key_account(request: Request, session: SessionDep, secret: str) -> Account:
    """The account an API key acts as, counted for the key, provided the route is one a key
    reaches and the key's scope and the organisation's plan allow the request."""
    found = find_key(session, secret)
    if found is None:
        raise HTTPException(401, KEY_UNKNOWN, headers=CHALLENGE)
    key, organization, user, role = found
    about(user_id=user.id, org_id=organization.id, api_key_id=key.id)
    if expired(key):
        raise HTTPException(401, key_expired(key, user), headers=CHALLENGE)
    refuse_past(session, (API_REQUESTS, by_key(key.id)))
    if request.scope.get("endpoint") not in getattr(request.app.state, "keyed", ()):
        raise HTTPException(403, ELSEWHERE)
    if key.scope == "read" and request.method not in SAFE_METHODS:
        raise HTTPException(403, READ_ONLY_KEY)
    if refusal := check_api(organization):
        raise PlanRefused(refusal)
    touch_key(session, key)
    return Account(organization, user, role, key=key)


PersonDep = Annotated[Account | Outsider, Depends(get_person)]


def get_account(person: PersonDep) -> Account:
    """The account a request acts as, refused for a signed-in user who belongs to no workspace:
    there is nothing for them to see but that, until they are invited again."""
    if isinstance(person, Outsider):
        raise HTTPException(403, NO_WORKSPACE)
    return person


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


UsableAccountDep = Annotated[Account, Depends(get_usable_account)]


def get_secured_account(account: UsableAccountDep) -> Account:
    """The account, provided it signs in as its organisation requires: with two-factor, where
    the organisation asks for it. What the request answers shows what work cost to an owner or
    an admin alone (app/costs.py)."""
    if mfa_due(account):
        raise HTTPException(403, mfa_pending(account.organization))
    show_costs(account.is_at_least("admin"))
    return account


AccountDep = Annotated[Account, Depends(get_secured_account)]


def get_editor(account: AccountDep, session: SessionDep) -> Account:
    """The account, provided its role allows changing what the organisation holds and the
    organisation holds no more than its plan allows."""
    _refuse_viewer(account)
    if refusal := read_only(session, account.organization):
        raise PlanRefused(refusal)
    return account


def get_writer(account: AccountDep, session: SessionDep) -> Account:
    """The account, provided the organisation holds no more than its plan allows: whoever may
    keep things of their own in it, such as My files (app/my_files.py), a viewer too, since
    those change nothing the organisation's members share."""
    if refusal := read_only(session, account.organization):
        raise PlanRefused(refusal)
    return account


def _refuse_viewer(account: Account) -> None:
    if not account.is_at_least("member"):
        raise HTTPException(403, READ_ONLY)


EditorDep = Annotated[Account, Depends(get_editor)]
WriterDep = Annotated[Account, Depends(get_writer)]


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
        refuse_past(session, (limit, by_user(account.user)))

    return dependency


def flagged(name: str) -> Callable[..., None]:
    """The dependency that hides a route behind the feature flag `name` (app/flags.py): for an
    organisation the flag is off for, the route answers as a path no route takes. It goes in the
    route's or its router's `dependencies`. A name FLAGS does not hold raises UnknownFlag here,
    as the route's module is imported, so that a misspelt flag stops the app rather than hide
    the feature from everyone."""
    flag(name)

    def dependency(account: AccountDep) -> None:
        if not flag_on(account.organization, name):
            raise HTTPException(404, NOT_FOUND)

    return dependency


def get_project(project_id: int, session: SessionDep, account: AccountDep) -> Project:
    project = session.scalar(select(Project).where(Project.id == project_id, visible_to(account)))
    if project is None:
        raise HTTPException(404, "Project not found")
    about(project_id=project.id)
    return project


def get_document(document_id: int, session: SessionDep, account: AccountDep) -> Document:
    document = _in_organization(session, account, Document, document_id)
    if document is None:
        trashed = _in_organization(session, account, Document, document_id, trashed=True)
        raise HTTPException(404, DOCUMENT_IN_TRASH if trashed else "Document not found")
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
        trashed = _in_organization(session, account, Passage, passage_id, trashed=True)
        raise HTTPException(404, PASSAGE_IN_TRASH if trashed else "Passage not found")
    return passage


def get_chat(chat_id: int, session: SessionDep, account: AccountDep) -> Chat:
    chat = _in_organization(session, account, Chat, chat_id)
    if chat is None or (chat.visibility == "private" and chat.user_id != account.user.id):
        raise HTTPException(404, "Chat not found")
    return chat


def _in_organization[T: (Document, Folder, Passage, Chat)](
    session: Session, account: Account, model: type[T], object_id: int, trashed: bool = False
) -> T | None:
    """The row with this id, provided its project is one of the organisation's that the account
    sees, out of the trash; and the row itself out of the trash too, or in it with `trashed` (a
    passage by its document's). What the request logs from here on names the project, once it
    is known to be the account's."""
    query = (
        select(model)
        .join(Project, Project.id == model.project_id)
        .where(model.id == object_id, visible_to(account))
    )
    if model is Passage:
        query = query.join(Document, Document.id == Passage.document_id)
    deleted_at = Document.deleted_at if model is Passage else model.deleted_at
    found = session.scalar(
        query.where(deleted_at.is_not(None) if trashed else deleted_at.is_(None))
    )
    if found is not None:
        about(project_id=found.project_id)
    return found


def get_template(template_id: int, session: SessionDep, account: AccountDep) -> ExtractionTemplate:
    """A project's own extraction template (app/extraction.py), provided the account sees the
    project and it is out of the trash."""
    template = session.scalar(
        select(ExtractionTemplate)
        .join(Project, Project.id == ExtractionTemplate.project_id)
        .where(ExtractionTemplate.id == template_id, visible_to(account))
    )
    if template is None:
        raise HTTPException(404, "Template not found")
    about(project_id=template.project_id)
    return template


def get_cross_check(check_id: int, session: SessionDep, account: AccountDep) -> CrossCheck:
    """A project's cross-check (app/cross_checks.py), provided the account sees the project and
    it is out of the trash."""
    check = session.scalar(
        select(CrossCheck)
        .join(Project, Project.id == CrossCheck.project_id)
        .where(CrossCheck.id == check_id, visible_to(account))
    )
    if check is None:
        raise HTTPException(404, "Cross-check not found")
    about(project_id=check.project_id)
    return check


def get_run(run_id: int, session: SessionDep, account: AccountDep) -> WorkflowRun:
    """A run of a project's cross-checks (app/check_runs.py), provided the account sees the
    project and it is out of the trash."""
    run = session.scalar(
        select(WorkflowRun)
        .join(Project, Project.id == WorkflowRun.project_id)
        .where(WorkflowRun.id == run_id, visible_to(account))
    )
    if run is None:
        raise HTTPException(404, "Run not found")
    about(project_id=run.project_id)
    return run


def get_automation(automation_id: int, session: SessionDep, account: AccountDep) -> Automation:
    """A project's automation (app/automations.py), provided the account sees the project and it
    is out of the trash."""
    automation = session.scalar(
        select(Automation)
        .join(Project, Project.id == Automation.project_id)
        .where(Automation.id == automation_id, visible_to(account))
    )
    if automation is None:
        raise HTTPException(404, "Automation not found")
    about(project_id=automation.project_id)
    return automation


def get_question_set(set_id: int, session: SessionDep, account: AccountDep) -> QuestionSet:
    """A project's question set (app/question_sets.py), provided the account sees the project
    and it is out of the trash."""
    row = session.scalar(
        select(QuestionSet)
        .join(Project, Project.id == QuestionSet.project_id)
        .where(QuestionSet.id == set_id, visible_to(account))
    )
    if row is None:
        raise HTTPException(404, "Question set not found")
    about(project_id=row.project_id)
    return row


def get_report(report_id: int, session: SessionDep, account: AccountDep) -> QuestionReport:
    """A report of a project's question set (app/question_reports.py), under the same terms."""
    report = session.scalar(
        select(QuestionReport)
        .join(Project, Project.id == QuestionReport.project_id)
        .where(QuestionReport.id == report_id, visible_to(account))
    )
    if report is None:
        raise HTTPException(404, "Report not found")
    about(project_id=report.project_id)
    return report


def get_draft(draft_id: int, session: SessionDep, account: AccountDep) -> Draft:
    """A draft of a project's (app/drafts.py), provided the account sees the project and it is
    out of the trash."""
    draft = session.scalar(
        select(Draft)
        .join(Project, Project.id == Draft.project_id)
        .where(Draft.id == draft_id, visible_to(account))
    )
    if draft is None:
        raise HTTPException(404, "Draft not found")
    about(project_id=draft.project_id)
    return draft


def get_my_file(file_id: int, session: SessionDep, account: AccountDep) -> MyFile:
    """A file of the account's own My files (app/my_files.py), provided it still sees the
    project and the project is out of the trash. Another member's file, the project's owners'
    and admins' alike, is not found: it is its member's alone."""
    file = session.scalar(
        select(MyFile)
        .join(Project, Project.id == MyFile.project_id)
        .where(MyFile.id == file_id, MyFile.user_id == account.user.id, visible_to(account))
    )
    if file is None:
        raise HTTPException(404, "File not found")
    about(project_id=file.project_id)
    return file


def get_fact(fact_id: int, session: SessionDep, account: AccountDep) -> Fact:
    """A fact read from a document (app/fact_checks.py), provided the account sees the
    document's project and the document is out of the trash."""
    return _read_from(session, account, Fact, fact_id, FACT_GONE)


def get_fact_check(check_id: int, session: SessionDep, account: AccountDep) -> FactCheck:
    """A member's check of a document's fact, under the same terms as the fact."""
    return _read_from(session, account, FactCheck, check_id, CHECK_GONE)


def get_thread(thread_id: int, session: SessionDep, account: AccountDep) -> CommentThread:
    """A thread of a document's comments (app/comments.py), under the same terms as a fact."""
    return _read_from(session, account, CommentThread, thread_id, "Thread not found")


def get_comment(comment_id: int, session: SessionDep, account: AccountDep) -> Comment:
    """A comment in a thread, under the same terms as its thread."""
    return _read_from(session, account, Comment, comment_id, "Comment not found")


def _read_from[T: (Fact, FactCheck, CommentThread, Comment)](
    session: Session, account: Account, model: type[T], object_id: int, missing: str
) -> T:
    query = select(model, Document.project_id, Document.deleted_at, Document.id)
    if model is Comment:
        query = query.join(CommentThread, CommentThread.id == Comment.thread_id)
        document_id = CommentThread.document_id
    else:
        document_id = model.document_id
    found = session.execute(
        query.join(Document, Document.id == document_id)
        .join(Project, Project.id == Document.project_id)
        .where(model.id == object_id, visible_to(account))
    ).first()
    if found is None:
        raise HTTPException(404, missing)
    row, project_id, deleted_at, document_id = found
    if deleted_at is not None:
        raise HTTPException(404, DOCUMENT_IN_TRASH)
    about(project_id=project_id, document_id=document_id)
    return row


ProjectDep = Annotated[Project, Depends(get_project)]
DocumentDep = Annotated[Document, Depends(get_document)]
FolderDep = Annotated[Folder, Depends(get_folder)]
PassageDep = Annotated[Passage, Depends(get_passage)]
ChatDep = Annotated[Chat, Depends(get_chat)]
ThreadDep = Annotated[CommentThread, Depends(get_thread)]
MyFileDep = Annotated[MyFile, Depends(get_my_file)]
CommentDep = Annotated[Comment, Depends(get_comment)]


def get_editable_project(project: ProjectDep, account: AccountDep, session: SessionDep) -> Project:
    get_editor(account, session)
    return project


def get_editable_document(
    document: DocumentDep, account: AccountDep, session: SessionDep
) -> Document:
    get_editor(account, session)
    return document


def get_editable_folder(folder: FolderDep, account: AccountDep, session: SessionDep) -> Folder:
    get_editor(account, session)
    return folder


def get_editable_template(
    template: Annotated[ExtractionTemplate, Depends(get_template)],
    account: AccountDep,
    session: SessionDep,
) -> ExtractionTemplate:
    get_editor(account, session)
    return template


def get_editable_cross_check(
    check: Annotated[CrossCheck, Depends(get_cross_check)],
    account: AccountDep,
    session: SessionDep,
) -> CrossCheck:
    get_editor(account, session)
    return check


def get_editable_automation(
    automation: Annotated[Automation, Depends(get_automation)],
    account: AccountDep,
    session: SessionDep,
) -> Automation:
    get_editor(account, session)
    return automation


def get_editable_run(
    run: Annotated[WorkflowRun, Depends(get_run)], account: AccountDep, session: SessionDep
) -> WorkflowRun:
    get_editor(account, session)
    return run


def get_deletable_run(
    run: Annotated[WorkflowRun, Depends(get_run)], account: AccountDep
) -> WorkflowRun:
    """The run, for deleting it: by role alone, as a document is."""
    _refuse_viewer(account)
    return run


def get_editable_question_set(
    row: Annotated[QuestionSet, Depends(get_question_set)],
    account: AccountDep,
    session: SessionDep,
) -> QuestionSet:
    get_editor(account, session)
    return row


def get_deletable_question_set(
    row: Annotated[QuestionSet, Depends(get_question_set)], account: AccountDep
) -> QuestionSet:
    """The set, for deleting it: by role alone, as a document is."""
    _refuse_viewer(account)
    return row


def get_deletable_report(
    report: Annotated[QuestionReport, Depends(get_report)], account: AccountDep
) -> QuestionReport:
    """The report, for deleting it: by role alone, as a run of cross-checks is."""
    _refuse_viewer(account)
    return report


def get_editable_draft(
    draft: Annotated[Draft, Depends(get_draft)], account: AccountDep, session: SessionDep
) -> Draft:
    get_editor(account, session)
    return draft


def get_deletable_draft(draft: Annotated[Draft, Depends(get_draft)], account: AccountDep) -> Draft:
    """The draft, for deleting it: by role alone, as a document is."""
    _refuse_viewer(account)
    return draft


def get_editable_fact(
    fact: Annotated[Fact, Depends(get_fact)], account: AccountDep, session: SessionDep
) -> Fact:
    get_editor(account, session)
    return fact


def get_editable_fact_check(
    check: Annotated[FactCheck, Depends(get_fact_check)], account: AccountDep, session: SessionDep
) -> FactCheck:
    get_editor(account, session)
    return check


def get_deletable_project(project: ProjectDep, account: AccountDep) -> Project:
    """The project, for deleting it: by role alone, since deleting is how an organisation past
    its plan gets within it."""
    _refuse_viewer(account)
    return project


def get_deletable_document(document: DocumentDep, account: AccountDep) -> Document:
    _refuse_viewer(account)
    return document


def get_deletable_folder(folder: FolderDep, account: AccountDep) -> Folder:
    _refuse_viewer(account)
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
EditableTemplateDep = Annotated[ExtractionTemplate, Depends(get_editable_template)]
CrossCheckDep = Annotated[CrossCheck, Depends(get_cross_check)]
EditableCrossCheckDep = Annotated[CrossCheck, Depends(get_editable_cross_check)]
RunDep = Annotated[WorkflowRun, Depends(get_run)]
EditableRunDep = Annotated[WorkflowRun, Depends(get_editable_run)]
DeletableRunDep = Annotated[WorkflowRun, Depends(get_deletable_run)]
DraftDep = Annotated[Draft, Depends(get_draft)]
EditableDraftDep = Annotated[Draft, Depends(get_editable_draft)]
DeletableDraftDep = Annotated[Draft, Depends(get_deletable_draft)]
AutomationDep = Annotated[Automation, Depends(get_automation)]
EditableAutomationDep = Annotated[Automation, Depends(get_editable_automation)]
QuestionSetDep = Annotated[QuestionSet, Depends(get_question_set)]
EditableQuestionSetDep = Annotated[QuestionSet, Depends(get_editable_question_set)]
DeletableQuestionSetDep = Annotated[QuestionSet, Depends(get_deletable_question_set)]
ReportDep = Annotated[QuestionReport, Depends(get_report)]
DeletableReportDep = Annotated[QuestionReport, Depends(get_deletable_report)]
EditableFactDep = Annotated[Fact, Depends(get_editable_fact)]
EditableFactCheckDep = Annotated[FactCheck, Depends(get_editable_fact_check)]
DeletableProjectDep = Annotated[Project, Depends(get_deletable_project)]
DeletableDocumentDep = Annotated[Document, Depends(get_deletable_document)]
DeletableFolderDep = Annotated[Folder, Depends(get_deletable_folder)]
OwnChatDep = Annotated[Chat, Depends(get_own_chat)]


def get_trashed_project(project_id: int, session: SessionDep, account: AccountDep) -> Project:
    """A project in the trash that the account may see, for restoring it or deleting it for
    good, which a member or better may."""
    project = session.scalar(
        select(Project).where(
            Project.id == project_id, sees(account), Project.deleted_at.is_not(None)
        )
    )
    if project is None:
        raise HTTPException(404, NOT_IN_TRASH)
    _refuse_viewer(account)
    about(project_id=project.id)
    return project


def get_trashed_folder(folder_id: int, session: SessionDep, account: AccountDep) -> Folder:
    folder = _in_organization(session, account, Folder, folder_id, trashed=True)
    if folder is None:
        raise HTTPException(404, NOT_IN_TRASH)
    _refuse_viewer(account)
    return folder


def get_trashed_document(document_id: int, session: SessionDep, account: AccountDep) -> Document:
    document = _in_organization(session, account, Document, document_id, trashed=True)
    if document is None:
        raise HTTPException(404, NOT_IN_TRASH)
    _refuse_viewer(account)
    about(document_id=document.id)
    return document


def get_trashed_chat(chat_id: int, session: SessionDep, account: AccountDep) -> Chat:
    """One of the account's own chats in the trash, which is theirs alone, shared or not."""
    chat = _in_organization(session, account, Chat, chat_id, trashed=True)
    if chat is None or chat.user_id != account.user.id:
        raise HTTPException(404, NOT_IN_TRASH)
    return chat


TrashedProjectDep = Annotated[Project, Depends(get_trashed_project)]
TrashedFolderDep = Annotated[Folder, Depends(get_trashed_folder)]
TrashedDocumentDep = Annotated[Document, Depends(get_trashed_document)]
TrashedChatDep = Annotated[Chat, Depends(get_trashed_chat)]
