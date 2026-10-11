"""Background work: threads that claim jobs from the queue (app/jobs.py) and run them, one that
keeps their leases alive, and one that keeps the stored data tidy at intervals."""

import logging
import os
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import text

from app.db import SessionLocal
from app.indexing.pipeline import Services, process_document
from app.jobs import INDEX_DOCUMENT, claim, extend_leases, finish
from app.maintenance import housekeeping
from app.models import Job

log = logging.getLogger(__name__)

POLL_SECONDS = 2.0
# How long a thread waits after the database refused a claim before it tries again.
RETRY_SECONDS = 5.0
# How often the leases of the jobs this process holds are extended; well inside LEASE, so a
# missed round or two does not hand a running job to another worker.
HEARTBEAT_SECONDS = 60.0
# Housekeeping (app/maintenance.py) first runs a minute after start, so that a process
# restarted in a loop does not walk the storage every time, and then at this interval.
HOUSEKEEPING_DELAY = 60.0
HOUSEKEEPING_INTERVAL = 6 * 3600.0

CRASHED = "Indexing stopped unexpectedly. Try reprocessing."


@dataclass(frozen=True)
class Handler:
    """What the worker does with one kind of job.

    `run` does the work; it may be run again after a lost lease, so it resumes or repeats
    safely. `abandon` is called instead when the job is given up because every attempt was lost
    to a process that died: it leaves what the job was about in a state the user can act on.
    """

    run: Callable[[Job, Services], None]
    abandon: Callable[[Job, Services, str], None]


def _run_index(job: Job, services: Services) -> None:
    try:
        process_document(job.payload["document_id"], services)
    except Exception:
        # Each stage catches its own errors; this is the pipeline itself failing around them.
        _fail_document(job.payload["document_id"], CRASHED)
        raise


def _abandon_index(job: Job, services: Services, reason: str) -> None:
    _fail_document(job.payload["document_id"], reason)


def _fail_document(document_id: int, error: str) -> None:
    with SessionLocal() as session:
        session.execute(
            text("UPDATE documents SET status = 'failed', error = :error WHERE id = :id"),
            {"id": document_id, "error": error},
        )
        session.commit()


# The kinds this worker runs. A kind in JOB_KINDS that is missing here is left in the queue for
# a worker that has it.
HANDLERS: dict[str, Handler] = {INDEX_DOCUMENT: Handler(_run_index, _abandon_index)}


class Worker:
    def __init__(self, services: Services, concurrency: int):
        self._services = services
        self._concurrency = concurrency
        self._id = f"{socket.gethostname()}:{os.getpid()}"
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        # The jobs the threads are running now, whose leases the heartbeat extends.
        self._held: set[int] = set()
        self._held_lock = threading.Lock()

    def start(self) -> None:
        for index in range(self._concurrency):
            self._spawn(self._loop, f"indexer-{index}")
        self._spawn(self._heartbeat, "heartbeat")
        self._spawn(self._housekeep, "housekeeper")

    def _spawn(self, target: Callable[[], None], name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for thread in self._threads:
            thread.join(timeout=5)

    def notify(self) -> None:
        """Wake the workers after jobs were queued."""
        self._wake.set()

    def _loop(self) -> None:
        kinds = list(HANDLERS)
        while not self._stop.is_set():
            try:
                job = claim(self._id, kinds)
            except Exception:
                # A database that refused one claim is usually back by the next; the thread
                # waits rather than dying and leaving the pool one thread smaller.
                log.exception("Claiming a job failed; trying again in %s seconds", RETRY_SECONDS)
                self._stop.wait(RETRY_SECONDS)
                continue
            if job is None:
                self._wake.wait(POLL_SECONDS)
                self._wake.clear()
                continue
            self._run(job)

    def _run(self, job: Job) -> None:
        handler = HANDLERS[job.kind]
        try:
            with self._holding(job.id):
                if job.attempts > job.max_attempts:
                    # Every attempt so far ended with the lease running out: the process died
                    # each time, so running it once more would most likely kill this one.
                    reason = (
                        f"Indexing stopped unexpectedly {job.max_attempts} times and was not "
                        "tried again. Try reprocessing later, or ask support about this file."
                    )
                    log.error(
                        "Job %s (%s) was lost %s times and is given up",
                        job.id,
                        job.kind,
                        job.max_attempts,
                    )
                    handler.abandon(job, self._services, reason)
                    self._finish(job, "failed", f"Given up after {job.max_attempts} lost attempts")
                    return
                try:
                    handler.run(job, self._services)
                except Exception as error:
                    log.exception("Job %s (%s) failed", job.id, job.kind)
                    self._finish(job, "failed", f"{type(error).__name__}: {error}")
                else:
                    self._finish(job, "done")
        except Exception:
            # The database went away while the job was being settled. Its lease runs out and
            # another claim runs it again, which every handler must be able to take.
            log.exception("Job %s (%s) could not be settled", job.id, job.kind)

    def _finish(self, job: Job, status: str, error: str | None = None) -> None:
        if not finish(job, self._id, status, error):
            log.warning(
                "Job %s (%s) was taken over or removed before this worker finished it",
                job.id,
                job.kind,
            )

    @contextmanager
    def _holding(self, job_id: int) -> Iterator[None]:
        with self._held_lock:
            self._held.add(job_id)
        try:
            yield
        finally:
            with self._held_lock:
                self._held.discard(job_id)

    def _heartbeat(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            with self._held_lock:
                held = sorted(self._held)
            if not held:
                continue
            try:
                kept = extend_leases(held, self._id)
            except Exception:
                log.exception("Could not extend the leases of jobs %s", held)
                continue
            if lost := set(held) - kept:
                log.warning(
                    "Jobs %s ran past their lease and were taken over while this worker was "
                    "still running them",
                    sorted(lost),
                )

    def _housekeep(self) -> None:
        delay = HOUSEKEEPING_DELAY
        while not self._stop.wait(delay):
            try:
                housekeeping(self._services.storage)
            except Exception:
                # A storage or database that is unreachable now may be back by the next round;
                # the thread stays alive for it.
                log.exception(
                    "Housekeeping failed; it runs again in %s hours", HOUSEKEEPING_INTERVAL / 3600
                )
            delay = HOUSEKEEPING_INTERVAL
