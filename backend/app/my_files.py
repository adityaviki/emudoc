"""My files: each member's own files in a project (`MyFile`, `MyFileVersion`), the Word documents
and Excel workbooks they keep there and the agent writes and edits for them (decided with the
owner on 2026-11-12; indexing-approach.md, "My files").

A file is seen by its member alone: never by the project's other members, its search, facts, share
links, `/api/v1`, webhooks, digests or export, until the member copies it into the project's
library, where it becomes a document like any upload (app/api/my_files.py). It is no document, so
that none of the many places that read a project's documents can show it to another member by
forgetting to leave it out; it is not indexed, and the agent reads it with tools of its own.

A file keeps its versions, never changed: each of its member's uploads and each of the agent's
writings adds one, the newest being the file. A version the member uploaded is scanned for
malware first; each is shown as it looks by the PDF the converter makes of it (app/conversion.py),
both done by a `view_my_file` job. Until its scan passes a version is neither served, drawn nor
copied. Its bytes count toward the plan's storage (`check_room` in app/entitlements.py), not
toward its documents, and its files go with its row: the storage walk sweeps a file's whose row
is gone (`_live_prefixes` in app/maintenance.py), so a member's account purged or their leaving
the organisation needs only the rows deleted.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.conversion import (
    FIRST_WAIT,
    GAVE_UP,
    MAX_TRIES,
    UNSHOWN,
    Converter,
    ConvertError,
    convert_file,
)
from app.db import SessionLocal
from app.entitlements import check_room
from app.errors import shown
from app.jobs import LIVE, VIEW_MY_FILE, Job, Lease, LeaseLost, enqueue, renew
from app.models import MyFile, MyFileVersion, Organization, Project
from app.rendering import RenderError
from app.scanning import ScanError, Scanner
from app.storage import Storage, StorageError, my_file_key, my_file_prefix
from app.uploads import screened, spooled

log = logging.getLogger(__name__)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Each kind of file My files keeps (MY_FILE_KINDS), by the type it is stored and served as.
MEDIA_TYPES = {"docx": DOCX, "xlsx": XLSX}
# What a kind is called in the sentences a member reads.
KIND_NAMES = {"docx": "Word document", "xlsx": "Excel workbook"}
# How many files a member keeps in one project's My files.
MAX_FILES = 200
# How long a file's name may be before its extension.
NAME_CHARS = 200
# What a member's own upload says it changed, in the versions' list.
UPLOADED = "Uploaded"

WRONG_KIND = (
    "My files keeps Word documents (.docx) and Excel workbooks (.xlsx). Save this file as one of "
    "them, then upload it again."
)
OTHER_KIND = "This file is a {kind}, so its new version must be one too: a {suffix} file."
TOO_MANY = (
    f"You have {MAX_FILES} files in My files in this project, as many as it keeps. Delete some to "
    "add more."
)
SAVED_MEANWHILE = (
    "A newer version of this file was added meanwhile. Look at it, then upload yours again to keep "
    "it as the newest."
)
FLAGGED = (
    "The malware scan flagged this file as {threat}, so it cannot be opened, downloaded or copied. "
    "Delete it."
)


class Refused(Exception):
    """A file or a version My files does not take, with the sentence its member reads, the
    status the route answers, and for a plan's refusal the figure it is held to (`Refusal`)."""

    def __init__(self, reason: str, status: int = 400, limit: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.limit = limit


def kind_of(name: str) -> str | None:
    """The kind of file a name's extension says, None for one My files does not keep."""
    return {".docx": "docx", ".xlsx": "xlsx"}.get(Path(name).suffix.lower())


def own_name(name: str, kind: str) -> str:
    """The name a file is kept under: its own, without folders, cut to NAME_CHARS, with the
    extension of its kind whatever was typed."""
    typed = Path(name).name.strip()
    stem = Path(typed).stem if Path(typed).suffix.lower() == f".{kind}" else typed
    return f"{stem[:NAME_CHARS].strip() or 'Untitled'}.{kind}"


def files_of(session: Session, project_id: int, user_id: int) -> list[MyFile]:
    """The member's files in the project, the one changed last first."""
    return list(
        session.scalars(
            select(MyFile)
            .where(MyFile.project_id == project_id, MyFile.user_id == user_id)
            .order_by(MyFile.updated_at.desc(), MyFile.id.desc())
        )
    )


def versions_of(session: Session, file_id: int) -> list[MyFileVersion]:
    """A file's versions, the newest first."""
    return list(
        session.scalars(
            select(MyFileVersion)
            .where(MyFileVersion.file_id == file_id)
            .order_by(MyFileVersion.number.desc())
        )
    )


def newest(session: Session, file_id: int) -> MyFileVersion:
    """The file's newest version, the file itself as it now is."""
    return session.scalars(
        select(MyFileVersion)
        .where(MyFileVersion.file_id == file_id)
        .order_by(MyFileVersion.number.desc())
        .limit(1)
    ).one()


def servable(version: MyFileVersion) -> bool:
    """Whether the version may be served, drawn or copied: the scan passed it, or there was none
    to run, or the agent wrote it."""
    return version.scan in ("passed", "skipped")


def add_file(
    session: Session,
    storage: Storage,
    organization: Organization,
    project: Project,
    user_id: int,
    name: str,
    upload: BinaryIO,
    *,
    scanner: Scanner | None,
    converter: Converter | None,
    by_model: bool = False,
    note: str = UPLOADED,
    origin_document_id: int | None = None,
    answer: tuple[int, int] | None = None,
) -> MyFile:
    """Keep `upload`, named `name`, as a new file of the member's My files in the project and
    commit; raises `Refused` with nothing written. `by_model` says the agent wrote it, which needs
    no scan; the member's upload is scanned when there is a `scanner`, and shown as it looks when
    there is a `converter`, by the job this queues. `answer` is the answer that made it and which
    of its files it is (app/agent/writing.py)."""
    kind = kind_of(name)
    if kind is None:
        raise Refused(WRONG_KIND)
    try:
        held = session.scalar(
            select(func.count(MyFile.id)).where(
                MyFile.project_id == project.id, MyFile.user_id == user_id
            )
        )
        if held >= MAX_FILES:
            raise Refused(TOO_MANY, 409)
        with _spooled(upload, f".{kind}") as (spool, size, sha256):
            _refuse_past_room(session, organization, size)
            file = MyFile(
                org_id=organization.id,
                project_id=project.id,
                user_id=user_id,
                name=own_name(name, kind),
                kind=kind,
                origin_document_id=origin_document_id,
                answer_id=answer[0] if answer else None,
                answer_ordinal=answer[1] if answer else None,
            )
            session.add(file)
            session.flush()
            _version(
                session,
                storage,
                file,
                1,
                spool,
                size,
                sha256,
                user_id=user_id,
                by_model=by_model,
                note=note,
                scanning=scanner is not None,
                viewing=converter is not None,
            )
    except Refused:
        session.rollback()
        raise
    session.commit()
    return file


def add_version(
    session: Session,
    storage: Storage,
    organization: Organization,
    file: MyFile,
    user_id: int,
    upload: BinaryIO,
    *,
    scanner: Scanner | None,
    converter: Converter | None,
    name: str | None = None,
    by_model: bool = False,
    note: str = UPLOADED,
    base: int | None = None,
) -> MyFileVersion:
    """Keep `upload` as the file's next version and commit; raises `Refused` with nothing
    written. `name`, when given, must name a file of the file's kind; `base` is the version the
    member worked from, refused when another was added meanwhile."""
    suffix = f".{file.kind}"
    if name is not None and kind_of(name) != file.kind:
        raise Refused(OTHER_KIND.format(kind=KIND_NAMES[file.kind], suffix=suffix))
    try:
        with _spooled(upload, suffix) as (spool, size, sha256):
            # One version at a time, so that two uploads at once never take the same number.
            session.refresh(file, with_for_update=True)
            number = newest(session, file.id).number
            if base is not None and base != number:
                raise Refused(SAVED_MEANWHILE, 409)
            _refuse_past_room(session, organization, size)
            version = _version(
                session,
                storage,
                file,
                number + 1,
                spool,
                size,
                sha256,
                user_id=user_id,
                by_model=by_model,
                note=note,
                scanning=scanner is not None,
                viewing=converter is not None,
            )
    except Refused:
        session.rollback()
        raise
    session.commit()
    return version


def rename(session: Session, file: MyFile, name: str) -> None:
    """Give the file a new name, keeping the extension of its kind, and commit."""
    file.name = own_name(name, file.kind)
    file.updated_at = datetime.now(UTC)
    session.commit()


def delete_file(session: Session, storage: Storage, file: MyFile) -> None:
    """Delete the file for good with its versions, then their files; a file the storage could not
    delete is the storage walk's to sweep, its row being gone."""
    prefix = my_file_prefix(file.org_id, file.project_id, file.id)
    session.delete(file)
    session.commit()
    try:
        storage.delete_prefix(prefix)
    except StorageError as error:
        log.warning("Could not delete the files of a file in My files at %s: %s", prefix, error)


def drop_files(session: Session, org_id: int, user_id: int) -> None:
    """Delete the member's files in every project of the organisation they are leaving, in the
    caller's transaction; the storage walk sweeps their files once it has committed."""
    session.execute(delete(MyFile).where(MyFile.org_id == org_id, MyFile.user_id == user_id))


def ask_view(
    session: Session, version: MyFileVersion, scanner: Scanner | None, converter: Converter | None
) -> bool:
    """Queue the scan and the view a version still lacks, from "Try again", in the caller's
    session; False when there is nothing to do: it was flagged, or its scan passed and its view is
    queued, made, or not made for want of a converter."""
    if version.scan == "flagged":
        return False
    scanning = version.scan == "pending" and scanner is not None
    viewing = converter is not None and version.view_state in (None, "failed")
    if not scanning and not viewing:
        return False
    if version.scan == "pending" and scanner is None:
        version.scan = "skipped"
    if viewing or version.view_state == "failed":
        version.view_state = "queued" if converter is not None else None
        version.view_page_sizes = []
        version.view_error = None
    _queue(session, version, version.file_id)
    return True


def _version(
    session: Session,
    storage: Storage,
    file: MyFile,
    number: int,
    spool: Path,
    size: int,
    sha256: str,
    *,
    user_id: int,
    by_model: bool,
    note: str,
    scanning: bool,
    viewing: bool,
) -> MyFileVersion:
    """Add the version's row, store its file and queue what it needs, in the caller's
    transaction."""
    key = my_file_key(file.org_id, file.project_id, file.id, number, f".{file.kind}")
    scan = "pending" if scanning and not by_model else "skipped"
    version = MyFileVersion(
        file_id=file.id,
        number=number,
        storage_key=key,
        size_bytes=size,
        sha256=sha256,
        by_model=by_model,
        user_id=user_id,
        note=note[:500],
        scan=scan,
        view_state="queued" if viewing else None,
    )
    session.add(version)
    session.flush()
    storage.put(key, spool, MEDIA_TYPES[file.kind])
    file.updated_at = datetime.now(UTC)
    if scan == "pending" or viewing:
        _queue(session, version, file.id)
    return version


def _queue(session: Session, version: MyFileVersion, file_id: int) -> None:
    file = session.get(MyFile, file_id)
    enqueue(
        session,
        VIEW_MY_FILE,
        org_id=file.org_id,
        project_id=file.project_id,
        payload={"version_id": version.id},
        priority=LIVE,
    )


def _refuse_past_room(session: Session, organization: Organization, size: int) -> None:
    if refusal := check_room(session, organization, size):
        raise Refused(refusal.sentence, 402, refusal.limit)


@contextmanager
def _spooled(upload: BinaryIO, suffix: str) -> Iterator[tuple[Path, int, str]]:
    """The upload copied to a temporary file and screened as any upload is (app/uploads.py): its
    path, size and SHA-256; raises `Refused` for an empty file, one too large, or bytes that are
    not the type its extension says."""
    limit = get_settings().max_upload_mb * 1024 * 1024
    spool, size, sha256 = spooled(upload, limit)
    try:
        if found := screened(spool, size, suffix):
            reason, label = found
            raise Refused(reason, 413 if label == "too_large" else 400)
        yield spool, size, sha256
    finally:
        spool.unlink(missing_ok=True)


def view_my_file(
    job: Job, scanner: Scanner | None, converter: Converter | None, storage: Storage
) -> str | None:
    """Scan the version the job names when it waits for its scan, then have its view made and
    kept, as app/conversion.py makes a document's. Returns the reason it gave up for good when the
    scanner or the converter stayed out of reach, the operator's to hear of; the member is told
    on the version. Run again after a lost lease, it scans and converts again, which changes
    nothing a first run settled."""
    lease = Lease(job.id, job.worker_id)
    version_id = job.payload["version_id"]
    tries = job.payload.get("try", 1)
    with SessionLocal() as session:
        version = session.get(MyFileVersion, version_id)
        if version is None:
            return None
        kind = session.get(MyFile, version.file_id).kind
        original, key, scan = version.storage_key, version.view_key, version.scan
        viewing = version.view_state == "queued"
    try:
        if scan == "pending":
            scan, threat = _scanned(scanner, storage, original)
            with SessionLocal() as session:
                if scan == "flagged":
                    _settle(
                        session,
                        version_id,
                        lease,
                        scan=scan,
                        state="failed",
                        error=FLAGGED.format(threat=threat),
                    )
                    log.warning("Version %s in My files was flagged by the scan", version_id)
                    return None
                _settle(session, version_id, lease, scan=scan, keep_view=True)
        sizes = None
        if viewing and converter is not None:
            sizes = convert_file(converter, storage, original, key, kind)
    except (ScanError, ConvertError, RenderError, StorageError, FileNotFoundError) as error:
        return _failed(job, lease, error, tries)
    except Exception as error:
        # Not a failure anyone foresaw: the version says it failed, and the worker logs the rest.
        _failed(job, lease, ConvertError(type(error).__name__), MAX_TRIES)
        raise
    if viewing:
        with SessionLocal() as session:
            state = "ready" if sizes is not None else None
            _settle(session, version_id, lease, state=state, sizes=sizes)
    return None


def _scanned(scanner: Scanner | None, storage: Storage, original: str) -> tuple[str, str | None]:
    """The version's scan, and the threat it found: `skipped` once no scanner is configured."""
    if scanner is None:
        return "skipped", None
    with storage.localize(original) as path:
        threat = scanner.scan(path)
    return ("flagged", threat) if threat else ("passed", None)


def _failed(job: Job, lease: Lease, error: Exception, tries: int) -> str | None:
    """Ask again later for a passing failure, or say on the version why it has no view."""
    transient = getattr(error, "transient", False)
    version_id = job.payload["version_id"]
    with SessionLocal() as session:
        if transient and tries < MAX_TRIES:
            wait = FIRST_WAIT * 2 ** (tries - 1)
            log.info("Version %s in My files is shown again in %s: %s", version_id, wait, error)
            enqueue(
                session,
                VIEW_MY_FILE,
                org_id=job.org_id,
                project_id=job.project_id,
                payload={"version_id": version_id, "try": tries + 1},
                run_at=datetime.now(UTC) + wait,
            )
            _commit(session, lease)
            return None
        development = get_settings().environment == "development"
        told = GAVE_UP if transient and not development else shown(error, development=development)
        log.warning("Version %s in My files has no view: %s", version_id, error)
        _settle(session, version_id, lease, state="failed", error=told)
    if transient:
        return f"The scanner or the converter stayed out of reach for {MAX_TRIES} tries: {error}"
    return None


def _settle(
    session: Session,
    version_id: int,
    lease: Lease,
    *,
    scan: str | None = None,
    state: str | None = None,
    sizes: list[list[float]] | None = None,
    error: str | None = None,
    keep_view: bool = False,
) -> None:
    """Write where the version stands under its row's lock and the job's lease; nothing once the
    file was deleted meanwhile."""
    version = session.get(MyFileVersion, version_id, with_for_update=True, populate_existing=True)
    if version is None:
        session.rollback()
        return
    if scan is not None:
        version.scan = scan
    if not keep_view:
        version.view_state = state
        version.view_page_sizes = sizes or []
        version.view_error = error
    _commit(session, lease)


def _commit(session: Session, lease: Lease) -> None:
    if not renew(session, lease):
        session.rollback()
        raise LeaseLost(lease)
    session.commit()


def abandon_view(job: Job) -> None:
    """The job was lost to a dying worker every time it ran: the version says its view failed,
    and the member may ask again."""
    with SessionLocal() as session:
        version = session.get(
            MyFileVersion, job.payload["version_id"], with_for_update=True, populate_existing=True
        )
        if version is not None and version.view_state == "queued":
            version.view_state = "failed"
            version.view_page_sizes = []
            version.view_error = UNSHOWN
            session.commit()
