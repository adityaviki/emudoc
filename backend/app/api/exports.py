"""A project's exports: a member asks for one, the worker makes it (app/export.py), and the member
lists theirs and downloads each until it expires.

An export holds every document the project holds, with what was read from it and the chats the
member may read, their private ones among them, or the documents the member chose in the library
alone (app/selection.py), without the chats; so it is the member's alone, and only a member or
better asks for one: a viewer reads the documents one by one, which an organisation that gives
someone a viewer's role may mean as the limit of what they take away. Asking reads
nothing the member could not read, so an organisation past its plan or set to be deleted may
ask, the latter so that its members can take what they need. One is made at a time for a member
and a project. Asking is in the audit log; the file is served as the originals are, an
attachment the browser keeps nothing of.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import ColumnElement, or_, select
from starlette.responses import Response

from app.api.deps import AccountDep, ProjectDep, ServicesDep, SessionDep
from app.audit import audit
from app.export import EXPORT_DAYS, queue_export
from app.models import ProjectExport
from app.schemas import ProjectExportIn, ProjectExportOut
from app.selection import NOT_CHOSEN, chosen_documents
from app.storage import export_key

router = APIRouter(prefix="/projects", tags=["exports"])

MEMBERS_EXPORT = (
    "Your role in this workspace is viewer, which reads a project's documents one at a time. "
    "Ask an owner or admin to make you a member to export them together."
)
UNDER_WAY = (
    "An export of this project is being made for you already. It is listed here when it is "
    "ready, and you are told."
)
EXPORT_GONE = (
    f"That export is not there to download: it is still being made, it failed, or it expired "
    f"{EXPORT_DAYS} days after it was made. Make another."
)


@router.post("/{project_id}/exports", response_model=ProjectExportOut, status_code=202)
def export_project(
    project: ProjectDep,
    account: AccountDep,
    request: Request,
    session: SessionDep,
    body: ProjectExportIn | None = None,
):
    """Ask for an export of the project, or of the documents chosen in it, which the worker
    makes and tells the member of."""
    if not account.is_at_least("member"):
        raise HTTPException(403, MEMBERS_EXPORT)
    under_way = session.scalar(
        select(ProjectExport.id).where(
            ProjectExport.project_id == project.id,
            ProjectExport.user_id == account.user.id,
            ProjectExport.status == "queued",
        )
    )
    if under_way is not None:
        raise HTTPException(409, UNDER_WAY)
    chosen = None
    if body is not None and body.document_ids is not None:
        documents = chosen_documents(session, project.id, body.document_ids)
        if documents is None:
            raise HTTPException(404, NOT_CHOSEN)
        chosen = [document.id for document in documents]
    export = queue_export(session, project, account.user.id, chosen)
    audit(
        session,
        account,
        request,
        "project_exported",
        project_id=project.id,
        target=("project", project.id),
        detail={"name": project.name, **({} if chosen is None else {"documents": len(chosen)})},
    )
    session.commit()
    return export


@router.get("/{project_id}/exports", response_model=list[ProjectExportOut])
def list_exports(project: ProjectDep, account: AccountDep, session: SessionDep):
    """The member's exports of the project that have not expired, the newest first."""
    return session.scalars(
        select(ProjectExport)
        .where(
            ProjectExport.project_id == project.id,
            ProjectExport.user_id == account.user.id,
            _unexpired(),
        )
        .order_by(ProjectExport.id.desc())
    ).all()


@router.get("/{project_id}/exports/{export_id}/file")
def download_export(
    project: ProjectDep,
    export_id: int,
    account: AccountDep,
    session: SessionDep,
    services: ServicesDep,
) -> Response:
    """The export's ZIP file, while it is ready and has not expired."""
    export = session.scalar(
        select(ProjectExport).where(
            ProjectExport.id == export_id,
            ProjectExport.project_id == project.id,
            ProjectExport.user_id == account.user.id,
            ProjectExport.status == "ready",
            _unexpired(),
        )
    )
    if export is None:
        raise HTTPException(404, EXPORT_GONE)
    named = f"{_filename(project.name)} {export.created_at:%Y-%m-%d}"
    if export.chosen is not None:
        named += f" ({export.chosen} document{'' if export.chosen == 1 else 's'})"
    named += ".zip"
    try:
        return services.storage.response(
            export_key(project.org_id, project.id, export.id),
            media_type="application/zip",
            filename=named,
        )
    except FileNotFoundError:
        raise HTTPException(404, EXPORT_GONE) from None


def _unexpired() -> ColumnElement[bool]:
    """Not expired: still being made, failed, or ready and within its days."""
    return or_(ProjectExport.expires_at.is_(None), ProjectExport.expires_at > datetime.now(UTC))


def _filename(name: str) -> str:
    """The project's name as a file's: without what a file system would refuse in one."""
    return "".join("-" if char in '/\\:*?"<>|' else char for char in name).strip() or "project"
