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
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import Connection, DateTime, bindparam, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import JOB_KINDS, Document, Job

log = logging.getLogger(__name__)

INDEX_DOCUMENT = "index_document"
# Lower runs first. An upload is work a user is waiting for; a reprocess of a whole project, or
# of everything after a model change, is bulk work that should not hold an upload up.
BULK = 10
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

# The due jobs are ordered by priority, then by how much of the workers their organisation has
# had lately (`share`: its jobs of these kinds running on a live lease or finished within
# SHARE_WINDOW), then by age. A plan's priority (an entitlement) belongs in `share`: a paid
# organisation's jobs could count for less, giving it the larger share. The due test is
# repeated on `j`, where the lock is taken, so that a row another worker claimed and committed
# since this statement's snapshot is checked again and skipped, not taken twice.
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
        FROM jobs AS j LEFT JOIN share ON share.org_id = j.org_id
        WHERE j.kind = ANY(:kinds)
          AND ((j.status = 'queued' AND j.run_at <= now())
               OR (j.status = 'running' AND j.lease_until < now()))
        ORDER BY j.priority, coalesce(share.served, 0), j.run_at, j.id
        FOR UPDATE OF j SKIP LOCKED
        LIMIT 1
    )
    RETURNING *
    """
)
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
_PRUNE = text("DELETE FROM jobs WHERE status IN ('done', 'failed') AND finished_at < now() - :age")
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


def enqueue(
    session: Session,
    kind: str,
    *,
    org_id: int,
    project_id: int | None = None,
    payload: dict | None = None,
    priority: int = 0,
    run_at: datetime | None = None,
) -> Job:
    """Add a job to the caller's session; it is queued when the caller commits."""
    if kind not in JOB_KINDS:
        raise ValueError(f"{kind!r} is not a job kind; add it to JOB_KINDS in app/models.py")
    job = Job(
        org_id=org_id,
        project_id=project_id,
        kind=kind,
        payload=payload or {},
        priority=priority,
        max_attempts=MAX_ATTEMPTS,
    )
    if run_at is not None:
        job.run_at = run_at
    session.add(job)
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
    return session.scalars(
        select(Job).from_statement(_QUEUE_INDEX),
        _queue_params([document.id], priority, run_at, retry),
    ).one()


def queue_index_by_id(connection: Connection, document_ids: list[int], priority: int = 0) -> int:
    """Queue these documents for the pipeline from SQL, where no session holds the rows (the
    maintenance that queues every document after a model change). Returns how many."""
    if not document_ids:
        return 0
    return connection.execute(_QUEUE_INDEX, _queue_params(document_ids, priority)).rowcount


def _queue_params(
    document_ids: list[int], priority: int, run_at: datetime | None = None, retry: int = 0
) -> dict:
    return {
        "ids": document_ids,
        "priority": priority,
        "extra": {"retry": retry} if retry else {},
        "max_attempts": MAX_ATTEMPTS,
        "run_at": run_at,
    }


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
    return {"worker": worker_id, "lease": LEASE, "kinds": kinds, "window": SHARE_WINDOW}


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
