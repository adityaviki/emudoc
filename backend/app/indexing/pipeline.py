"""The indexing pipeline. Each stage stores its output, so any stage can be re-run alone."""

import io
import logging
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from queue import SimpleQueue

from pgvector import Vector
from sqlalchemy import delete, select, update
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.config import get_settings
from app.db import SessionLocal, connection_lost
from app.embeddings import Embedder
from app.entitlements import check_pages
from app.errors import AppError, shown, summary
from app.indexing.enrich import (
    ExtractedFact,
    Reading,
    analyze_document,
    infer_sections,
    normalize_fact_name,
    parse_iso_date,
)
from app.indexing.structure import SectionHint, build_outline
from app.jobs import LIVE, RENDER_PAGES, Lease, LeaseLost, enqueue, renew
from app.llm import LLM, LLMError, OcrBlock
from app.metrics import DOCUMENT_READY, INDEX_RUNS, PARSES, STAGE_RUNS, STAGE_SECONDS
from app.models import STAGES, Block, Document, Fact, Passage, Section
from app.parsing import IMAGE_TYPES, FileTooLarge, ParsedBlock, UnsupportedFile
from app.parsing.images import open_image
from app.parsing.limits import ParseLimits, parse_within_limits
from app.parsing.types import clean_text
from app.rendering import FOR_OCR, RenderError, renderer
from app.scanning import NOT_SCANNED, UNSCANNED, Scanner, quarantine_message
from app.spending import check_platform_spend, check_spend, day_of
from app.storage import Storage
from app.tracing import PARSE, STAGE, note
from app.usage import DOCUMENT, EMBED, ENRICH, INFER_SECTIONS, OCR, UPLOAD, Meter, ModelUsage

log = logging.getLogger(__name__)

# Without the scan nothing may open the file, and without parse and structure there is
# nothing to search; the other stages only add to it.
REQUIRED_STAGES = ("scan", "parse", "structure")
# Longest image edge sent for OCR; larger scans are scaled down to this.
MAX_IMAGE_EDGE = 2576
# A stage's detail while it waits to run again after the database went away under it.
LOST_DATABASE = "The connection to the database was lost while this stage ran."
# Passages embedded per call of the embedder, whose vectors are held as Python floats until
# the call returns, and passages whose vectors are written per statement.
EMBED_CHUNK = 512
WRITE_CHUNK = 500
MISSING_ORIGINAL = (
    "The uploaded file is missing from the storage. Delete this document and upload it again."
)
# What a deployment's members read of a stage that raised something the app did not expect,
# where the stage's error names the exception's kind for the operator, whose log has the rest.
STAGE_CRASHED = (
    'Emudoc ran into an error of its own at this stage, which it recorded. Use "Index again" '
    "to try once more."
)


class ParseSlots:
    """The parses a process runs at once, PARSE_CONCURRENCY, read at each parse.

    A parse is a child process that may hold PARSE_MEMORY_MB, and the indexers that start them
    (WORKER_CONCURRENCY) spend most of a document waiting on a model, which costs no memory:
    so the worker's memory bounds its parses, not its indexers, and an indexer whose turn has
    not come waits here. Meanwhile it holds the copy of the original it will parse, but no
    database connection, since the stage has read nothing yet; and no parse waits on a model,
    since the slot is let go when the child ends, before any scan is read.
    """

    def __init__(self) -> None:
        self._changed = threading.Condition()
        self._running = 0
        self._waiting = 0

    @contextmanager
    def held(self) -> Iterator[None]:
        """Hold one of the parses for the block, once one is free, and note the block as a
        span of the run being timed, with the seconds it waited for its turn."""
        began = time.monotonic()
        with self._changed:
            self._waiting += 1
            self._show()
            self._changed.wait_for(lambda: self._running < get_settings().parse_concurrency)
            self._waiting -= 1
            self._running += 1
            self._show()
        waited = time.monotonic() - began
        outcome = "failed"
        try:
            yield
            outcome = "ok"
        finally:
            with self._changed:
                self._running -= 1
                self._show()
                self._changed.notify()
            note(PARSE, began, waited=round(waited, 3), outcome=outcome)

    def _show(self) -> None:
        PARSES.labels("running").set(self._running)
        PARSES.labels("waiting").set(self._waiting)


PARSING = ParseSlots()


@dataclass
class Services:
    llm: LLM
    embedder: Embedder
    storage: Storage
    # None when SCANNER_URL names no daemon: the scan stage is skipped and says so.
    scanner: Scanner | None = None


class PipelineError(AppError):
    """A stage failed for a reason the user can act on."""


class MalwareFound(PipelineError):
    """The scanner flagged the file. The document is quarantined rather than failed: the
    verdict is final for the file as it is, and the message tells the members what to do."""

    def __init__(self, threat: str):
        super().__init__(quarantine_message(threat))
        self.threat = threat


class StageDeferred(PipelineError):
    """The stage must wait until `until`, for a reason that passes at that moment: the day's
    spending limit, which resets at midnight UTC (app/spending.py). The worker queues the
    document again for then rather than after its backoff (app/worker.py)."""

    def __init__(self, message: str, until: datetime):
        super().__init__(message, transient=True)
        self.until = until


class StageSkipped(AppError):
    """The stage does not run for this document, or cannot as the deployment is set up; the
    message says which, as the stage's detail, and `members` as for an error."""


@dataclass(frozen=True)
class StageFailure:
    """A stage that failed in this run: which, why, and whether running it again in a while may
    succeed (the cause was a provider or the storage being out of reach, not the document)."""

    stage: str
    message: str
    transient: bool
    # When the stage asks to be run again, when it knows: the next midnight UTC for the day's
    # spending limit. None leaves the moment to the worker's backoff.
    retry_at: datetime | None = None
    # The threat the scan named: the document is quarantined, not failed, and never retried.
    threat: str | None = None
    # What the document's members read of it, in the stage's detail and the document's error:
    # the message, or in a deployment the members' sentence where the message is the
    # operator's (app/errors.py, `shown`). The message is the operator's, for the log and the
    # job's last_error. Empty is the message.
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.detail:
            object.__setattr__(self, "detail", self.message)

    @property
    def quarantine(self) -> bool:
        return self.threat is not None


Stage = Callable[[Session, Document, Services, Meter], str]


def process_document(
    document_id: int, services: Services, lease: Lease | None = None
) -> list[StageFailure]:
    """Run every stage that is not already done, in order, and report the stages that failed.

    A failed required stage ends the run with the document failed; a failed optional stage
    leaves it ready without that stage's output. The job that runs the pipeline decides from
    the failures whether to queue the document again (app/worker.py).

    `lease` is that job's hold on the document (app/jobs.py). The run renews it in the
    transaction of every commit, and a commit that finds the job no longer this worker's is
    rolled back and ends the run with `LeaseLost`: a worker that was outrun by the next claim,
    or whose job was handed back at stop, never writes a document that is another worker's
    now. Without a lease the run is unguarded, as the tests and fixtures run it.

    What the stages use (the pages a scan took to read, the model calls, the texts embedded)
    is recorded as the organisation's usage at the end of each stage (app/usage.py).
    """
    with SessionLocal() as session:
        document = session.get(Document, document_id)
        if document is None:
            return []
        todo = [name for name in STAGES if document.stages.get(name, {}).get("status") != "done"]
        if not todo:
            # A job queued for a document that another job has since finished, such as a retry
            # that "Index again" overtook.
            _make_ready(session, document, lease)
            return []
        document.status = "processing"
        document.error = None
        _commit(session, lease)
        # The stages run for the document, not for a member: the member's part is the upload
        # that brought it in (app/api/documents.py).
        meter = Meter(
            org_id=document.project.org_id,
            project_id=document.project_id,
            user_id=None,
            ref=(DOCUMENT, document.id),
        )

        failures: list[StageFailure] = []
        for name in todo:
            failure = _run_stage(session, document, name, _STAGES[name], services, lease, meter)
            if failure is None:
                continue
            failures.append(failure)
            if failure.quarantine:
                _quarantine(session, document, failure, lease)
                INDEX_RUNS.labels("quarantined").inc()
                return failures
            if name in REQUIRED_STAGES:
                document.status = "failed"
                document.error = failure.detail
                _commit(session, lease)
                INDEX_RUNS.labels("failed").inc()
                return failures

        _make_ready(session, document, lease)
        INDEX_RUNS.labels("ready").inc()
        return failures


def _make_ready(session: Session, document: Document, lease: Lease | None) -> None:
    """Make the document ready. The first time, which ends its upload's indexing however many
    runs and retries that took, the time since the upload is what its members waited
    (`DOCUMENT_READY`); a later run, after "Index again" or a new embedding model, measures
    nothing."""
    document.status = "ready"
    first = document.ready_at is None
    if first:
        document.ready_at = datetime.now(UTC)
    _commit(session, lease)
    if first:
        waited = (document.ready_at - document.created_at).total_seconds()
        # The upload's time is the database's clock and this one the worker's.
        DOCUMENT_READY.observe(max(waited, 0.0))


def _quarantine(
    session: Session, document: Document, failure: StageFailure, lease: Lease | None
) -> None:
    """Set a flagged document apart: its status says so, its error carries the verdict for
    the members, and whatever an earlier run indexed of it is dropped, so that nothing of the
    file is searched, read or served (the routes refuse the file by the status and the scan
    stage, app/scanning.py). The file itself stays in the storage for a delete to remove."""
    _delete_derived(session, document.id, Fact, Passage, Section, Block)
    document.status = "quarantined"
    document.error = failure.detail
    audit_event(
        session,
        "document_quarantined",
        org_id=document.project.org_id,
        project_id=document.project_id,
        target=("document", document.id),
        detail={"filename": document.filename, "threat": failure.threat},
    )
    _commit(session, lease)


def reset_stages(document: Document, from_stage: str = "parse") -> None:
    """Queue a document to run again from the given stage. A stage before it that is not done
    runs too, so a quarantined document asked to run from parse is scanned again first."""
    rerun = STAGES[STAGES.index(from_stage) :]
    document.stages = {
        name: {"status": "pending", "detail": ""}
        if name in rerun
        else document.stages.get(name, {})
        for name in STAGES
    }
    document.status = "queued"
    document.error = None


def await_retry(document: Document, failure: StageFailure, notice: str) -> None:
    """Set the document up to run again from the failed stage, with the notice in that stage's
    detail for as long as the retry waits.

    The stage keeps saying it failed, and why, with the notice after it; the stages behind it
    run again too, as they do for "Index again". Without a required stage the document is
    queued, as a fresh upload is; without an optional one it stays ready, and what was indexed
    serves until the retry.
    """
    ready = document.status == "ready"
    reset_stages(document, failure.stage)
    document.stages = {
        **document.stages,
        failure.stage: {"status": "failed", "detail": f"{failure.detail} {notice}"},
    }
    if ready:
        document.status = "ready"


def give_up_retrying(document: Document, failure: StageFailure, notice: str) -> None:
    """Leave the document as the last run left it, with the notice after the stage's error, and
    in the document's own error when the stage was a required one."""
    detail = f"{failure.detail} {notice}"
    document.stages = {**document.stages, failure.stage: {"status": "failed", "detail": detail}}
    if document.status == "failed":
        document.error = detail


def _run_stage(
    session: Session,
    document: Document,
    name: str,
    stage: Stage,
    services: Services,
    lease: Lease | None,
    meter: Meter,
) -> StageFailure | None:
    """Run one stage, record its outcome and then what it used. Returns the failure, if it
    failed."""
    _set_stage(session, document, name, "running", lease=lease)
    document_id = document.id
    started = time.monotonic()
    # How the run ended, for the metrics (app/metrics.py); a lease lost at any commit is that.
    outcome = "lease_lost"
    try:
        detail = stage(session, document, services, meter)
        # The stage's rows are flushed with its mark, so a database lost at this commit is
        # lost under the stage, and handled below like one lost while it ran.
        _set_stage(session, document, name, "done", detail, lease)
        outcome = "done"
        return None
    except LeaseLost:
        raise
    except StageSkipped as skipped:
        session.rollback()
        outcome = "skipped"
        _set_stage(session, document, name, "skipped", _shown(skipped), lease)
        return None
    except (AppError, UnsupportedFile, FileTooLarge) as error:
        session.rollback()
        transient = isinstance(error, AppError) and error.transient
        retry_at = error.until if isinstance(error, StageDeferred) else None
        threat = error.threat if isinstance(error, MalwareFound) else None
        failure = StageFailure(name, str(error), transient, retry_at, threat, _shown(error))
        outcome = _outcome(failure)
        if isinstance(error, AppError) and error.members is not None and not transient:
            # The members read their own sentence, so the operator's is told here; the worker
            # tells one it runs again (app/worker.py).
            log.warning("Stage %s of document %s failed: %s", name, document_id, error)
        _set_stage(session, document, name, "failed", failure.detail, lease)
        return failure
    except Exception as error:
        session.rollback()
        if connection_lost(error):
            # The database went away under the stage, as in a failover. Once it answers again
            # the stage is run again with the usual backoff, as for a provider that is down;
            # while it does not, marking the stage raises, and the job's lease runs out for
            # the next claim to resume the document (app/worker.py).
            log.warning(
                "Stage %s of document %s lost its database connection: %s",
                name,
                document_id,
                summary(error.orig or error).splitlines()[0],
            )
            outcome = "database_lost"
            _set_stage(session, document, name, "failed", LOST_DATABASE, lease)
            return StageFailure(name, LOST_DATABASE, transient=True)
        log.exception("Stage %s failed for document %s", name, document_id)
        outcome = "crashed"
        crash = AppError(f"Unexpected error: {type(error).__name__}", members=STAGE_CRASHED)
        failure = StageFailure(name, str(crash), transient=False, detail=_shown(crash))
        _set_stage(session, document, name, "failed", failure.detail, lease)
        return failure
    finally:
        STAGE_RUNS.labels(name, outcome).inc()
        if outcome != "skipped":
            STAGE_SECONDS.labels(name).observe(time.monotonic() - started)
        note(STAGE, started, stage=name, outcome=outcome)
        _charge(session, meter, document_id)


def _shown(error: Exception) -> str:
    """What the document's members read of what ended a stage: the worker writes the stage's
    detail, so its environment decides (app/errors.py, `shown`)."""
    return shown(error, development=get_settings().environment == "development")


def _outcome(failure: StageFailure) -> str:
    """How the metrics call a stage that failed for a reason of its own (app/metrics.py)."""
    if failure.quarantine:
        return "quarantined"
    if failure.retry_at is not None:
        return "deferred"
    return "transient" if failure.transient else "failed"


def _charge(session: Session, meter: Meter, document_id: int) -> None:
    """Record what the stage used, in a transaction of its own once the stage's has been
    committed or rolled back: a stage that failed made its calls before it failed, and one
    whose work a lost lease discarded made them too, like the next worker that runs it again
    (app/usage.py). A stage whose failure could not be marked leaves its transaction broken,
    so the record starts from a rollback, which after a commit is nothing."""
    try:
        session.rollback()
        if meter.record(session):
            session.commit()
    except PoolTimeout:
        session.rollback()
        log.warning(
            "The usage of document %s was not recorded: every database connection is in use",
            document_id,
        )
    except Exception as error:
        # A stage that lost the database ends here on its way out; what it raised is what
        # the worker must see, not this.
        if not connection_lost(error):
            raise
        session.rollback()
        log.warning(
            "The usage of document %s was not recorded: the database could not be reached",
            document_id,
        )


def _set_stage(
    session: Session,
    document: Document,
    name: str,
    status: str,
    detail: str = "",
    lease: Lease | None = None,
) -> None:
    document.stages = {**document.stages, name: {"status": status, "detail": detail}}
    _commit(session, lease)


def _commit(session: Session, lease: Lease | None) -> None:
    """Commit the document's session, having renewed the lease in the same transaction.

    A lease that cannot be renewed is another worker's now, or was handed back at stop: the
    transaction is rolled back, so a stage's rows and its "done" mark are discarded together
    and the worker that took over runs the stage from a clean row set, and the run ends with
    `LeaseLost`. Renewing here also keeps the lease alive across the stage boundaries of a run
    whose heartbeat thread is stuck.
    """
    if lease is not None and not renew(session, lease):
        session.rollback()
        raise LeaseLost(lease)
    session.commit()


def _scan(session: Session, document: Document, services: Services, meter: Meter) -> str:
    """Have the file scanned for malware before any stage opens it or any route serves it.

    The scanner reads the file as it is in the storage, streamed to it from a local copy. A
    scanner that cannot be reached fails the stage transient, so the worker tries it again
    and the upload waits in the queue; a verdict that names a threat quarantines the document
    (`_quarantine`); without a scanner the stage is skipped and the document says so.
    """
    if services.scanner is None:
        raise StageSkipped(NOT_SCANNED, members=UNSCANNED)
    if not services.storage.exists(document.storage_key):
        raise PipelineError(MISSING_ORIGINAL)
    with services.storage.localize(document.storage_key) as path:
        threat = services.scanner.scan(path)
    if threat is not None:
        raise MalwareFound(threat)
    # A document that was quarantined, reported and then cleared by a scan with newer
    # signatures is reported again should a later scan flag it.
    document.quarantine_reported_at = None
    return "clean"


def _parse(session: Session, document: Document, services: Services, meter: Meter) -> str:
    if not services.storage.exists(document.storage_key):
        raise PipelineError(MISSING_ORIGINAL)
    # The parsers and the page renderer open files by path, so a remote original is copied to
    # this machine for the stage; the local storage hands out the file itself.
    with services.storage.localize(document.storage_key) as path:
        parsed = parse_within_limits(
            path, document.filename, _parse_limits(), document.id, slot=PARSING.held
        )
        # The pages are a measure of the upload, counted when the first parse learns how many
        # there are; a parse run again for "Index again" brings nothing new in. The plan's
        # month is checked before a scan is read, since reading it costs model calls.
        new_pages = (parsed.page_count or 0) if document.page_count is None else 0
        # The platform's ceiling on scanned pages comes before the plan's month: a document
        # over it is refused whatever the plan, so an upgrade is not the answer.
        if reason := _too_many_scans(len(parsed.ocr_pages)):
            raise PipelineError(reason)
        if reason := check_pages(session, document.project.organization, new_pages):
            raise PipelineError(reason)
        blocks = parsed.blocks
        note = ""

        if parsed.ocr_pages:
            if services.llm.available:
                # Each page is a model call, so the day's spending limit is checked here, after
                # the refusals that no day would lift, and before a page is read.
                _wait_for_the_day(session, document)
                _done_reading(session)
                blocks = _with_ocr(
                    path, document.filename, parsed.blocks, parsed.ocr_pages, services.llm, meter
                )
            elif blocks:
                note = f"; {len(parsed.ocr_pages)} scanned pages skipped (OCR needs the AI model)"
            else:
                raise PipelineError(
                    "This file has no text layer, and reading scans and images needs the AI "
                    f"model. {services.llm.problem()}",
                    members=(
                        "This file has no text layer, and reading scans and images needs the AI "
                        'model, which is unavailable just now. Use "Index again" later.'
                    ),
                )
    if not blocks:
        raise PipelineError("No text could be extracted from this file.")

    _delete_derived(session, document.id, Fact, Passage, Section, Block)
    session.add_all(
        Block(
            document_id=document.id,
            ordinal=ordinal,
            kind=block.kind,
            level=block.level,
            text=block.text,
            page=block.page,
            bbox=[round(value, 2) for value in block.bbox] if block.bbox else None,
        )
        for ordinal, block in enumerate(blocks)
    )
    if document.page_count is None:
        meter.add(UPLOAD, "pages", new_pages)
    document.page_count = parsed.page_count
    document.page_sizes = [[round(w, 2), round(h, 2)] for w, h in parsed.page_sizes]
    document.title = parsed.title
    _queue_drawing_ahead(session, document)
    return f"{len(blocks)} blocks{note}"


def _queue_drawing_ahead(session: Session, document: Document) -> None:
    """Queue the drawing of a PDF's first PRERENDER_PAGES pages, with the stage's commit, so
    that opening the document shows them from the storage at once (app/rendering.py).

    The job goes before the uploads waiting to be indexed: it is a few pages, done in moments
    by the next indexer that is free, and worth something only if done before anybody opens
    the document. One queued by a parse run again finds the pages kept and draws nothing.
    """
    pages = min(get_settings().prerender_pages, document.page_count or 0)
    if document.mime_type != "application/pdf" or not pages:
        return
    enqueue(
        session,
        RENDER_PAGES,
        org_id=document.project.org_id,
        project_id=document.project_id,
        payload={"document_id": document.id, "pages": list(range(1, pages + 1))},
        priority=LIVE,
    )


def _spending_limit(session: Session, document: Document) -> str | None:
    """Why the day's spending limit, the platform's or the plan's, leaves no room for a model
    call for this document, or None while it does (app/spending.py)."""
    return check_platform_spend(session) or check_spend(session, document.project.organization)


def _wait_for_the_day(session: Session, document: Document) -> None:
    """Put the stage off until midnight UTC when the day's spending limit is reached."""
    if reason := _spending_limit(session, document):
        raise StageDeferred(reason, day_of()[1])


def _too_many_scans(pages: int) -> str | None:
    """Why the document's scanned pages are more than OCR_MAX_PAGES lets the model read, or
    None when they fit (or the limit is off)."""
    cap = get_settings().ocr_max_pages
    if not cap or pages <= cap:
        return None
    return (
        f"This file has {pages} scanned pages to read, more than the {cap} one document can "
        "have. Split it and upload the parts."
    )


def _parse_limits() -> ParseLimits:
    settings = get_settings()
    return ParseLimits(
        timeout=settings.parse_timeout,
        memory_mb=settings.parse_memory_mb,
        max_pages=settings.parse_max_pages,
        max_rows=settings.parse_max_rows,
        max_chars=settings.parse_max_chars,
    )


def _with_ocr(
    path: Path,
    filename: str,
    blocks: list[ParsedBlock],
    ocr_pages: list[int],
    llm: LLM,
    meter: Meter,
) -> list[ParsedBlock]:
    """Transcribe the pages that have no text layer and merge them in page order.

    OCR_CONCURRENCY pages are read at once (`_side_by_side`), each drawn by one of the
    renderer's children and then sent to the model, so that a scan takes its requests' time
    shared among them rather than one after another. Each page read is counted as it is
    answered, whatever became of the others. A page that fails starts no more: the pages in
    flight are let end, and are counted, and the failure of the first in page order is raised,
    so a scan that fails at its tenth page is charged for nine and for those after it that
    were being read meanwhile.
    """
    suffix = Path(filename).suffix.lower()
    by_page: dict[int, list[ParsedBlock]] = defaultdict(list)
    for block in blocks:
        by_page[block.page or 1].append(block)

    def read(page: int) -> tuple[list[OcrBlock], ModelUsage]:
        usage = ModelUsage()
        if suffix == ".pdf":
            try:
                image = renderer().render(path, page, purpose=FOR_OCR, edge=MAX_IMAGE_EDGE)
            except RenderError as error:
                raise PipelineError(
                    f"Page {page} could not be drawn to read it as a scan. {error}",
                    transient=error.transient,
                ) from error
            media_type = "image/png"
        else:
            image, media_type = _prepare_image(path, IMAGE_TYPES[suffix])
        return llm.ocr_page(image, media_type, usage), usage

    failures: dict[int, BaseException] = {}
    for page, outcome in _side_by_side(read, ocr_pages, get_settings().ocr_concurrency):
        if isinstance(outcome, BaseException):
            failures[page] = outcome
            continue
        transcribed, usage = outcome
        meter.calls(OCR).include(usage)
        meter.add(OCR, "pages", 1)
        for found in transcribed:
            text = clean_text(found.text)
            if text:
                level = (found.level or 1) if found.kind == "heading" else None
                by_page[page].append(ParsedBlock(found.kind, text, level=level, page=page))
    if failures:
        raise failures[min(failures)]
    return [block for page in sorted(by_page) for block in by_page[page]]


def _side_by_side[T](
    work: Callable[[int], T], pages: Sequence[int], at_once: int
) -> Iterator[tuple[int, T | BaseException]]:
    """Run `work` on each page, `at_once` at a time, each on a thread of its own started in
    page order as an earlier one ends, and yield each page with what `work` returned or raised
    as it ends. Once one has raised no more are started, and those running are waited for.

    The threads are daemons, as the worker's are, so that a process that stops does not wait
    out a model's request; and each runs in a copy of the indexer's context, so that its lines
    name the job and its spans are the run's (app/logs.py, app/tracing.py).
    """
    ended: SimpleQueue[tuple[int, T | BaseException]] = SimpleQueue()

    def run(page: int) -> None:
        try:
            ended.put((page, work(page)))
        except BaseException as error:
            ended.put((page, error))

    waiting = deque(pages)
    running = 0
    failed = False
    while True:
        while waiting and running < at_once and not failed:
            page = waiting.popleft()
            thread = threading.Thread(
                target=copy_context().run, args=(run, page), name=f"ocr-{page}", daemon=True
            )
            thread.start()
            running += 1
        if not running:
            return
        page, outcome = ended.get()
        running -= 1
        failed = failed or isinstance(outcome, BaseException)
        yield page, outcome


def _prepare_image(path: Path, media_type: str) -> tuple[bytes, str]:
    """Return the image as uploaded, or scaled down when it is larger than the model reads."""
    with open_image(path) as image:
        if max(image.size) <= MAX_IMAGE_EDGE:
            return path.read_bytes(), media_type
        image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=90)
        return buffer.getvalue(), "image/jpeg"


def _structure(session: Session, document: Document, services: Services, meter: Meter) -> str:
    blocks = _blocks(session, document.id)
    hints: list[SectionHint] = []
    note = ""
    if services.llm.available and _needs_inferred_sections(blocks):
        if reason := _spending_limit(session, document):
            # The outline falls back to one section per page, as it does when the model fails:
            # the document cannot be searched without this stage, and is better searched today
            # by a rougher outline than held until midnight for a finer one.
            log.warning("Sections of document %s were not inferred: %s", document.id, reason)
            note = "; sections not inferred (the day's spending limit is reached)"
        else:
            usage = meter.calls(INFER_SECTIONS)
            _done_reading(session)
            try:
                hints = infer_sections(services.llm, blocks, usage)
            except LLMError as error:
                # The outline falls back to one section per page.
                log.warning("Section inference failed for document %s: %s", document.id, error)

    stem = Path(document.filename).stem
    outline = build_outline(blocks, document.title or stem, document.page_count, hints)
    document.title = outline.title if outline.title != stem else None

    _delete_derived(session, document.id, Fact, Passage, Section)
    rows: dict[int, Section] = {}
    for ordinal, draft in enumerate(outline.sections):
        row = Section(
            document_id=document.id,
            project_id=document.project_id,
            parent_id=rows[id(draft.parent)].id if draft.parent else None,
            ordinal=ordinal,
            level=draft.level,
            title=draft.title[:1000],
            path=draft.path(outline.title),
            page_start=draft.page_start,
            page_end=draft.page_end,
            block_start=draft.block_start,
            block_end=draft.block_end,
        )
        session.add(row)
        session.flush()  # children need the parent's id
        rows[id(draft)] = row

    language = get_settings().fts_language
    session.add_all(
        Passage(
            document_id=document.id,
            project_id=document.project_id,
            section_id=rows[id(draft.section)].id,
            ordinal=ordinal,
            text=draft.text,
            context=rows[id(draft.section)].path,
            language=language,
            page_start=draft.page_start,
            page_end=draft.page_end,
            block_start=draft.block_start,
            block_end=draft.block_end,
        )
        for ordinal, draft in enumerate(outline.passages)
    )
    return f"{len(outline.sections)} sections, {len(outline.passages)} passages{note}"


def _needs_inferred_sections(blocks: Sequence[Block]) -> bool:
    if any(block.kind == "heading" for block in blocks):
        return False
    return len(blocks) >= 15 or sum(len(block.text) for block in blocks) >= 3000


def _enrich(session: Session, document: Document, services: Services, meter: Meter) -> str:
    if not services.llm.available:
        raise StageSkipped(
            f"Summaries and facts need the AI model. {services.llm.problem()}",
            members=(
                "Summaries and facts need the AI model, which is unavailable just now. Use "
                '"Index again" later to add them.'
            ),
        )
    _wait_for_the_day(session, document)

    blocks = _blocks(session, document.id)
    sections = session.scalars(
        select(Section).where(Section.document_id == document.id).order_by(Section.ordinal)
    ).all()
    usage = meter.calls(ENRICH)
    _done_reading(session)
    # The day's spending limits are checked above, once for the stage: they hold the next unit
    # of work, and the most a document's summaries and facts may take is bounded by this.
    limit = get_settings().enrich_max_requests
    analysis = analyze_document(services.llm, document, sections, blocks, usage, limit)

    profile = analysis.profile
    old_root = document.title or Path(document.filename).stem
    document.title = clean_text(profile.title)[:500] or document.title
    document.doc_type = clean_text(profile.doc_type).lower()[:100] or None
    document.doc_date = parse_iso_date(profile.doc_date)
    document.summary = profile.summary.strip() or None

    new_root = document.title or old_root
    for section in sections:
        section.summary = analysis.summaries.get(section.id)
        if new_root != old_root:
            # Paths start with the document title, and passages are indexed with their path.
            section.path = new_root + section.path[len(old_root) :]
            session.execute(
                update(Passage).where(Passage.section_id == section.id).values(context=section.path)
            )

    session.execute(delete(Fact).where(Fact.document_id == document.id))
    facts = _fact_rows(session, document, analysis.facts, blocks, analysis.sampled)
    session.add_all(facts)
    summarized = sum(1 for section in sections if section.summary)
    detail = f"{summarized} section summaries, {len(facts)} facts"
    if analysis.reading is not None:
        detail += _partly_read(analysis.reading, document.page_count)
    return detail


def _partly_read(reading: Reading, page_count: int | None) -> str:
    """What the enrich stage's detail adds for a document longer than the model reads."""
    if reading.page and page_count:
        reach = f"pages 1 to {reading.page} of {page_count}"
    else:
        reach = f"the first {max(int(reading.share * 100), 1)} % of the document"
    return (
        f", from {reach}: the model reads no more of one document, and the rest is searched "
        "without summaries or facts"
    )


_WHITESPACE = re.compile(r"\s+")
_QUOTE_MARKS = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text.translate(_QUOTE_MARKS)).strip().lower()


def _fact_rows(
    session: Session,
    document: Document,
    extracted: list[ExtractedFact],
    blocks: Sequence[Block],
    sampled: frozenset[int] = frozenset(),
) -> list[Fact]:
    """The facts as rows, each linked to the block its quote is in and that block's passage;
    none from a block in `sampled`, a table the model was shown in part (app/indexing/
    enrich.py)."""
    by_ordinal = {block.ordinal: block for block in blocks}
    normalized = {block.ordinal: _normalize(block.text) for block in blocks}
    # Where each passage is, and no more: a document's passages may be tens of thousands,
    # each with its text, its text index and, run again, its vector.
    passages = session.execute(
        select(Passage.id, Passage.block_start, Passage.block_end)
        .where(Passage.document_id == document.id)
        .order_by(Passage.ordinal)
    ).all()

    rows: list[Fact] = []
    seen: set[tuple[str, str]] = set()
    for fact in extracted:
        name = normalize_fact_name(fact.name)
        value = clean_text(fact.value)
        if not name or not value or (name, value.lower()) in seen:
            continue

        # Trust the cited block only if the quote is really in it; otherwise look for the quote.
        quote = _normalize(fact.quote)
        block = by_ordinal.get(fact.block)
        verified = bool(quote) and block is not None and quote in normalized[block.ordinal]
        if not verified and quote:
            found = next((o for o, text in normalized.items() if quote in text), None)
            if found is not None:
                block, verified = by_ordinal[found], True
        if block is not None and block.ordinal in sampled:
            continue
        seen.add((name, value.lower()))

        passage = None
        if block is not None:
            passage = next(
                (p for p in passages if p.block_start <= block.ordinal < p.block_end),
                None,
            )
        rows.append(
            Fact(
                document_id=document.id,
                project_id=document.project_id,
                name=name,
                value_type=fact.type,
                value=value[:2000],
                number=fact.number if fact.type in ("number", "money") else None,
                date=parse_iso_date(fact.date) if fact.type == "date" else None,
                currency=(fact.currency or "").upper()[:10] or None,
                quote=clean_text(fact.quote)[:1000] or None,
                page=block.page if block else None,
                block_ordinal=block.ordinal if block else None,
                passage_id=passage.id if passage else None,
                verified=verified,
            )
        )
    return rows


def _embed(session: Session, document: Document, services: Services, meter: Meter) -> str:
    # What is embedded, and no more: a passage's text index and its old vector stay unread.
    passages = session.execute(
        select(Passage.id, Passage.context, Passage.text)
        .where(Passage.document_id == document.id)
        .order_by(Passage.ordinal)
    ).all()
    if not passages:
        return "no passages"
    _done_reading(session)
    # The tokens are counted batch by batch, as the endpoint bills them, and priced by
    # MODEL_PRICES under EMBEDDING_MODEL like a model's (app/usage.py). Each chunk's vectors
    # come back as Python floats, about 25 KB a passage at 768 dimensions, and are kept as
    # float32 from then on, 3 KB, which is what the column stores: tens of thousands held as
    # floats took the worker past a gigabyte.
    usage = meter.calls(EMBED)
    vectors: list[Vector] = []
    for start in range(0, len(passages), EMBED_CHUNK):
        chunk = passages[start : start + EMBED_CHUNK]
        texts = [f"{passage.context}\n\n{passage.text}" for passage in chunk]
        vectors.extend(Vector(vector) for vector in services.embedder.embed_documents(texts, usage))
    # The texts are counted once the vectors are back: a request that failed embedded nothing
    # the document keeps, and the stage runs again.
    model = services.embedder.model
    meter.add(EMBED, "inputs", len(passages), model=model)
    # Written a statement of WRITE_CHUNK at a time, each vector as text only for its own.
    for start in range(0, len(passages), WRITE_CHUNK):
        session.execute(
            update(Passage),
            [
                {"id": passage.id, "embedding": vector, "embedding_model": model}
                for passage, vector in zip(
                    passages[start : start + WRITE_CHUNK],
                    vectors[start : start + WRITE_CHUNK],
                    strict=True,
                )
            ],
        )
    return f"{len(passages)} passages"


def _done_reading(session: Session) -> None:
    """End the stage's transaction before it waits on the model or the embeddings endpoint, so
    that the wait holds no connection, no snapshot and no lock.

    The stage has only read so far, so the commit writes nothing, and unlike a rollback it
    keeps what was read loaded (the session does not expire on commit); what the stage writes
    afterwards goes into a transaction of its own, committed with the stage's mark under the
    lease. The waits run to minutes (a long document's summaries, a scan's pages), and a
    transaction open across one keeps vacuum from removing any row replaced since it began,
    every re-index's old passages among them, holds a server connection of a pooler in
    transaction mode, and is ended by the role's idle_in_transaction_session_timeout, which
    fails the stage at its next statement (deploying.md, "The database"). The scan and the
    parse child need nothing of this: the stage has read nothing when they run.
    """
    session.commit()


def _blocks(session: Session, document_id: int) -> Sequence[Block]:
    return session.scalars(
        select(Block).where(Block.document_id == document_id).order_by(Block.ordinal)
    ).all()


def _delete_derived(session: Session, document_id: int, *models: type) -> None:
    for model in models:
        session.execute(delete(model).where(model.document_id == document_id))


_STAGES: dict[str, Stage] = {
    "scan": _scan,
    "parse": _parse,
    "structure": _structure,
    "enrich": _enrich,
    "embed": _embed,
}
