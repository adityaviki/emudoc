import base64
import hashlib
import io
import json
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Annotated

from anyio import to_thread
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from PIL import ImageOps, UnidentifiedImageError
from sqlalchemy import DateTime, and_, func, literal_column, or_, select, tuple_
from sqlalchemy.orm import Session

from app.api.auth import app_url
from app.api.deps import (
    AccountDep,
    DeletableDocumentDep,
    DocumentDep,
    EditableDocumentDep,
    EditableProjectDep,
    PassageDep,
    ProjectDep,
    ServicesDep,
    SessionDep,
    Stage,
    WorkerDep,
    limited,
)
from app.api.folders import resolve_folder
from app.audit import audit
from app.config import Settings, get_settings
from app.entitlements import PlanRefused, check_upload, size_label
from app.fact_checks import fact_out
from app.folders import (
    folder_names,
)
from app.indexing.pipeline import OPTIONAL_STAGES, reset_stages, went_without
from app.jobs import queue_index
from app.mail import LISTED
from app.metrics import UPLOAD_REJECTIONS
from app.models import STAGES, Block, Document, Fact, Project, Section
from app.notices import NOT_INDEXED, record_upload, settle_upload
from app.parsing import IMAGE_TYPES, FileTooLarge
from app.parsing.images import open_image
from app.ratelimit import UPLOADS
from app.rendering import FOR_PANE, FOR_THUMBNAIL, RenderError, renderer
from app.sample import SAMPLE_UPLOADS
from app.scanning import scan_passed
from app.schemas import (
    DocumentContent,
    DocumentOut,
    DocumentPage,
    DocumentPatch,
    FactOut,
    PassageOut,
    SectionOut,
    UploadFailureOut,
    UploadResult,
    UploadStatusOut,
)
from app.storage import Storage
from app.trash import trash_document
from app.uploads import Incoming, take_files
from app.usage import DOCUMENT, RENDER, record
from app.version import VERSION

router = APIRouter(tags=["documents"])

CHUNK = 1024 * 1024
# The longer side of a thumbnail, in pixels: enough for a tile on a high-density screen.
THUMBNAIL_SIZE = 320
MISSING_FILE = "The original file is missing from the storage"
# Why a file is not served (app/scanning.py): for good, or until its scan has passed.
QUARANTINED_FILE = (
    "The malware scan flagged this file, so it cannot be opened or downloaded. Delete it."
)
UNSCANNED_FILE = (
    "This file is served once the malware scan has passed; the document shows how far the scan is."
)
# An upload's `paths` are each file's folder, so a list of another length cannot be matched up.
ONE_PATH_A_FILE = "Give one path for each file uploaded, or none."
# How many documents a page of a folder holds unless the request asks for another number.
PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
BAD_CURSOR = "This page of the folder cannot be found. List the folder again from its start."
# The transaction that wrote a row, which every change to it moves (`_page_tag`).
ROW_VERSION = literal_column("documents.xmin::text")
# How many uploads one request may ask about. Each request of the page's upload is an upload of
# its own, so a large folder is many, which the page asks about this many at a time
# (frontend/src/workspace/uploads.ts).
MAX_FOLLOWED = 100
# A queued or ready document whose stage failed for a reason that passes waits for the stage to
# run again by itself (`await_retry` in app/indexing/pipeline.py), which drops the mark when it
# runs; `waitingRetry` in frontend/src/indexing.ts reads the same.
_WAITING = and_(
    Document.status.in_(("queued", "ready")),
    or_(*(Document.stages[name]["retry_at"].is_not(None) for name in STAGES)),
)
_FAILED = Document.status.in_(("failed", "quarantined"))
# Where an upload's documents stand, each counted once (`UploadStatusOut`).
_STANDING = (
    func.count().label("documents"),
    func.count().filter(Document.status == "ready", ~_WAITING).label("ready"),
    func.count().filter(went_without(*OPTIONAL_STAGES)).label("partly_indexed"),
    func.count().filter(Document.status.in_(("queued", "processing")), ~_WAITING).label("indexing"),
    func.count().filter(_WAITING).label("retrying"),
    func.min(
        func.least(
            *(
                Document.stages[name]["retry_at"].astext.cast(DateTime(timezone=True))
                for name in STAGES
            )
        )
    )
    .filter(_WAITING)
    .label("retry_at"),
    func.count().filter(_FAILED).label("failed"),
)


@router.get("/projects/{project_id}/documents", response_model=DocumentPage)
def list_documents(
    project: ProjectDep,
    session: SessionDep,
    request: Request,
    response: Response,
    folder_id: int | None = None,
    after: str | None = None,
    limit: int = Query(PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """A page of the documents directly in a folder (the root when no folder is given), in
    the library's order, past the one `after` names when it is given.

    The page carries an ETag of exactly what it shows, and a request whose If-None-Match names
    it is answered 304 without a body: the library asks again every moment while a document in
    the folder is being indexed, and most of those moments change nothing on its page.
    """
    folder = resolve_folder(session, project, folder_id)
    query = select(Document.id, ROW_VERSION).where(
        Document.project_id == project.id,
        Document.folder_id == folder.id if folder else Document.folder_id.is_(None),
        Document.deleted_at.is_(None),
    )
    if after is not None:
        query = query.where(tuple_(Document.filename, Document.id) > tuple_(*_cursor(after)))
    query = query.order_by(Document.filename, Document.id).limit(limit + 1)
    # The versions alone first: when they are the ones the client holds, nothing more is read
    # or serialised. A page that is sent carries the tag of its own rows, read with them.
    tag = _page_tag(session.execute(query).all(), limit)
    if tag in _tags_held(request):
        return Response(status_code=304, headers={"ETag": tag})
    rows = session.execute(query.with_only_columns(Document, ROW_VERSION)).all()
    response.headers["ETag"] = _page_tag(
        [(document.id, version) for document, version in rows], limit
    )
    documents = [document for document, _ in rows[:limit]]
    return DocumentPage(
        documents=documents,
        next_after=_cursor_of(documents[-1]) if len(rows) > limit else None,
    )


def _page_tag(rows: Sequence[tuple[int, str]], limit: int) -> str:
    """The ETag of a page: the version of each row on it and whether more follow. A row's
    version is its `xmin`, the transaction that wrote it, which every change to it moves,
    an ORM update or a raw one alike; a timestamp of the change would not do, since a
    transaction that began earlier can commit later with an older one. The release is in it
    too, since a new one may serve the same rows otherwise."""
    shown = ",".join(f"{document_id}:{version}" for document_id, version in rows[:limit])
    more = "more" if len(rows) > limit else "last"
    return f'"{hashlib.sha256(f"{VERSION}|{shown}|{more}".encode()).hexdigest()[:32]}"'


def _tags_held(request: Request) -> set[str]:
    """The ETags an If-None-Match names, weak or strong alike: a proxy that compresses the
    response may weaken the tag on the way, and the comparison for this header is weak."""
    header = request.headers.get("if-none-match", "")
    return {tag.strip().removeprefix("W/") for tag in header.split(",") if tag.strip()}


def _cursor_of(document: Document) -> str:
    """Where the next page starts: after this document's name and id, as `after` takes it. It
    carries the name rather than the id alone, so that it still holds once the document is
    renamed, moved or deleted."""
    encoded = json.dumps([document.filename, document.id]).encode()
    return base64.urlsafe_b64encode(encoded).decode().rstrip("=")


def _cursor(after: str) -> tuple[str, int]:
    """The name and id an `after` carries; anything else is refused rather than guessed at."""
    try:
        name, document_id = json.loads(base64.urlsafe_b64decode(after + "=" * (-len(after) % 4)))
    except (ValueError, TypeError):
        raise HTTPException(422, BAD_CURSOR) from None
    if not isinstance(name, str) or not isinstance(document_id, int) or not 0 < document_id < 2**31:
        raise HTTPException(422, BAD_CURSOR)
    return name, document_id


@router.post(
    "/projects/{project_id}/documents",
    response_model=UploadResult,
    status_code=201,
    dependencies=[Depends(limited(UPLOADS))],
)
def upload_documents(
    project: EditableProjectDep,
    files: list[UploadFile],
    session: SessionDep,
    services: ServicesDep,
    worker: WorkerDep,
    account: AccountDep,
    request: Request,
    folder_id: int | None = None,
    paths: Annotated[list[str] | None, Form()] = None,
):
    """Upload files into a folder of the project (the root when no folder is given), each
    into the folder below it that its entry of `paths` names ("Invoices/2024", "" for that folder
    itself) when the request gives one per file, as an upload of a folder does: a folder is made
    where none of its name is, and one that is goes on holding what it held.

    The request is refused whole, before anything is stored, when it carries more files or
    more bytes than one upload may (413, naming the limit), and when the organisation's plan
    has no room for a document at all (402, which the app shows as the way to upgrade); each
    file is then checked for its own size, for whether its bytes are the type its name claims
    (app/parsing/signatures.py) and against the plan, and one that fails is rejected with the
    sentence while the others go in (app/entitlements.py). The sample project takes none (409),
    since it counts against nothing the plan holds (app/sample).
    """
    if project.sample:
        raise HTTPException(409, SAMPLE_UPLOADS)
    settings = get_settings()
    if reason := too_many(files, settings):
        UPLOAD_REJECTIONS.labels("too_many").inc()
        raise HTTPException(413, reason)
    if paths is not None and len(paths) != len(files):
        raise HTTPException(422, ONE_PATH_A_FILE)
    organization = account.organization
    if refusal := check_upload(session, organization):
        UPLOAD_REJECTIONS.labels("plan").inc(len(files))
        raise PlanRefused(refusal)
    folder = resolve_folder(session, project, folder_id)

    def audited(document: Document, size: int) -> None:
        audit(
            session,
            account,
            request,
            "document_uploaded",
            project_id=project.id,
            target=("document", document.id),
            detail={"filename": document.filename, "size_bytes": size},
        )

    documents, rejected = take_files(
        session,
        services.storage,
        project,
        organization,
        folder.id if folder else None,
        (
            Incoming(
                upload.filename or "unnamed",
                folder_names(paths[index]) if paths is not None else [],
                upload.file,
            )
            for index, upload in enumerate(files)
        ),
        user_id=account.user.id,
        audited=audited,
    )

    # The files kept are one upload, made once they are all stored, so that it is never found
    # settled while the request still stores some; any indexed meanwhile are looked at here.
    if upload_id := record_upload(
        session, project.id, account.user.id, [document.id for document in documents]
    ):
        session.commit()
        settle_upload(session, upload_id, app_url(request))
    if documents and worker:
        worker.notify()
    return UploadResult(documents=documents, rejected=rejected, upload_id=upload_id)


@router.get("/projects/{project_id}/uploads", response_model=UploadStatusOut)
def upload_status(
    project: ProjectDep,
    session: SessionDep,
    ids: Annotated[list[int], Query(min_length=1, max_length=MAX_FOLLOWED)],
):
    """How far the documents of the project's uploads named by `ids` are indexed, counted by
    where each stands, with the first that could not be indexed and why: what the library
    follows of an upload once its files are sent, one request of the page's being one upload
    on the server (app/notices.py). An id that is not one of the project's uploads counts
    nothing, and a document in the trash is left out, as the upload's notice leaves it out.
    """
    of_uploads = (
        Document.project_id == project.id,
        Document.upload_id.in_(ids),
        Document.deleted_at.is_(None),
    )
    counts = session.execute(select(*_STANDING).where(*of_uploads)).one()
    failures = session.execute(
        select(Document.id, Document.filename, Document.status, Document.error)
        .where(*of_uploads, _FAILED)
        .order_by(Document.filename, Document.id)
        .limit(LISTED)
    ).all()
    return UploadStatusOut(
        documents=counts.documents,
        ready=counts.ready,
        partly_indexed=counts.partly_indexed,
        indexing=counts.indexing,
        retrying=counts.retrying,
        retry_at=counts.retry_at,
        failed=counts.failed,
        failures=[
            UploadFailureOut(
                id=document_id,
                filename=filename,
                reason=error or NOT_INDEXED,
                quarantined=status == "quarantined",
            )
            for document_id, filename, status, error in failures
        ],
    )


def too_many(files: list[UploadFile], settings: Settings) -> str | None:
    """Why one request may not carry these files, or None when it may: more of them than
    MAX_UPLOAD_FILES, or more bytes together than MAX_UPLOAD_REQUEST_MB. The sizes are what
    the multipart parser counted as it spooled the body, so nothing is read again."""
    if len(files) > settings.max_upload_files:
        return (
            f"This upload has {len(files)} files, and one upload can have "
            f"{settings.max_upload_files} at most. Upload them in smaller batches."
        )
    total = sum(upload.size or 0 for upload in files)
    if total > settings.max_upload_request_mb * 1024 * 1024:
        return (
            f"These files add up to {size_label(total)}, and one upload can carry "
            f"{settings.max_upload_request_mb} MB at most. Upload them in smaller batches."
        )
    return None


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document: DocumentDep):
    return document


@router.patch("/documents/{document_id}", response_model=DocumentOut)
def update_document(document: EditableDocumentDep, body: DocumentPatch, session: SessionDep):
    """Rename the document, move it to another folder, or both."""
    project = session.get(Project, document.project_id)
    folder_id = document.folder_id
    if "folder_id" in body.model_fields_set:
        folder = resolve_folder(session, project, body.folder_id)
        folder_id = folder.id if folder else None
    filename = document.filename
    if body.filename is not None:
        filename = renamed(document.filename, body.filename)
    if (filename, folder_id) != (document.filename, document.folder_id):
        clash = session.scalar(
            select(Document.id).where(
                Document.project_id == project.id,
                Document.folder_id == folder_id,
                Document.deleted_at.is_(None),
                func.lower(Document.filename) == filename.lower(),
                Document.id != document.id,
            )
        )
        if clash is not None:
            raise HTTPException(409, f"A file named {filename!r} already exists here")
    document.filename = filename
    document.folder_id = folder_id
    session.commit()
    return document


def renamed(current: str, requested: str) -> str:
    """The new file name, keeping the extension: the parser picks its format from it."""
    name = Path(" ".join(requested.split())).name
    if not name or name in {".", ".."}:
        raise HTTPException(422, "A file name cannot be empty")
    suffix = Path(current).suffix
    if Path(name).suffix.lower() != suffix.lower():
        name += suffix
    return name


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(
    document: DeletableDocumentDep, session: SessionDep, account: AccountDep, request: Request
):
    """Put the document in the project's trash, where nothing reads it until it is restored."""
    audit(
        session,
        account,
        request,
        "document_deleted",
        project_id=document.project_id,
        target=("document", document.id),
        detail={"filename": document.filename},
    )
    trash_document(session, document, account.user.id)
    session.commit()


@router.post("/documents/{document_id}/reprocess", response_model=DocumentOut, status_code=202)
def reprocess_document(
    document: EditableDocumentDep,
    session: SessionDep,
    worker: WorkerDep,
    from_stage: Stage = "parse",
):
    if document.status == "processing":
        raise HTTPException(409, "This document is being indexed right now")
    # A quarantined document is scanned again before anything else, whatever stage is asked
    # for: the run takes every stage that is not done, in order (app/indexing/pipeline.py).
    reset_stages(document, from_stage)
    queue_index(session, document)
    session.commit()
    if worker:
        worker.notify()
    return document


def _scanned(document: Document) -> None:
    """Refuse to serve, or render from, a file the malware scan has not passed: a quarantined
    one for good, and one still waiting for its scan until it is done."""
    if document.status == "quarantined":
        raise HTTPException(403, QUARANTINED_FILE)
    if not scan_passed(document):
        raise HTTPException(409, UNSCANNED_FILE)


@router.get("/documents/{document_id}/file")
def download_file(
    document: DocumentDep,
    services: ServicesDep,
    session: SessionDep,
    account: AccountDep,
    request: Request,
) -> Response:
    """The uploaded file as it was uploaded; the storage decides whether it is sent from here or
    from a signed URL.

    It comes as a download: a PDF that a browser opened in its own viewer could run scripts and
    submit forms, and the app shows a document's pages from its own renderings anyway. An image
    is the exception: the document pane's preview is an `<img>`, and a browser runs nothing in one.

    Every time the file is sent, or a URL to it signed, is a download in the audit log, the
    image preview's included: the bytes leave either way.
    """
    response = original_response(services.storage, document)
    audit(
        session,
        account,
        request,
        "document_downloaded",
        project_id=document.project_id,
        target=("document", document.id),
        detail={"filename": document.filename},
    )
    session.commit()
    return response


def original_response(storage: Storage, document: Document) -> Response:
    """The document's original as `download_file` sends it, refused before anything is sent when
    the malware scan has not passed it or the storage lacks it; a share link's file too
    (app/api/share_links.py)."""
    _scanned(document)
    if not storage.exists(document.storage_key):
        raise HTTPException(404, MISSING_FILE)
    return storage.response(
        document.storage_key,
        media_type=document.mime_type,
        filename=document.filename,
        inline=document.mime_type in IMAGE_TYPES.values(),
    )


@router.get("/documents/{document_id}/pages/{page}.png")
async def page_image(
    document: DocumentDep,
    page: int,
    request: Request,
    services: ServicesDep,
    session: SessionDep,
    account: AccountDep,
) -> Response:
    """A rendered PDF page, for showing where on the page a passage sits. A PDF's first pages
    are drawn once it is parsed (PRERENDER_PAGES, app/rendering.py), any other the first time
    it is asked for, and each is kept beside the original for PAGE_IMAGE_DAYS."""
    return await page_response(
        request, session, services.storage, document, page, account.organization.id, account.user.id
    )


async def page_response(
    request: Request,
    session: Session,
    storage: Storage,
    document: Document,
    page: int,
    org_id: int,
    user_id: int,
) -> Response:
    """A page of the document as `page_image` sends it, drawn first when it is not kept, which
    is charged to the member `user_id` names; a share link's pages too, its maker's
    (app/api/share_links.py)."""
    if document.mime_type != "application/pdf":
        raise HTTPException(404, "Only PDF documents have page images")
    if not 1 <= page <= (document.page_count or 0):
        raise HTTPException(404, "No such page")
    _scanned(document)
    key = document.page_image_key(page)
    if not await to_thread.run_sync(storage.exists, key):
        await _drawn(request, session, partial(_draw_page, storage, document, page, key))
        await to_thread.run_sync(_charge_render, session, org_id, user_id, document)
    return storage.response(key, media_type="image/png")


@router.get("/documents/{document_id}/thumbnail.png")
async def thumbnail(
    document: DocumentDep,
    request: Request,
    services: ServicesDep,
    session: SessionDep,
    account: AccountDep,
) -> Response:
    """A small picture of the document for the library: a PDF's first page, or the image itself."""
    if document.mime_type != "application/pdf" and document.mime_type not in IMAGE_TYPES.values():
        raise HTTPException(404, "Only PDF and image documents have thumbnails")
    _scanned(document)
    storage = services.storage
    if not await to_thread.run_sync(storage.exists, document.thumbnail_key):
        await _drawn(request, session, partial(_draw_thumbnail, storage, document))
        await to_thread.run_sync(
            _charge_render, session, account.organization.id, account.user.id, document
        )
    return storage.response(document.thumbnail_key, media_type="image/png")


async def _drawn(request: Request, session: Session, draw: Callable[[], None]) -> None:
    """Run `draw`, which draws an image and keeps it, in one of the threads the API keeps for
    drawing (`app.state.drawing`, RENDER_PROCESSES of them), so that the requests waiting for a
    page hold none of the threads every other route runs in.

    The request's session gives its connection back first: a member scrolling through a long
    scan asks for more pages than are drawn at once, and each request waiting for its turn held
    a connection. It has only read, so the commit writes nothing, and what it read stays loaded
    for the charge after the drawing, which takes a connection again.
    """
    await to_thread.run_sync(session.commit)
    try:
        await to_thread.run_sync(draw, limiter=request.app.state.drawing)
    except FileNotFoundError:
        raise HTTPException(404, MISSING_FILE) from None
    except RenderError as error:
        if error.transient:
            raise HTTPException(503, str(error), headers={"Retry-After": "5"}) from error
        raise HTTPException(404, str(error)) from error


def _draw_page(storage: Storage, document: Document, page: int, key: str) -> None:
    # Kept, since the next page asked for is most likely of the same document: from a bucket
    # each page would otherwise download the whole original again.
    with storage.kept(document.storage_key) as original:
        storage.put(key, renderer().render(original, page, purpose=FOR_PANE), "image/png")


def _draw_thumbnail(storage: Storage, document: Document) -> None:
    with storage.kept(document.storage_key) as original:
        if document.mime_type == "application/pdf":
            image = renderer().render(
                original, 1, purpose=FOR_THUMBNAIL, scale=None, edge=THUMBNAIL_SIZE
            )
        else:
            image = _image_thumbnail(original)
    storage.put(document.thumbnail_key, image, "image/png")


def _image_thumbnail(path: Path) -> bytes:
    try:
        with open_image(path) as original:
            image = ImageOps.exif_transpose(original)
            image.thumbnail((THUMBNAIL_SIZE, THUMBNAIL_SIZE))
            if image.mode == "CMYK":
                image = image.convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=True)
    except (UnidentifiedImageError, OSError, IndexError, FileTooLarge) as error:
        raise HTTPException(404, "The file could not be rendered") from error
    return buffer.getvalue()


def _charge_render(session: Session, org_id: int, user_id: int, document: Document) -> None:
    """A page rendered for the first time is the member's usage; one served again from the
    storage costs nothing more (app/usage.py)."""
    record(
        session,
        org_id=org_id,
        project_id=document.project_id,
        user_id=user_id,
        kind=RENDER,
        measures={"pages": 1},
        ref=(DOCUMENT, document.id),
    )
    session.commit()


@router.get("/documents/{document_id}/outline", response_model=list[SectionOut])
def get_outline(document: DocumentDep, session: SessionDep):
    sections = session.scalars(
        select(Section).where(Section.document_id == document.id).order_by(Section.ordinal)
    ).all()
    nodes = {
        section.id: SectionOut(
            id=section.id,
            title=section.title,
            level=section.level,
            summary=section.summary,
            page_start=section.page_start,
            page_end=section.page_end,
            block_start=section.block_start,
            block_end=section.block_end,
        )
        for section in sections
    }
    roots: list[SectionOut] = []
    for section in sections:
        parent = nodes.get(section.parent_id) if section.parent_id else None
        (parent.children if parent else roots).append(nodes[section.id])
    return roots


@router.get("/documents/{document_id}/content", response_model=DocumentContent)
def get_content(document: DocumentDep, session: SessionDep):
    blocks = session.scalars(
        select(Block).where(Block.document_id == document.id).order_by(Block.ordinal)
    ).all()
    return DocumentContent(blocks=blocks, page_sizes=document.page_sizes or [])


@router.get("/documents/{document_id}/facts", response_model=list[FactOut])
def get_document_facts(document: DocumentDep, session: SessionDep):
    facts = session.scalars(
        select(Fact).where(Fact.document_id == document.id).order_by(Fact.name, Fact.id)
    ).all()
    return [fact_out(fact, document) for fact in facts]


@router.get("/passages/{passage_id}", response_model=PassageOut)
def get_passage(passage: PassageDep, session: SessionDep):
    document = session.get(Document, passage.document_id)
    return PassageOut(
        id=passage.id,
        document_id=passage.document_id,
        document_title=document.display_title,
        section_id=passage.section_id,
        context=passage.context,
        text=passage.text,
        page_start=passage.page_start,
        page_end=passage.page_end,
        block_start=passage.block_start,
        block_end=passage.block_end,
    )
