"""The job queue: a `jobs` table in Postgres that workers claim from with `FOR UPDATE SKIP LOCKED`.

Anything the app does in the background is a job: a row that names its kind and what it is about,
queued in the same transaction as the change that needs it, so a document and the job that
indexes it exist together or not at all. The claim takes the oldest queued job of the kinds the
worker can run, in priority order, and in the same statement marks it running, counts the
attempt and leases it to the worker for LEASE. The worker extends the lease while it works
(`extend_leases`), so a job whose worker died is claimed again by the next worker once the lease
runs out, and nothing at start needs to guess which jobs were abandoned. A job that was lost that
way `max_attempts` times is given up rather than run forever.

Within a priority the workers are shared between organisations rather than given to whoever
queued first: the claim counts each organisation's jobs that are running or finished within
SHARE_WINDOW and takes the job of the organisation that has had the least. One organisation's
upload of five hundred documents then costs another's single upload one slot, not five hundred;
with only one organisation queuing, every worker is its. Picking by `row_number()` within each
organisation would not do: the busy organisation's next job is always older than the newcomer's,
so it would win every tie.

The row is the claim and the retry bookkeeping. What the job is about keeps its own user-visible
state: a document's `status` and `stages` are what the UI shows, and the index job writes them
through the pipeline as before.

An index job runs whatever stages its document has not finished, so two queued for the same
document would do the same work twice, or at once on two workers: queuing one replaces any
still queued for that document (`queue_index`), keeping the better priority of the two. That is
how "Index again" overtakes a retry waiting for its backoff, and how a retry never doubles a
job the user queued meanwhile.

A lease only keeps two workers off one document if the worker that lost it stops. A heartbeat
that could not reach the database for LEASE, or a stage that ran that long while the heartbeat
thread was stuck, lets the next claim take the job over while the first worker is still running
it, and both would then write the same document. So the pipeline renews the lease (`renew`) in
the transaction of every commit it makes, and a renewal that finds the job no longer this
worker's ends the run with `LeaseLost` before anything of that stage is written. The same test
stops a run whose job a stopping worker handed back to the queue.

A job queued while a request or another job runs carries that work's trace in its payload
(`trace_id`, app/tracing.py), so that the worker logs it under the same trace.

Nothing else would know whether anyone takes the queue's work: a worker serves nothing to ask,
and a deployment that never started one leaves every upload queued and every question
unanswered. So each worker writes a row of `workers` at its start and with every heartbeat
(`beat`), and marks it at its stop (`record_stop`); `work_running` is what the members' status
line reads, `workers_seen` what the operator reads, both by the database's clock.

Queuing a job also sends a NOTIFY on CHANNEL, delivered when the transaction commits, and the
worker listens for it (`listen`) on a connection of its own: a question is picked up the moment
the API commits it rather than at the worker's next poll, which is what makes answers as jobs
feel like answers in the request.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from sqlalchemy import Connection, DateTime, bindparam, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.entitlements import share_weights
from app.logs import current_context
from app.models import JOB_KINDS, Document, Job

log = logging.getLogger(__name__)

INDEX_DOCUMENT = "index_document"
ANSWER_QUESTION = "answer_question"
RENDER_PAGES = "render_pages"
SEND_EMAIL = "send_email"
APPLY_LANGUAGE = "apply_language"
EXPORT_PROJECT = "export_project"
RUN_WORKFLOW = "run_workflow"
RUN_AUTOMATION = "run_automation"
WRITE_DRAFT = "write_draft"
RECEIVE_MAIL = "receive_mail"
SYNC_CONNECTOR = "sync_connector"
DELIVER_WEBHOOK = "deliver_webhook"
# Lower runs first. An answer is work a user is watching for; an upload is work a user is
# waiting for; a reprocess of a whole project, or of everything after a model change, is bulk
# work that should not hold either up.
LIVE = -10
BULK = 10
# The channel a queued job is announced on, for the workers that listen.
CHANNEL = "jobs"
# How far back the claim remembers which organisation had the workers: a job finished longer
# ago than this no longer counts towards its organisation's share. A few minutes keeps one
# worker thread alternating between two organisations' backlogs (the shares it compares are
# otherwise both zero whenever it is free) without holding what an organisation did an hour
# ago against it.
SHARE_WINDOW = timedelta(minutes=5)
# How long a claim lasts unless the worker extends it. Long enough that a slow database or a
# busy worker does not lose a job it is still running; short enough that a document stuck in
# "processing" after a crash is picked up again within minutes.
LEASE = timedelta(minutes=5)
# How many claims a job gets before it is given up: a job whose worker dies every time (a parser
# that takes the process down) must not take every worker down in turn.
MAX_ATTEMPTS = 3
# Finished jobs are kept this long for the operator (the failed ones carry `last_error`), then
# pruned by housekeeping (app/maintenance.py).
HISTORY = timedelta(days=30)
# How long after a worker was last seen it still counts as running: three of its heartbeats
# (HEARTBEAT_SECONDS in app/worker.py), so that a round it missed does not read as no worker.
# For the members' status line a worker that stopped itself counts too until then, so that a
# release, which stops one worker and starts the next a few seconds later, never reads as one.
SEEN_WITHIN = timedelta(minutes=3)
# How long a worker's row is kept after it was last seen, for the operator to read, before
# housekeeping (app/maintenance.py) removes it.
WORKERS_KEPT = timedelta(days=1)
# How many workers the operator is shown, the latest seen first: one restarted in a loop under a
# new host or pid leaves a row each time.
WORKERS_SHOWN = 20
# How a job stands for the operator: due (its run_at has come), waiting (a retry's backoff, or
# the day's spending limit), running, or failed and kept for HISTORY (app/deadletters.py).
JOB_STATES = ("due", "waiting", "running", "failed")

# The queue by kind and state, with how long the oldest of each has been due: the metrics'
# figures (app/metrics.py) and the `jobs` command's (app/maintenance.py). `done` is left out:
# it only grows until the housekeeping prunes it.
QUEUE_BY_STATE = text(
    "SELECT kind, CASE WHEN status = 'queued' AND run_at <= now() THEN 'due' "
    "WHEN status = 'queued' THEN 'waiting' ELSE status END AS state, count(*) AS jobs, "
    "extract(epoch FROM now() - min(run_at)) AS oldest "
    "FROM jobs WHERE status IN ('queued', 'running', 'failed') GROUP BY 1, 2"
)

# The due jobs are ordered by priority, then by how much of the workers their organisation has
# had lately (`share`: its jobs of these kinds running on a live lease or finished within
# SHARE_WINDOW), divided by the weight its plan gives it (`weights`, by plan name, from
# app/entitlements.py) or its agreed terms do (`overrides`), so that a paying organisation's
# jobs count for less and its backlog gets the larger share; then by age. A job of no
# organisation (mail about an account, app/postbox.py) has had no share. The due test is
# repeated on `j`, where the lock is taken, so that a row another worker claimed and
# committed since this statement's snapshot is checked again and skipped, not taken twice.
_CLAIM = text(
    """
    WITH share AS (
        SELECT org_id, count(*) AS served
        FROM jobs
        WHERE kind = ANY(:kinds)
          AND ((status = 'running' AND lease_until >= now())
               OR finished_at > now() - :window)
        GROUP BY org_id
    )
    UPDATE jobs
    SET status = 'running', attempts = attempts + 1, worker_id = :worker,
        lease_until = now() + :lease
    WHERE id = (
        SELECT j.id
        FROM jobs AS j
             LEFT JOIN organizations AS o ON o.id = j.org_id
             LEFT JOIN share ON share.org_id = j.org_id
        WHERE j.kind = ANY(:kinds)
          AND ((j.status = 'queued' AND j.run_at <= now())
               OR (j.status = 'running' AND j.lease_until < now()))
        ORDER BY j.priority,
                 coalesce(share.served, 0)
                     / coalesce(CAST(o.overrides ->> 'priority' AS numeric),
                                CAST(CAST(:weights AS jsonb) ->> o.plan AS numeric), 1),
                 j.run_at, j.id
        FOR UPDATE OF j SKIP LOCKED
        LIMIT 1
    )
    RETURNING *
    """
).bindparams(bindparam("weights", type_=JSONB))
_EXTEND = text(
    """
    UPDATE jobs SET lease_until = now() + :lease
    WHERE id = ANY(:ids) AND worker_id = :worker AND status = 'running'
    RETURNING id
    """
)
_FINISH = text(
    """
    UPDATE jobs
    SET status = :status, last_error = :error, finished_at = now(), lease_until = NULL
    WHERE id = :id AND worker_id = :worker AND status = 'running'
    """
)
_RELEASE = text(
    """
    UPDATE jobs
    SET status = 'queued', lease_until = NULL, worker_id = NULL,
        attempts = greatest(attempts - 1, 0)
    WHERE id = ANY(:ids) AND worker_id = :worker AND status = 'running'
    RETURNING id
    """
)
# The same test as `_EXTEND`, on one job, in the caller's transaction: a job that is running
# as this worker's is renewed, whatever its lease says, since a lease that ran out before
# anyone claimed the job is still this worker's.
_RENEW = text(
    """
    UPDATE jobs SET lease_until = now() + :lease
    WHERE id = :id AND worker_id = :worker AND status = 'running'
    """
)
_PRUNE = text("DELETE FROM jobs WHERE status IN ('done', 'failed') AND finished_at < now() - :age")
# A worker's start and every heartbeat; the same host and pid again, as a container restarted
# in place has, is a new start of the same row.
_BEAT = text(
    """
    INSERT INTO workers (id, version, started_at, last_seen_at)
    VALUES (:id, :version, :started, now())
    ON CONFLICT (id) DO UPDATE SET version = excluded.version, started_at = excluded.started_at,
        last_seen_at = now(), stopped_at = NULL
    """
)
_STOPPED = text("UPDATE workers SET last_seen_at = now(), stopped_at = now() WHERE id = :id")
_LAST_SEEN = text(
    "SELECT max(last_seen_at), coalesce(max(last_seen_at) > now() - :within, false) FROM workers"
)
_SEEN = text(
    """
    SELECT id, version, started_at, last_seen_at, stopped_at,
        stopped_at IS NULL AND last_seen_at > now() - :within AS running
    FROM workers WHERE last_seen_at > now() - :kept ORDER BY last_seen_at DESC, id LIMIT :limit
    """
)
_PRUNE_WORKERS = text("DELETE FROM workers WHERE last_seen_at < now() - :age")
_NOTIFY = text(f"SELECT pg_notify('{CHANNEL}', '')")
# A queued job for the same document is replaced, and the new one takes the better priority of
# the two: an upload's job stays ahead of bulk work when a project reprocess overtakes it.
_QUEUE_INDEX = text(
    """
    WITH superseded AS (
        DELETE FROM jobs
        WHERE kind = 'index_document' AND status = 'queued'
          AND (payload->>'document_id')::bigint = ANY(:ids)
        RETURNING (payload->>'document_id')::bigint AS document_id, priority
    )
    INSERT INTO jobs (org_id, project_id, kind, payload, priority, max_attempts, run_at)
    SELECT p.org_id, d.project_id, 'index_document',
           jsonb_build_object('document_id', d.id) || :extra,
           least(:priority, (SELECT min(s.priority) FROM superseded AS s
                             WHERE s.document_id = d.id)),
           :max_attempts, coalesce(:run_at, now())
    FROM documents AS d JOIN projects AS p ON p.id = d.project_id
    WHERE d.id = ANY(:ids)
    RETURNING *
    """
).bindparams(bindparam("extra", type_=JSONB), bindparam("run_at", type_=DateTime(timezone=True)))


@dataclass(frozen=True)
class Lease:
    """A worker's hold on a job, which the work done under it renews before each commit."""

    job_id: int
    worker_id: str


@dataclass(frozen=True)
class Work:
    """Whether a worker takes the queue's work, for the members' status line: one was seen
    within SEEN_WITHIN, a stopped one included; and when the latest was, None before any."""

    running: bool
    last_seen_at: datetime | None


@dataclass(frozen=True)
class SeenWorker:
    """A worker as the operator reads it: running while it was seen within SEEN_WITHIN and has
    not stopped; one neither running nor stopped died, or lost the database."""

    id: str
    version: str
    started_at: datetime
    last_seen_at: datetime
    stopped_at: datetime | None
    running: bool


class LeaseLost(Exception):
    """The job is no longer this worker's: another worker took it over after its lease ran
    out, or a stopping worker handed it back to the queue. The run that raised this wrote
    nothing since its last commit, and the job is the next worker's to finish."""

    def __init__(self, lease: Lease):
        super().__init__(
            f"Job {lease.job_id} is no longer worker {lease.worker_id}'s: it was taken over by "
            "another worker or handed back to the queue, and that run of it stopped"
        )
        self.lease = lease


def enqueue(
    session: Session,
    kind: str,
    *,
    org_id: int | None,
    project_id: int | None = None,
    payload: dict | None = None,
    priority: int = 0,
    run_at: datetime | None = None,
) -> Job:
    """Add a job to the caller's session; it is queued when the caller commits, under the
    current trace."""
    if kind not in JOB_KINDS:
        raise ValueError(f"{kind!r} is not a job kind; add it to JOB_KINDS in app/models.py")
    job = Job(
        org_id=org_id,
        project_id=project_id,
        kind=kind,
        payload={**_traced(), **(payload or {})},
        priority=priority,
        max_attempts=MAX_ATTEMPTS,
    )
    if run_at is not None:
        job.run_at = run_at
    session.add(job)
    session.execute(_NOTIFY)
    return job


def queue_index(
    session: Session,
    document: Document,
    priority: int = 0,
    *,
    run_at: datetime | None = None,
    retry: int = 0,
) -> Job:
    """Queue a document for the pipeline; its stages say where the run starts.

    `run_at` holds the job back until then, and `retry` counts the automatic retries so far
    for the job that decides on the next (app/worker.py); both are for the retry after a
    transient failure. The job replaces any still queued for the document (the module's
    docstring says why), and is queued when the caller commits, with the document's changes.
    """
    job = session.scalars(
        select(Job).from_statement(_QUEUE_INDEX),
        _queue_params([document.id], priority, run_at, retry),
    ).one()
    session.execute(_NOTIFY)
    return job


def queue_index_by_id(connection: Connection, document_ids: list[int], priority: int = 0) -> int:
    """Queue these documents for the pipeline from SQL, where no session holds the rows (the
    maintenance that queues every document after a model change). Returns how many."""
    if not document_ids:
        return 0
    queued = connection.execute(_QUEUE_INDEX, _queue_params(document_ids, priority)).rowcount
    connection.execute(_NOTIFY)
    return queued


def _queue_params(
    document_ids: list[int], priority: int, run_at: datetime | None = None, retry: int = 0
) -> dict:
    return {
        "ids": document_ids,
        "priority": priority,
        "extra": {**_traced(), **({"retry": retry} if retry else {})},
        "max_attempts": MAX_ATTEMPTS,
        "run_at": run_at,
    }


def _traced() -> dict[str, str]:
    """The payload's part that names the trace the job is queued under, if any."""
    trace = current_context().get("trace_id")
    return {"trace_id": trace} if isinstance(trace, str) else {}


def claim(worker_id: str, kinds: list[str]) -> Job | None:
    """Take the next job of these kinds for this worker, or None when there is none to take.

    The job comes back running and leased; the caller runs it and then calls `finish`.
    """
    with SessionLocal() as session:
        job = session.scalars(
            select(Job).from_statement(_CLAIM), claim_params(worker_id, kinds)
        ).first()
        session.commit()
        return job


def claim_params(worker_id: str, kinds: list[str]) -> dict:
    """The bound values of `_CLAIM`, for a test that runs the statement on its own connection."""
    return {
        "worker": worker_id,
        "lease": LEASE,
        "kinds": kinds,
        "window": SHARE_WINDOW,
        "weights": share_weights(),
    }


def extend_leases(job_ids: list[int], worker_id: str) -> set[int]:
    """Give the jobs this worker holds another LEASE from now.

    Returns the ids that were still this worker's: a job missing from the result ran past its
    lease and was claimed by another worker, or was removed with its project.
    """
    if not job_ids:
        return set()
    with SessionLocal() as session:
        kept = set(session.scalars(_EXTEND, {"ids": job_ids, "worker": worker_id, "lease": LEASE}))
        session.commit()
        return kept


def renew(session: Session, lease: Lease) -> bool:
    """Give the job another LEASE from now, in the caller's transaction and without committing.

    False when the job is no longer this worker's, in which case the caller must not commit
    what it did under the lease. Run just before each commit of work done under a lease, it
    makes the commit the lease test: the two cannot pass each other, so a worker that was
    outrun writes nothing more.
    """
    renewed = session.execute(
        _RENEW, {"id": lease.job_id, "worker": lease.worker_id, "lease": LEASE}
    ).rowcount
    return renewed == 1


def finish(job: Job, worker_id: str, status: str, error: str | None = None) -> bool:
    """Record how the job ended. False when it was no longer this worker's to finish."""
    with SessionLocal() as session:
        settled = session.execute(
            _FINISH, {"id": job.id, "worker": worker_id, "status": status, "error": error}
        ).rowcount
        session.commit()
    return settled == 1


def release(job_ids: list[int], worker_id: str) -> set[int]:
    """Hand the jobs this worker holds back to the queue, as a worker that is stopping does.

    The next claim takes them at once rather than when their lease would have run out. The
    attempt is not counted: it was cut short by the operator, not lost to a process that died,
    and a job must not be given up for having been restarted three times mid-run. Returns the
    ids that were still this worker's.
    """
    if not job_ids:
        return set()
    with SessionLocal() as session:
        released = set(session.scalars(_RELEASE, {"ids": job_ids, "worker": worker_id}))
        session.commit()
        return released


def prune_finished(connection: Connection, age: timedelta = HISTORY) -> int:
    """Remove the jobs that finished longer ago than `age`; returns how many."""
    return connection.execute(_PRUNE, {"age": age}).rowcount


def beat(worker_id: str, version: str, started: datetime) -> None:
    """Record that the worker runs, as its heartbeat does at its start and every round."""
    with SessionLocal() as session:
        session.execute(_BEAT, {"id": worker_id, "version": version, "started": started})
        session.commit()


def record_stop(worker_id: str) -> None:
    """Record that the worker stopped itself, as a stopping worker does once it handed its jobs
    back."""
    with SessionLocal() as session:
        session.execute(_STOPPED, {"id": worker_id})
        session.commit()


def work_running(session: Session) -> Work:
    last_seen, running = session.execute(_LAST_SEEN, {"within": SEEN_WITHIN}).one()
    return Work(running=running, last_seen_at=last_seen)


def workers_seen(session: Session, limit: int = WORKERS_SHOWN) -> list[SeenWorker]:
    """The workers seen within WORKERS_KEPT, the latest seen first."""
    rows = session.execute(_SEEN, {"within": SEEN_WITHIN, "kept": WORKERS_KEPT, "limit": limit})
    return [SeenWorker(*row) for row in rows]


def prune_workers(connection: Connection, age: timedelta = WORKERS_KEPT) -> int:
    """Forget the workers last seen longer ago than `age`; returns how many."""
    return connection.execute(_PRUNE_WORKERS, {"age": age}).rowcount


def listen(
    stop: threading.Event,
    woken: Callable[[], None],
    timeout: float,
    listening: threading.Event | None = None,
) -> None:
    """Call `woken` each time a job is queued, until `stop` is set.

    The connection is the process's own, outside the pool: a pooled one would be held for the
    life of the process and counted against the pool. It goes to DATABASE_LISTEN_URL when that
    is set (`Settings.listen_url`), since a pooler in transaction mode cannot carry a LISTEN;
    the caller does not listen when there is nowhere to. It is checked for a notification every
    `timeout` seconds without a query, which is how often a stop is noticed. A connection that
    fails ends the call with the error; the caller decides whether to listen again, and clears
    `listening`, which is set once the LISTEN is in place, for the metrics to say so.
    """
    address = get_settings().listen_url
    if address is None:
        raise RuntimeError("There is no database address to listen at; set DATABASE_LISTEN_URL.")
    url = make_url(address).set(drivername="postgresql")
    with psycopg.connect(url.render_as_string(hide_password=False), autocommit=True) as conn:
        conn.execute(f"LISTEN {CHANNEL}")
        if listening is not None:
            listening.set()
        while not stop.is_set():
            for _ in conn.notifies(timeout=timeout):
                woken()
