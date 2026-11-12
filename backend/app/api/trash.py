"""The trash's routes (app/trash.py): what it holds, a restore, and deleting for good.

The organisation's trash holds its projects, a project's trash its folders and documents and
the asker's own chats; each is reached through the account like what is live (app/api/deps.py),
so another organisation's is not found, nor a private project's by a member not chosen for it.
Anyone who sees a project reads its trash; a member or better restores from it and deletes for
good, which removes the files. A chat is its starter's to restore or
delete for good, whatever their role.
"""

from fastapi import APIRouter, Request

from app.access import sees
from app.api.chats import as_read, pinned_by
from app.api.deps import (
    AccountDep,
    EditableProjectDep,
    EditorDep,
    ProjectDep,
    ServicesDep,
    SessionDep,
    TrashedChatDep,
    TrashedDocumentDep,
    TrashedFolderDep,
    TrashedProjectDep,
    WorkerDep,
)
from app.api.folders import held_by
from app.api.projects import document_count, project_out
from app.audit import audit
from app.schemas import (
    ChatOut,
    DocumentOut,
    FolderOut,
    ProjectOut,
    ProjectTrashOut,
    TrashedOut,
    TrashedProjectOut,
    TrashOut,
)
from app.trash import (
    TRASH_DAYS,
    TRASH_KEPT,
    empty_trash,
    purge_chat,
    purge_document,
    purge_folder,
    purge_project,
    restore_chat,
    restore_document,
    restore_folder,
    restore_project,
    trash_of,
    trashed_projects,
    went_with,
)

router = APIRouter(tags=["trash"])


@router.get("/trash", response_model=TrashOut)
def organization_trash(session: SessionDep, account: AccountDep):
    """The organisation's projects in the trash that the account may see."""
    return TrashOut(
        projects=[
            TrashedProjectOut(
                id=project.id,
                name=project.name,
                sample=project.sample,
                document_count=held,
                deleted_at=project.deleted_at,
                deleted_by=by,
                purged_at=project.deleted_at + TRASH_KEPT,
            )
            for project, by, held in trashed_projects(session, sees(account))
        ],
        days=TRASH_DAYS,
    )


@router.post("/trash/projects/{project_id}/restore", response_model=ProjectOut)
def restore_trashed_project(
    project: TrashedProjectDep,
    session: SessionDep,
    account: EditorDep,
    worker: WorkerDep,
    request: Request,
):
    """Take the project out of the trash with everything in it."""
    restore_project(session, project)
    audit(
        session,
        account,
        request,
        "project_restored",
        project_id=project.id,
        target=("project", project.id),
        detail={"name": project.name, "documents": document_count(session, project)},
    )
    session.commit()
    if worker:
        worker.notify()
    return project_out(session, project)


@router.delete("/trash/projects/{project_id}", status_code=204)
def purge_trashed_project(
    project: TrashedProjectDep,
    session: SessionDep,
    services: ServicesDep,
    account: AccountDep,
    request: Request,
):
    """Delete the project in the trash for good, with everything in it and every file."""
    audit(
        session,
        account,
        request,
        "project_purged",
        project_id=project.id,
        target=("project", project.id),
        detail={"name": project.name, "documents": document_count(session, project)},
    )
    prefixes = purge_project(session, project)
    session.commit()
    services.storage.discard(*prefixes)


@router.get("/projects/{project_id}/trash", response_model=ProjectTrashOut)
def project_trash(project: ProjectDep, session: SessionDep, account: AccountDep):
    """The newest of what the project's trash holds: its folders and documents, and the asker's
    own chats."""
    items, total = trash_of(session, project, account.user.id)
    return ProjectTrashOut(
        items=[
            TrashedOut(
                kind=item.kind,
                id=item.id,
                name=item.name,
                place=item.place,
                documents=item.documents,
                mime_type=item.mime_type,
                deleted_at=item.deleted_at,
                deleted_by=item.deleted_by,
                purged_at=item.purged_at,
            )
            for item in items
        ],
        total=total,
        days=TRASH_DAYS,
    )


@router.delete("/projects/{project_id}/trash", status_code=204)
def empty_project_trash(
    project: EditableProjectDep,
    session: SessionDep,
    services: ServicesDep,
    account: AccountDep,
    request: Request,
):
    """Delete for good everything in the project's trash, the asker's own chats among it."""
    purged = empty_trash(session, project, account.user.id)
    for folder in purged.folders:
        _audit_purge(session, account, request, folder.project_id, "folder", folder.id, folder.name)
    for document in purged.documents:
        _audit_purge(
            session,
            account,
            request,
            document.project_id,
            "document",
            document.id,
            document.filename,
        )
    for chat in purged.chats:
        _audit_purge(session, account, request, chat.project_id, "chat", chat.id)
    session.commit()
    services.storage.discard(*purged.prefixes)


@router.post("/trash/folders/{folder_id}/restore", response_model=FolderOut)
def restore_trashed_folder(
    folder: TrashedFolderDep,
    session: SessionDep,
    account: EditorDep,
    worker: WorkerDep,
    request: Request,
):
    """Take the folder out of the trash with what went with it, into its place or the nearest
    live folder above it, renamed when a live folder there took its name meanwhile."""
    ids, documents = went_with(session, folder)
    restore_folder(session, folder, ids, documents)
    audit(
        session,
        account,
        request,
        "folder_restored",
        project_id=folder.project_id,
        target=("folder", folder.id),
        detail={"name": folder.name, "documents": len(documents)},
    )
    session.commit()
    if worker:
        worker.notify()
    return FolderOut.model_validate(folder).model_copy(
        update={"document_count": held_by(session, folder)}
    )


@router.delete("/trash/folders/{folder_id}", status_code=204)
def purge_trashed_folder(
    folder: TrashedFolderDep,
    session: SessionDep,
    services: ServicesDep,
    account: AccountDep,
    request: Request,
):
    """Delete the folder in the trash for good, with everything under it and every file."""
    name, project_id = folder.name, folder.project_id
    prefixes = purge_folder(session, folder)
    _audit_purge(
        session, account, request, project_id, "folder", folder.id, name, documents=len(prefixes)
    )
    session.commit()
    services.storage.discard(*prefixes)


@router.post("/trash/documents/{document_id}/restore", response_model=DocumentOut)
def restore_trashed_document(
    document: TrashedDocumentDep,
    session: SessionDep,
    account: EditorDep,
    worker: WorkerDep,
    request: Request,
):
    """Take the document out of the trash, into its folder or the nearest live one above it,
    renamed when a live file there took its name meanwhile."""
    restore_document(session, document)
    audit(
        session,
        account,
        request,
        "document_restored",
        project_id=document.project_id,
        target=("document", document.id),
        detail={"filename": document.filename},
    )
    session.commit()
    if worker:
        worker.notify()
    return document


@router.delete("/trash/documents/{document_id}", status_code=204)
def purge_trashed_document(
    document: TrashedDocumentDep,
    session: SessionDep,
    services: ServicesDep,
    account: AccountDep,
    request: Request,
):
    """Delete the document in the trash for good, with its files."""
    _audit_purge(
        session,
        account,
        request,
        document.project_id,
        "document",
        document.id,
        document.filename,
    )
    prefixes = purge_document(session, document)
    session.commit()
    services.storage.discard(*prefixes)


@router.post("/trash/chats/{chat_id}/restore", response_model=ChatOut)
def restore_trashed_chat(
    chat: TrashedChatDep, session: SessionDep, account: AccountDep, request: Request
):
    restore_chat(session, chat)
    audit(
        session,
        account,
        request,
        "chat_restored",
        project_id=chat.project_id,
        target=("chat", chat.id),
    )
    session.commit()
    # Its pins waited with it in the trash.
    return as_read(chat, pinned_by(session, chat, account))


@router.delete("/trash/chats/{chat_id}", status_code=204)
def purge_trashed_chat(
    chat: TrashedChatDep, session: SessionDep, account: AccountDep, request: Request
):
    _audit_purge(session, account, request, chat.project_id, "chat", chat.id)
    purge_chat(session, chat)
    session.commit()


def _audit_purge(
    session: SessionDep,
    account: AccountDep,
    request: Request,
    project_id: int,
    kind: str,
    target_id: int,
    name: str | None = None,
    **detail: int,
) -> None:
    """The row of something deleted for good from the trash: its name as it was, never a
    chat's title, which is its first question until renamed."""
    named = {} if name is None else {"filename" if kind == "document" else "name": name}
    audit(
        session,
        account,
        request,
        f"{kind}_purged",
        project_id=project_id,
        target=(kind, target_id),
        detail={**named, **detail},
    )
