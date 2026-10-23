"""The background worker: threads that claim jobs from the queue (app/jobs.py) and run them,
one that keeps their leases alive, one that hears of new jobs, and one that keeps the stored
data tidy at intervals.

It is its own process, `python -m app.worker`, so that a deployment scales it apart from the
API: the API replicas serve requests while as many workers as the backlog needs index documents
and answer questions. A worker starts like the API does (app/startup.py) and claims the kinds
of job it has a handler for (`HANDLERS`), in two lanes: WORKER_CONCURRENCY indexer threads for
the documents (and for a PDF's first pages, drawn ahead once it is parsed, app/rendering.py),
and ANSWER_CONCURRENCY answerer threads for the questions (app/answers.py) and the runs of
cross-checks with their reports (app/check_runs.py), so that a backlog
of parses never holds an answer up and the other way round. An indexer spends most of a
document waiting on the model, so they are many, and only PARSE_CONCURRENCY of them run a
parse, the part that takes memory, at once (`ParseSlots` in app/indexing/pipeline.py). The
listener thread wakes them all when a job is queued anywhere, so a question is taken the moment
it is asked.
In development the API process runs the same threads itself, so that one `uvicorn` does
everything (RUN_WORKER in app/config.py).

The process watches its own threads: one that ended, which none does by design, stops the
worker with exit status 1, so that whatever runs it starts a whole one again rather than leave
it running a thread short. While they all run it touches a file (--alive-file), whose age a
health check reads, since the worker serves nothing to ask.

A stop is graceful: the threads stop claiming, the jobs they are running get a grace period to
finish, and the ones that did not finish are handed back to the queue with their lease cleared,
so that the next worker takes them at once rather than when the lease would have run out.

The heartbeat thread also says that the worker runs, at its start and every round, and the stop
says that it stopped (`beat`, `record_stop` in app/jobs.py): the worker serves nothing to ask,
so that is how the members' status line and the operator know whether anyone takes the queue's
work.

A job is only ever written by one worker at a time. The heartbeat thread extends the leases of
the jobs this process runs, and the pipeline renews its job's lease in the transaction of every
commit it makes (`Lease` in app/jobs.py): a run whose job was taken over after a heartbeat that
could not reach the database, or handed back by a stop, ends with `LeaseLost` at its next commit
with nothing of that stage written, and the job is the other worker's to finish.

A stage that failed because a provider or the storage was out of reach is tried again: the index
handler queues the document again from that stage after a wait that doubles each time, a few
times, and the stage's detail says so for as long as the user looks. A stage that failed for a
reason that will not pass on its own (the file, the key, the model's reply) is not. A stage
that the day's spending limit put off (app/spending.py) is queued again for midnight UTC, when
the limit resets, and that wait counts as no retry.

Every run of a job is timed, its stages, model requests and tool calls among it, and ends with
one line in the log that says where its time went, under the trace of the request that queued
it (app/tracing.py).

A job the worker could not complete ends `failed`, with `last_error` saying why: lost to a
dying process `max_attempts` times, a handler that raised, or a stage that kept failing through
every retry. Those are the dead letters (app/deadletters.py): the alerter thread mails the new
ones to ALERT_EMAIL every quarter of an hour, and the `failed-jobs` command lists them. The same
thread warns the operator when a day's spending nears a limit (app/spending.py), reports
the uploads the malware scan quarantined (app/scanning.py), and writes when something it can see
goes wrong and when it is over: a backlog, indexing or answers that fail, a model failing over,
an endpoint, the scanner or the storage that does not answer, the database falling behind
(app/watch.py).
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
from functools import partial
from pathlib import Path

from sqlalchemy import text

from app.answers import abandon_answer, run_answer
from app.bounces import read_ses_events
from app.check_runs import abandon_run, run_workflow
from app.config import MAIL_THREADS, Settings, get_settings
from app.db import SessionLocal, connection_lost
from app.deadletters import alert_failed_jobs
from app.errors import summary
from app.export import abandon_export, make_export
from app.indexing.pipeline import (
    REQUIRED_STAGES,
    Services,
    StageFailure,
    await_retry,
    give_up_retrying,
    process_document,
)
from app.jobs import (
    ANSWER_QUESTION,
    APPLY_LANGUAGE,
    EXPORT_PROJECT,
    INDEX_DOCUMENT,
    LEASE,
    RENDER_PAGES,
    RUN_WORKFLOW,
    SEEN_WITHIN,
    SEND_EMAIL,
    Lease,
    LeaseLost,
    beat,
    claim,
    extend_leases,
    finish,
    listen,
    queue_index,
    record_stop,
    release,
)
from app.languages import apply_language
from app.logs import configure_logging, logging_about
from app.mail import Mailer
from app.maintenance import housekeeping
from app.metrics import (
    ANSWERER,
    INDEXER,
    JOB_RUNS,
    JOB_SECONDS,
    JOB_WAIT,
    LANES,
    MAILER,
    MetricsRefused,
    export_metrics,
    lane_of,
)
from app.models import Document, Job
from app.notices import after_index
from app.postbox import abandon_email, run_email
from app.rendering import draw_ahead
from app.scanning import ScanError, alert_quarantines
from app.spending import alert_spending, when
from app.startup import prepare
from app.storage import StorageError
from app.tracing import describe, timed, trace_of
from app.tracking import start_tracking
from app.version import VERSION
from app.watch import watch

# By its name rather than `__name__`, which is "__main__" when the module runs as a command.
log = logging.getLogger("app.worker")

POLL_SECONDS = 2.0
# How often the listener checks whether the worker is stopping, between notifications; no
# query is made for it.
LISTEN_SECONDS = 0.25
# How long a thread waits after the database refused a claim before it tries again.
RETRY_SECONDS = 5.0
# How often the leases of the jobs this process holds are extended, and the worker says it runs;
# well inside LEASE, so a missed round or two does not hand a running job to another worker,
# and a third of SEEN_WITHIN, so that it does not read as no worker either.
HEARTBEAT_SECONDS = 60.0
# Housekeeping (app/maintenance.py) first runs a minute after start, so that a process
# restarted in a loop does not walk the storage every time, and then at this interval.
HOUSEKEEPING_DELAY = 60.0
HOUSEKEEPING_INTERVAL = 6 * 3600.0
# How often the jobs this process and the others gave up on are mailed to ALERT_EMAIL, as one
# digest (app/deadletters.py), the day's spending is measured against its limits
# (app/spending.py), the quarantined uploads are reported (app/scanning.py) and the watch looks
# (app/watch.py): within the quarter of an hour the retries take anyway, and seldom enough that
# a backlog of bad files is one message, not hundreds.
ALERT_INTERVAL = 15 * 60.0
# How long a stopping worker lets the jobs it is running finish before it hands them back.
# Under the 10 seconds Docker allows by default before it kills a container; a deployment that
# allows more can pass more with --grace.
GRACE = 8.0
# How often the process checks that its threads are all running and touches the --alive-file;
# a health check gives it a few rounds before it calls the worker stuck.
ALIVE_SECONDS = 10.0

CRASHED = "Indexing stopped unexpectedly. Try reprocessing."
# The outcomes of a run after which the job's row says it ended (`Handler.after`).
ENDED = ("done", "failed", "crashed", "abandoned")
# How many times a stage that failed for a passing reason is run again, and how long the first
# wait is; each wait is twice the one before, so four retries span a quarter of an hour. An
# outage longer than that leaves the stage failed until the user presses "Index again", or for
# an optional stage "Index the rest".
MAX_RETRIES = 4
FIRST_BACKOFF = timedelta(minutes=1)


@dataclass(frozen=True)
class Handler:
    """What the worker does with one kind of job.

    `run` does the work; it may be run again after a lost lease or a stop, so it resumes or
    repeats safely. It returns None, or the reason it gave the job up for good, which the worker
    records as the job's `last_error` for the operator (app/deadletters.py); what the job was
    about must already say so to the user. `abandon` is called instead when the job is given up
    because every attempt (`max_attempts` of them) was lost to a process that died: it leaves
    what the job was about in a state the user can act on, saying so in its own words.
    `after`, when there is one, runs once the job's row says it ended, done or failed, for
    what must see it ended: an upload's notice waits for the jobs of all its documents
    (app/notices.py). A run lost to another worker leaves it to that one; a failure of it is
    logged and changes nothing of the job.
    """

    run: Callable[[Job, Services], str | None]
    abandon: Callable[[Job, Services], None]
    after: Callable[[Job, Services], None] | None = None


def _run_index(job: Job, services: Services) -> str | None:
    document_id = job.payload["document_id"]
    try:
        failures = process_document(document_id, services, Lease(job.id, job.worker_id))
    except LeaseLost:
        # The document is another worker's now, which marks it as its run goes.
        raise
    except Exception as error:
        # Each stage catches its own errors; this is the pipeline itself failing around them,
        # or the database still away after a stage lost it, which the next claim resumes.
        if not connection_lost(error):
            _fail_document(document_id, CRASHED)
        raise
    # From the first stage worth running again; the ones behind it run again with it.
    if failure := next((failure for failure in failures if failure.transient), None):
        return _retry_index(job, failure)
    return None


def _retry_index(job: Job, failure: StageFailure) -> str | None:
    """Queue the document to run again from the failed stage after the next backoff, or at the
    moment the failure asks for, or give up once it has been retried MAX_RETRIES times, saying
    either in the stage's detail.

    A stage the day's spending limit put off (`retry_at`) waits for the limit to reset, and
    that wait is no retry: nothing was out of reach, and the next day's run is the first try
    against a limit with room. Returns the reason when it gives up, for the job to end failed
    with.
    """
    document_id = job.payload["document_id"]
    deferred = failure.retry_at is not None
    retry = job.payload.get("retry", 0) + (0 if deferred else 1)
    with SessionLocal() as session:
        document = session.get(Document, document_id)
        if document is None or document.stages.get(failure.stage, {}).get("status") != "failed":
            # Deleted, or queued again by the user since the run ended: that job runs the stage.
            return None
        if retry > MAX_RETRIES:
            tries = MAX_RETRIES + 1
            waited = FIRST_BACKOFF * (2**MAX_RETRIES - 1)
            # A ready document keeps what it has, so its fix runs what is missing alone.
            fix = "Index again" if failure.stage in REQUIRED_STAGES else "Index the rest"
            give_up_retrying(
                document,
                failure,
                f'Tried {tries} times over {_minutes(waited)}; use "{fix}" once the service is '
                "back.",
            )
            session.commit()
            reason = (
                f"Stage {failure.stage} failed {tries} times over {_minutes(waited)}: "
                f"{failure.message}"
            )
            log.error("Document %s is not tried again. %s", document_id, reason)
            return reason
        if deferred:
            run_at = failure.retry_at
            notice = f"Indexing continues at {when(run_at)}."
        else:
            wait = FIRST_BACKOFF * 2 ** (retry - 1)
            run_at = datetime.now(UTC) + wait
            notice = f"Retrying in {_minutes(wait)} ({retry} of {MAX_RETRIES})."
        await_retry(document, failure, notice, run_at)
        queue_index(session, document, job.priority, run_at=run_at, retry=retry)
        session.commit()
    if deferred:
        log.info(
            "Document %s: stage %s waits for the day's spending limit to reset, running again at "
            "%s: %s",
            document_id,
            failure.stage,
            when(run_at),
            failure.message,
        )
    else:
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
    return None


def _minutes(delta: timedelta) -> str:
    minutes = max(1, round(delta.total_seconds() / 60))
    return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"


def _abandon_index(job: Job, services: Services) -> None:
    _fail_document(
        job.payload["document_id"],
        f"Indexing stopped unexpectedly {job.max_attempts} times and was not tried again. Try "
        "reprocessing later, or ask support about this file.",
    )


def _fail_document(document_id: int, error: str) -> None:
    with SessionLocal() as session:
        session.execute(
            text("UPDATE documents SET status = 'failed', error = :error WHERE id = :id"),
            {"id": document_id, "error": error},
        )
        session.commit()


def _run_render(job: Job, services: Services) -> str | None:
    draw_ahead(job.payload["document_id"], job.payload["pages"], services.storage)
    return None


def _abandon_render(job: Job, services: Services) -> None:
    # The pages are drawn when the document pane asks for them; nobody waits on this job.
    pass


def _run_export(job: Job, services: Services) -> str | None:
    return make_export(job.payload["export_id"], services.storage, Lease(job.id, job.worker_id))


def _abandon_export(job: Job, services: Services) -> None:
    abandon_export(job.payload["export_id"])


def _run_language(job: Job, services: Services) -> str | None:
    apply_language(job.org_id, Lease(job.id, job.worker_id))
    return None


def _run_workflow(job: Job, services: Services) -> str | None:
    return run_workflow(job.payload["run_id"], services, Lease(job.id, job.worker_id))


def _abandon_workflow(job: Job, services: Services) -> None:
    abandon_run(job.payload["run_id"], job.max_attempts)


def _abandon_language(job: Job, services: Services) -> None:
    # The passages not moved yet are searched in the language they have, as before the change;
    # the next change of language, or indexing a document again, moves them.
    pass


# The kinds this worker runs. A kind in JOB_KINDS that is missing here is left in the queue for
# a worker that has it. The answers and the mail have a lane of threads each (`LANE_KINDS`),
# the runs of cross-checks the answers', since a member waits for them and their reports are
# the agent's; every other kind shares the indexers.
HANDLERS: dict[str, Handler] = {
    INDEX_DOCUMENT: Handler(_run_index, _abandon_index, after_index),
    ANSWER_QUESTION: Handler(run_answer, abandon_answer),
    RENDER_PAGES: Handler(_run_render, _abandon_render),
    SEND_EMAIL: Handler(run_email, abandon_email),
    APPLY_LANGUAGE: Handler(_run_language, _abandon_language),
    EXPORT_PROJECT: Handler(_run_export, _abandon_export),
    RUN_WORKFLOW: Handler(_run_workflow, _abandon_workflow),
}
LANE_KINDS = {ANSWERER: [ANSWER_QUESTION, RUN_WORKFLOW], MAILER: [SEND_EMAIL]}


class Worker:
    def __init__(
        self,
        services: Services,
        concurrency: int,
        mailer: Mailer | None = None,
        *,
        answer_concurrency: int = 0,
    ):
        self._services = services
        self._concurrency = concurrency
        self._answer_concurrency = answer_concurrency
        # What the jobs given up on and the spending warnings are reported through; without it,
        # or without ALERT_EMAIL, the failures are in the log and the failed-jobs command only,
        # and nobody is warned of the spending.
        self._mailer = mailer
        self._id = f"{socket.gethostname()}:{os.getpid()}"
        self._started = datetime.now(UTC)
        self._wake = threading.Event()
        self._stop = threading.Event()
        # Set while the listener's LISTEN is in place; clear while the threads only poll.
        self._listening = threading.Event()
        self._threads: list[threading.Thread] = []
        # The jobs the threads are running now, whose leases the heartbeat extends, with the
        # lane of the thread that runs each.
        self._held: dict[int, str] = {}
        self._held_lock = threading.Lock()

    @property
    def id(self) -> str:
        """What the jobs this worker claims carry as `worker_id`."""
        return self._id

    def start(self) -> None:
        laned = {kind for kinds in LANE_KINDS.values() for kind in kinds}
        index_kinds = [kind for kind in HANDLERS if kind not in laned]
        for index in range(self._concurrency):
            self._spawn(partial(self._loop, index_kinds), f"{INDEXER}-{index}")
        for index in range(self._answer_concurrency):
            self._spawn(partial(self._loop, LANE_KINDS[ANSWERER]), f"{ANSWERER}-{index}")
        for index in range(MAIL_THREADS):
            self._spawn(partial(self._loop, LANE_KINDS[MAILER]), f"{MAILER}-{index}")
        settings = get_settings()
        if settings.mail_events_queue:
            self._spawn(
                partial(read_ses_events, settings.mail_events_queue, self._stop), "mail-events"
            )
        if settings.listen_url:
            self._spawn(self._listen, "listener")
        else:
            log.warning(
                "DATABASE_URL goes through a pooler in transaction mode, which cannot carry the "
                "LISTEN that tells the worker a job was queued, and DATABASE_LISTEN_URL is not "
                "set: the threads find queued jobs by polling, so a question waits up to %g "
                "seconds before its answer starts. Set DATABASE_LISTEN_URL to the database "
                "itself, or to the pooler's port in session mode.",
                POLL_SECONDS,
            )
        self._spawn(self._heartbeat, "heartbeat")
        self._spawn(self._housekeep, "housekeeper")
        if self._mailer is not None and settings.alert_email:
            self._spawn(self._alert, "alerter")
        elif settings.environment == "production":
            log.warning(
                "ALERT_EMAIL is not set: the jobs the worker gives up on are reported in the "
                "log only, and listed by `python -m app.maintenance failed-jobs`, and nobody is "
                "warned when a day's spending nears a limit or when the worker's watch sees "
                "something go wrong"
            )

    def stopped_threads(self) -> list[str]:
        """The names of the threads that ended. Each loops until the worker stops and catches
        what its work raises, so before a stop this is empty unless something went wrong that
        the worker cannot see; and a worker a thread short claims less, or keeps no lease."""
        return [thread.name for thread in self._threads if not thread.is_alive()]

    def lanes(self) -> dict[str, tuple[int, int]]:
        """Each lane's job threads that are alive, and those running a job now, for the
        metrics (app/metrics.py)."""
        with self._held_lock:
            running = list(self._held.values())
        return {
            lane: (
                sum(
                    thread.name.startswith(f"{lane}-") and thread.is_alive()
                    for thread in self._threads
                ),
                running.count(lane),
            )
            for lane in LANES
        }

    def listening(self) -> bool:
        """Whether the worker hears of queued jobs as they are queued, rather than finding them
        by polling: its listener holds its LISTEN (app/jobs.py). It does not where there is
        nowhere to listen, behind a pooler in transaction mode without DATABASE_LISTEN_URL, and
        while a lost connection waits to be made again."""
        return self._listening.is_set()

    def _spawn(self, target: Callable[[], None], name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def stop(self, grace: float = GRACE) -> None:
        """Stop claiming, let the running jobs finish for `grace` seconds, hand back the rest,
        and say that the worker stopped.

        A job handed back is queued again with its lease cleared, so the next worker takes it at
        once. Nothing stops the thread that is still running it: the process exits right after
        this returns, and a thread that gets further finds the job handed back at its next
        commit and stops there (`LeaseLost`), so the next worker's run never meets it.
        """
        self._stop.set()
        self._wake.set()
        deadline = time.monotonic() + grace
        for thread in self._threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        self._hand_back(grace)
        try:
            record_stop(self._id)
        except Exception:
            log.exception(
                "Could not record that worker %s stopped; it counts as running until %s after "
                "its last beat",
                self._id,
                SEEN_WITHIN,
            )

    def _hand_back(self, grace: float) -> None:
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

    def _listen(self) -> None:
        """Wake the threads when a job is queued anywhere, in this process or another: the queue
        announces it with NOTIFY (app/jobs.py). The threads poll meanwhile, so a listener that
        is down costs a question up to POLL_SECONDS, not the answer."""
        while not self._stop.is_set():
            try:
                listen(self._stop, self.notify, LISTEN_SECONDS, self._listening)
            except Exception:
                # The threads poll from now on, and the metrics say so while the retry waits.
                self._listening.clear()
                log.exception(
                    "Listening for queued jobs failed; the threads poll every %s seconds "
                    "meanwhile, and listening starts again in %s",
                    POLL_SECONDS,
                    RETRY_SECONDS,
                )
                self._stop.wait(RETRY_SECONDS)
            else:
                self._listening.clear()

    def _loop(self, kinds: list[str]) -> None:
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
        # From when it was due: a retry's backoff is no wait for a worker.
        waited = max((datetime.now(UTC) - job.run_at).total_seconds(), 0.0)
        JOB_WAIT.labels(job.kind).observe(waited)
        # Every line the run logs, the handler's and the settling's, names the job, what it is
        # for and the trace it was queued under, and the run's spans are timed for its last.
        with (
            logging_about(
                trace_id=trace_of(job.payload),
                job_id=job.id,
                job_kind=job.kind,
                org_id=job.org_id,
                project_id=job.project_id,
                document_id=job.payload.get("document_id"),
                message_id=job.payload.get("message_id"),
            ),
            timed(waited) as timeline,
        ):
            outcome = self._settle(job)
            timing = timeline.timing(outcome)
            log.info("%s", describe(job.id, job.kind, timing), extra={"timing": timing})
        JOB_RUNS.labels(job.kind, outcome).inc()
        JOB_SECONDS.labels(job.kind).observe(timing["seconds"])

    def _settle(self, job: Job) -> str:
        """Run the job and record how it ended; returns that, as the metrics call it
        (`JOB_RUNS` in app/metrics.py)."""
        handler = HANDLERS[job.kind]
        try:
            with self._holding(job):
                if job.attempts > job.max_attempts:
                    # Every attempt so far ended with the lease running out: the process died
                    # each time, so running it once more would most likely kill this one.
                    log.error(
                        "Job %s (%s) was lost %s times and is given up",
                        job.id,
                        job.kind,
                        job.max_attempts,
                    )
                    outcome = "abandoned"
                    handler.abandon(job, self._services)
                    self._finish(job, "failed", f"Given up after {job.max_attempts} lost attempts")
                else:
                    try:
                        given_up = handler.run(job, self._services)
                    except LeaseLost:
                        # The job is the other worker's to finish, or queued for the next one.
                        outcome = "lost"
                        log.warning(
                            "Job %s (%s) was taken over or handed back while this worker was "
                            "running it; the run stopped at its next commit",
                            job.id,
                            job.kind,
                        )
                    except Exception as error:
                        if connection_lost(error):
                            # As for a process that died: the lease runs out, and the next claim
                            # runs the job again, given up after max_attempts of these.
                            outcome = "lost"
                            log.warning(
                                "Job %s (%s) stopped: the database could not be reached (%s); the "
                                "next claim runs it again once its lease has run out",
                                job.id,
                                job.kind,
                                summary(error.orig or error).splitlines()[0],
                            )
                            return outcome
                        outcome = "crashed"
                        log.exception("Job %s (%s) failed", job.id, job.kind)
                        # The operator reads it in the digest: the kind and where, as in the log.
                        self._finish(job, "failed", summary(error))
                    else:
                        outcome = "failed" if given_up else "done"
                        self._finish(job, "failed" if given_up else "done", given_up)
        except Exception:
            # The database went away while the job was being settled. Its lease runs out and
            # another claim runs it again, which every handler must be able to take.
            log.exception("Job %s (%s) could not be settled", job.id, job.kind)
            outcome = "unsettled"
        if handler.after is not None and outcome in ENDED:
            try:
                handler.after(job, self._services)
            except Exception:
                log.exception("What follows job %s (%s) failed", job.id, job.kind)
        return outcome

    def _finish(self, job: Job, status: str, error: str | None = None) -> None:
        if not finish(job, self._id, status, error):
            log.warning(
                "Job %s (%s) was taken over, handed back or removed before this worker finished it",
                job.id,
                job.kind,
            )

    @contextmanager
    def _holding(self, job: Job) -> Iterator[None]:
        with self._held_lock:
            self._held[job.id] = lane_of(job.kind)
        try:
            yield
        finally:
            with self._held_lock:
                self._held.pop(job.id, None)

    def _heartbeat(self) -> None:
        # At once, so that a worker counts as running from its start.
        self._beat()
        while not self._stop.wait(HEARTBEAT_SECONDS):
            self._beat()
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
                    "still running them; their runs stop at their next commit",
                    sorted(lost),
                )

    def _beat(self) -> None:
        """Say that this worker runs, whether it holds jobs or not (app/jobs.py)."""
        # A beat after the stop's record would undo it.
        if self._stop.is_set():
            return
        try:
            beat(self._id, VERSION, self._started)
        except Exception:
            # A database that is unreachable now may be back by the next round; meanwhile the
            # worker counts as running for SEEN_WITHIN after its last beat.
            log.exception("Could not record that worker %s is running", self._id)

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

    def _alert(self) -> None:
        reports: dict[str, Callable[[Mailer, str], int]] = {
            "failed jobs": alert_failed_jobs,
            "spending": alert_spending,
            "quarantines": alert_quarantines,
            "watch": partial(watch, self._services),
        }
        while not self._stop.wait(ALERT_INTERVAL):
            for name, report in reports.items():
                try:
                    report(self._mailer, get_settings().alert_email)
                except Exception:
                    # A relay or a database that is unreachable now may be back by the next
                    # round, and the failures and warnings stay unreported until then.
                    log.exception(
                        "Reporting to ALERT_EMAIL failed (%s); it runs again in %s minutes",
                        name,
                        ALERT_INTERVAL / 60,
                    )


def serve(
    services: Services,
    settings: Settings,
    stopping: threading.Event,
    grace: float = GRACE,
    alive: Path | None = None,
) -> bool:
    """Run the worker until `stopping` is set, or until one of its threads ends, then stop it
    within `grace` seconds. Returns False when a thread ended, for the process to exit with
    an error.

    Every ALIVE_SECONDS meanwhile, while every thread runs, it touches `alive`, which it
    removes at the end: a health check calls the worker stuck when the file grows old.

    With METRICS_ADDRESS it serves its metrics there, and the database's figures
    (app/metrics.py); an address it cannot listen on raises `MetricsRefused` before any thread
    starts.
    """
    worker = Worker(
        services,
        settings.worker_concurrency,
        services.mailer,
        answer_concurrency=settings.answer_concurrency,
    )
    exporter = export_metrics(settings, worker)
    worker.start()
    log.info(
        "Worker %s is running, %s documents and %s answers at a time",
        worker.id,
        settings.worker_concurrency,
        settings.answer_concurrency,
    )
    whole = True
    while True:
        if stopped := worker.stopped_threads():
            log.error(
                "The worker's %s %s stopped unexpectedly; the worker stops, to be started again",
                ", ".join(stopped),
                "thread" if len(stopped) == 1 else "threads",
            )
            whole = False
            break
        if alive is not None:
            _touch(alive)
        if stopping.wait(ALIVE_SECONDS):
            break
    log.info("Stopping; the running jobs have %s seconds to finish", grace)
    worker.stop(grace)
    if exporter is not None:
        exporter.close()
    if alive is not None:
        alive.unlink(missing_ok=True)
    log.info("Worker %s stopped", worker.id)
    return whole


def _touch(alive: Path) -> None:
    try:
        alive.touch()
    except OSError as error:
        # The command checked the file at start, so this is a filesystem that filled up or
        # went away since; the health check reports the worker stuck, which sends the
        # operator to this line.
        log.warning("Could not touch the --alive-file %s: %s", alive, error.strerror)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.worker",
        description="Run the background worker, which indexes uploaded documents, answers the "
        "questions asked in the chats and keeps the stored data tidy. Run it in the backend "
        "directory with `uv run`; it reads the same .env "
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
    parser.add_argument(
        "--alive-file",
        type=Path,
        metavar="PATH",
        help=f"a file to touch every {ALIVE_SECONDS:g} seconds while every thread of the worker "
        "runs, and to remove at stop, for a health check that reads its age",
    )
    args = parser.parse_args(argv)
    if args.alive_file is not None:
        # Before anything starts, so that a path the process cannot write in is a refusal to
        # start, not a health check that fails while the worker runs. The file is there only
        # while the worker runs, from the first round of `serve`, so a stale one from a process
        # that was killed goes too.
        try:
            args.alive_file.touch()
            args.alive_file.unlink()
        except OSError as error:
            sys.exit(f"The --alive-file {args.alive_file} cannot be written: {error.strerror}.")
    settings = get_settings()
    configure_logging(settings.log_format)
    start_tracking(settings)
    # The schema check sets up a migration context, which announces itself; nothing to act on.
    logging.getLogger("alembic.runtime.migration").setLevel(logging.WARNING)

    try:
        services = prepare(settings)
    except (RuntimeError, StorageError, ScanError) as error:
        sys.exit(str(error))
    stopping = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopping.set())
    try:
        whole = serve(services, settings, stopping, args.grace, args.alive_file)
    except MetricsRefused as error:
        sys.exit(str(error))
    if not whole:
        sys.exit(1)


if __name__ == "__main__":
    main()
