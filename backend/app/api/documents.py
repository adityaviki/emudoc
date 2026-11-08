import base64
import datetime as dt
import hashlib
import io
import json
import operator
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Annotated, Any

from anyio import to_thread
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from PIL import ImageOps, UnidentifiedImageError
from sqlalchemy import (
    ColumnElement,
    DateTime,
    Row,
    and_,
    func,
    literal_column,
    or_,
    select,
    text,
    tuple_,
)
from sqlalchemy.orm import Session

from app.accounts import Account
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
    WorkerDep,
    limited,
)
from app.api.folders import resolve_folder
from app.audit import audit
from app.config import Settings, get_settings
from app.conversion import converts, queue_view
from app.costs import costs_shown
from app.details import edit_details
from app.entitlements import PlanRefused, check_upload, size_label
from app.fact_checks import fact_out
from app.folders import (
    folder_names,
)
from app.indexing.pipeline import OPTIONAL_STAGES, reset_stages, unfinished_stages, went_without
from app.jobs import BULK, LIVE, queue_index
from app.languages import UNKNOWN_LANGUAGE, known_languages
from app.mail import LISTED
from app.metrics import UPLOAD_REJECTIONS
from app.models import (
    DOCUMENT_DETAILS,
    STAGES,
    Block,
    Document,
    DocumentFavorite,
    DocumentVisit,
    Fact,
    Project,
    Section,
)
from app.notices import NOT_INDEXED, record_upload, settle_upload
from app.parsing import IMAGE_TYPES, PAGE_TYPES, FileTooLarge
from app.parsing.images import open_image
from app.ratelimit import UPLOADS
from app.recents import RECENT
from app.rendering import FOR_PANE, FOR_THUMBNAIL, RenderError, renderer
from app.sample import SAMPLE_UPLOADS
from app.scanning import scan_passed
from app.schemas import (
    DocumentContent,
    DocumentDetailsIn,
    DocumentOut,
    DocumentPage,
    DocumentPatch,
    DocumentSort,
    DocumentsReprocessIn,
    DocumentsReprocessOut,
    FactOut,
    PassageOut,
    SectionOut,
    SortOrder,
    Stage,
    UploadFailureOut,
    UploadResult,
    UploadStatusOut,
)
from app.selection import NOT_CHOSEN, chosen_documents
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
# Why a document has no pages to draw, or no thumbnail, and why it has no view to make.
NO_PAGES = (
    "Only PDF documents, scans and the documents shown as they look have page images; this one "
    "is shown by its text."
)
NO_THUMBNAIL = (
    "Only PDF documents, scans, images and the documents shown as they look have thumbnails."
)
NOT_CONVERTED = (
    "This document is shown by its text: this deployment shows no files as they look, or none "
    "of its type."
)
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
BOTH_WAYS = "Give a detail a value of your own, or take what the reading found for it, not both."
ONE_PLACE = (
    "Ask for one place of the library at a time: a folder, your favourites or your recent "
    "documents."
)
# A name filter looks through every folder of the project, so it cannot be one folder's.
NAME_IN_FOLDER = (
    "A filter by name looks in every folder of the project: ask for it without a folder."
)
# The longest part of a file's name the library's filter looks for.
MAX_NAME_FILTER = 200
# The columns each order of a listing runs by before the id, which `ix_documents_listing` and
# the `ix_documents_listing_*` indexes serve for a folder; "date" is `doc_date`, its undated
# documents after the dated (`_listed`).
SORTS = {
    "name": (Document.filename,),
    "added": (),
    "date": (Document.doc_date,),
    "size": (Document.size_bytes,),
}
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
    account: AccountDep,
    request: Request,
    response: Response,
    folder_id: int | None = None,
    favorites: bool = False,
    recent: bool = False,
    sort: DocumentSort = "name",
    order: SortOrder = "asc",
    after: str | None = None,
    limit: int = Query(PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    name: str | None = Query(None, max_length=MAX_NAME_FILTER),
):
    """A page of the documents in one place of the library, in the order asked for, past the one
    `after` names when it is given: directly in a folder (the root when no folder is given); or
    wherever they are in the project, the reader's favourites, or the documents they opened
    lately, the newest first (`RECENT` of them, on one page, whatever the order asked for). With
    `name`, only the files whose names hold those letters in any case, from every folder of the
    project, or from the favourites or the recent documents asked for.

    The page carries an ETag of exactly what it shows, and a request whose If-None-Match names
    it is answered 304 without a body: the library asks again every moment while a document in
    the place is being indexed, and most of those moments change nothing on its page.
    """
    place = _place(session, project, account, folder_id, favorites, recent, name)
    if place.recent:
        limit = RECENT
    cursor = None if after is None or place.recent else _cursor(after, sort, order)
    # The versions alone first: when they are the ones the client holds, nothing more is read
    # or serialised. A page that is sent carries the tag of its own rows, read with them.
    versions = _listed(session, place, (Document.id, ROW_VERSION), sort, order, cursor, limit)
    tag_held = _page_tag(versions, limit)
    if tag_held in _tags_held(request):
        return Response(status_code=304, headers={"ETag": tag_held})
    rows = _listed(session, place, (Document, ROW_VERSION), sort, order, cursor, limit)
    response.headers["ETag"] = _page_tag(
        [(document.id, version) for document, version in rows], limit
    )
    documents = [document for document, _ in rows[:limit]]
    return DocumentPage(
        documents=documents,
        next_after=_cursor_of(documents[-1], sort, order) if len(rows) > limit else None,
    )


@dataclass(frozen=True)
class _Place:
    """What a listing's documents are: the conditions on them, and the reader's favourites or
    visits joined to them; `flat` for a place that spans the project's folders."""

    conditions: tuple[ColumnElement, ...]
    joined: tuple[type[DocumentFavorite] | type[DocumentVisit], ColumnElement] | None = None
    flat: bool = False
    recent: bool = False


def _place(
    session: Session,
    project: Project,
    account: Account,
    folder_id: int | None,
    favorites: bool,
    recent: bool,
    name: str | None = None,
) -> _Place:
    """The place a listing asks for, the project's own; a folder that is not found, and more
    than one place, are refused. A name narrows the favourites or the recent ones, and on its
    own spans the project's folders, so it is refused with a folder."""
    named = [favorites, recent]
    if sum(named) > 1 or (any(named) and folder_id is not None):
        raise HTTPException(422, ONE_PLACE)
    words = (name or "").strip()
    if words and folder_id is not None:
        raise HTTPException(422, NAME_IN_FOLDER)
    live = (Document.project_id == project.id, Document.deleted_at.is_(None))
    if words:
        live = (*live, Document.filename.icontains(words, autoescape=True))
        if not any(named):
            return _Place(live, flat=True)
    if favorites or recent:
        held = DocumentFavorite if favorites else DocumentVisit
        joined = and_(held.document_id == Document.id, held.user_id == account.user.id)
        return _Place(live, joined=(held, joined), flat=True, recent=recent)
    folder = resolve_folder(session, project, folder_id)
    where = Document.folder_id == folder.id if folder else Document.folder_id.is_(None)
    return _Place((*live, where))


def _listed(
    session: Session,
    place: _Place,
    columns: tuple,
    sort: str,
    order: str,
    cursor: tuple[Any, int] | None,
    limit: int,
) -> list[Row]:
    """The place's rows past the cursor in the order asked for, one more than the page holds
    when there are more. By a document's date, the dated come first, then the undated, each run
    by its own index scan, the cursor saying which of the two it is in by its date or its None."""
    query = select(*columns).where(*place.conditions)
    if place.joined is not None:
        query = query.join(*place.joined)
    if place.recent:
        latest = DocumentVisit.opened_at.desc(), Document.id.desc()
        return list(session.execute(query.order_by(*latest).limit(limit)).all())
    if place.flat:
        # How many documents a tag or the favourites take decides the plan, so it is planned
        # for this project rather than once for every one (database.md).
        session.execute(text("SET LOCAL plan_cache_mode = force_custom_plan"))
    after = operator.lt if order == "desc" else operator.gt
    by = (lambda column: column.desc()) if order == "desc" else (lambda column: column.asc())
    if sort != "date":
        keys = (*SORTS[sort], Document.id)
        if cursor is not None:
            value, document_id = cursor
            held = (value, document_id) if SORTS[sort] else (document_id,)
            query = query.where(after(tuple_(*keys), tuple_(*held)))
        return list(session.execute(query.order_by(*map(by, keys)).limit(limit + 1)).all())
    rows: list[Row] = []
    if cursor is None or cursor[0] is not None:
        dated = query.where(Document.doc_date.is_not(None))
        if cursor is not None:
            dated = dated.where(after(tuple_(Document.doc_date, Document.id), tuple_(*cursor)))
        keys = Document.doc_date, Document.id
        rows = list(session.execute(dated.order_by(*map(by, keys)).limit(limit + 1)).all())
    if len(rows) <= limit:
        undated = query.where(Document.doc_date.is_(None))
        if cursor is not None and cursor[0] is None:
            undated = undated.where(after(Document.id, cursor[1]))
        rest = undated.order_by(by(Document.id)).limit(limit + 1 - len(rows))
        rows += session.execute(rest).all()
    return rows


def _page_tag(rows: Sequence[tuple[int, str]], limit: int) -> str:
    """The ETag of a page: the version of each row on it and whether more follow. A row's
    version is its `xmin`, the transaction that wrote it, which every change to it moves,
    an ORM update or a raw one alike; a timestamp of the change would not do, since a
    transaction that began earlier can commit later with an older one. The release is in it
    too, since a new one may serve the same rows otherwise, and whether the reader sees what
    the documents cost, which a change of their role changes (app/costs.py)."""
    shown = ",".join(f"{document_id}:{version}" for document_id, version in rows[:limit])
    more = "more" if len(rows) > limit else "last"
    costs = "costs" if costs_shown() else "counts"
    return f'"{hashlib.sha256(f"{VERSION}|{costs}|{shown}|{more}".encode()).hexdigest()[:32]}"'


def _tags_held(request: Request) -> set[str]:
    """The ETags an If-None-Match names, weak or strong alike: a proxy that compresses the
    response may weaken the tag on the way, and the comparison for this header is weak."""
    header = request.headers.get("if-none-match", "")
    return {tag.strip().removeprefix("W/") for tag in header.split(",") if tag.strip()}


def _cursor_of(document: Document, sort: str, order: str) -> str:
    """Where the next page starts: after this document's place in the order, as `after` takes
    it. It carries the value the document is ordered by rather than its id alone, so that it
    still holds once the document is renamed, moved or deleted; and the order, so that a cursor
    of another is refused rather than read as this one's. By name ascending, the library's first
    order, it is the name and id alone, as it was before the others."""
    if (sort, order) == ("name", "asc"):
        held: list = [document.filename, document.id]
    else:
        value = {
            "name": document.filename,
            "added": None,
            "date": document.doc_date.isoformat() if document.doc_date else None,
            "size": document.size_bytes,
        }[sort]
        held = [f"{sort}:{order}", value, document.id]
    encoded = json.dumps(held).encode()
    return base64.urlsafe_b64encode(encoded).decode().rstrip("=")


def _cursor(after: str, sort: str, order: str) -> tuple[Any, int]:
    """The value and id an `after` carries for the order asked for; anything else, a cursor of
    another order among them, is refused rather than guessed at."""
    try:
        held = json.loads(base64.urlsafe_b64decode(after + "=" * (-len(after) % 4)))
    except (ValueError, TypeError):
        raise HTTPException(422, BAD_CURSOR) from None
    if not isinstance(held, list):
        raise HTTPException(422, BAD_CURSOR)
    if len(held) == 2:
        held = ["name:asc", *held]
    if len(held) != 3 or held[0] != f"{sort}:{order}":
        raise HTTPException(422, BAD_CURSOR)
    _, value, document_id = held
    if not isinstance(document_id, int) or not 0 < document_id < 2**31:
        raise HTTPException(422, BAD_CURSOR)
    if sort == "name" and isinstance(value, str):
        return value, document_id
    if sort == "added" and value is None:
        return None, document_id
    if sort == "size" and type(value) is int and 0 <= value < 2**63:
        return value, document_id
    if sort == "date" and value is None:
        return None, document_id
    if sort == "date" and isinstance(value, str):
        try:
            return dt.date.fromisoformat(value), document_id
        except ValueError:
            raise HTTPException(422, BAD_CURSOR) from None
    raise HTTPException(422, BAD_CURSOR)


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
    since it counts against nothing the plan holds (app/sample). A message saved from a mail
    program (.eml) that the page uploads is opened, its text and attachments taken in its place;
    one a program sends with an API key is refused as any other type the parsers do not read
    (app/unpacking.py).
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
        # A program pairs the documents kept with the files it sent, one each, so a file that
        # brings others is opened for the page alone, never for an API key (app/unpacking.py).
        unpack=account.key is None,
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


@router.patch("/documents/{document_id}/details", response_model=DocumentOut)
def change_details(document: EditableDocumentDep, body: DocumentDetailsIn, session: SessionDep):
    """Set the document's title, kind, date or language as the member's, which later readings
    keep, or take what the reading found again (app/details.py)."""
    given = {
        name: getattr(body, name) for name in DOCUMENT_DETAILS if name in body.model_fields_set
    }
    as_read = set(body.as_read)
    if as_read & given.keys():
        raise HTTPException(422, BOTH_WAYS)
    if given.get("language") is not None and body.language not in known_languages(session):
        raise HTTPException(400, UNKNOWN_LANGUAGE)
    edit_details(session, document, given, as_read)
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


@router.post(
    "/projects/{project_id}/documents/reprocess",
    response_model=DocumentsReprocessOut,
    status_code=202,
)
def reprocess_documents(
    project: EditableProjectDep,
    body: DocumentsReprocessIn,
    session: SessionDep,
    worker: WorkerDep,
):
    """Index the documents chosen in the library again, each as its own "Index again" (from
    `from_stage`) or "Index the rest" (without one) would, behind fresh uploads as the project's
    "Index the rest" is. One being indexed right now is left to its run and counted, where its
    own route refuses it, so that the others still go."""
    documents = chosen_documents(session, project.id, body.document_ids, lock=True)
    if documents is None:
        raise HTTPException(404, NOT_CHOSEN)
    queued, skipped = [], 0
    for document in documents:
        if document.status == "processing":
            skipped += 1
            continue
        stage = body.from_stage or next(iter(unfinished_stages(document)), None)
        if stage is None:
            continue
        reset_stages(document, stage)
        queue_index(session, document, priority=BULK)
        queued.append(document)
    session.commit()
    if worker and queued:
        worker.notify()
    return DocumentsReprocessOut(documents=queued, skipped=skipped)


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
    """A rendered page of a PDF or a scan, for showing where on the page a passage sits. Its
    first pages are drawn once it is parsed (PRERENDER_PAGES, app/rendering.py), any other the
    first time it is asked for, and each is kept beside the original for PAGE_IMAGE_DAYS."""
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
    drawn = drawn_from(document)
    if drawn is None:
        raise HTTPException(404, NO_PAGES)
    source, pages = drawn
    if not 1 <= page <= pages:
        raise HTTPException(404, "No such page")
    _scanned(document)
    key = document.page_image_key(page)
    if not await to_thread.run_sync(storage.exists, key):
        await _drawn(request, session, partial(_draw_page, storage, source, page, key))
        await to_thread.run_sync(_charge_render, session, org_id, user_id, document)
    return storage.response(key, media_type="image/png")


def drawn_from(document: Document) -> tuple[str, int] | None:
    """The file a document's pages are drawn from, by its key, and how many pages it has: a
    PDF's or a scan's own, or the view of a Word, PowerPoint or Excel file once it is made
    (app/conversion.py), which has no pages of its own; None for a document with none to draw."""
    if document.mime_type in PAGE_TYPES:
        return document.storage_key, document.page_count or 0
    if document.view_state == "ready" and document.view_page_sizes:
        return document.view_key, len(document.view_page_sizes)
    return None


@router.post("/documents/{document_id}/view", response_model=DocumentOut)
def make_view(document: DocumentDep, services: ServicesDep, session: SessionDep) -> Document:
    """Have the document's view made (app/conversion.py): one it never had, as a member opens it
    (a document from before the converter answered), or one that failed, from "Try again". Any
    member who reads the project may, a viewer too: it changes nothing of the project, only
    how a document of it is shown. One queued or made already is left as it is. 409 when there is
    none to make: a deployment without a converter, or a type that is not converted."""
    if services.converter is None or not converts(document):
        raise HTTPException(409, NOT_CONVERTED)
    _scanned(document)
    # Under the row's lock, so that two tabs opening the document at once queue one job.
    document = session.get(Document, document.id, with_for_update=True, populate_existing=True)
    if document.view_state == "failed":
        document.view_state = None
    if queue_view(session, document, services.converter, priority=LIVE):
        session.commit()
    else:
        session.rollback()
    return document


@router.get("/documents/{document_id}/thumbnail.png")
async def thumbnail(
    document: DocumentDep,
    request: Request,
    services: ServicesDep,
    session: SessionDep,
    account: AccountDep,
) -> Response:
    """A small picture of the document for the library: the first page of a PDF, a scan or a
    document shown as it looks (app/conversion.py), or the image itself."""
    if drawn_from(document) is None and document.mime_type not in IMAGE_TYPES.values():
        raise HTTPException(404, NO_THUMBNAIL)
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


def _draw_page(storage: Storage, source: str, page: int, key: str) -> None:
    # Kept, since the next page asked for is most likely of the same document: from a bucket
    # each page would otherwise download the whole original again.
    with storage.kept(source) as original:
        storage.put(key, renderer().render(original, page, purpose=FOR_PANE), "image/png")


def _draw_thumbnail(storage: Storage, document: Document) -> None:
    drawn = drawn_from(document)
    with storage.kept(drawn[0] if drawn else document.storage_key) as original:
        if drawn:
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
    return DocumentContent(
        blocks=blocks,
        page_sizes=document.page_sizes or [],
        view_page_sizes=_view_pages(document),
    )


def _view_pages(document: Document) -> list[list[float]]:
    """Its view's page sizes, once the view is made (app/conversion.py)."""
    return document.view_page_sizes or [] if document.view_state == "ready" else []


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
