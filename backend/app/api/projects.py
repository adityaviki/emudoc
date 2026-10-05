import shutil

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import ProjectDep, SessionDep, Stage, WorkerDep
from app.config import get_settings
from app.indexing.pipeline import reset_stages
from app.models import Document, Project
from app.schemas import ProjectIn, ProjectOut

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
def list_projects(session: SessionDep):
    rows = session.execute(
        select(Project, func.count(Document.id))
        .outerjoin(Document, Document.project_id == Project.id)
        .group_by(Project.id)
        .order_by(Project.created_at.desc(), Project.id.desc())
    ).all()
    return [_out(project, count) for project, count in rows]


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectIn, session: SessionDep):
    project = Project(name=body.name.strip(), description=body.description.strip())
    session.add(project)
    session.commit()
    return _out(project, 0)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project: ProjectDep, session: SessionDep):
    return _out(project, _document_count(session, project))


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project: ProjectDep, body: ProjectIn, session: SessionDep):
    project.name = body.name.strip()
    project.description = body.description.strip()
    session.commit()
    return _out(project, _document_count(session, project))


@router.delete("/{project_id}", status_code=204)
def delete_project(project: ProjectDep, session: SessionDep):
    settings = get_settings()
    document_ids = session.scalars(
        select(Document.id).where(Document.project_id == project.id)
    ).all()
    session.delete(project)
    session.commit()
    shutil.rmtree(settings.files_dir / str(project.id), ignore_errors=True)
    for document_id in document_ids:
        shutil.rmtree(settings.pages_dir / str(document_id), ignore_errors=True)


@router.post("/{project_id}/reprocess", status_code=202)
def reprocess_project(
    project: ProjectDep,
    session: SessionDep,
    worker: WorkerDep,
    from_stage: Stage = "enrich",
):
    """Queue every document in the project to run again from the given stage."""
    documents = session.scalars(
        select(Document).where(Document.project_id == project.id, Document.status != "processing")
    ).all()
    for document in documents:
        reset_stages(document, from_stage)
    session.commit()
    if worker:
        worker.notify()
    return {"queued": len(documents)}
