"""The job queue: a `jobs` table in Postgres that workers claim from with `FOR UPDATE SKIP LOCKED`.

Anything the app does in the background is a job: a row that names its kind and what it is about,
queued in the same transaction as the change that needs it, so a document and the job that
indexes it exist together or not at all. The claim takes the oldest queued job of the kinds the
worker can run, in priority order, and in the same statement marks it running, counts the
attempt and leases it to the worker for LEASE. The worker extends the lease while it works
(`extend_leases`), so a job whose worker died is claimed again by the next worker once the lease
runs out, and nothing at start needs to guess which jobs were abandoned. A job that was lost that
way `max_attempts` times is given up rather than run forever.

The row is the claim and the retry bookkeeping. What the job is about keeps its own user-visible
state: a document's `status` and `stages` are what the UI shows, and the index job writes them
through the pipeline as before.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import Connection, select, text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import JOB_KINDS, Document, Job

log = logging.getLogger(__name__)

INDEX_DOCUMENT = "index_document"
# Lower runs first. An upload is work a user is waiting for; a reprocess of a whole project, or
# of everything after a model change, is bulk work that should not hold an upload up.
BULK = 10
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

_CLAIM = text(
    """
    UPDATE jobs
    SET status = 'running', attempts = attempts + 1, worker_id = :worker,
        lease_until = now() + :lease
    WHERE id = (
        SELECT id FROM jobs
        WHERE kind = ANY(:kinds)
          AND ((status = 'queued' AND run_at <= now())
               OR (status = 'running' AND lease_until < now()))
        ORDER BY priority, run_at, id
        FOR UPDATE SKIP LOCKED
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
_PRUNE = text("DELETE FROM jobs WHERE status IN ('done', 'failed') AND finished_at < now() - :age")
_INSERT_INDEX = text(
    """
    INSERT INTO jobs (org_id, project_id, kind, payload, priority)
    SELECT p.org_id, d.project_id, 'index_document', jsonb_build_object('document_id', d.id),
           :priority
    FROM documents AS d JOIN projects AS p ON p.id = d.project_id
    WHERE d.id = ANY(:ids)
    """
)


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


def queue_index(session: Session, document: Document, org_id: int, priority: int = 0) -> Job:
    """Queue a document for the pipeline; its stages say where the run starts."""
    return enqueue(
        session,
        INDEX_DOCUMENT,
        org_id=org_id,
        project_id=document.project_id,
        payload={"document_id": document.id},
        priority=priority,
    )


def queue_index_by_id(connection: Connection, document_ids: list[int], priority: int = 0) -> int:
    """Queue these documents for the pipeline from SQL, where no session holds the rows (the
    maintenance that queues every document after a model change). Returns how many."""
    if not document_ids:
        return 0
    return connection.execute(_INSERT_INDEX, {"ids": document_ids, "priority": priority}).rowcount


def claim(worker_id: str, kinds: list[str]) -> Job | None:
    """Take the next job of these kinds for this worker, or None when there is none to take.

    The job comes back running and leased; the caller runs it and then calls `finish`.
    """
    with SessionLocal() as session:
        job = session.scalars(
            select(Job).from_statement(_CLAIM),
            {"worker": worker_id, "lease": LEASE, "kinds": kinds},
        ).first()
        session.commit()
        return job


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


def prune_finished(connection: Connection, age: timedelta = HISTORY) -> int:
    """Remove the jobs that finished longer ago than `age`; returns how many."""
    return connection.execute(_PRUNE, {"age": age}).rowcount
