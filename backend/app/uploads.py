"""Files taken into a project's library: those an upload request carries (app/api/documents.py),
those of a message sent to the project's address (app/inbound.py) and those of a connected
folder (app/connectors/sync.py), each through the same checks.

A file is refused with a sentence about it, while the others go in: a type the app does not
read, nothing in it, more bytes than one file may have, bytes that are not the type its name
claims (app/parsing/signatures.py), bytes the project holds already (in its library or its
trash, which the sentence names), or no room left on the organisation's plan
(app/entitlements.py). A file kept is a document, stored before its row is committed, with the
job that indexes it, the member's usage of its bytes and its audit row in the same commit, one
file at a time, so that each one committed fills the plan for the next.
"""

import hashlib
import tempfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.entitlements import check_upload
from app.folders import (
    MAX_DEPTH,
    NAME_LENGTH,
    PATH_SEPARATOR,
    folder_at,
    folder_paths,
    unique_filename,
)
from app.jobs import Lease, LeaseLost, queue_index, renew
from app.metrics import UPLOAD_BYTES, UPLOAD_REJECTIONS
from app.models import STAGES, Document, Folder, Organization, Project
from app.parsing import MIME_TYPES, SUPPORTED_EXTENSIONS
from app.parsing.signatures import wrong_type
from app.restore import place_taken
from app.schemas import RejectedFile
from app.storage import Storage, StorageError, original_key
from app.unpacking import NESTED, PACKED, Refused, unpacked
from app.usage import DOCUMENT, UPLOAD, record

CHUNK = 1024 * 1024
# The unique constraint on (project_id, sha256), named by the convention in db.py: the insert
# of a second copy of the same bytes fails on it when two uploads race.
DUPLICATE_CONSTRAINT = "documents_project_id_sha256_key"
# When the copy that won the race is gone again before it can be named.
LOST_RACE = (
    "Another upload of this file finished first. Upload it again if it is not in the project."
)
UNMAKEABLE_FOLDER = (
    f"Its folders cannot be made: a folder's name cannot be '.' or '..' or longer than "
    f"{NAME_LENGTH} characters, and an upload goes at most {MAX_DEPTH} folders deep. Move it "
    "into a folder that can, then upload it again."
)


@dataclass(frozen=True)
class Incoming:
    """A file on its way into a project: its own name, the folders below the target it goes
    into (None when they cannot be made, which rejects it), and its bytes."""

    name: str
    folders: list[str] | None
    file: BinaryIO


def take_files(
    session: Session,
    storage: Storage,
    project: Project,
    organization: Organization,
    target: int | None,
    files: Iterable[Incoming],
    *,
    user_id: int | None,
    audited: Callable[[Document, int], None],
    lease: Lease | None = None,
    unpack: bool = False,
) -> tuple[list[Document], list[RejectedFile]]:
    """Keep each file as a document of the project in the folder `target` (the root for None),
    or below it where its `folders` say, a folder being made where none of its name is; returns
    the documents kept and the files rejected. `user_id` is whose upload it is, for the usage of
    its bytes (None for nobody's), and `audited(document, size)` writes the audit row of each
    document kept, in its commit. Under a job's `lease` each commit renews it first, and one
    lost raises LeaseLost with nothing of that file written. With `unpack`, as the members'
    page uploads, a file that brings others (`PACKED`: a message) is opened and what it brings
    is taken in its place, each under the file's place in the request (app/unpacking.py)."""
    settings = get_settings()
    limit = settings.max_upload_mb * 1024 * 1024
    taken = SUPPORTED_EXTENSIONS | PACKED if unpack else SUPPORTED_EXTENSIONS
    documents: list[Document] = []
    rejected: list[RejectedFile] = []
    # The folders these files found or made, by their names below the target.
    made: dict[tuple[str, ...], int | None] = {(): target}

    def take(index: int, incoming: Incoming) -> None:
        """Keep one file, or reject it with the sentence for why."""
        name = Path(incoming.name or "unnamed").name
        if incoming.folders is None:
            rejected.append(RejectedFile(filename=name, reason=UNMAKEABLE_FOLDER, index=index))
            UPLOAD_REJECTIONS.labels("folder").inc()
            return
        names = incoming.folders
        # A file in a folder is named by its path, so that two of one name are told apart.
        filename = PATH_SEPARATOR.join([*names, name])
        suffix = Path(name).suffix.lower()
        if unpack and suffix in PACKED:
            # One file brought by another: what it brings in turn is not opened.
            rejected.append(RejectedFile(filename=filename, reason=NESTED, index=index))
            UPLOAD_REJECTIONS.labels("unopened").inc()
            return
        if suffix not in SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(taken))
            rejected.append(
                RejectedFile(
                    filename=filename, reason=f"Unsupported type. Use {supported}.", index=index
                )
            )
            UPLOAD_REJECTIONS.labels("unsupported").inc()
            return

        # Spooled to a local temporary file while hashing: the hash finds a re-upload of the
        # same bytes before anything is stored, and the key needs the row's id. The spool goes
        # whether the file is kept, rejected or fails to store.
        spool, size, sha256 = _spool(incoming.file, limit)
        try:
            if size > limit or size == 0:
                reason = "Empty file." if size == 0 else f"Larger than {settings.max_upload_mb} MB."
                rejected.append(RejectedFile(filename=filename, reason=reason, index=index))
                UPLOAD_REJECTIONS.labels("empty" if size == 0 else "too_large").inc()
                return
            # The extension picks the parser and the type the file is served as, so the bytes
            # must be what it says; refused here, before a duplicate is looked for or the plan
            # is counted, with a sentence about the file rather than a parser's a stage later.
            if reason := wrong_type(spool, suffix):
                rejected.append(RejectedFile(filename=filename, reason=reason, index=index))
                UPLOAD_REJECTIONS.labels("wrong_type").inc()
                return

            duplicate = already_present(session, project.id, sha256, filename, index)
            if duplicate:
                rejected.append(duplicate)
                UPLOAD_REJECTIONS.labels("duplicate").inc()
                return
            # Counted again per file: each one committed above fills the plan a little more.
            if refusal := check_upload(session, organization, size):
                rejected.append(
                    RejectedFile(
                        filename=filename,
                        reason=refusal.sentence,
                        limit=refusal.limit,
                        index=index,
                    )
                )
                UPLOAD_REJECTIONS.labels("plan").inc()
                return

            if tuple(names) not in made:
                made[tuple(names)] = folder_at(session, project.id, target, names)
            folder_of = made[tuple(names)]
            document = Document(
                project_id=project.id,
                folder_id=folder_of,
                filename=unique_filename(session, project.id, folder_of, name),
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
                    already_present(session, project.id, sha256, filename, index)
                    or RejectedFile(filename=filename, reason=LOST_RACE, index=index)
                )
                UPLOAD_REJECTIONS.labels("duplicate").inc()
                return
            document.storage_key = original_key(project.org_id, project.id, document.id, suffix)
            # A document's place in the storage is named by its ids, so a place that holds files
            # already is a lost document's, as a restore to an earlier point loses the ones
            # uploaded since, and this one would be served its rendered pages (app/restore.py).
            if storage.holds(document.storage_prefix):
                raise StorageError(place_taken(document.id, document.storage_prefix))
            # Stored before the row is committed: a storage that fails leaves no row behind,
            # and the error says what happened (`storage_unavailable` in main.py).
            storage.put(document.storage_key, spool, document.mime_type)
            # The document, the job that indexes it, the member's usage and the audit row are
            # committed together. The bytes are the upload's; its pages are counted when the
            # parse learns how many there are (app/indexing/pipeline.py).
            queue_index(session, document)
            record(
                session,
                org_id=project.org_id,
                project_id=project.id,
                user_id=user_id,
                kind=UPLOAD,
                measures={"bytes": size},
                ref=(DOCUMENT, document.id),
            )
            audited(document, size)
            if lease is not None and not renew(session, lease):
                session.rollback()
                raise LeaseLost(lease)
            session.commit()
            documents.append(document)
            UPLOAD_BYTES.observe(size)
        finally:
            spool.unlink(missing_ok=True)

    def opened(index: int, incoming: Incoming, name: str) -> Iterator[Incoming]:
        """What a packed file brings (app/unpacking.py), each below the folders the file went
        into, or nothing when it is refused, as any file would be or as one that cannot be
        opened; the file itself is spooled while what it brings is taken, and never kept."""
        filename = PATH_SEPARATOR.join([*(incoming.folders or []), name])
        spool, size, _ = _spool(incoming.file, limit)
        try:
            reason = None
            if size > limit or size == 0:
                reason = "Empty file." if size == 0 else f"Larger than {settings.max_upload_mb} MB."
            else:
                reason = wrong_type(spool, Path(name).suffix.lower())
            if reason is None:
                try:
                    inside = unpacked(spool, name)
                except Refused as refused:
                    reason = str(refused)
            if reason is not None:
                rejected.append(RejectedFile(filename=filename, reason=reason, index=index))
                UPLOAD_REJECTIONS.labels("unopened").inc()
                return
            for found in inside:
                yield Incoming(found.name, [*(incoming.folders or []), *found.folders], found.file)
        finally:
            spool.unlink(missing_ok=True)

    for index, incoming in enumerate(files):
        name = Path(incoming.name or "unnamed").name
        if unpack and Path(name).suffix.lower() in PACKED and incoming.folders is not None:
            for found in opened(index, incoming, name):
                take(index, found)
        else:
            take(index, incoming)
    return documents, rejected


def already_present(
    session: Session, project_id: int, sha256: str, filename: str, index: int | None = None
) -> RejectedFile | None:
    """The rejection for a file whose bytes the project already holds, naming where: in the
    library, or in its trash (app/trash.py), from which restoring it costs nothing that
    indexing it again would. `index` is the file's place among those it came with."""
    duplicate = session.scalar(
        select(Document).where(Document.project_id == project_id, Document.sha256 == sha256)
    )
    if duplicate is None:
        return None
    if duplicate.deleted_at is not None:
        return RejectedFile(
            filename=filename,
            reason=_in_trash(session, duplicate),
            index=index,
            duplicate_of=duplicate.id,
        )
    where = ""
    if duplicate.folder_id is not None:
        where = f" in {folder_paths(session, project_id)[duplicate.folder_id]}"
    return RejectedFile(
        filename=filename,
        reason=f"Already in this project as {duplicate.filename!r}{where}.",
        index=index,
        duplicate_of=duplicate.id,
    )


def _in_trash(session: Session, document: Document) -> str:
    """Where in the trash a document is: by itself, or with the folder it went with."""
    went_with = None
    folder = session.get(Folder, document.folder_id) if document.folder_id else None
    while folder is not None and folder.deleted_at == document.deleted_at:
        went_with = folder
        folder = session.get(Folder, folder.parent_id) if folder.parent_id else None
    within = f", deleted with the folder {went_with.name!r}" if went_with else ""
    return (
        f"Already in this project's trash as {document.filename!r}{within}. Restore it from "
        "there, or delete it there for good to upload it again."
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
        try:
            while chunk := upload.read(CHUNK):
                size += len(chunk)
                if size > limit:
                    break
                digest.update(chunk)
                spool.write(chunk)
        except BaseException:
            # A file read as it downloads (app/connectors/) may fail partway.
            Path(spool.name).unlink(missing_ok=True)
            raise
    return Path(spool.name), size, digest.hexdigest()
