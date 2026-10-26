"""An organisation's projects: making, listing, renaming and deleting them into the trash
(app/trash.py), indexing a whole one again, and who sees one (app/access.py), which whoever may
change it chooses."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import and_, func, select

from app.access import access_refusal, chosen_members, set_access, visible_to
from app.api.deps import (
    AccountDep,
    DeletableProjectDep,
    EditableProjectDep,
    EditorDep,
    ProjectDep,
    SessionDep,
    WorkerDep,
)
from app.audit import audit
from app.entitlements import PlanRefused, check_project
from app.indexing.pipeline import OPTIONAL_STAGES, reset_stages, unfinished_stages, went_without
from app.jobs import BULK, queue_index
from app.models import Document, Project, ProjectMember
from app.schemas import (
    NewProjectIn,
    ProjectAccessIn,
    ProjectAccessOut,
    ProjectIn,
    ProjectOut,
    Stage,
)
from app.trash import trash_project

router = APIRouter(prefix="/projects", tags=["projects"])


# The documents a project holds out of the trash, those that are partly indexed (ready without
# an optional stage, app/indexing/pipeline.py), and those of them without summaries and facts,
# which need the model to be indexed in full.
_COUNTS = (
    func.count(Document.id),
    func.count(Document.id).filter(went_without(*OPTIONAL_STAGES)),
    func.count(Document.id).filter(went_without("enrich")),
)
_LIVE = Document.deleted_at.is_(None)


def _out(
    project: Project, document_count: int, partly_indexed_count: int = 0, unenriched_count: int = 0
) -> ProjectOut:
    return ProjectOut.model_validate(project).model_copy(
        update={
            "document_count": document_count,
            "partly_indexed_count": partly_indexed_count,
            "unenriched_count": unenriched_count,
        }
    )


def project_out(session: SessionDep, project: Project) -> ProjectOut:
    """The project with its counts, as every route that answers with one gives it."""
    counts = session.execute(select(*_COUNTS).where(Document.project_id == project.id, _LIVE)).one()
    return _out(project, *counts)


def document_count(session: SessionDep, project: Project) -> int:
    """How many documents the project holds out of the trash."""
    return (
        session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.project_id == project.id, _LIVE)
        )
        or 0
    )


@router.get("", response_model=list[ProjectOut])
def list_projects(session: SessionDep, account: AccountDep):
    """The organisation's projects the account sees, the newest first."""
    rows = session.execute(
        select(Project, *_COUNTS)
        .outerjoin(Document, and_(Document.project_id == Project.id, _LIVE))
        .where(visible_to(account))
        .group_by(Project.id)
        .order_by(Project.created_at.desc(), Project.id.desc())
    ).all()
    return [_out(project, *counts) for project, *counts in rows]


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: NewProjectIn, session: SessionDep, account: EditorDep):
    """Make a project, open to everyone in the organisation or private to its maker."""
    if refusal := check_project(session, account.organization):
        raise PlanRefused(refusal)
    project = Project(
        org_id=account.organization.id,
        name=body.name.strip(),
        description=body.description.strip(),
        visibility=body.visibility,
    )
    session.add(project)
    session.flush()
    if body.visibility == "private":
        session.add(ProjectMember(project_id=project.id, user_id=account.user.id))
    session.commit()
    return _out(project, 0)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project: ProjectDep, session: SessionDep):
    return project_out(session, project)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project: EditableProjectDep, body: ProjectIn, session: SessionDep):
    project.name = body.name.strip()
    project.description = body.description.strip()
    session.commit()
    return project_out(session, project)


@router.get("/{project_id}/access", response_model=ProjectAccessOut)
def get_access(project: ProjectDep, session: SessionDep):
    """Who sees the project besides the organisation's owners and admins."""
    return ProjectAccessOut(
        visibility=project.visibility, user_ids=chosen_members(session, project)
    )


@router.put("/{project_id}/access", response_model=ProjectAccessOut)
def change_access(
    project: EditableProjectDep,
    body: ProjectAccessIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Open the project to everyone in the organisation, or keep it to the members chosen."""
    if refusal := access_refusal(session, account, body.visibility, body.user_ids):
        raise HTTPException(400, refusal)
    change = set_access(session, project, body.visibility, body.user_ids)
    if change.added or change.removed or change.visibility != body.visibility:
        audit(
            session,
            account,
            request,
            "project_access_changed",
            project_id=project.id,
            target=("project", project.id),
            detail={
                "name": project.name,
                "from": change.visibility,
                "to": body.visibility,
                "added": change.added,
                "removed": change.removed,
            },
        )
    session.commit()
    return get_access(project, session)


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project: DeletableProjectDep, session: SessionDep, account: AccountDep, request: Request
):
    """Put the project in the organisation's trash with everything in it, until a member
    restores it or deletes it for good, or the housekeeper does TRASH_DAYS later."""
    audit(
        session,
        account,
        request,
        "project_deleted",
        project_id=project.id,
        target=("project", project.id),
        detail={"name": project.name, "documents": document_count(session, project)},
    )
    trash_project(session, project, account.user.id)
    session.commit()


@router.post("/{project_id}/reprocess", status_code=202)
def reprocess_project(
    project: EditableProjectDep,
    session: SessionDep,
    worker: WorkerDep,
    from_stage: Stage | None = None,
):
    """Queue the project's documents to run again. Without a stage, the partly indexed ones,
    each from the first stage it went without ("Index the rest" in the library), which spends
    nothing on what is indexed already; with one, every document from that stage. A quarantined
    document is left alone: the run would only scan it again."""
    which = (
        went_without(*OPTIONAL_STAGES)
        if from_stage is None
        else Document.status.not_in(("processing", "quarantined"))
    )
    documents = session.scalars(
        select(Document).where(Document.project_id == project.id, _LIVE, which)
    ).all()
    for document in documents:
        reset_stages(document, from_stage or unfinished_stages(document)[0])
        # Behind fresh uploads, which someone is waiting for.
        queue_index(session, document, priority=BULK)
    session.commit()
    if worker:
        worker.notify()
    return {"queued": len(documents)}
