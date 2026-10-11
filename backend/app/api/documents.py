import hashlib
import io
import tempfile
from pathlib import Path
from typing import BinaryIO

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps, UnidentifiedImageError
from pypdfium2 import PdfiumError
from sqlalchemy import func, select

from app.api.deps import (
    DocumentDep,
    EditableDocumentDep,
    EditableProjectDep,
    PassageDep,
    ProjectDep,
    ServicesDep,
    SessionDep,
    Stage,
    WorkerDep,
)
from app.api.folders import folder_paths, resolve_folder
from app.config import get_settings
from app.indexing.pipeline import reset_stages
from app.models import STAGES, Block, Document, Fact, Project, Section
from app.parsing import IMAGE_TYPES, MIME_TYPES, SUPPORTED_EXTENSIONS
from app.parsing.pdf import render_page, render_thumbnail
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

router = APIRouter(tags=["documents"])

CHUNK = 1024 * 1024
# The longer side of a thumbnail, in pixels: enough for a tile on a high-density screen.
THUMBNAIL_SIZE = 320
# How long the browser may keep a rendered page or thumbnail; they never change.
IMAGE_MAX_AGE = 86400
MISSING_FILE = "The original file is missing from the storage"


@router.get("/projects/{project_id}/documents", response_model=list[DocumentOut])
def list_documents(project: ProjectDep, session: SessionDep):
    return session.scalars(
        select(Document).where(Document.project_id == project.id).order_by(Document.id.desc())
    ).all()


@router.post("/projects/{project_id}/documents", response_model=UploadResult, status_code=201)
def upload_documents(
    project: EditableProjectDep,
    files: list[UploadFile],
    session: SessionDep,
    services: ServicesDep,
    worker: WorkerDep,
    folder_id: int | None = None,
):
    """Upload files into a folder of the project (the root when no folder is given)."""
    settings = get_settings()
    limit = settings.max_upload_mb * 1024 * 1024
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

            duplicate = session.scalar(
                select(Document).where(Document.project_id == project.id, Document.sha256 == sha256)
            )
            if duplicate:
                where = ""
                if duplicate.folder_id is not None:
                    where = f" in {folder_paths(session, project.id)[duplicate.folder_id]}"
                rejected.append(
                    RejectedFile(
                        filename=filename,
                        reason=f"Already in this project as {duplicate.filename!r}{where}.",
                    )
                )
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
            session.add(document)
            session.flush()
            document.storage_key = original_key(project.org_id, project.id, document.id, suffix)
            # Stored before the row is committed: a storage that fails leaves no row behind,
            # and the error says what happened (`storage_unavailable` in main.py).
            services.storage.put(document.storage_key, spool, document.mime_type)
            session.commit()
            documents.append(document)
        finally:
            spool.unlink(missing_ok=True)

    if documents and worker:
        worker.notify()
    return UploadResult(documents=documents, rejected=rejected)


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
    reset_stages(document, from_stage)
    session.commit()
    if worker:
        worker.notify()
    return document


@router.get("/documents/{document_id}/file")
def download_file(document: DocumentDep, services: ServicesDep) -> Response:
    """The uploaded file as it was uploaded; the storage decides whether it is sent from here or
    from a signed URL."""
    storage = services.storage
    if not storage.exists(document.storage_key):
        raise HTTPException(404, MISSING_FILE)
    return storage.response(
        document.storage_key, media_type=document.mime_type, filename=document.filename
    )


@router.get("/documents/{document_id}/pages/{page}.png")
def page_image(document: DocumentDep, page: int, services: ServicesDep) -> Response:
    """A rendered PDF page, for showing where on the page a passage sits. Pages are rendered
    the first time they are asked for and kept beside the original."""
    if document.mime_type != "application/pdf":
        raise HTTPException(404, "Only PDF documents have page images")
    if not 1 <= page <= (document.page_count or 0):
        raise HTTPException(404, "No such page")
    storage = services.storage
    key = document.page_image_key(page)
    if not storage.exists(key):
        try:
            with storage.localize(document.storage_key) as original:
                storage.put(key, render_page(original, page), "image/png")
        except FileNotFoundError:
            raise HTTPException(404, MISSING_FILE) from None
    return storage.response(key, media_type="image/png", max_age=IMAGE_MAX_AGE)


@router.get("/documents/{document_id}/thumbnail.png")
def thumbnail(document: DocumentDep, services: ServicesDep) -> Response:
    """A small picture of the document for the library: a PDF's first page, or the image itself."""
    if document.mime_type != "application/pdf" and document.mime_type not in IMAGE_TYPES.values():
        raise HTTPException(404, "Only PDF and image documents have thumbnails")
    storage = services.storage
    if not storage.exists(document.thumbnail_key):
        try:
            with storage.localize(document.storage_key) as original:
                image = _thumbnail_of(original, document.mime_type)
        except FileNotFoundError:
            raise HTTPException(404, MISSING_FILE) from None
        except (PdfiumError, UnidentifiedImageError, OSError, IndexError) as error:
            raise HTTPException(404, "The file could not be rendered") from error
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        storage.put(document.thumbnail_key, buffer.getvalue(), "image/png")
    return storage.response(document.thumbnail_key, media_type="image/png", max_age=IMAGE_MAX_AGE)


def _thumbnail_of(path: Path, mime_type: str) -> Image.Image:
    if mime_type == "application/pdf":
        return render_thumbnail(path, THUMBNAIL_SIZE)
    with Image.open(path) as original:
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
