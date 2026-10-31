"""A project's question sets: questions its members saved to be asked of its documents on their
own, each run answering every one of them as a chat would and keeping the answers as a report
(app/question_reports.py), told to whom the set names.

A set runs on a schedule, every day or one day a week at an hour of its maker's zone
(`users.time_zone`, UTC while they chose none or once their account is gone), on documents
arriving in the project, or both; a member who may change the project can also run it there and
then. Nothing runs on a clock in the app: a set's next moment is a job's `run_at`, one job
queued for a set at most (`queue_set`, under the set's row), which makes the report when it is
due and queues the next as it ends, so a schedule is a job that keeps queuing itself. A job that
wakes before anything is due, its set changed since it was queued, queues the next for then and
ends; one the set no longer needs finds nothing due and queues none. The housekeeper queues the
job of a set with a schedule or arrivals waiting that has none (`queue_due_sets`), for one lost
with its job.

A document arrives as the upload it came in settles (`settle_upload` in app/notices.py, in its
transaction), ready and out of the trash, as for the automations (app/automations.py): each is
kept for every set that runs on arrivals as a `QuestionSetArrival`, and the set's job is queued
for AUTOMATION_WAIT seconds later, or moved that early. The report is made once the arrivals have
gone quiet, the wait after the last or MOST_HELD after the first, so that forty invoices sent in
four requests make one report, not four.

The questions are a member's own words, asked of the model, so they stay with the set and its
reports, never in a log line, a notice or the audit log, which names a set by its name and counts
its questions.
"""

from datetime import UTC, date, datetime, time, timedelta, tzinfo

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.accounts import zone_of
from app.automations import MAKER_GONE, MOST_HELD
from app.config import get_settings
from app.entitlements import check_question_sets
from app.jobs import LIVE, RUN_QUESTION_SET, enqueue
from app.models import (
    Document,
    Membership,
    Organization,
    QuestionSet,
    QuestionSetArrival,
    User,
)

# The question sets one project may have, and the questions one set may ask.
MAX_QUESTION_SETS = 10
MAX_SET_QUESTIONS = 10
# The characters of one saved question: a question, not a document.
QUESTION_CHARS = 2_000
# Of a weekly schedule, the days by `weekday`, Monday 0, as datetime numbers them.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# The job queued and not yet claimed for a set, which a change or an arrival moves rather than
# adding a second; and moving it, to the earlier of its moment and the one given.
_QUEUED = text(
    "SELECT id FROM jobs WHERE kind = :kind AND status = 'queued' "
    "AND (payload->>'set_id')::bigint = :id"
)
_SOONER = text("UPDATE jobs SET run_at = least(run_at, :at) WHERE id = :id")
# The sets that are on, with a schedule or arrivals waiting, and no job queued or running, whose
# job was lost.
_UNQUEUED = text(
    """
    SELECT s.id FROM question_sets AS s
    WHERE s.enabled
      AND (s.schedule IS NOT NULL
           OR EXISTS (SELECT 1 FROM question_set_arrivals AS a WHERE a.set_id = s.id))
      AND s.id NOT IN (SELECT (j.payload->>'set_id')::bigint FROM jobs AS j
                       WHERE j.kind = :kind AND j.status IN ('queued', 'running'))
    ORDER BY s.id
    """
)


def next_run(
    schedule: str | None, weekday: int | None, hour: int, zone: tzinfo, after: datetime
) -> datetime | None:
    """The first moment past `after` the schedule names, at `hour` o'clock on the day it says in
    the zone, in UTC; None without a schedule. The days are counted in the zone, so a run stays
    at its hour through a change of the clocks; an hour the clocks skip that day is read as the
    offset before the change gives it."""
    if schedule is None:
        return None
    day: date = after.astimezone(zone).date()
    for _ in range(15):
        if schedule == "daily" or day.weekday() == weekday:
            moment = datetime.combine(day, time(hour), tzinfo=zone)
            if moment > after:
                return moment.astimezone(UTC)
        day += timedelta(days=1)
    raise AssertionError("a schedule names a day within two weeks")


def zone_for(session: Session, row: QuestionSet) -> tzinfo:
    """The zone the set's hour is in: its maker's, UTC while they chose none or once their
    account is gone."""
    if row.created_by is None:
        return UTC
    return zone_of(session.scalar(select(User.time_zone).where(User.id == row.created_by)) or "")


def zone_name(session: Session, row: QuestionSet) -> str:
    """The name of the zone the set's hour is in, "UTC" for UTC, for its page."""
    zone = zone_for(session, row)
    return getattr(zone, "key", "UTC")


def plan_next(session: Session, row: QuestionSet, after: datetime | None = None) -> None:
    """Set when the schedule runs the set next, past `after` (now), and queue its job for then,
    in the caller's session, which commits; none while it is off or has no schedule."""
    after = after or datetime.now(UTC)
    row.next_run_at = (
        next_run(row.schedule, row.weekday, row.hour, zone_for(session, row), after)
        if row.enabled
        else None
    )
    if row.next_run_at is not None:
        queue_set(session, row, row.next_run_at)


def queue_set(session: Session, row: QuestionSet, run_at: datetime) -> None:
    """Have the set's job wake at `run_at`, or sooner if it is queued for sooner: one queued a
    set at most, which is moved rather than added to, in the caller's session."""
    # A job added in this session is found too, which a statement of text alone does not flush.
    session.flush()
    queued = session.scalar(_QUEUED, {"kind": RUN_QUESTION_SET, "id": row.id})
    if queued is not None:
        session.execute(_SOONER, {"at": run_at, "id": queued})
        return
    enqueue(
        session,
        RUN_QUESTION_SET,
        org_id=row.org_id,
        project_id=row.project_id,
        payload={"set_id": row.id},
        priority=LIVE,
        run_at=run_at,
    )


def queue_due_sets(session: Session) -> int:
    """Queue the job of each set that is on, with a schedule or arrivals waiting, and has none,
    whose job was lost; returns how many, committed. The housekeeper's."""
    queued = 0
    for set_id in session.scalars(_UNQUEUED, {"kind": RUN_QUESTION_SET}).all():
        row = session.scalar(select(QuestionSet).where(QuestionSet.id == set_id).with_for_update())
        if row is None:
            continue
        if row.schedule is not None and row.next_run_at is None:
            plan_next(session, row)
        # A moment that passed while it had no job runs it at once.
        for moment in (row.next_run_at, arrivals_due_at(session, row.id)):
            if moment is not None:
                queue_set(session, row, moment)
        session.commit()
        queued += 1
    return queued


def problem_of(session: Session, row: QuestionSet, organization: Organization) -> str | None:
    """Why the set would run nothing, or tell nobody, as its organisation stands now; None when
    it would run as it says."""
    if refusal := check_question_sets(organization):
        return refusal.sentence
    if row.audience == "maker" and (
        row.created_by is None
        or session.scalar(
            select(Membership.user_id).where(
                Membership.org_id == row.org_id, Membership.user_id == row.created_by
            )
        )
        is None
    ):
        return MAKER_GONE
    return None


def arrived(session: Session, upload_id: int, project_id: int) -> int:
    """Keep the upload's documents for each of its project's sets that runs on arrivals, and have
    each one's job wake once they could have gone quiet, in the caller's session, which commits;
    returns for how many sets they were kept. Run as the upload settles, after the automations'
    (app/automations.py), whose rows it locks first."""
    rows = session.scalars(
        select(QuestionSet)
        .where(
            QuestionSet.project_id == project_id,
            QuestionSet.on_arrivals.is_(True),
            QuestionSet.enabled.is_(True),
        )
        .order_by(QuestionSet.id)
    ).all()
    if not rows:
        return 0
    documents = session.scalars(
        select(Document.id).where(
            Document.upload_id == upload_id,
            Document.project_id == project_id,
            Document.status == "ready",
            Document.deleted_at.is_(None),
        )
    ).all()
    if not documents:
        return 0
    for row in rows:
        # Held until the commit: a report being made for it takes these too, or waits for them.
        session.execute(select(QuestionSet.id).where(QuestionSet.id == row.id).with_for_update())
        session.execute(
            insert(QuestionSetArrival)
            .values([{"set_id": row.id, "document_id": document_id} for document_id in documents])
            .on_conflict_do_nothing()
        )
        queue_set(session, row, _wait_from(datetime.now(UTC)))
    return len(rows)


def arrivals_due_at(session: Session, set_id: int) -> datetime | None:
    """When the set's arrivals have gone quiet enough to make its report: the wait after the
    last of them, or MOST_HELD after the first; None while none waits."""
    first, last = session.execute(
        select(
            func.min(QuestionSetArrival.arrived_at), func.max(QuestionSetArrival.arrived_at)
        ).where(QuestionSetArrival.set_id == set_id)
    ).one()
    if first is None:
        return None
    return min(_wait_from(last), first + MOST_HELD)


def take_arrivals(session: Session, set_id: int) -> list[int]:
    """The set's arrivals waiting, taken for its report in the caller's session: the ids of the
    documents, in the order they came."""
    taken = session.execute(
        delete(QuestionSetArrival)
        .where(QuestionSetArrival.set_id == set_id)
        .returning(QuestionSetArrival.document_id, QuestionSetArrival.arrived_at)
    ).all()
    ordered = sorted(taken, key=lambda row: (row.arrived_at, row.document_id))
    return [row.document_id for row in ordered]


def schedule_words(schedule: str | None, weekday: int | None, hour: int) -> str | None:
    """A schedule as the audit log and a notice name it: "daily at 08:00", "on Mondays at
    08:00"; None for none."""
    if schedule is None:
        return None
    at = f"at {hour:02d}:00"
    return f"daily {at}" if schedule == "daily" else f"on {WEEKDAYS[weekday or 0]}s {at}"


def _wait_from(moment: datetime) -> datetime:
    return moment + timedelta(seconds=get_settings().automation_wait)
