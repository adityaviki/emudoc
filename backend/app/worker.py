"""The background worker: threads that claim jobs from the queue (app/jobs.py) and run them,
one that keeps their leases alive, and one that keeps the stored data tidy at intervals.

It is its own process, `python -m app.worker`, so that a deployment scales it apart from the
API: the API replicas serve requests while as many workers as the backlog needs index documents.
A worker starts like the API does (app/startup.py) and claims the kinds of job it has a handler
for (`HANDLERS`). In development the API process runs the same threads itself, so that one
`uvicorn` does everything (RUN_WORKER in app/config.py).

A stop is graceful: the threads stop claiming, the jobs they are running get a grace period to
finish, and the ones that did not finish are handed back to the queue with their lease cleared,
so that the next worker takes them at once rather than when the lease would have run out.

A stage that failed because a provider or the storage was out of reach is tried again: the index
handler queues the document again from that stage after a wait that doubles each time, a few
times, and the stage's detail says so for as long as the user looks. A stage that failed for a
reason that will not pass on its own (the file, the key, the model's reply) is not.
"""

import argparse
import logging
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.indexing.pipeline import (
    Services,
    StageFailure,
    await_retry,
    give_up_retrying,
    process_document,
)
from app.jobs import INDEX_DOCUMENT, LEASE, claim, extend_leases, finish, queue_index, release
from app.maintenance import housekeeping
from app.models import Document, Job
from app.startup import prepare
from app.storage import StorageError

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
# How long a stopping worker lets the jobs it is running finish before it hands them back.
# Under the 10 seconds Docker allows by default before it kills a container; a deployment that
# allows more can pass more with --grace.
GRACE = 8.0

CRASHED = "Indexing stopped unexpectedly. Try reprocessing."
# How many times a stage that failed for a passing reason is run again, and how long the first
# wait is; each wait is twice the one before, so four retries span a quarter of an hour. An
# outage longer than that leaves the stage failed until the user presses "Index again".
MAX_RETRIES = 4
FIRST_BACKOFF = timedelta(minutes=1)


@dataclass(frozen=True)
class Handler:
    """What the worker does with one kind of job.

    `run` does the work; it may be run again after a lost lease or a stop, so it resumes or
    repeats safely. `abandon` is called instead when the job is given up because every attempt
    was lost to a process that died: it leaves what the job was about in a state the user can
    act on.
    """

    run: Callable[[Job, Services], None]
    abandon: Callable[[Job, Services, str], None]


def _run_index(job: Job, services: Services) -> None:
    document_id = job.payload["document_id"]
    try:
        failures = process_document(document_id, services)
    except Exception:
        # Each stage catches its own errors; this is the pipeline itself failing around them.
        _fail_document(document_id, CRASHED)
        raise
    # From the first stage worth running again; the ones behind it run again with it.
    if failure := next((failure for failure in failures if failure.transient), None):
        _retry_index(job, failure)


def _retry_index(job: Job, failure: StageFailure) -> None:
    """Queue the document to run again from the failed stage after the next backoff, or give up
    once it has been retried MAX_RETRIES times, saying either in the stage's detail."""
    document_id = job.payload["document_id"]
    retry = job.payload.get("retry", 0) + 1
    with SessionLocal() as session:
        document = session.get(Document, document_id)
        if document is None or document.stages.get(failure.stage, {}).get("status") != "failed":
            # Deleted, or queued again by the user since the run ended: that job runs the stage.
            return
        if retry > MAX_RETRIES:
            tries = MAX_RETRIES + 1
            waited = FIRST_BACKOFF * (2**MAX_RETRIES - 1)
            give_up_retrying(
                document,
                failure,
                f'Tried {tries} times over {_minutes(waited)}; use "Index again" once the '
                "service is back.",
            )
            session.commit()
            log.warning(
                "Document %s: stage %s failed %s times for a passing reason and is not tried "
                "again (%s)",
                document_id,
                failure.stage,
                tries,
                failure.message,
            )
            return
        wait = FIRST_BACKOFF * 2 ** (retry - 1)
        await_retry(document, failure, f"Retrying in {_minutes(wait)} ({retry} of {MAX_RETRIES}).")
        queue_index(
            session,
            document,
            job.priority,
            run_at=datetime.now(UTC) + wait,
            retry=retry,
        )
        session.commit()
    log.info(
        "Document %s: stage %s failed for a passing reason, running it again in %s "
        "(retry %s of %s): %s",
        document_id,
        failure.stage,
        _minutes(wait),
        retry,
        MAX_RETRIES,
        failure.message,
    )


def _minutes(delta: timedelta) -> str:
    minutes = max(1, round(delta.total_seconds() / 60))
    return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"


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

    @property
    def id(self) -> str:
        """What the jobs this worker claims carry as `worker_id`."""
        return self._id

    def start(self) -> None:
        for index in range(self._concurrency):
            self._spawn(self._loop, f"indexer-{index}")
        self._spawn(self._heartbeat, "heartbeat")
        self._spawn(self._housekeep, "housekeeper")

    def _spawn(self, target: Callable[[], None], name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def stop(self, grace: float = GRACE) -> None:
        """Stop claiming, let the running jobs finish for `grace` seconds, and hand back the rest.

        A job handed back is queued again with its lease cleared, so the next worker takes it at
        once. Nothing stops the thread that is still running it, but the process exits right
        after this returns, so the thread gets no further than its next statement; and a
        handler resumes or repeats safely anyway, as it must after a lost lease.
        """
        self._stop.set()
        self._wake.set()
        deadline = time.monotonic() + grace
        for thread in self._threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._held_lock:
            unfinished = sorted(self._held)
        if not unfinished:
            return
        try:
            released = release(unfinished, self._id)
        except Exception:
            log.exception(
                "Jobs %s did not finish within %s seconds and could not be handed back; "
                "another worker takes them once their lease runs out, in %s",
                unfinished,
                grace,
                LEASE,
            )
            return
        log.warning(
            "Jobs %s did not finish within %s seconds and were queued again",
            sorted(released),
            grace,
        )

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
                # A stop sets the wake event once for every thread; one that cleared it would
                # leave the others waiting out a whole poll.
                if not self._stop.is_set():
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
                "Job %s (%s) was taken over, handed back or removed before this worker finished it",
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


def serve(
    services: Services, settings: Settings, stopping: threading.Event, grace: float = GRACE
) -> None:
    """Run the worker until `stopping` is set, then stop it within `grace` seconds."""
    worker = Worker(services, settings.worker_concurrency)
    worker.start()
    log.info("Worker %s is running, %s jobs at a time", worker.id, settings.worker_concurrency)
    stopping.wait()
    log.info("Stopping; the running jobs have %s seconds to finish", grace)
    worker.stop(grace)
    log.info("Worker %s stopped", worker.id)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.worker",
        description="Run the background worker, which indexes uploaded documents and keeps the "
        "stored data tidy. Run it in the backend directory with `uv run`; it reads the same .env "
        "as the API, and refuses the same misconfigurations. Ctrl+C or SIGTERM stops it: the "
        "running jobs get a moment to finish, and the rest are queued again for the next worker.",
    )
    parser.add_argument(
        "--grace",
        type=float,
        default=GRACE,
        metavar="SECONDS",
        help="how long the running jobs get to finish at stop (default: %(default)s); keep it "
        "under what the deployment allows before it kills the process",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # The schema check sets up a migration context, which announces itself; nothing to act on.
    logging.getLogger("alembic.runtime.migration").setLevel(logging.WARNING)

    settings = get_settings()
    try:
        services = prepare(settings)
    except (RuntimeError, StorageError) as error:
        sys.exit(str(error))
    stopping = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopping.set())
    serve(services, settings, stopping, args.grace)


if __name__ == "__main__":
    main()
