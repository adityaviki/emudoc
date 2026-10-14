from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import (
    AccountDep,
    EditableProjectDep,
    EditorDep,
    ProjectDep,
    ServicesDep,
    SessionDep,
    Stage,
    WorkerDep,
)
from app.entitlements import check_project
from app.indexing.pipeline import reset_stages
from app.jobs import BULK, queue_index
from app.models import Document, Project
from app.schemas import ProjectIn, ProjectOut
from app.storage import project_prefix

router = APIRouter(prefix="/projects", tags=["projects"])


def _out(project: Project, document_count: int) -> ProjectOut:
    return ProjectOut.model_validate(project).model_copy(update={"document_count": document_count})


def _document_count(session: SessionDep, project: Project) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(Document).where(Document.project_id == project.id)
        )
        or 0
    )


@router.get("", response_model=list[ProjectOut])
def list_projects(session: SessionDep, account: AccountDep):
    rows = session.execute(
        select(Project, func.count(Document.id))
        .outerjoin(Document, Document.project_id == Project.id)
        .where(Project.org_id == account.organization.id)
        .group_by(Project.id)
        .order_by(Project.created_at.desc(), Project.id.desc())
    ).all()
    return [_out(project, count) for project, count in rows]


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectIn, session: SessionDep, account: EditorDep):
    if reason := check_project(session, account.organization):
        raise HTTPException(402, reason)
    project = Project(
        org_id=account.organization.id,
        name=body.name.strip(),
        description=body.description.strip(),
    )
    session.add(project)
    session.commit()
    return _out(project, 0)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project: ProjectDep, session: SessionDep):
    return _out(project, _document_count(session, project))


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project: EditableProjectDep, body: ProjectIn, session: SessionDep):
    project.name = body.name.strip()
    project.description = body.description.strip()
    session.commit()
    return _out(project, _document_count(session, project))


@router.delete("/{project_id}", status_code=204)
def delete_project(project: EditableProjectDep, session: SessionDep, services: ServicesDep):
    session.delete(project)
    session.commit()
    # Every file of every document in the project sits under the project's prefix.
    services.storage.discard(project_prefix(project.org_id, project.id))


@router.post("/{project_id}/reprocess", status_code=202)
def reprocess_project(
    project: EditableProjectDep,
    session: SessionDep,
    worker: WorkerDep,
    from_stage: Stage = "enrich",
):
    """Queue every document in the project to run again from the given stage. A quarantined
    document is left alone: the run would only scan it again."""
    documents = session.scalars(
        select(Document).where(
            Document.project_id == project.id,
            Document.status.not_in(("processing", "quarantined")),
        )
    ).all()
    for document in documents:
        reset_stages(document, from_stage)
        # Behind fresh uploads, which someone is waiting for.
        queue_index(session, document, priority=BULK)
    session.commit()
    if worker:
        worker.notify()
    return {"queued": len(documents)}
