"""My files (app/my_files.py): each member's own Word and Excel files in a project, which they add,
look at as they look, download, give new versions, rename, delete and copy into the project's
library. A member reaches their own files alone (`MyFileDep`): another member's, an owner's or an
admin's included, are not found.

A member also asks the agent to write a document of a kind into their My files (app/writings.py):
a disputed document's review asks for its dispute letter. The writing is listed with their files
while the worker writes it, and once failed, until they try it again or dismiss it; written, it is
a file like the others. It is a run of the agent, so asking is held to what holds a question, the
agent's runs in flight and the day's spending, and counts as one of the workspace's questions.

Every route takes a session alone, never an API key, since a program has no files of its own here.
Whoever sees the project keeps files there, a viewer too; copying one into the library adds a
document, so it is held to what holds an upload (`EditorDep`, the sample project's refusal). Each
change is a row of the organisation's audit log naming the file by its kind and id, never its name,
which is its member's own.
"""

import io
from typing import Annotated

from anyio import to_thread
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import Account
from app.api.auth import app_url
from app.api.check_runs import count_question, refuse_agent_run
from app.api.deps import (
    AccountDep,
    EditorDep,
    MyFileDep,
    MyFileWritingDep,
    ProjectDep,
    ServicesDep,
    SessionDep,
    WorkerDep,
    limited,
)
from app.api.documents import _draw_page, _drawn
from app.api.folders import resolve_folder
from app.audit import audit
from app.models import Document, MyFile, MyFileVersion, MyFileWriting, Project
from app.my_files import (
    MAX_FILES,
    MEDIA_TYPES,
    TOO_MANY,
    Refused,
    add_file,
    add_version,
    ask_view,
    delete_file,
    files_of,
    rename,
    servable,
    versions_of,
)
from app.notices import record_upload, settle_upload
from app.ratelimit import EXPORTS, QUESTIONS, UPLOADS
from app.sample import SAMPLE_UPLOADS
from app.schemas import (
    DocumentOut,
    MyFileCopyIn,
    MyFileDetailOut,
    MyFileOut,
    MyFileRenameIn,
    MyFilesOut,
    MyFileVersionOut,
    MyFileWritingIn,
    MyFileWritingOut,
)
from app.uploads import Incoming, take_files
from app.usage import RENDER, record
from app.versions import VersionRefused, take_version
from app.writings import queue_writing, start_writing, writings_of

router = APIRouter(tags=["my files"])

NO_SUCH_VERSION = "This file has no such version."
UNSCANNED = (
    "This version waits for its malware scan, so it cannot be opened, downloaded or copied yet. "
    "Try again in a minute."
)
FLAGGED = "The malware scan flagged this version, so it cannot be opened, downloaded or copied."
NO_VIEW = "This version has no pages to show yet. Download it to open it."
NO_ORIGIN = (
    "The document this file was copied from is no longer in the project's library. Copy it in as "
    "a new document instead."
)
WRITING_NOW = "The agent is writing this document now. It will be in your My files shortly."
# What a refusal of the copy's upload is answered with, by its label, as an upload's is.
REFUSED = {"too_large": 413, "duplicate": 409, "indexing": 409}


@router.get("/projects/{project_id}/my-files", response_model=MyFilesOut)
def list_files(project: ProjectDep, session: SessionDep, account: AccountDep):
    """The member's files in the project, the one changed last first."""
    files = files_of(session, project.id, account.user.id)
    return MyFilesOut(
        files=_summaries(session, files),
        limit=MAX_FILES,
        writings=[
            MyFileWritingOut.model_validate(writing)
            for writing in writings_of(session, project.id, account.user.id)
        ],
    )


@router.post(
    "/projects/{project_id}/my-files/writings",
    response_model=MyFileWritingOut,
    status_code=201,
    dependencies=[Depends(limited(QUESTIONS))],
)
def ask_writing(
    project: ProjectDep, body: MyFileWritingIn, session: SessionDep, account: AccountDep
):
    """Have the agent write a document of the kind about the documents named into the member's
    My files, to the project's template of the kind; the worker writes it, which their My files
    follow."""
    asked = list(dict.fromkeys(body.document_ids))
    found = {
        document.id: document
        for document in session.scalars(
            select(Document).where(
                Document.id.in_(asked),
                Document.project_id == project.id,
                Document.deleted_at.is_(None),
            )
        )
    }
    if len(found) < len(asked):
        raise HTTPException(404, "Document not found")
    _refuse_writing(session, account, project.id)
    writing = start_writing(
        session, project, account.user.id, body.kind, [found[each] for each in asked]
    )
    count_question(session, account, project.id)
    session.commit()
    return MyFileWritingOut.model_validate(writing)


@router.post(
    "/my-file-writings/{writing_id}/retry",
    response_model=MyFileWritingOut,
    dependencies=[Depends(limited(QUESTIONS))],
)
def retry_writing(writing: MyFileWritingDep, session: SessionDep, account: AccountDep):
    """Have the agent write a failed writing again, from the documents and the template as they
    are now."""
    # Held against a second press at once and the worker settling it.
    session.refresh(writing, with_for_update=True)
    if writing.status != "failed":
        raise HTTPException(409, WRITING_NOW)
    _refuse_writing(session, account, writing.project_id)
    queue_writing(session, writing)
    count_question(session, account, writing.project_id)
    session.commit()
    return MyFileWritingOut.model_validate(writing)


@router.delete("/my-file-writings/{writing_id}", status_code=204)
def dismiss_writing(writing: MyFileWritingDep, session: SessionDep):
    """Forget a writing: a failed one leaves the member's list, and one under way keeps nothing
    the worker writes (app/writings.py)."""
    session.delete(writing)
    session.commit()
    return Response(status_code=204)


@router.post(
    "/projects/{project_id}/my-files",
    response_model=MyFileDetailOut,
    status_code=201,
    dependencies=[Depends(limited(UPLOADS))],
)
def upload_file(
    project: ProjectDep,
    file: Annotated[UploadFile, File()],
    account: AccountDep,
    services: ServicesDep,
    session: SessionDep,
    request: Request,
    worker: WorkerDep,
):
    """Keep a Word or Excel file in the member's My files in the project."""
    try:
        kept = add_file(
            session,
            services.storage,
            account.organization,
            project,
            account.user.id,
            file.filename or "Untitled",
            file.file,
            scanner=services.scanner,
            converter=services.converter,
        )
    except Refused as refused:
        raise _refusal(refused) from None
    _audit(session, account, request, "my_file_added", kept, size_bytes=_newest(kept).size_bytes)
    if worker:
        worker.notify()
    return _detail(session, kept)


@router.get("/my-files/{file_id}", response_model=MyFileDetailOut)
def get_file(file: MyFileDep, session: SessionDep):
    return _detail(session, file)


@router.patch("/my-files/{file_id}", response_model=MyFileDetailOut)
def rename_file(
    file: MyFileDep,
    body: MyFileRenameIn,
    account: AccountDep,
    session: SessionDep,
    request: Request,
):
    rename(session, file, body.name)
    _audit(session, account, request, "my_file_renamed", file)
    return _detail(session, file)


@router.delete("/my-files/{file_id}", status_code=204)
def remove_file(
    file: MyFileDep,
    account: AccountDep,
    services: ServicesDep,
    session: SessionDep,
    request: Request,
):
    """Delete the file for good with every version; the page asks first, since nothing brings it
    back."""
    _audit(session, account, request, "my_file_deleted", file)
    delete_file(session, services.storage, file)
    return Response(status_code=204)


@router.post(
    "/my-files/{file_id}/versions",
    response_model=MyFileDetailOut,
    status_code=201,
    dependencies=[Depends(limited(UPLOADS))],
)
def upload_version(
    file: MyFileDep,
    upload: Annotated[UploadFile, File(alias="file")],
    account: AccountDep,
    services: ServicesDep,
    session: SessionDep,
    request: Request,
    worker: WorkerDep,
    base: Annotated[int | None, Form(ge=1)] = None,
):
    """Keep a file the member edited elsewhere as the file's next version. `base`, the version
    they started from, refuses it with 409 when another was added meanwhile."""
    try:
        version = add_version(
            session,
            services.storage,
            file,
            account.user.id,
            upload.file,
            scanner=services.scanner,
            converter=services.converter,
            name=upload.filename,
            base=base,
        )
    except Refused as refused:
        raise _refusal(refused) from None
    _audit(
        session,
        account,
        request,
        "my_file_version_added",
        file,
        version=version.number,
        size_bytes=version.size_bytes,
    )
    if worker:
        worker.notify()
    return _detail(session, file)


@router.get("/my-files/{file_id}/versions/{number}/file", dependencies=[Depends(limited(EXPORTS))])
def download_version(
    file: MyFileDep,
    number: Annotated[int, Path(ge=1)],
    account: AccountDep,
    services: ServicesDep,
    session: SessionDep,
    request: Request,
):
    """A version's file as a download, a row of the audit log as every file that leaves is."""
    version = _served(_version(session, file, number))
    response = services.storage.response(
        version.storage_key, media_type=MEDIA_TYPES[file.kind], filename=file.name
    )
    _audit(session, account, request, "my_file_downloaded", file, version=number)
    return response


@router.get("/my-files/{file_id}/versions/{number}/pages/{page}.png")
async def page_image(
    file: MyFileDep,
    number: Annotated[int, Path(ge=1)],
    page: int,
    request: Request,
    services: ServicesDep,
    session: SessionDep,
    account: AccountDep,
) -> Response:
    """A page of a version's view, drawn the first time it is asked for and kept beside it for
    PAGE_IMAGE_DAYS, as a document's pages are; a page drawn is the member's usage."""
    version = _served(await to_thread.run_sync(_version, session, file, number))
    if version.view_state != "ready" or not version.view_page_sizes:
        raise HTTPException(404, NO_VIEW)
    if not 1 <= page <= len(version.view_page_sizes):
        raise HTTPException(404, "No such page")
    key = version.page_image_key(page)
    if not await to_thread.run_sync(services.storage.exists, key):
        project_id, org_id, user_id = file.project_id, file.org_id, account.user.id
        await _drawn(
            request, session, lambda: _draw_page(services.storage, version.view_key, page, key)
        )
        await to_thread.run_sync(_charge_render, session, org_id, project_id, user_id)
    return services.storage.response(key, media_type="image/png")


@router.post("/my-files/{file_id}/versions/{number}/view", response_model=MyFileDetailOut)
def make_view(
    file: MyFileDep,
    number: Annotated[int, Path(ge=1)],
    services: ServicesDep,
    session: SessionDep,
    worker: WorkerDep,
):
    """Have a version scanned and shown again, from "Try again" once either failed."""
    version = _version(session, file, number)
    version = session.get(MyFileVersion, version.id, with_for_update=True, populate_existing=True)
    if ask_view(session, version, services.scanner, services.converter):
        session.commit()
        if worker:
            worker.notify()
    else:
        session.rollback()
    return _detail(session, file)


@router.post("/my-files/{file_id}/copy", response_model=DocumentOut, status_code=201)
def copy_to_library(
    file: MyFileDep,
    body: MyFileCopyIn,
    account: EditorDep,
    services: ServicesDep,
    session: SessionDep,
    request: Request,
    worker: WorkerDep,
):
    """Copy the file's newest version into the project's library: a new document in a folder,
    or the next version of the document it was copied from. It is an upload like any, scanned,
    indexed and counted as one, and the file stays in My files."""
    project = session.get(Project, file.project_id)
    if project.sample:
        raise HTTPException(409, SAMPLE_UPLOADS)
    version = _served(_newest(file))
    data = io.BytesIO(services.storage.get(version.storage_key))
    if body.as_version:
        document = _copied_as_version(session, services, account, request, project, file, data)
    else:
        document = _copied_as_document(
            session, services, account, request, project, file, data, body
        )
    _audit(
        session,
        account,
        request,
        "my_file_copied",
        file,
        version=version.number,
        document=document.id,
        as_version=body.as_version,
    )
    if worker:
        worker.notify()
    session.refresh(document)
    return document


def _copied_as_document(
    session: Session,
    services,
    account: Account,
    request: Request,
    project: Project,
    file: MyFile,
    data: io.BytesIO,
    body: MyFileCopyIn,
) -> Document:
    folder = resolve_folder(session, project, body.folder_id)

    def audited(document: Document, size: int) -> None:
        audit(
            session,
            account,
            request,
            "document_uploaded",
            project_id=project.id,
            target=("document", document.id),
            detail={"filename": document.filename, "size_bytes": size, "from_my_files": True},
        )

    documents, rejected = take_files(
        session,
        services.storage,
        project,
        folder.id if folder else None,
        [Incoming(file.name, [], data)],
        user_id=account.user.id,
        audited=audited,
    )
    if rejected:
        refused = rejected[0]
        raise HTTPException(409 if refused.duplicate_of else 400, refused.reason)
    if upload_id := record_upload(session, project.id, account.user.id, [documents[0].id]):
        session.commit()
        settle_upload(session, upload_id, app_url(request))
    return documents[0]


def _copied_as_version(
    session: Session,
    services,
    account: Account,
    request: Request,
    project: Project,
    file: MyFile,
    data: io.BytesIO,
) -> Document:
    document = session.get(Document, file.origin_document_id) if file.origin_document_id else None
    if document is None or document.deleted_at is not None or document.project_id != project.id:
        raise HTTPException(409, NO_ORIGIN)

    def audited(action: str, detail: dict) -> None:
        audit(
            session,
            account,
            request,
            action,
            project_id=project.id,
            target=("document", document.id),
            detail={**detail, "from_my_files": True}
            if action.startswith("document_version")
            else detail,
        )

    try:
        take_version(
            session,
            services.storage,
            project,
            document,
            file.name,
            data,
            user_id=account.user.id,
            audited=audited,
        )
    except VersionRefused as refused:
        session.rollback()
        raise HTTPException(REFUSED.get(refused.label, 400), refused.reason) from None
    if upload_id := record_upload(session, project.id, account.user.id, [document.id]):
        session.commit()
        settle_upload(session, upload_id, app_url(request))
    return document


def _summaries(session: Session, files: list[MyFile]) -> list[MyFileOut]:
    """The files as their list shows them, each with its newest version and its count, read in
    one query each rather than one a file."""
    if not files:
        return []
    ids = [file.id for file in files]
    counts = dict(
        session.execute(
            select(MyFileVersion.file_id, func.count())
            .where(MyFileVersion.file_id.in_(ids))
            .group_by(MyFileVersion.file_id)
        ).all()
    )
    numbers = (
        select(MyFileVersion.file_id, func.max(MyFileVersion.number).label("number"))
        .where(MyFileVersion.file_id.in_(ids))
        .group_by(MyFileVersion.file_id)
        .subquery()
    )
    newest = {
        version.file_id: version
        for version in session.scalars(
            select(MyFileVersion).join(
                numbers,
                (numbers.c.file_id == MyFileVersion.file_id)
                & (numbers.c.number == MyFileVersion.number),
            )
        )
    }
    origins = _origin_names(session, files)
    return [
        _out(file, newest[file.id], counts[file.id], origins.get(file.origin_document_id))
        for file in files
    ]


def _detail(session: Session, file: MyFile) -> MyFileDetailOut:
    versions = versions_of(session, file.id)
    origin = _origin_names(session, [file]).get(file.origin_document_id)
    out = _out(file, versions[0], len(versions), origin)
    return MyFileDetailOut(
        **out.model_dump(),
        history=[MyFileVersionOut.model_validate(version) for version in versions],
    )


def _out(file: MyFile, latest: MyFileVersion, versions: int, origin: str | None) -> MyFileOut:
    return MyFileOut(
        id=file.id,
        project_id=file.project_id,
        name=file.name,
        kind=file.kind,
        origin_document_id=file.origin_document_id if origin is not None else None,
        origin_name=origin,
        latest=MyFileVersionOut.model_validate(latest),
        versions=versions,
        created_at=file.created_at,
        updated_at=file.updated_at,
    )


def _origin_names(session: Session, files: list[MyFile]) -> dict[int, str]:
    """The names of the documents these files were copied from, those still in the library;
    one in the trash or gone is no origin to copy back to."""
    ids = {file.origin_document_id for file in files if file.origin_document_id is not None}
    if not ids:
        return {}
    return dict(
        session.execute(
            select(Document.id, Document.filename).where(
                Document.id.in_(ids), Document.deleted_at.is_(None)
            )
        ).all()
    )


def _version(session: Session, file: MyFile, number: int) -> MyFileVersion:
    version = session.scalar(
        select(MyFileVersion).where(
            MyFileVersion.file_id == file.id, MyFileVersion.number == number
        )
    )
    if version is None:
        raise HTTPException(404, NO_SUCH_VERSION)
    return version


def _newest(file: MyFile) -> MyFileVersion:
    session = Session.object_session(file)
    return session.scalars(
        select(MyFileVersion)
        .where(MyFileVersion.file_id == file.id)
        .order_by(MyFileVersion.number.desc())
        .limit(1)
    ).one()


def _served(version: MyFileVersion) -> MyFileVersion:
    """The version, refused with 409 while its scan has not passed it."""
    if version.scan == "flagged":
        raise HTTPException(409, FLAGGED)
    if not servable(version):
        raise HTTPException(409, UNSCANNED)
    return version


def _refusal(refused: Refused) -> Exception:
    return HTTPException(refused.status, refused.reason)


def _refuse_writing(session: Session, account: Account, project_id: int) -> None:
    """Refuse a writing that could not be kept, My files being full, or that the agent may not run
    now, as a question is refused, before anything is spent on it."""
    held = session.scalar(
        select(func.count(MyFile.id)).where(
            MyFile.project_id == project_id, MyFile.user_id == account.user.id
        )
    )
    under_way = session.scalar(
        select(func.count(MyFileWriting.id)).where(
            MyFileWriting.project_id == project_id,
            MyFileWriting.user_id == account.user.id,
            MyFileWriting.status != "failed",
        )
    )
    if held + under_way >= MAX_FILES:
        raise HTTPException(409, TOO_MANY)
    refuse_agent_run(session, account, f"A document written into My files in project {project_id}")


def _charge_render(session: Session, org_id: int, project_id: int, user_id: int) -> None:
    """A page drawn for the first time is the member's usage, as a document's is (app/usage.py);
    it names no document, a file of My files being none."""
    record(
        session,
        org_id=org_id,
        project_id=project_id,
        user_id=user_id,
        kind=RENDER,
        measures={"pages": 1},
    )
    session.commit()


def _audit(
    session: Session,
    account: Account,
    request: Request,
    action: str,
    file: MyFile,
    **detail,
) -> None:
    """A row of the organisation's audit log about the file, by its kind and id, and commit."""
    audit(
        session,
        account,
        request,
        action,
        project_id=file.project_id,
        target=("my_file", file.id),
        detail={"kind": file.kind, **detail},
    )
    session.commit()
