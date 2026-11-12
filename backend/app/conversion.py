"""Showing a Word, PowerPoint, Excel, RTF or HTML document as it looks: its file converted to a PDF
by the converter (app/converter.py, the image run as a container of its own, which CONVERTER_URL
names), kept beside the original as its view (`Document.view_key`), and drawn page by page as a
PDF's own pages are (`page_response` in app/api/documents.py) for the document pane, the library's
thumbnail and a share link's page.

The view is how the file looks and nothing more: the document is searched, cited and asked about
by its text as before, and the pane offers that text beside the pages, where a cited passage is
marked. So the conversion is no stage of the indexing, and its failure fails nothing but the
view: a `convert_document` job, queued once the parse stage has read the file (`queue_view`, so
after its malware scan), or when a member opens a document that has none yet (one uploaded before
the converter answered, or before views were made at all).

`Document.view_state` says where a view stands: null for none (a type that is not converted, or
one not asked for yet), `queued` while the job waits or runs, `ready` with each page's size in
`view_page_sizes`, `failed` with the sentence a member is told why in `view_error`. A converter
out of reach, or busy, is a passing problem tried again MAX_TRIES times with a doubling wait; a
file it could not convert is not, and the member may ask again ("Try again", `POST
/documents/{id}/view`).

The PDF comes from a process that read an upload with LibreOffice, so it is read as any upload
is: never by pdfium in this process, but measured and drawn by the render children
(app/rendering.py), and never sent to a browser, which is shown the pages drawn from it.

Without CONVERTER_URL nothing is converted, and the pane shows these documents by their text.
"""

import logging
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.config import Settings, get_settings, parse_converter_url
from app.db import SessionLocal
from app.errors import AppError, shown
from app.jobs import CONVERT_DOCUMENT, Job, Lease, LeaseLost, enqueue, renew
from app.models import Document
from app.rendering import RenderError, renderer
from app.scanning import scan_passed
from app.storage import Storage, StorageError

log = logging.getLogger(__name__)

# The types that are converted, by MIME type (MIME_TYPES in app/parsing/__init__.py), each with
# the name the converter takes it by (TYPES in app/converter.py).
CONVERTIBLE = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/rtf": "rtf",
    "text/html": "html",
}
# How long the converter may take to answer, once it took the connection: its own limit on a
# conversion (`--timeout`, 120 s) and as long again for a turn behind the others.
TIMEOUT = httpx.Timeout(300, connect=10)
CHECK_TIMEOUT = 10
# The most of a PDF kept as a view; one larger is refused, as a file too large to show.
MAX_VIEW_BYTES = 500 * 1024 * 1024
# A converter out of reach is asked again after FIRST_WAIT, then twice as long each time, up to
# MAX_TRIES tries in all: about a quarter of an hour.
MAX_TRIES = 4
FIRST_WAIT = timedelta(minutes=2)
PDF_HEADER = b"%PDF-"

# What a member is told while the converter cannot be asked, and once it could not be for good.
UNAVAILABLE = (
    "The service that shows files as they look is unavailable just now, so this file's pages "
    "will be made once it is back. Its text is below meanwhile."
)
GAVE_UP = (
    "The service that shows files as they look was unavailable, so this file's pages could not "
    "be made. Try again later; its text is below meanwhile."
)
# What a member is told of a file the converter could not show.
UNSHOWN = (
    "This file could not be shown as it looks. Its text is below; to see the file itself, "
    "download the original."
)
TOO_MANY_PAGES = (
    "This file could not be shown as it looks: its pages would be more than {limit:,}. Its text "
    "is below; to see the file itself, download the original."
)


class ConvertError(AppError):
    """No PDF was made of the file: the converter could not be reached or was busy (passing), or
    it could not convert the file (not). The message names the converter and what it answered,
    the operator's; `members` says what the view shows."""

    members = UNSHOWN


class Converter:
    """The converter CONVERTER_URL names, over HTTP (app/converter.py)."""

    def __init__(self, url: str):
        self.url = url
        # Not through a proxy the environment names: the converter is on the deployment's own
        # network, beside the worker, and nothing else should see the files.
        self._client = httpx.Client(base_url=url, timeout=TIMEOUT, trust_env=False)

    def check(self) -> str:
        """LibreOffice's version as the converter reports it; raises `ConvertError` when it does
        not answer."""
        try:
            response = self._client.get("/health", timeout=CHECK_TIMEOUT)
        except httpx.HTTPError as error:
            raise self._unreachable(error) from error
        if response.status_code != 200:
            raise ConvertError(
                f"The converter at {self.url} answered its health check with "
                f"{response.status_code}.",
                transient=True,
                members=UNAVAILABLE,
            )
        try:
            return str(response.json()["libreoffice"])
        except (ValueError, KeyError, TypeError):
            return "an unknown LibreOffice"

    def convert(self, source: Path, kind: str, into: Path) -> None:
        """Write the PDF the converter makes of `source`, a file of `kind` (CONVERTIBLE), to
        `into`. Raises `ConvertError`."""
        size = source.stat().st_size
        try:
            with (
                source.open("rb") as file,
                self._client.stream(
                    "POST",
                    "/convert",
                    params={"type": kind},
                    content=file,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(size),
                    },
                ) as response,
            ):
                if response.status_code != 200:
                    # The converter's own sentence, which names no file and quotes none.
                    raise _refused(self.url, response.status_code, response.read())
                written = 0
                with into.open("wb") as out:
                    for chunk in response.iter_bytes():
                        written += len(chunk)
                        if written > MAX_VIEW_BYTES:
                            raise ConvertError(
                                f"The PDF the converter made of a {kind} file was larger than "
                                f"{MAX_VIEW_BYTES // (1024 * 1024)} MB, so it was not kept."
                            )
                        out.write(chunk)
        except httpx.TransportError as error:
            raise self._unreachable(error) from error
        with into.open("rb") as made:
            if made.read(len(PDF_HEADER)) != PDF_HEADER:
                raise ConvertError(f"The converter at {self.url} answered with what is not a PDF.")

    def _unreachable(self, error: Exception) -> ConvertError:
        return ConvertError(
            f"The converter at {self.url} could not be reached ({type(error).__name__}); check "
            "that its container runs and that CONVERTER_URL names it.",
            transient=True,
            members=UNAVAILABLE,
        )


def _refused(url: str, status: int, body: bytes) -> ConvertError:
    """What a refusal of the converter's means: busy, or a server's failure, passes; the rest is
    about the file, or about a converter that takes other types than this app sends."""
    said = body.decode(errors="replace").strip()[:300] or "no reason"
    transient = status == 503 or (status >= 500 and status != 504)
    return ConvertError(
        f"The converter at {url} answered {status}: {said}",
        transient=transient,
        members=UNAVAILABLE if transient else UNSHOWN,
    )


def converter_from_settings(settings: Settings) -> Converter | None:
    url = parse_converter_url(settings.converter_url)
    return None if url is None else Converter(url)


def check_converter(converter: Converter | None) -> None:
    """At start, say which converter makes the views, or warn when it does not answer. A
    converter that is down is a passing problem each view's job tries again, and the documents
    are shown by their text meanwhile, so the process starts either way."""
    if converter is None:
        log.info(
            "No converter is configured (CONVERTER_URL), so Word, PowerPoint and Excel files are "
            "shown by their text"
        )
        return
    try:
        version = converter.check()
    except ConvertError as error:
        log.warning("%s Their documents are shown by their text until it answers.", error)
        return
    log.info(
        "Word, PowerPoint and Excel files are shown as they look by %s at %s",
        version,
        converter.url,
    )


def converts(document: Document) -> bool:
    """Whether the document is of a type that gets a view."""
    return document.mime_type in CONVERTIBLE


def queue_view(
    session: Session, document: Document, converter: Converter | None, *, priority: int = 0
) -> bool:
    """Queue the making of the document's view in the caller's session, which commits it; False
    when there is none to make: no converter, a type that is not converted, a file whose scan
    has not passed, or a view queued or made already."""
    if (
        converter is None
        or not converts(document)
        or document.status == "quarantined"
        or not scan_passed(document)
        or document.view_state in ("queued", "ready")
    ):
        return False
    document.view_state = "queued"
    document.view_page_sizes = []
    document.view_error = None
    enqueue(
        session,
        CONVERT_DOCUMENT,
        org_id=document.project.org_id,
        project_id=document.project_id,
        payload={"document_id": document.id},
        priority=priority,
    )
    return True


def convert_document(job: Job, converter: Converter | None, storage: Storage) -> str | None:
    """Make the view of the document the job names: its file sent to the converter, the PDF
    measured by a render child and kept at its `view_key`, and the document told where its view
    stands. Returns the reason it gave up for good when the converter stayed out of reach, which
    is the operator's to hear of (app/deadletters.py); a file that could not be converted is the
    member's, told on the document, and returns None.

    Run again after a lost lease, it makes the view again and replaces the file. A document that
    got a new version meanwhile keeps nothing of this run: its own parse queues its own view.
    """
    lease = Lease(job.id, job.worker_id)
    document_id = job.payload["document_id"]
    tries = job.payload.get("try", 1)
    with SessionLocal() as session:
        document = session.get(Document, document_id)
        if document is None:
            return None
        if converter is None or not _wanted(document):
            # Nothing will make it: say so, rather than leave it being made for good.
            if document.view_state == "queued":
                _settle(session, document_id, document.storage_key, lease, state=None)
            return None
        original, key, kind = (
            document.storage_key,
            document.view_key,
            CONVERTIBLE[document.mime_type],
        )
    try:
        sizes = convert_file(converter, storage, original, key, kind)
    except (ConvertError, RenderError, StorageError, FileNotFoundError) as error:
        return _failed(job, lease, original, error, tries)
    except Exception as error:
        # Not a failure anyone foresaw: the view says it failed, and the worker logs the rest.
        _failed(job, lease, original, ConvertError(f"{type(error).__name__}"), MAX_TRIES)
        raise
    with SessionLocal() as session:
        if not _settle(session, document_id, original, lease, state="ready", sizes=sizes):
            # A new version took the document's place meanwhile; its parse makes its own view,
            # and the earlier file's drawings went with it (app/versions.py).
            _discard(storage, key)
    return None


def _wanted(document: Document) -> bool:
    return (
        document.deleted_at is None
        and document.status != "quarantined"
        and scan_passed(document)
        and converts(document)
    )


def convert_file(
    converter: Converter, storage: Storage, original: str, key: str, kind: str
) -> list[list[float]]:
    """The view made and kept; each of its pages' sizes. No transaction is open meanwhile."""
    settings = get_settings()
    with storage.localize(original) as path, tempfile.TemporaryDirectory() as folder:
        pdf = Path(folder) / "view.pdf"
        converter.convert(path, kind, pdf)
        sizes = renderer().measure(pdf)
        if settings.parse_max_pages and len(sizes) > settings.parse_max_pages:
            raise ConvertError(
                f"The PDF the converter made of a {kind} file has {len(sizes):,} pages, more "
                f"than PARSE_MAX_PAGES ({settings.parse_max_pages:,}), so it was not kept.",
                members=TOO_MANY_PAGES.format(limit=settings.parse_max_pages),
            )
        storage.put(key, pdf, "application/pdf")
    return sizes


def _failed(job: Job, lease: Lease, original: str, error: Exception, tries: int) -> str | None:
    """Ask again later for a passing failure, or say on the document why it has no view."""
    transient = getattr(error, "transient", False)
    document_id = job.payload["document_id"]
    with SessionLocal() as session:
        if transient and tries < MAX_TRIES:
            wait = FIRST_WAIT * 2 ** (tries - 1)
            log.info(
                "The view of document %s is asked for again in %s: %s",
                document_id,
                wait,
                error,
            )
            enqueue(
                session,
                CONVERT_DOCUMENT,
                org_id=job.org_id,
                project_id=job.project_id,
                payload={"document_id": document_id, "try": tries + 1},
                run_at=datetime.now(UTC) + wait,
            )
            _commit(session, lease)
            return None
        development = get_settings().environment == "development"
        told = GAVE_UP if transient and not development else shown(error, development=development)
        log.warning("Document %s has no view: %s", document_id, error)
        _settle(session, document_id, original, lease, state="failed", error=told)
    if transient:
        return f"The converter stayed out of reach for {MAX_TRIES} tries: {error}"
    return None


def _settle(
    session: Session,
    document_id: int,
    original: str,
    lease: Lease,
    *,
    state: str | None,
    sizes: list[list[float]] | None = None,
    error: str | None = None,
) -> bool:
    """Write where the document's view stands, under its row's lock and the job's lease; False,
    writing nothing, when the document is gone or its file is no longer the one converted."""
    document = session.get(Document, document_id, with_for_update=True, populate_existing=True)
    if document is None or document.storage_key != original:
        session.rollback()
        return False
    document.view_state = state
    document.view_page_sizes = sizes or []
    document.view_error = error
    _commit(session, lease)
    return True


def _commit(session: Session, lease: Lease) -> None:
    if not renew(session, lease):
        session.rollback()
        raise LeaseLost(lease)
    session.commit()


def _discard(storage: Storage, key: str) -> None:
    try:
        storage.delete([key])
    except StorageError as error:
        log.warning("Could not delete the view at %s: %s", key, error)


def abandon_view(job: Job) -> None:
    """The job was lost to a dying worker every time it ran: the view says it failed, and the
    member may ask again."""
    with SessionLocal() as session:
        document = session.get(Document, job.payload["document_id"], with_for_update=True)
        if document is not None and document.view_state == "queued":
            document.view_state = "failed"
            document.view_page_sizes = []
            document.view_error = UNSHOWN
            session.commit()
