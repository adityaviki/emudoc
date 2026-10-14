import hashlib
import io
import tempfile
from pathlib import Path
from typing import BinaryIO

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps, UnidentifiedImageError
from pypdfium2 import PdfiumError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.accounts import Account
from app.api.deps import (
    AccountDep,
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
from app.api.folders import folder_paths, resolve_folder
from app.config import Settings, get_settings
from app.entitlements import check_upload, size_label
from app.indexing.pipeline import reset_stages
from app.jobs import queue_index
from app.models import STAGES, Block, Document, Fact, Project, Section
from app.parsing import IMAGE_TYPES, MIME_TYPES, SUPPORTED_EXTENSIONS, FileTooLarge
from app.parsing.images import open_image
from app.parsing.pdf import render_page, render_thumbnail
from app.parsing.signatures import wrong_type
from app.ratelimit import UPLOADS
from app.scanning import scan_passed
from app.schemas import (
    DocumentContent,
    DocumentOut,
    DocumentPatch,
    FactOut,
    PassageOut,
    RejectedFile,
    SectionOut,
    UploadResult,
)
from app.storage import original_key
from app.usage import DOCUMENT, RENDER, UPLOAD, record

router = APIRouter(tags=["documents"])

CHUNK = 1024 * 1024
# The longer side of a thumbnail, in pixels: enough for a tile on a high-density screen.
THUMBNAIL_SIZE = 320
# How long the browser may keep a rendered page or thumbnail; they never change.
IMAGE_MAX_AGE = 86400
MISSING_FILE = "The original file is missing from the storage"
# Why a file is not served (app/scanning.py): for good, or until its scan has passed.
QUARANTINED_FILE = (
    "The malware scan flagged this file, so it cannot be opened or downloaded. Delete it."
)
UNSCANNED_FILE = (
    "This file is served once the malware scan has passed; the document shows how far the scan is."
)
# The unique constraint on (project_id, sha256), named by the convention in db.py: the insert
# of a second copy of the same bytes fails on it when two uploads race.
DUPLICATE_CONSTRAINT = "documents_project_id_sha256_key"
# When the copy that won the race is gone again before it can be named.
LOST_RACE = (
    "Another upload of this file finished first. Upload it again if it is not in the project."
)


@router.get("/projects/{project_id}/documents", response_model=list[DocumentOut])
def list_documents(project: ProjectDep, session: SessionDep):
    return session.scalars(
        select(Document).where(Document.project_id == project.id).order_by(Document.id.desc())
    ).all()


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
    folder_id: int | None = None,
):
    """Upload files into a folder of the project (the root when no folder is given).

    The request is refused whole, before anything is stored, when it carries more files or
    more bytes than one upload may (413, naming the limit), and when the organisation's plan
    has no room for a document at all (402, which the app shows as the way to upgrade); each
    file is then checked for its own size, for whether its bytes are the type its name claims
    (app/parsing/signatures.py) and against the plan, and one that fails is rejected with the
    sentence while the others go in (app/entitlements.py).
    """
    settings = get_settings()
    limit = settings.max_upload_mb * 1024 * 1024
    if reason := too_many(files, settings):
        raise HTTPException(413, reason)
    organization = account.organization
    if reason := check_upload(session, organization):
        raise HTTPException(402, reason)
    folder = resolve_folder(session, project, folder_id)
    documents: list[Document] = []
    rejected: list[RejectedFile] = []

    for upload in files:
        filename = Path(upload.filename or "unnamed").name
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            rejected.append(
                RejectedFile(filename=filename, reason=f"Unsupported type. Use {supported}.")
            )
            continue

        # Spooled to a local temporary file while hashing: the hash finds a re-upload of the
        # same bytes before anything is stored, and the key needs the row's id. The spool goes
        # whether the file is kept, rejected or fails to store.
        spool, size, sha256 = _spool(upload.file, limit)
        try:
            if size > limit or size == 0:
                reason = "Empty file." if size == 0 else f"Larger than {settings.max_upload_mb} MB."
                rejected.append(RejectedFile(filename=filename, reason=reason))
                continue
            # The extension picks the parser and the type the file is served as, so the bytes
            # must be what it says; refused here, before a duplicate is looked for or the plan
            # is counted, with a sentence about the file rather than a parser's a stage later.
            if reason := wrong_type(spool, suffix):
                rejected.append(RejectedFile(filename=filename, reason=reason))
                continue

            duplicate = _already_present(session, project.id, sha256, filename)
            if duplicate:
                rejected.append(duplicate)
                continue
            # Counted again per file: each one committed above fills the plan a little more.
            if reason := check_upload(session, organization, size):
                rejected.append(RejectedFile(filename=filename, reason=reason))
                continue

            document = Document(
                project_id=project.id,
                folder_id=folder.id if folder else None,
                filename=unique_filename(
                    session, project.id, folder.id if folder else None, filename
                ),
                mime_type=MIME_TYPES[suffix],
                size_bytes=size,
                sha256=sha256,
                storage_key="",
                status="queued",
                stages={name: {"status": "pending", "detail": ""} for name in STAGES},
            )
            # The insert runs in a savepoint: another request may have stored the same bytes
            # between the check above and here, and the unique constraint is the only thing
            # that sees both. Its failure rolls the row back and leaves the transaction good
            # for the next file; the file is rejected as it would have been a moment later.
            try:
                with session.begin_nested():
                    session.add(document)
                    session.flush()
            except IntegrityError as error:
                if _constraint_of(error) != DUPLICATE_CONSTRAINT:
                    raise
                rejected.append(
                    _already_present(session, project.id, sha256, filename)
                    or RejectedFile(filename=filename, reason=LOST_RACE)
                )
                continue
            document.storage_key = original_key(project.org_id, project.id, document.id, suffix)
            # Stored before the row is committed: a storage that fails leaves no row behind,
            # and the error says what happened (`storage_unavailable` in main.py).
            services.storage.put(document.storage_key, spool, document.mime_type)
            # The document, the job that indexes it and the member's usage are committed
            # together. The bytes are the upload's; its pages are counted when the parse
            # learns how many there are (app/indexing/pipeline.py).
            queue_index(session, document)
            record(
                session,
                org_id=project.org_id,
                project_id=project.id,
                user_id=account.user.id,
                kind=UPLOAD,
                measures={"bytes": size},
                ref=(DOCUMENT, document.id),
            )
            session.commit()
            documents.append(document)
        finally:
            spool.unlink(missing_ok=True)

    if documents and worker:
        worker.notify()
    return UploadResult(documents=documents, rejected=rejected)


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


def _already_present(
    session: SessionDep, project_id: int, sha256: str, filename: str
) -> RejectedFile | None:
    """The rejection for a file whose bytes the project already holds, naming where."""
    duplicate = session.scalar(
        select(Document).where(Document.project_id == project_id, Document.sha256 == sha256)
    )
    if duplicate is None:
        return None
    where = ""
    if duplicate.folder_id is not None:
        where = f" in {folder_paths(session, project_id)[duplicate.folder_id]}"
    return RejectedFile(
        filename=filename, reason=f"Already in this project as {duplicate.filename!r}{where}."
    )


def _constraint_of(error: IntegrityError) -> str | None:
    """The name of the constraint the database refused the statement on, as psycopg reports it."""
    diagnostics = getattr(error.orig, "diag", None)
    return getattr(diagnostics, "constraint_name", None)


def _spool(upload: BinaryIO, limit: int) -> tuple[Path, int, str]:
    """Copy the upload to a temporary file, up to the limit, hashing it on the way.

    Returns the file, how many bytes were read and the hash; a file over the limit stops at the
    first byte past it, so the size reported is just above the limit, never the upload's.
    """
    digest = hashlib.sha256()
    size = 0
    with tempfile.NamedTemporaryFile(prefix="emudoc-upload-", delete=False) as spool:
        while chunk := upload.read(CHUNK):
            size += len(chunk)
            if size > limit:
                break
            digest.update(chunk)
            spool.write(chunk)
    return Path(spool.name), size, digest.hexdigest()


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


def unique_filename(session: SessionDep, project_id: int, folder_id: int | None, name: str) -> str:
    """The name, or "name (2).ext" and so on when a sibling already carries it."""
    taken = {
        existing.lower()
        for existing in session.scalars(
            select(Document.filename).where(
                Document.project_id == project_id, Document.folder_id == folder_id
            )
        )
    }
    if name.lower() not in taken:
        return name
    stem, suffix = Path(name).stem, Path(name).suffix
    for number in range(2, 10_000):
        candidate = f"{stem} ({number}){suffix}"
        if candidate.lower() not in taken:
            return candidate
    return name


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document: EditableDocumentDep, session: SessionDep, services: ServicesDep):
    session.delete(document)
    session.commit()
    services.storage.discard(document.storage_prefix)


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
def download_file(document: DocumentDep, services: ServicesDep) -> Response:
    """The uploaded file as it was uploaded; the storage decides whether it is sent from here or
    from a signed URL.

    It comes as a download: a PDF that a browser opened in its own viewer could run scripts and
    submit forms, and the app shows a document's pages from its own renderings anyway. An image
    is the exception: the document pane's preview is an `<img>`, and a browser runs nothing in one.
    """
    _scanned(document)
    storage = services.storage
    if not storage.exists(document.storage_key):
        raise HTTPException(404, MISSING_FILE)
    return storage.response(
        document.storage_key,
        media_type=document.mime_type,
        filename=document.filename,
        inline=document.mime_type in IMAGE_TYPES.values(),
    )


@router.get("/documents/{document_id}/pages/{page}.png")
def page_image(
    document: DocumentDep,
    page: int,
    services: ServicesDep,
    session: SessionDep,
    account: AccountDep,
) -> Response:
    """A rendered PDF page, for showing where on the page a passage sits. Pages are rendered
    the first time they are asked for and kept beside the original for PAGE_IMAGE_DAYS."""
    if document.mime_type != "application/pdf":
        raise HTTPException(404, "Only PDF documents have page images")
    if not 1 <= page <= (document.page_count or 0):
        raise HTTPException(404, "No such page")
    _scanned(document)
    storage = services.storage
    key = document.page_image_key(page)
    if not storage.exists(key):
        try:
            with storage.localize(document.storage_key) as original:
                storage.put(key, render_page(original, page), "image/png")
        except FileNotFoundError:
            raise HTTPException(404, MISSING_FILE) from None
        _charge_render(session, account, document)
    return storage.response(key, media_type="image/png", max_age=IMAGE_MAX_AGE)


@router.get("/documents/{document_id}/thumbnail.png")
def thumbnail(
    document: DocumentDep, services: ServicesDep, session: SessionDep, account: AccountDep
) -> Response:
    """A small picture of the document for the library: a PDF's first page, or the image itself."""
    if document.mime_type != "application/pdf" and document.mime_type not in IMAGE_TYPES.values():
        raise HTTPException(404, "Only PDF and image documents have thumbnails")
    _scanned(document)
    storage = services.storage
    if not storage.exists(document.thumbnail_key):
        try:
            with storage.localize(document.storage_key) as original:
                image = _thumbnail_of(original, document.mime_type)
        except FileNotFoundError:
            raise HTTPException(404, MISSING_FILE) from None
        except (PdfiumError, UnidentifiedImageError, OSError, IndexError, FileTooLarge) as error:
            raise HTTPException(404, "The file could not be rendered") from error
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        storage.put(document.thumbnail_key, buffer.getvalue(), "image/png")
        _charge_render(session, account, document)
    return storage.response(document.thumbnail_key, media_type="image/png", max_age=IMAGE_MAX_AGE)


def _charge_render(session: SessionDep, account: Account, document: Document) -> None:
    """A page rendered for the first time is the member's usage; one served again from the
    storage costs nothing more (app/usage.py)."""
    record(
        session,
        org_id=account.organization.id,
        project_id=document.project_id,
        user_id=account.user.id,
        kind=RENDER,
        measures={"pages": 1},
        ref=(DOCUMENT, document.id),
    )
    session.commit()


def _thumbnail_of(path: Path, mime_type: str) -> Image.Image:
    if mime_type == "application/pdf":
        return render_thumbnail(path, THUMBNAIL_SIZE)
    with open_image(path) as original:
        image = ImageOps.exif_transpose(original)
        image.thumbnail((THUMBNAIL_SIZE, THUMBNAIL_SIZE))
        return image.convert("RGB") if image.mode == "CMYK" else image


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


def fact_out(fact: Fact, document: Document) -> FactOut:
    return FactOut(
        id=fact.id,
        document_id=fact.document_id,
        document_title=document.display_title,
        name=fact.name,
        value_type=fact.value_type,
        value=fact.value,
        number=fact.number,
        date=fact.date,
        currency=fact.currency,
        quote=fact.quote,
        page=fact.page,
        block_ordinal=fact.block_ordinal,
        passage_id=fact.passage_id,
        verified=fact.verified,
    )
