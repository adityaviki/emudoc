"""The versioned API's projects, folders and documents (app/api/v1/): listed a page at a time, made
with an Idempotency-Key, changed, deleted into the trash, and a document's file and facts.

What is made, changed or deleted goes through the page's own routes (app/api/projects.py,
app/api/folders.py, app/api/documents.py), whose checks, audit rows and refusals hold here
as there; this module reads its own pages and words the answers in the API's models
(app/api/v1/schemas.py).
"""

import datetime as dt
import hashlib
from typing import Annotated, BinaryIO, Literal

from fastapi import APIRouter, Depends, Form, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import and_, func, select

from app.access import visible_to
from app.api import documents, folders, projects, versions
from app.api.deps import (
    AccountDep,
    DocumentDep,
    EditableDocumentDep,
    EditableFolderDep,
    EditableProjectDep,
    EditorDep,
    FolderDep,
    ProjectDep,
    ServicesDep,
    SessionDep,
    WorkerDep,
    limited,
)
from app.api.folders import documents_held, held_by, resolve_folder
from app.api.v1.deps import MOST, PAGE, After, Limit, OnceDep, cursor, cursor_of, shown
from app.api.v1.query import fact_of
from app.api.v1.schemas import (
    Document,
    DocumentChange,
    DocumentList,
    FactList,
    Folder,
    FolderChange,
    FolderList,
    NewFolder,
    NewProject,
    Project,
    ProjectChange,
    ProjectList,
    RejectedFile,
    Upload,
    Version,
    VersionList,
)
from app.folders import folder_paths
from app.idempotency import fingerprint
from app.indexing.pipeline import unfinished_stages
from app.models import Document as DocumentRow
from app.models import DocumentVersion as VersionRow
from app.models import Fact
from app.models import Folder as FolderRow
from app.models import Project as ProjectRow
from app.ratelimit import UPLOADS
from app.schemas import DocumentPatch, FolderIn, FolderPatch, NewProjectIn, ProjectIn, ProjectOut
from app.versions import current_since

router = APIRouter()

_LIVE = DocumentRow.deleted_at.is_(None)
CHUNK = 1024 * 1024


@router.get("/projects", response_model=ProjectList, tags=["projects"])
def list_projects(
    session: SessionDep, account: AccountDep, limit: Limit = PAGE, after: After = None
):
    """The projects the key's member sees, the newest first."""
    start = cursor(after, int)
    query = (
        select(ProjectRow, func.count(DocumentRow.id))
        .outerjoin(DocumentRow, and_(DocumentRow.project_id == ProjectRow.id, _LIVE))
        .where(visible_to(account))
        .group_by(ProjectRow.id)
    )
    if start:
        query = query.where(ProjectRow.id < start[0])
    rows, more = shown(
        session.execute(query.order_by(ProjectRow.id.desc()).limit(limit + 1)).all(), limit
    )
    return ProjectList(
        data=[project_of(project, count) for project, count in rows],
        next=cursor_of(rows[-1][0].id) if more else None,
    )


@router.post("/projects", response_model=Project, status_code=201, tags=["projects"])
def create_project(
    body: NewProject, request: Request, session: SessionDep, account: EditorDep, once: OnceDep
):
    """Make a project."""

    def work() -> Project:
        made = projects.create_project(NewProjectIn(**body.model_dump()), session, account)
        return _from_page(made)

    return once.run(lambda: fingerprint(request, body.model_dump(mode="json")), 201, work)


@router.get("/projects/{project_id}", response_model=Project, tags=["projects"])
def get_project(project: ProjectDep, session: SessionDep):
    return _from_page(projects.project_out(session, project))


@router.patch("/projects/{project_id}", response_model=Project, tags=["projects"])
def update_project(project: EditableProjectDep, body: ProjectChange, session: SessionDep):
    """Rename the project, describe it anew, or both."""
    changed = ProjectIn(
        name=body.name if body.name is not None else project.name,
        description=body.description if body.description is not None else project.description,
    )
    return _from_page(projects.update_project(project, changed, session))


@router.delete("/projects/{project_id}", status_code=204, tags=["projects"])
def delete_project(
    project: EditableProjectDep, session: SessionDep, account: AccountDep, request: Request
):
    """Put the project in the organisation's trash with everything in it, from which a member
    restores it in the app until it is deleted for good."""
    projects.delete_project(project, session, account, request)


@router.get("/projects/{project_id}/folders", response_model=FolderList, tags=["folders"])
def list_folders(
    project: ProjectDep, session: SessionDep, limit: Limit = PAGE, after: After = None
):
    """The project's folders, the oldest first, each with its path and how many documents it
    holds itself."""
    start = cursor(after, int)
    held = documents_held(project.id)
    query = (
        select(FolderRow, held.c.documents)
        .outerjoin(held, held.c.folder_id == FolderRow.id)
        .where(FolderRow.project_id == project.id, FolderRow.deleted_at.is_(None))
    )
    if start:
        query = query.where(FolderRow.id > start[0])
    rows, more = shown(session.execute(query.order_by(FolderRow.id).limit(limit + 1)).all(), limit)
    paths = folder_paths(session, project.id)
    return FolderList(
        data=[folder_of(folder, paths, count or 0) for folder, count in rows],
        next=cursor_of(rows[-1][0].id) if more else None,
    )


@router.post(
    "/projects/{project_id}/folders", response_model=Folder, status_code=201, tags=["folders"]
)
def create_folder(
    project: EditableProjectDep,
    body: NewFolder,
    request: Request,
    session: SessionDep,
    once: OnceDep,
):
    """Make a folder at the project's root or in one of its folders; its name is the only one of
    its folder's, whatever their case."""

    def work() -> Folder:
        made = folders.create_folder(project, FolderIn(**body.model_dump()), session)
        return folder_of(made, folder_paths(session, project.id), 0)

    return once.run(lambda: fingerprint(request, body.model_dump(mode="json")), 201, work)


@router.get("/folders/{folder_id}", response_model=Folder, tags=["folders"])
def get_folder(folder: FolderDep, session: SessionDep):
    return folder_of(folder, folder_paths(session, folder.project_id), held_by(session, folder))


@router.patch("/folders/{folder_id}", response_model=Folder, tags=["folders"])
def update_folder(folder: EditableFolderDep, body: FolderChange, session: SessionDep):
    """Rename the folder, move it, or both."""
    changed = FolderPatch.model_validate(body.model_dump(exclude_unset=True))
    out = folders.update_folder(folder, changed, session)
    return folder_of(folder, folder_paths(session, folder.project_id), out.document_count)


@router.delete("/folders/{folder_id}", status_code=204, tags=["folders"])
def delete_folder(
    folder: EditableFolderDep, session: SessionDep, account: AccountDep, request: Request
):
    """Put the folder in the project's trash with its folders and every document in them."""
    folders.delete_folder(folder, session, account, request)


@router.get("/projects/{project_id}/documents", response_model=DocumentList, tags=["documents"])
def list_documents(
    project: ProjectDep,
    session: SessionDep,
    folder_id: Annotated[
        int | None, Query(description="Only the documents directly in this folder.")
    ] = None,
    ids: Annotated[
        list[int] | None, Query(max_length=MOST, description="Only the documents of these ids.")
    ] = None,
    status: Annotated[
        Literal["queued", "processing", "ready", "failed", "quarantined"] | None,
        Query(description="Only the documents that stand so."),
    ] = None,
    limit: Limit = PAGE,
    after: After = None,
):
    """The project's documents, the newest first, those of one folder, of some ids or of a
    status alone when asked; a program follows its uploads by their ids until none is queued or
    processing."""
    start = cursor(after, int)
    filters = [DocumentRow.project_id == project.id, _LIVE]
    if folder_id is not None:
        filters.append(DocumentRow.folder_id == resolve_folder(session, project, folder_id).id)
    if ids:
        filters.append(DocumentRow.id.in_(ids))
    if status is not None:
        filters.append(DocumentRow.status == status)
    if start:
        filters.append(DocumentRow.id < start[0])
    query = select(DocumentRow).where(*filters).order_by(DocumentRow.id.desc()).limit(limit + 1)
    rows, more = shown(session.scalars(query).all(), limit)
    return DocumentList(
        data=[document_of(document) for document in rows],
        next=cursor_of(rows[-1].id) if more else None,
    )


@router.post(
    "/projects/{project_id}/documents",
    response_model=Upload,
    status_code=201,
    dependencies=[Depends(limited(UPLOADS))],
    tags=["documents"],
)
def upload_documents(
    project: EditableProjectDep,
    files: list[UploadFile],
    session: SessionDep,
    services: ServicesDep,
    worker: WorkerDep,
    account: AccountDep,
    request: Request,
    once: OnceDep,
    folder_id: Annotated[
        int | None, Query(description="The folder the files go into; the root when none.")
    ] = None,
    paths: Annotated[
        list[str] | None,
        Form(
            description=(
                "One for each file, in their order: the folders below `folder_id` it goes into, "
                "such as `Invoices/2024`, made where none of their name is, or empty for that "
                "folder itself."
            )
        ),
    ] = None,
):
    """Upload files into the project, as a multipart form of `files`, each indexed in the
    background. The request is refused whole when it carries more files or bytes than one upload may
    (413; `GET /api/v1/status` says how many); a file is refused alone, with why, for its size, for
    bytes that are not the type its name says, or as a file the project holds already
    (`duplicate_of`), while the others are kept. With an Idempotency-Key, the upload sent again is
    given the first one's answer."""

    def asked() -> str:
        sent = [[upload.filename, _sha256(upload.file)] for upload in files]
        return fingerprint(request, {"files": sent, "paths": paths})

    def work() -> Upload:
        result = documents.upload_documents(
            project, files, session, services, worker, account, request, folder_id, paths
        )
        kept = [session.get(DocumentRow, document.id) for document in result.documents]
        return Upload(
            documents=[document_of(document) for document in kept],
            rejected=[
                RejectedFile.model_validate({**file.model_dump(), "limit": None})
                for file in result.rejected
            ],
        )

    return once.run(asked, 201, work)


@router.get("/documents/{document_id}", response_model=Document, tags=["documents"])
def get_document(document: DocumentDep):
    """The document, and how far it is indexed."""
    return document_of(document)


@router.patch("/documents/{document_id}", response_model=Document, tags=["documents"])
def update_document(document: EditableDocumentDep, body: DocumentChange, session: SessionDep):
    """Rename the document, move it to another folder, or both."""
    changed = DocumentPatch.model_validate(body.model_dump(exclude_unset=True))
    return document_of(documents.update_document(document, changed, session))


@router.delete("/documents/{document_id}", status_code=204, tags=["documents"])
def delete_document(
    document: EditableDocumentDep, session: SessionDep, account: AccountDep, request: Request
):
    """Put the document in the project's trash, where nothing reads it until it is restored."""
    documents.delete_document(document, session, account, request)


@router.get(
    "/documents/{document_id}/file",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}, "description": "The file."}},
    tags=["documents"],
)
def download_file(
    document: DocumentDep,
    services: ServicesDep,
    session: SessionDep,
    account: AccountDep,
    request: Request,
) -> Response:
    """The file as it was uploaded, or a redirect to it, once the malware scan has passed (409
    until then; 403 for one it flagged). Each download is a row of the organisation's audit
    log."""
    return documents.download_file(document, services, session, account, request)


@router.get("/documents/{document_id}/versions", response_model=VersionList, tags=["documents"])
def list_versions(document: DocumentDep, session: SessionDep):
    """The document's versions: its current file first, then the files it had before, the latest
    first, every one on one page."""
    earlier = session.scalars(
        select(VersionRow)
        .where(VersionRow.document_id == document.id)
        .order_by(VersionRow.number.desc())
    ).all()
    current = Version(
        number=document.version,
        current=True,
        filename=document.filename,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        page_count=document.page_count,
        uploaded_at=current_since(session, document),
    )
    return VersionList(
        data=[
            current,
            *(
                Version(
                    number=version.number,
                    current=False,
                    filename=version.filename,
                    mime_type=version.mime_type,
                    size_bytes=version.size_bytes,
                    sha256=version.sha256,
                    page_count=version.page_count,
                    uploaded_at=version.uploaded_at,
                )
                for version in earlier
            ),
        ],
        next=None,
    )


@router.post(
    "/documents/{document_id}/versions",
    response_model=Document,
    status_code=201,
    dependencies=[Depends(limited(UPLOADS))],
    tags=["documents"],
)
def upload_version(
    document: EditableDocumentDep,
    file: UploadFile,
    session: SessionDep,
    services: ServicesDep,
    worker: WorkerDep,
    account: AccountDep,
    request: Request,
    once: OnceDep,
):
    """Give the document a new file, as a multipart form of `file`, which it reads in the earlier
    one's place, keeping its id and what names it; the earlier file stays as a version
    to download, and its pages count again as the new one is read. Refused, with nothing changed,
    for a file an upload would refuse (400, 413), for bytes the project holds already (409), and
    while the document is being indexed (409; send it again once it is not). With an
    Idempotency-Key, the version sent again is given the first one's answer."""

    def asked() -> str:
        return fingerprint(request, {"file": [file.filename, _sha256(file.file)]})

    def work() -> Document:
        given = versions.upload_version(document, file, session, services, worker, account, request)
        return document_of(given)

    return once.run(asked, 201, work)


@router.get(
    "/documents/{document_id}/versions/{number}/file",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}, "description": "The file."}},
    tags=["documents"],
)
def download_version(
    document: DocumentDep,
    number: int,
    services: ServicesDep,
    session: SessionDep,
    account: AccountDep,
    request: Request,
) -> Response:
    """An earlier version's file as it was uploaded, or a redirect to it (404 for a number that is
    no earlier version; the current file is the document's own). One replaced before the malware
    scan passed it is never served (409). Each download is a row of the organisation's audit
    log."""
    return versions.download_version(document, number, services, session, account, request)


@router.get("/documents/{document_id}/facts", response_model=FactList, tags=["facts"])
def get_document_facts(document: DocumentDep, session: SessionDep):
    """The facts read from the document, by name: every one of them, on one page."""
    facts = session.scalars(
        select(Fact).where(Fact.document_id == document.id).order_by(Fact.name, Fact.id)
    ).all()
    return FactList(data=[fact_of(fact, document) for fact in facts], next=None)


def project_of(project: ProjectRow, document_count: int) -> Project:
    return Project(
        id=project.id,
        name=project.name,
        description=project.description,
        visibility=project.visibility,
        sample=project.sample,
        document_count=document_count,
        created_at=project.created_at,
    )


def _from_page(out: ProjectOut) -> Project:
    return Project.model_validate(out.model_dump())


def folder_of(folder: FolderRow, paths: dict[int, str], document_count: int) -> Folder:
    return Folder(
        id=folder.id,
        project_id=folder.project_id,
        parent_id=folder.parent_id,
        name=folder.name,
        path=paths.get(folder.id, folder.name),
        document_count=document_count,
        created_at=folder.created_at,
    )


def document_of(document: DocumentRow) -> Document:
    return Document(
        id=document.id,
        project_id=document.project_id,
        folder_id=document.folder_id,
        filename=document.filename,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        status=document.status,
        partly_indexed=bool(unfinished_stages(document)),
        retry_at=_retry_at(document),
        error=document.error,
        page_count=document.page_count,
        title=document.title,
        doc_type=document.doc_type,
        doc_date=document.doc_date,
        summary=document.summary,
        created_at=document.created_at,
        version=document.version,
    )


def _retry_at(document: DocumentRow) -> dt.datetime | None:
    """When the soonest stage waiting to run again by itself does, as the library's upload
    status counts it (`_WAITING` in app/api/documents.py)."""
    if document.status not in ("queued", "ready"):
        return None
    waits = [stage["retry_at"] for stage in document.stages.values() if stage.get("retry_at")]
    return min(dt.datetime.fromisoformat(wait) for wait in waits) if waits else None


def _sha256(file: BinaryIO) -> str:
    """The SHA-256 of a file of the request, read from its start, which is where it is left."""
    digest = hashlib.sha256()
    file.seek(0)
    while chunk := file.read(CHUNK):
        digest.update(chunk)
    file.seek(0)
    return digest.hexdigest()
