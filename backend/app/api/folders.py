"""Folders: the tree of a project's library. A document sits in one folder or at the root."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    AccountDep,
    DeletableFolderDep,
    EditableFolderDep,
    EditableProjectDep,
    ProjectDep,
    ServicesDep,
    SessionDep,
)
from app.audit import audit
from app.folders import PATH_SEPARATOR, folder_paths, subtree_ids
from app.models import Document, Folder, Project
from app.schemas import FolderIn, FolderOut, FolderPatch

router = APIRouter(tags=["folders"])

__all__ = ["folder_paths", "resolve_folder", "router", "subtree_ids"]


@router.get("/projects/{project_id}/folders", response_model=list[FolderOut])
def list_folders(project: ProjectDep, session: SessionDep):
    """Every folder of the project, flat, with how many documents each holds itself; the
    client builds the tree from parent_id."""
    held = (
        select(Document.folder_id, func.count().label("documents"))
        .where(Document.project_id == project.id, Document.folder_id.is_not(None))
        .group_by(Document.folder_id)
        .subquery()
    )
    rows = session.execute(
        select(Folder, held.c.documents)
        .outerjoin(held, held.c.folder_id == Folder.id)
        .where(Folder.project_id == project.id)
        .order_by(Folder.name, Folder.id)
    ).all()
    return [_out(folder, count or 0) for folder, count in rows]


@router.post("/projects/{project_id}/folders", response_model=FolderOut, status_code=201)
def create_folder(project: EditableProjectDep, body: FolderIn, session: SessionDep):
    parent = resolve_folder(session, project, body.parent_id)
    name = clean_name(body.name)
    check_folder_name(session, project.id, parent.id if parent else None, name)
    folder = Folder(project_id=project.id, parent_id=parent.id if parent else None, name=name)
    session.add(folder)
    session.commit()
    return folder


@router.patch("/folders/{folder_id}", response_model=FolderOut)
def update_folder(folder: EditableFolderDep, body: FolderPatch, session: SessionDep):
    """Rename the folder, move it, or both."""
    project = session.get(Project, folder.project_id)
    parent_id = folder.parent_id
    if "parent_id" in body.model_fields_set:
        parent = resolve_folder(session, project, body.parent_id)
        parent_id = parent.id if parent else None
        if parent_id in subtree_ids(session, folder):
            raise HTTPException(409, "A folder cannot be moved into itself")
    name = clean_name(body.name) if body.name is not None else folder.name
    check_folder_name(session, project.id, parent_id, name, exclude_id=folder.id)
    folder.parent_id = parent_id
    folder.name = name
    session.commit()
    count = session.scalar(
        select(func.count()).select_from(Document).where(Document.folder_id == folder.id)
    )
    return _out(folder, count or 0)


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(
    folder: DeletableFolderDep,
    session: SessionDep,
    services: ServicesDep,
    account: AccountDep,
    request: Request,
):
    """Delete the folder with its subfolders and every document in them."""
    documents = session.scalars(
        select(Document).where(Document.folder_id.in_(subtree_ids(session, folder)))
    ).all()
    audit(
        session,
        account,
        request,
        "folder_deleted",
        project_id=folder.project_id,
        target=("folder", folder.id),
        detail={"name": folder.name, "documents": len(documents)},
    )
    session.delete(folder)
    session.commit()
    services.storage.discard(*(document.storage_prefix for document in documents))


def _out(folder: Folder, document_count: int) -> FolderOut:
    return FolderOut.model_validate(folder).model_copy(update={"document_count": document_count})


def clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name or PATH_SEPARATOR in name:
        raise HTTPException(422, "A folder name cannot be empty or contain '/'")
    return name


def resolve_folder(session: Session, project: Project, folder_id: int | None) -> Folder | None:
    """The folder with this id in the project, or None for the root."""
    if folder_id is None:
        return None
    folder = session.get(Folder, folder_id)
    if folder is None or folder.project_id != project.id:
        raise HTTPException(404, "Folder not found")
    return folder


def check_folder_name(
    session: Session,
    project_id: int,
    parent_id: int | None,
    name: str,
    exclude_id: int | None = None,
) -> None:
    """Sibling folders need distinct names, whatever their case."""
    query = select(Folder.id).where(
        Folder.project_id == project_id,
        Folder.parent_id == parent_id,
        func.lower(Folder.name) == name.lower(),
    )
    if exclude_id is not None:
        query = query.where(Folder.id != exclude_id)
    if session.scalar(query) is not None:
        raise HTTPException(409, f"A folder named {name!r} already exists here")
