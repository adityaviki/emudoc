"""A document's versions: a member gives a document a new file, as a
contract comes back signed or an invoice is sent again corrected, and the document reads the new
file in the earlier one's place. It keeps its id, and so everything that names it: its chats'
scope, its review, its share links, its members' checks of its facts, the API's `Document.id` and
what the webhooks told of it.

The file it had becomes an earlier version (`DocumentVersion`): the file stays where it was in
the storage, and its text, the blocks its parse read, is kept on the row, so that a member opens
and downloads it; only the current file is searched, asked about and read for facts. The new file
goes under the document's prefix as `versions/<n>/` (`version_key` in app/storage.py), with the
pages and the thumbnail drawn from it, and the earlier file's drawings are discarded once the
change is committed, so that nothing drawn from one is served for the other.

A new version is checked as an upload's file is (app/uploads.py): its type, its size, its bytes
against its extension, bytes the project holds already (the document's own current file among them);
its pages are counted again as it is read, its usage starting again, the earlier one's staying with
its row. It is an upload of its own (`record_upload`, by the caller), so that its member's notice,
the project's automations and the `document.ready` webhook follow its reading as they follow a new
document's. What the document read from the earlier file goes at once: its blocks, outline, passages
and facts, with what their reading changed, and its title, kind, date and summary until the new file
is read. A member's check of a fact goes back on a fact the new file reads the same
(app/fact_checks.py), and a review that approved or disputed the earlier file is opened again
(`to_review`, its assignee kept). A document being indexed takes no new version until it is done,
since the run would go on with the earlier file.

The connected folders give a document a new version as its file changes at the service
(app/connectors/sync.py).
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.details import settle_details
from app.fact_checks import hold as hold_checks
from app.folders import unique_filename
from app.indexing.pipeline import reset_stages
from app.jobs import Lease, LeaseLost, queue_index, renew
from app.metrics import UPLOAD_BYTES, UPLOAD_REJECTIONS
from app.models import (
    Block,
    Document,
    DocumentVersion,
    Fact,
    FactChange,
    Passage,
    Project,
    Section,
)
from app.parsing import MIME_TYPES, SUPPORTED_EXTENSIONS
from app.scanning import scan_passed
from app.storage import Storage, StorageError, version_key
from app.unpacking import Damaged
from app.uploads import (
    DUPLICATE_CONSTRAINT,
    LOST_RACE,
    already_present,
    constraint_of,
    screened,
    spooled,
    unsupported_type,
)
from app.usage import DOCUMENT, UPLOAD, record

log = logging.getLogger(__name__)

SAME_FILE = "This is the file the document holds now."
BEING_INDEXED = (
    "This document is being indexed right now. Give it its new version once that is done."
)
# The reviews a new file opens again: a member's word on the earlier file is no word on it.
REOPENED = ("approved", "disputed")


class VersionRefused(Exception):
    """A file not taken as a document's new version, with the sentence the member reads and the
    label it is counted by (`UPLOAD_REFUSALS` in app/metrics.py), and `duplicate_of` the
    document that holds its bytes."""

    def __init__(self, reason: str, label: str, *, duplicate_of: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.label = label
        self.duplicate_of = duplicate_of


def take_version(
    session: Session,
    storage: Storage,
    project: Project,
    document: Document,
    name: str,
    file: BinaryIO,
    *,
    user_id: int | None,
    audited: Callable[[str, dict], None],
    lease: Lease | None = None,
) -> DocumentVersion:
    """Make the file `name` the document's new version and commit; returns the earlier version
    it keeps. `user_id` is whose upload it is, for the usage of its bytes and the review it
    opens again (None for nobody's), and `audited(action, detail)` writes an audit row about the
    document in the commit: `document_version_uploaded`, and `document_review_changed` for a
    review opened again. Under a job's `lease` the commit renews it, and one lost raises
    LeaseLost with nothing written. A file it does not take raises VersionRefused, with nothing
    written either."""
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise _refused(unsupported_type(SUPPORTED_EXTENSIONS), "unsupported")
    try:
        spool, size, sha256 = spooled(file, _limit())
    except Damaged as damaged:
        raise _refused(str(damaged), "unopened") from None
    try:
        if screening := screened(spool, size, suffix):
            raise _refused(*screening)
        # Held for the rest of the transaction: a run of its index job takes the row before it
        # starts (app/indexing/pipeline.py), so the two never pass each other.
        session.refresh(document, with_for_update=True)
        if document.status == "processing":
            raise _refused(BEING_INDEXED, "indexing")
        if sha256 == document.sha256:
            raise _refused(SAME_FILE, "duplicate", duplicate_of=document.id)
        if duplicate := already_present(session, project.id, sha256, name):
            raise _refused(duplicate.reason, "duplicate", duplicate_of=duplicate.duplicate_of)

        earlier = _earlier(session, document)
        drawn = document.version_prefix
        number = document.version + 1
        key = version_key(project.org_id, project.id, document.id, number, suffix)
        # In a savepoint, as an upload's insert is (app/uploads.py): another request may have
        # stored the same bytes meanwhile, and the unique constraint is the only thing that sees
        # both, so the file is refused as it would have been a moment later.
        try:
            with session.begin_nested():
                session.add(earlier)
                _give(session, document, number, name, suffix, size, sha256, key)
                session.flush()
        except IntegrityError as error:
            if constraint_of(error) != DUPLICATE_CONSTRAINT:
                raise
            duplicate = already_present(session, project.id, sha256, name)
            raise _refused(
                duplicate.reason if duplicate else LOST_RACE,
                "duplicate",
                duplicate_of=duplicate.duplicate_of if duplicate else None,
            ) from None
        # Stored before the change is committed, as an upload's file is: a storage that fails
        # leaves the document as it was, and the place is the next version's to take.
        storage.put(key, spool, document.mime_type)
    finally:
        spool.unlink(missing_ok=True)

    hold_checks(session, document.id)
    for model in (Fact, FactChange, Passage, Section, Block):
        session.execute(delete(model).where(model.document_id == document.id))
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
    detail = {"filename": document.filename, "size_bytes": size, "version": number}
    if earlier.filename != document.filename:
        detail["from"] = earlier.filename
    audited("document_version_uploaded", detail)
    if document.review in REOPENED:
        audited(
            "document_review_changed",
            {"filename": document.filename, "from": document.review, "to": "to_review"},
        )
        document.review = "to_review"
        document.review_changed_by = user_id
        document.review_changed_at = datetime.now(UTC)
    if lease is not None and not renew(session, lease):
        session.rollback()
        raise LeaseLost(lease)
    session.commit()
    UPLOAD_BYTES.observe(size)
    _discard_drawings(storage, drawn)
    return earlier


def _refused(reason: str, label: str, **found) -> VersionRefused:
    UPLOAD_REJECTIONS.labels(label).inc()
    return VersionRefused(reason, label, **found)


def _limit() -> int:
    return get_settings().max_upload_mb * 1024 * 1024


def _earlier(session: Session, document: Document) -> DocumentVersion:
    """The document's current file as the earlier version it is about to become, with its text
    as its parse read it."""
    blocks = session.execute(
        select(Block.kind, Block.level, Block.text, Block.page)
        .where(Block.document_id == document.id)
        .order_by(Block.ordinal)
    ).all()
    return DocumentVersion(
        document_id=document.id,
        number=document.version,
        filename=document.filename,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        storage_key=document.storage_key,
        page_count=document.page_count,
        scan_passed=scan_passed(document) and document.status != "quarantined",
        blocks=[
            {"kind": kind, "level": level, "text": text, "page": page}
            for kind, level, text, page in blocks
        ],
        usage=document.usage,
        upload_id=document.upload_id,
        uploaded_at=current_since(session, document),
        replaced_at=datetime.now(UTC),
    )


def current_since(session: Session, document: Document) -> datetime:
    """When the document's current file came in: as the document, or when it replaced the
    version before it."""
    replaced = session.scalar(
        select(func.max(DocumentVersion.replaced_at)).where(
            DocumentVersion.document_id == document.id
        )
    )
    return replaced or document.created_at


def _give(
    session: Session,
    document: Document,
    number: int,
    name: str,
    suffix: str,
    size: int,
    sha256: str,
    key: str,
) -> None:
    """Make the document the new file, with nothing of the earlier one's reading left: it is
    read from its scan on, as a new upload is."""
    if Path(document.filename).suffix.lower() != suffix:
        # The extension picks the parser, so the name keeps its stem and takes the new one's,
        # unless another file of the folder has that name.
        document.filename = unique_filename(
            session, document.project_id, document.folder_id, Path(document.filename).stem + suffix
        )
    document.versions_bytes += document.size_bytes
    document.version = number
    document.storage_key = key
    document.mime_type = MIME_TYPES[suffix]
    document.size_bytes = size
    document.sha256 = sha256
    document.page_count = None
    document.page_sizes = []
    # The earlier file's view went with its drawings; the new one's parse queues its own
    # (app/conversion.py).
    document.view_state = None
    document.view_page_sizes = []
    document.view_error = None
    # What the earlier file was read to be; a detail a member set holds (app/details.py), the
    # row being held already.
    document.read_title = None
    document.read_doc_type = None
    document.read_doc_date = None
    document.read_language = None
    document.read_language_code = None
    settle_details(document)
    document.summary = None
    document.template = None
    document.quarantine_reported_at = None
    # What the new file uses is counted from nothing, so that its pages are counted as it is
    # parsed (`_pages_counted` in app/indexing/pipeline.py); the earlier file's is on its row.
    document.usage = {}
    reset_stages(document, "scan")
    log.info("Document %s was given version %s", document.id, number)


def _discard_drawings(storage: Storage, prefix: str) -> None:
    """Delete the pages, the thumbnail and the view drawn or made from the earlier file
    (app/conversion.py), which nothing serves any more. A storage that fails here leaves the
    drawings for the walk that removes old ones (app/maintenance.py), and the view with the
    document, whose prefix holds it."""
    try:
        storage.delete_prefix(f"{prefix}pages/")
        storage.delete([f"{prefix}thumbnail.png", f"{prefix}view.pdf"])
    except StorageError as error:
        log.warning("Could not delete the drawings under %s: %s", prefix, error)
