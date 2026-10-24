"""The dead-letter queue: the jobs the worker gave up on, how the operator hears of them, and
how their documents go back into the queue.

A job ends `failed` (app/worker.py) when the worker could not complete it: it was lost to a
dying process `max_attempts` times, its handler raised, or a stage kept failing for a passing
reason through every retry. Those are the dead letters. They keep their row for HISTORY
(app/jobs.py) with `last_error` saying why, and the user's side is settled already: the
document shows the failure and offers "Index again". What is left is for the operator: the
digest the worker's alerter thread mails to ALERT_EMAIL, listing the failures nobody has been
told about yet; the `failed-jobs` command (app/maintenance.py) that lists them all; and
`--requeue`, which queues the documents again from the stage they stopped at and empties the
list.
"""

from dataclasses import dataclass, field
from datetime import UTC

from sqlalchemy import ColumnElement, and_, select, text
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.db import SessionLocal
from app.indexing.pipeline import reset_stages
from app.jobs import INDEX_DOCUMENT, RECEIVE_MAIL, RENDER_PAGES, SEND_EMAIL, queue_index
from app.mail import Mailer, Message
from app.models import STAGES, Document, Job, Organization, Project

FAILED_JOBS_COMMAND = "uv run python -m app.maintenance failed-jobs"
# How many failures one digest lists in full. One backlog of bad files is hundreds of them,
# and the mail is the alert, not the listing: the command has the rest.
DIGEST_LIMIT = 50
# The kinds of job whose payload names a document, `{"document_id": 7, …}`.
ABOUT_A_DOCUMENT = (INDEX_DOCUMENT, RENDER_PAGES)

# The failures not yet reported, stamped in the same statement that selects them: two workers
# reporting at once cannot both take a row, since the second one waits on the row lock and
# finds `reported_at` set once the first has committed.
_TAKE = text(
    "UPDATE jobs SET reported_at = now() WHERE status = 'failed' AND reported_at IS NULL "
    "RETURNING id"
)


@dataclass(frozen=True)
class NamedJob:
    """A job with the names of what it is about: its organisation, if it is one's, its project
    and, for an index or a drawing job, its document, or for mail the message's subject. The
    operator's commands print them (`failed-jobs`, `jobs`)."""

    job: Job
    organization: str | None
    project: str | None
    document: str | None

    @property
    def about(self) -> str:
        """Where the job belongs, as "acme / Contracts / invoice.pdf"."""
        return " / ".join(part for part in (self.organization, self.project, self.document) if part)


def failed_jobs(session: Session) -> list[NamedJob]:
    """Every failed job the queue still keeps, oldest first."""
    return named_jobs(session, Job.status == "failed")


def take_unreported(session: Session) -> list[NamedJob]:
    """The failed jobs not reported yet, marked reported as of now in the caller's transaction;
    rolling it back leaves them for the next round."""
    ids = session.scalars(_TAKE).all()
    return named_jobs(session, Job.id.in_(ids)) if ids else []


def named_jobs(session: Session, condition: ColumnElement[bool]) -> list[NamedJob]:
    """The jobs that meet `condition`, with their names, in the order they finished, then by
    id. The document an index or a drawing job is about is named in its payload, and may be gone
    by now."""
    rows = session.execute(
        select(Job, Organization.slug, Project.name, Document.filename)
        .outerjoin(Organization, Organization.id == Job.org_id)
        .outerjoin(Project, Project.id == Job.project_id)
        .outerjoin(
            Document,
            and_(
                Job.kind.in_(ABOUT_A_DOCUMENT),
                Document.id == Job.payload["document_id"].as_integer(),
            ),
        )
        .where(condition)
        .order_by(Job.finished_at, Job.id)
    )
    return [
        NamedJob(job, organization, project, _document_name(job, filename))
        for job, organization, project, filename in rows
    ]


def _document_name(job: Job, filename: str | None) -> str | None:
    if job.kind == SEND_EMAIL:
        # The subject is the app's own sentence; the address is in the encrypted message alone.
        return f"the email {job.payload.get('subject', '')!r}"
    if job.kind == RECEIVE_MAIL:
        # The provider's id for it, by which its dashboard finds it.
        payload = job.payload
        return f"the message {payload.get('email_id', '')} from {payload.get('source', '')}"
    if filename is not None or job.kind not in ABOUT_A_DOCUMENT:
        return filename
    return f"document {job.payload['document_id']} (deleted)"


def describe(failure: NamedJob) -> str:
    """One failure as two lines: when, which job and what it was about; then why."""
    job = failure.job
    when = job.finished_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"{when}  job {job.id} ({job.kind})  {failure.about}\n    {job.last_error}"


def failed_jobs_email(failures: list[NamedJob]) -> tuple[str, str]:
    """The digest: the subject says how many, the body says which and why, up to DIGEST_LIMIT,
    and names the command that lists them all and runs them again."""
    count = _count(len(failures), "job")
    listed = "\n".join(describe(failure) for failure in failures[:DIGEST_LIMIT])
    more = len(failures) - DIGEST_LIMIT
    body = (
        f"The worker gave up on {count}. Each failure shows on its document, where the user "
        "can index it again; the reasons are below.\n\n"
        f"{listed}\n"
        + (f"\nand {more} more.\n" if more > 0 else "")
        + f"\nList them all with `{FAILED_JOBS_COMMAND}` in the backend directory; once the "
        "cause is fixed, add --requeue to run them again.\n"
    )
    return f"Emudoc gave up on {count}", body


def alert_failed_jobs(mailer: Mailer, to: str) -> int:
    """Mail the failed jobs not yet reported to `to`, as one message, and mark them reported.

    The marking is committed only once the message was handed over, so that a relay that is
    down leaves them for the next round. Returns how many were reported. With no address
    nothing is taken: the failures wait, listed by the command, until one is set.
    """
    if not to:
        return 0
    with SessionLocal() as session:
        failures = take_unreported(session)
        if not failures:
            return 0
        subject, body = failed_jobs_email(failures)
        mailer.send(Message(to, subject, body))
        session.commit()
    return len(failures)


@dataclass
class Requeue:
    """What `requeue` did with each failed job, one line each, and how many documents it queued."""

    queued: int = 0
    lines: list[str] = field(default_factory=list)


def requeue(session: Session, failures: list[NamedJob], operator: int | None = None) -> Requeue:
    """Queue each failed index job's document again from the stage it stopped at, as "Index
    again" does, and remove the failed jobs from the list. Each document's audit row names
    `operator`, the user's id, when an operator asked from the operator's area.

    A job whose document was deleted, or has a newer job queued or running already, has
    nothing left to run and is removed too: the newer job carries the document's fate. The
    changes are the caller's to commit.
    """
    outcome = Requeue()
    for failure in failures:
        job = failure.job
        if job.kind != INDEX_DOCUMENT:
            outcome.lines.append(
                f"Left job {job.id} ({job.kind}) alone: only index jobs are run again."
            )
            continue
        document = session.get(Document, job.payload["document_id"])
        if document is None:
            outcome.lines.append(f"Dropped job {job.id}: its document was deleted.")
        elif document.status == "processing":
            outcome.lines.append(f"Dropped job {job.id}: {failure.about} is being indexed now.")
        elif document.status == "queued":
            outcome.lines.append(f"Dropped job {job.id}: {failure.about} is queued already.")
        else:
            stage = next(
                (name for name in STAGES if document.stages.get(name, {}).get("status") != "done"),
                STAGES[0],
            )
            reset_stages(document, stage)
            queue_index(session, document)
            audit_event(
                session,
                "document_requeued",
                org_id=job.org_id,
                project_id=document.project_id,
                target=("document", document.id),
                detail={"filename": document.filename, "stage": stage}
                | ({} if operator is None else {"operator": operator}),
            )
            outcome.queued += 1
            outcome.lines.append(f"Queued {failure.about} again from stage {stage} (job {job.id}).")
        session.delete(job)
    return outcome


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"
