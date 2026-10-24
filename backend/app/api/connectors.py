"""A project's connected folders (app/connectors/): a folder of a member's Google Drive, Dropbox or
OneDrive whose files the project keeps in step with, which the library's "Connected folders"
shows.

Every route is behind the flag `connectors` (app/flags.py), and connecting one needs a plan that
includes them. A member who may change the project connects a service: the page posts which,
gets the service's consent page to send the browser to, with the handshake in a short-lived
cookie, and the service sends the browser back to the callback with a code, which is redeemed for
the member's grant, kept encrypted. The member then browses their own folders there and chooses
one, and where its files go in the project; the folder is chosen once, and another is another
connector. Whoever may change the project changes where the files go, asks for a sync now and
disconnects it; only its member browses their folders and connects it again once the service
took its access back. Everyone who sees the project reads how each stands. Each connector made,
changed and removed is a row of the audit log, which names the service and the folders.

A request that asks the service something holds no transaction while it waits.
"""

import base64
import json
import logging
from dataclasses import asdict, dataclass
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.accounts import Account
from app.api.auth import app_url
from app.api.deps import (
    AccountDep,
    DeletableProjectDep,
    EditableProjectDep,
    HttpDep,
    ProjectDep,
    SessionDep,
    flagged,
    get_editable_project,
    get_project,
)
from app.audit import audit
from app.config import get_settings
from app.connectors import LABELS, providers
from app.connectors.base import (
    CONNECT_COOKIE,
    CONNECT_WINDOW_SECONDS,
    CONSENT_FAILURES,
    ConnectorError,
    Provider,
)
from app.connectors.sync import SYNC_INTERVAL, label_of, queue_sync, sync_pending
from app.encrypted import CONNECTOR_TOKENS, decrypt, encrypt
from app.entitlements import PlanRefused, check_connectors
from app.errors import shown
from app.flags import CONNECTORS
from app.folders import folder_paths
from app.keyring import Undecryptable
from app.models import Connector, ConnectorFile, Document, Folder, Project, User
from app.schemas import (
    ConnectIn,
    ConnectorFileOut,
    ConnectorIn,
    ConnectorOut,
    ConnectorServiceOut,
    ConnectorsOut,
    ConsentOut,
    RemoteFolderOut,
    RemoteFoldersOut,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["connectors"], dependencies=[Depends(flagged(CONNECTORS))])

COOKIE_PATH = "/api/connectors"
MAX_CONNECTORS = 10
# How many of a connector's files that are no document it shows.
SHOWN = 20

NOT_FOUND = "Connected folder not found"
SAMPLE = (
    "The sample project shows what Emudoc does with documents of its own, so no folder can be "
    "connected to it. Connect one to a project of your own."
)
TOO_MANY = (
    f"A project may have {MAX_CONNECTORS} connected folders, and this one has as many. "
    "Disconnect one first."
)
NOT_YOURS = (
    "Only the member who connected this folder can choose it or connect it again, since the "
    "access is theirs."
)
CHOSEN = (
    "The folder was chosen already; to sync another, connect it as another folder, so that "
    "the documents of this one stay as they are."
)
NOT_CHOSEN = "Choose the folder first."
PAUSED = "The folder is paused. Its member can connect it again."
TAKEN = "This folder is connected to the project already."
FOLDER_GONE = "That folder is no longer there. Choose another."
NOT_A_FOLDER = "That is no folder of its own. Choose a folder within it."


@dataclass(frozen=True)
class Handshake:
    """What the app remembers between sending the browser to the service and its return."""

    provider: str
    state: str
    verifier: str
    org_id: int
    user_id: int
    project_id: int
    # The paused connector of the member's being connected again; 0 for a new one.
    connector_id: int = 0

    def encode(self) -> str:
        return base64.urlsafe_b64encode(json.dumps(asdict(self)).encode()).decode()

    @classmethod
    def decode(cls, value: str | None) -> "Handshake | None":
        if not value:
            return None
        try:
            return cls(**json.loads(base64.urlsafe_b64decode(value)))
        except (ValueError, TypeError):
            return None


@router.get("/projects/{project_id}/connectors", response_model=ConnectorsOut)
def list_connectors(project: ProjectDep, session: SessionDep, account: AccountDep):
    """The project's connected folders, and the services another may be connected from."""
    found = providers(get_settings())
    connectors = session.scalars(
        select(Connector).where(Connector.project_id == project.id).order_by(Connector.id)
    ).all()
    return ConnectorsOut(
        services=[ConnectorServiceOut(provider=name, label=p.label) for name, p in found.items()],
        connectors=[_out(session, project, connector, account) for connector in connectors],
        max_connectors=MAX_CONNECTORS,
        interval_minutes=int(SYNC_INTERVAL.total_seconds() // 60),
    )


@router.post("/projects/{project_id}/connectors", response_model=ConsentOut)
def connect(
    project: EditableProjectDep,
    body: ConnectIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
    response: Response,
):
    """Where to send the browser for the member's consent to read their files, the handshake
    kept in a cookie until it is back."""
    provider = _provider(body.provider)
    if project.sample:
        raise HTTPException(409, SAMPLE)
    if refusal := check_connectors(account.organization):
        raise PlanRefused(refusal)
    if body.connector_id is not None:
        connector = _connector(session, project, body.connector_id)
        if connector.user_id != account.user.id:
            raise HTTPException(403, NOT_YOURS)
        if connector.provider != provider.name:
            raise HTTPException(404, NOT_FOUND)
    elif _count(session, project) >= MAX_CONNECTORS:
        raise HTTPException(409, TOO_MANY)
    consent = provider.begin(_redirect_uri(request))
    handshake = Handshake(
        provider=provider.name,
        state=consent.state,
        verifier=consent.verifier,
        org_id=account.organization.id,
        user_id=account.user.id,
        project_id=project.id,
        connector_id=body.connector_id or 0,
    )
    response.set_cookie(
        CONNECT_COOKIE,
        handshake.encode(),
        max_age=CONNECT_WINDOW_SECONDS,
        httponly=True,
        samesite="lax",
        secure=auth.cookie_secure(request),
        path=COOKIE_PATH,
    )
    return ConsentOut(url=consent.url)


@router.get("/connectors/callback")
def connector_callback(
    request: Request,
    session: SessionDep,
    account: AccountDep,
    http: HttpDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Back from the service: redeem the code for the member's grant, keep it encrypted, and send
    the browser to the project's connected folders, where its folder is chosen."""
    handshake = Handshake.decode(request.cookies.get(CONNECT_COOKIE))
    if handshake is None or state != handshake.state:
        return _back(None, "expired")
    if handshake.user_id != account.user.id or handshake.org_id != account.organization.id:
        return _back(None, "expired")
    if error:
        return _back(handshake.project_id, "cancelled")
    if not code:
        return _back(handshake.project_id, "expired")
    provider = handshake.provider
    found = _provider(provider)
    project = _still_editable(handshake.project_id, session, account)
    if project is None:
        return _back(handshake.project_id, "not_allowed")
    # The service is asked outside any transaction.
    session.commit()
    try:
        grant = found.finish(http, code, _redirect_uri(request), handshake.verifier)
    except ConnectorError as failure:
        log.warning("Connecting %s to project %s failed: %s", provider, project.id, failure)
        return _back(project.id, failure.code)
    if handshake.connector_id:
        connector = session.get(Connector, handshake.connector_id)
        if (
            connector is None
            or connector.project_id != project.id
            or connector.user_id != account.user.id
            or connector.provider != found.name
        ):
            return _back(project.id, "expired")
        connector.refresh_token = encrypt(CONNECTOR_TOKENS, connector.id, grant.refresh_token)
        connector.account = grant.account
        connector.problem = ""
        if connector.folder_ref is not None:
            connector.status = "active"
            if not sync_pending(session, connector.id):
                queue_sync(session, connector, now=True)
        audit(
            session,
            account,
            request,
            "connector_changed",
            project_id=project.id,
            target=("project", project.id),
            detail={"provider": provider, "changed": ["grant"], "folder": connector.folder_name},
        )
    else:
        connector = Connector(
            org_id=project.org_id,
            project_id=project.id,
            provider=found.name,
            user_id=account.user.id,
            account=grant.account,
            refresh_token="",
            status="choosing",
        )
        session.add(connector)
        session.flush()
        connector.refresh_token = encrypt(CONNECTOR_TOKENS, connector.id, grant.refresh_token)
        audit(
            session,
            account,
            request,
            "connector_created",
            project_id=project.id,
            target=("project", project.id),
            detail={"provider": provider},
        )
    session.commit()
    return _back(project.id)


@router.get(
    "/projects/{project_id}/connectors/{connector_id}/folders", response_model=RemoteFoldersOut
)
def remote_folders(
    project: EditableProjectDep,
    connector_id: int,
    session: SessionDep,
    account: AccountDep,
    http: HttpDep,
    parent: Annotated[str | None, Query(max_length=300)] = None,
):
    """The folders in one of the member's folders at the service, or where browsing starts:
    for choosing the connector's folder, which only its member may."""
    connector = _connector(session, project, connector_id)
    if connector.user_id != account.user.id:
        raise HTTPException(403, NOT_YOURS)
    provider, token = _access(session, connector, http)
    try:
        folders = provider.folders(http, token, parent) if parent else provider.tops(http, token)
    except ConnectorError as failure:
        raise _refused(failure) from failure
    return RemoteFoldersOut(
        folders=[
            RemoteFolderOut(ref=folder.ref, name=folder.name, choosable=folder.choosable)
            for folder in folders
        ]
    )


@router.put("/projects/{project_id}/connectors/{connector_id}", response_model=ConnectorOut)
def change_connector(
    project: EditableProjectDep,
    connector_id: int,
    body: ConnectorIn,
    session: SessionDep,
    account: AccountDep,
    http: HttpDep,
    request: Request,
):
    """Choose the connector's folder at the service, while it is being chosen, and where its
    files go in the project; the first sync is queued once the folder is chosen."""
    connector = _connector(session, project, connector_id)
    if body.target_folder_id is not None:
        folder = session.get(Folder, body.target_folder_id)
        if folder is None or folder.project_id != project.id or folder.deleted_at is not None:
            raise HTTPException(404, "Folder not found")
    chosen = None
    if body.folder_ref is not None and body.folder_ref != connector.folder_ref:
        if connector.status != "choosing":
            raise HTTPException(409, CHOSEN)
        if connector.user_id != account.user.id:
            raise HTTPException(403, NOT_YOURS)
        if refusal := check_connectors(account.organization):
            raise PlanRefused(refusal)
        taken = session.scalar(
            select(Connector.id).where(
                Connector.project_id == project.id,
                Connector.provider == connector.provider,
                Connector.folder_ref == body.folder_ref,
            )
        )
        if taken is not None:
            raise HTTPException(409, TAKEN)
        provider, token = _access(session, connector, http)
        try:
            chosen = provider.folder(http, token, body.folder_ref)
        except ConnectorError as failure:
            raise _refused(failure) from failure
        if chosen is None:
            raise HTTPException(404, FOLDER_GONE)
        if not chosen.choosable:
            raise HTTPException(400, NOT_A_FOLDER)
    elif connector.status == "choosing":
        raise HTTPException(409, NOT_CHOSEN)
    connector = _connector(session, project, connector_id, lock=True)
    paths = folder_paths(session, project.id)
    changed = []
    if chosen is not None:
        connector.folder_ref = chosen.ref
        connector.folder_name = chosen.name[:300]
        connector.status = "active"
        changed.append("folder")
        queue_sync(session, connector, now=True)
    if body.target_folder_id != connector.target_folder_id:
        connector.target_folder_id = body.target_folder_id
        changed.append("target")
    if changed:
        audit(
            session,
            account,
            request,
            "connector_changed",
            project_id=project.id,
            target=("project", project.id),
            detail={
                "provider": connector.provider,
                "changed": changed,
                "folder": connector.folder_name,
                "target": paths.get(body.target_folder_id or -1),
            },
        )
    session.commit()
    return _out(session, project, connector, account)


@router.post(
    "/projects/{project_id}/connectors/{connector_id}/sync",
    response_model=ConnectorOut,
    status_code=202,
)
def sync_now(
    project: EditableProjectDep, connector_id: int, session: SessionDep, account: AccountDep
):
    """Sync the folder now rather than at its interval."""
    connector = _connector(session, project, connector_id, lock=True)
    if connector.status == "choosing":
        raise HTTPException(409, NOT_CHOSEN)
    if connector.status == "paused":
        raise HTTPException(409, PAUSED)
    if refusal := check_connectors(account.organization):
        raise PlanRefused(refusal)
    if not sync_pending(session, connector.id):
        queue_sync(session, connector, now=True)
    session.commit()
    return _out(session, project, connector, account)


@router.delete("/projects/{project_id}/connectors/{connector_id}", status_code=204)
def disconnect(
    project: DeletableProjectDep,
    connector_id: int,
    session: SessionDep,
    account: AccountDep,
    http: HttpDep,
    request: Request,
):
    """Disconnect the folder: it is synced no more, its documents stay in the library, and the
    member's grant is given back to the service where it takes it back."""
    connector = _connector(session, project, connector_id, lock=True)
    provider = providers(get_settings()).get(connector.provider)
    try:
        grant = decrypt(CONNECTOR_TOKENS, connector.id, connector.refresh_token)
    except Undecryptable:
        grant = None
    audit(
        session,
        account,
        request,
        "connector_deleted",
        project_id=project.id,
        target=("project", project.id),
        detail={"provider": connector.provider, "folder": connector.folder_name},
    )
    session.delete(connector)
    session.commit()
    if provider is not None and grant:
        try:
            provider.revoke(http, grant)
        except Exception:
            # The grant lapses at the service in its own time; the connector is gone either way.
            log.warning(
                "%s did not take back the grant of connector %s", provider.label, connector_id
            )


def _provider(name: str) -> Provider:
    found = providers(get_settings()).get(name)
    if found is None:
        raise HTTPException(404, f"{LABELS.get(name, 'That service')} is not set up here.")
    return found


def _redirect_uri(request: Request) -> str:
    """Where every service sends the browser back to, the one URI each client registers; the
    handshake says which service it is."""
    return f"{app_url(request)}/api/connectors/callback"


def _back(project_id: int | None, failure: str = "") -> RedirectResponse:
    """The browser back to the project's connected folders, with why nothing was connected."""
    assert not failure or failure in CONSENT_FAILURES
    page = f"/projects/{project_id}" if project_id is not None else "/"
    params = {"connectors": ""} if project_id is not None else {}
    if failure:
        params["connect_error"] = failure
    query = urlencode(params)
    response = RedirectResponse(f"{page}?{query}" if query else page, status_code=302)
    response.delete_cookie(CONNECT_COOKIE, path=COOKIE_PATH)
    return response


def _still_editable(project_id: int, session: Session, account: Account) -> Project | None:
    """The project, if the member still may change it and its plan still includes connected
    folders."""
    try:
        project = get_editable_project(get_project(project_id, session, account), account, session)
    except (HTTPException, PlanRefused):
        return None
    if project.sample or check_connectors(account.organization):
        return None
    return project


def _connector(
    session: Session, project: Project, connector_id: int, lock: bool = False
) -> Connector:
    statement = select(Connector).where(
        Connector.id == connector_id, Connector.project_id == project.id
    )
    connector = session.scalar(statement.with_for_update() if lock else statement)
    if connector is None:
        raise HTTPException(404, NOT_FOUND)
    return connector


def _count(session: Session, project: Project) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(Connector).where(Connector.project_id == project.id)
        )
        or 0
    )


def _access(session: Session, connector: Connector, http) -> tuple[Provider, str]:
    """The service and an access token bought with the member's grant, keeping a refresh token
    the service gives in its place; the transaction ends before the service is asked."""
    provider = providers(get_settings()).get(connector.provider)
    if provider is None:
        raise HTTPException(409, f"{label_of(connector)} is not set up here any more.")
    try:
        grant = decrypt(CONNECTOR_TOKENS, connector.id, connector.refresh_token)
    except Undecryptable as error:
        raise HTTPException(409, PAUSED) from error
    connector_id = connector.id
    session.commit()
    try:
        access = provider.access(http, grant)
    except ConnectorError as failure:
        if failure.revoked:
            found = session.get(Connector, connector_id)
            if found is not None:
                found.status = "paused"
                found.problem = shown(failure, development=_development())
                session.commit()
        raise _refused(failure) from failure
    if access.refresh_token and access.refresh_token != grant:
        found = session.get(Connector, connector_id)
        if found is not None:
            found.refresh_token = encrypt(CONNECTOR_TOKENS, connector_id, access.refresh_token)
            session.commit()
    return provider, access.token


def _refused(failure: ConnectorError) -> HTTPException:
    status = 503 if failure.transient else 409 if failure.revoked else 502
    return HTTPException(status, shown(failure, development=_development()))


def _development() -> bool:
    return get_settings().environment == "development"


def _out(
    session: Session, project: Project, connector: Connector, account: Account
) -> ConnectorOut:
    target = None
    if connector.target_folder_id is not None:
        target = folder_paths(session, project.id).get(connector.target_folder_id)
    member = session.get(User, connector.user_id)
    kept = session.scalar(
        select(func.count())
        .select_from(ConnectorFile)
        .join(Document, Document.id == ConnectorFile.document_id)
        .where(ConnectorFile.connector_id == connector.id, Document.deleted_at.is_(None))
    )
    not_kept = select(ConnectorFile).where(
        ConnectorFile.connector_id == connector.id, ConnectorFile.reason != ""
    )
    rejected = session.scalars(not_kept.order_by(ConnectorFile.path).limit(SHOWN)).all()
    return ConnectorOut(
        id=connector.id,
        provider=connector.provider,
        label=label_of(connector),
        account=connector.account,
        member=(member.name if member else "") or "a member",
        mine=connector.user_id == account.user.id,
        status=connector.status,
        folder=connector.folder_name if connector.folder_ref is not None else None,
        target_folder_id=connector.target_folder_id if target is not None else None,
        target=target,
        problem=connector.problem,
        synced_at=connector.synced_at,
        next_sync_at=connector.next_sync_at if connector.status == "active" else None,
        syncing=sync_pending(session, connector.id),
        kept=kept or 0,
        rejected=[ConnectorFileOut(path=row.path, reason=row.reason) for row in rejected],
        rejected_count=session.scalar(select(func.count()).select_from(not_kept.subquery())) or 0,
    )
