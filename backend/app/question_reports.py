"""Reports of a project's question sets (app/question_sets.py): each run of a set's questions
over the project's documents as they read then, every answer the agent's as a chat's is, kept
together with the passages they cite and what made the set run.

A report is made by the set's job (`run_question_set`) when its schedule or its arrivals are
due, or by a member's press (app/api/question_sets.py), with its questions as they were and
nothing answered, and a `write_question_report` job answers them one after another
(`write_report`). Each question is counted as a question asked as it is asked, held to the
day's spending first, as a member's is; a refusal answers it and every question after it with
the sentence, and the report is kept with what was answered before.
Each answer is kept under the job's lease as it is written, so a run lost to a dying worker
starts again at the question it was in, which it asks again without counting it twice. The
answers' model calls are recorded as `answer` usage of the report, whoever ran it.

The questions are asked of the whole project, as a chat's are; a report made by arrivals tells
the model which documents arrived, by their ids alone (a name could carry words for the model,
as app/scopes.py says of a chat's). Nobody is there to answer the model back, so its task says
to answer in full rather than ask.

Once every answer is kept, the report is told, once (`tell_report`): a member's run to that
member, the schedule's and the arrivals' to whom the set names (its maker, everyone who sees the
project, or nobody), each as they chose for the kind of notice, by counts and the set's name; and
the organisation's webhooks hear of it with its id and what it is (app/webhooks.py), never what
an answer says. A report is kept as long as the organisation keeps a chat (`prune_reports` in
app/retention.py).
"""

import logging
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session

from app.agent.loop import AGENT_PROMPTS, run_agent
from app.answers import Reply
from app.audit import audit_event
from app.db import SessionLocal
from app.flags import QUESTION_SETS, flag_on
from app.indexing.pipeline import Services
from app.jobs import LIVE, WRITE_QUESTION_REPORT, Lease, LeaseLost, enqueue, renew
from app.mail import report_ready_email
from app.models import Document, Project, QuestionReport, QuestionSet
from app.notices import (
    PROJECT_PAGE,
    REPORT_READY,
    app_address,
    audience_of,
    tell,
)
from app.prompts import prompt_version
from app.question_sets import (
    arrivals_due_at,
    plan_next,
    queue_set,
    schedule_words,
    take_arrivals,
)
from app.spending import check_spend
from app.usage import ANSWER, QUESTION_REPORT, ModelUsage, record

log = logging.getLogger(__name__)

PROJECT_GONE = (
    "The project went into the trash before this question was asked. Restore it to run the set "
    "again."
)
REPORT_FAILED = "The report failed unexpectedly. Run the set again in a moment."
# The names of the documents that arrived a report keeps, for its page.
ARRIVALS_NAMED = 3
# The ids of the documents that arrived the model is told, of a report made by many.
ARRIVALS_TOLD = 50
# Where a report's notice leads: the report in its project's library.
REPORT_PAGE = PROJECT_PAGE + "?report={}"
# A report made by a schedule or by arrivals waits behind the questions members are asking and
# watching for (LIVE); one a member ran there and then is one of those.
UNATTENDED = 0

ASKED_ALONE = """\
This question was saved by a member of the project to be asked on its own, and your answer is \
kept in a report they read later, beside the answers to the set's other questions. Nobody is \
there to answer you back: answer in full, as you would in a chat, citing as you would, and where \
the documents leave something open, say so and what would settle it rather than asking."""


@dataclass(frozen=True)
class Asked:
    """A question of a report about to be asked: its place among the report's, its words, the
    project, whose usage its model calls are, and the task said after the system prompt."""

    index: int
    question: str
    project_id: int
    payer: dict[str, int | None]
    task: str


def start_report(
    session: Session,
    project: Project,
    row: QuestionSet,
    trigger: str,
    user_id: int | None = None,
    landed: list[int] | None = None,
) -> QuestionReport:
    """A report of the set's questions as they are now, queued for the worker in the caller's
    session, made by `trigger` (REPORT_TRIGGERS): the member's who ran it, or for what arrived.
    The caller commits."""
    landed = landed or []
    named = landed[:ARRIVALS_NAMED]
    names = dict(
        session.execute(select(Document.id, Document.filename).where(Document.id.in_(named))).all()
    )
    report = QuestionReport(
        org_id=project.org_id,
        project_id=project.id,
        set_id=row.id,
        title=row.title,
        trigger=trigger,
        user_id=user_id,
        audience=row.audience,
        arrived={
            "documents": landed,
            "names": [names[document_id] for document_id in named if document_id in names],
        }
        if landed
        else None,
        answers=[_unasked(question) for question in row.questions],
    )
    session.add(report)
    session.flush()
    enqueue(
        session,
        WRITE_QUESTION_REPORT,
        org_id=report.org_id,
        project_id=report.project_id,
        payload={"report_id": report.id},
        priority=LIVE if trigger == "member" else UNATTENDED,
    )
    return report


def _unasked(question: str) -> dict[str, Any]:
    return {
        "question": question,
        "status": "queued",
        "content": "",
        "sources": [],
        "notice": None,
        "error": None,
    }


def run_question_set(set_id: int, lease: Lease | None = None) -> str | None:
    """The worker's handler of a set's job: make the report its schedule or its arrivals are due
    for, as a member's is made, and queue the job's next waking, for the schedule's next moment
    or the arrivals still settling. A job that wakes before either is due queues the next for
    then. Committed under the lease, a job run again after a lost one finds the arrivals it took
    gone with the report it made, or waiting still if the commit was lost too."""
    with SessionLocal() as session:
        row = session.scalar(select(QuestionSet).where(QuestionSet.id == set_id).with_for_update())
        if row is None:
            return None
        now = datetime.now(UTC)
        scheduled = (
            row.enabled
            and row.schedule is not None
            and row.next_run_at is not None
            and row.next_run_at <= now
        )
        arrivals_at = arrivals_due_at(session, row.id)
        landed = take_arrivals(session, row.id) if arrivals_at and arrivals_at <= now else []
        if scheduled:
            plan_next(session, row, now)
        elif row.next_run_at is not None and row.enabled:
            # Woken early, by arrivals or a change: the schedule's moment stays queued.
            queue_set(session, row, row.next_run_at)
        if arrivals_at is not None and arrivals_at > now:
            queue_set(session, row, arrivals_at)
        if scheduled or landed:
            _make(session, row, "schedule" if scheduled else "arrivals", landed)
        _commit(session, lease)
    return None


def _make(session: Session, row: QuestionSet, trigger: str, landed: list[int]) -> None:
    """The report the set's job is due for, unless the set may not run now: off, its project in
    the trash or the flag off. What arrived goes with the job either way."""
    project = session.get(Project, row.project_id)
    organization = project.organization
    live = set(
        session.scalars(
            select(Document.id).where(
                Document.id.in_(landed),
                Document.project_id == project.id,
                Document.deleted_at.is_(None),
            )
        )
    )
    landed = [document_id for document_id in landed if document_id in live]
    if trigger == "arrivals" and not landed:
        return
    if (
        not row.enabled
        or project.deleted_at is not None
        or not flag_on(organization, QUESTION_SETS)
    ):
        log.info("Question set %s was due and not run: it may not run now", row.id)
        return
    report = start_report(session, project, row, trigger, None, landed)
    audit_event(
        session,
        "question_set_run",
        org_id=organization.id,
        project_id=project.id,
        target=("question_report", report.id),
        detail=run_detail(row, trigger, len(landed)),
    )


def run_detail(row: QuestionSet, trigger: str, arrived: int) -> dict[str, Any]:
    """What the audit log keeps of a set's run: its name, how many questions it asks, what ran
    it and how many documents arrived for it, never a question."""
    return {
        "name": row.title,
        "questions": len(row.questions),
        "trigger": trigger,
        "schedule": schedule_words(row.schedule, row.weekday, row.hour),
        "arrived": arrived,
    }


def write_report(report_id: int, services: Services, lease: Lease | None = None) -> None:
    """The worker's handler of a report's job: ask its questions one after another, each kept
    as it is answered, and settle it once none is left, then tell it. If the run itself breaks,
    the questions left end failed with a sentence before the job fails with the traceback."""
    try:
        while (asked := _next(report_id, lease)) is not None:
            _answer(report_id, asked, services, lease)
        _settle(report_id, lease)
    except LeaseLost:
        raise
    except Exception:
        log.exception("Report %s of a question set failed", report_id)
        fail_report(report_id)
        raise
    tell_report(report_id, lease)


def _next(report_id: int, lease: Lease | None) -> Asked | None:
    """The report's next question to ask, counted as a question asked as it is marked asked,
    committed under the lease; None once none is left. One marked asked already, by a run lost
    while it was asked, is asked again and not counted again. A question the day's spending or
    the trash refuses ends failed with the sentence, and so does every one after it."""
    with SessionLocal() as session:
        report = session.scalar(
            select(QuestionReport).where(QuestionReport.id == report_id).with_for_update()
        )
        if report is None or report.status not in ("queued", "running"):
            return None
        answers = [dict(entry) for entry in report.answers]
        index = next(
            (
                place
                for place, entry in enumerate(answers)
                if entry["status"] not in ("done", "failed")
            ),
            None,
        )
        if index is None:
            return None
        project = session.get(Project, report.project_id)
        organization = project.organization
        report.status = "running"
        if answers[index]["status"] == "queued":
            refusal = PROJECT_GONE if project.deleted_at is not None else check_spend(session)
            if refusal is not None:
                log.info("Report %s stopped asking: %s", report_id, refusal)
                for entry in answers[index:]:
                    if entry["status"] == "queued":
                        entry.update(status="failed", error=refusal)
                report.answers = answers
                _commit(session, lease)
                return None
            # Counted as a member's question is.
            record(
                session,
                org_id=organization.id,
                project_id=project.id,
                user_id=report.user_id,
                kind=ANSWER,
                measures={"questions": 1},
            )
            answers[index]["status"] = "running"
            report.answers = answers
        asked = Asked(
            index=index,
            question=answers[index]["question"],
            project_id=project.id,
            payer={"org_id": report.org_id, "project_id": project.id, "user_id": report.user_id},
            task=ASKED_ALONE + _arrivals_told(session, report),
        )
        _commit(session, lease)
        return asked


def _arrivals_told(session: Session, report: QuestionReport) -> str:
    """What the task says of the documents that arrived for the report, by their ids, of those
    still in the project; nothing for a report nothing arrived for."""
    if not report.arrived:
        return ""
    ids = list(
        session.scalars(
            select(Document.id)
            .where(
                Document.id.in_(report.arrived["documents"]),
                Document.project_id == report.project_id,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.id)
            .limit(ARRIVALS_TOLD)
        )
    )
    if not ids:
        return ""
    return (
        f"\n\nThe set ran because documents arrived in the project: those with the ids "
        f"{', '.join(map(str, ids))}. Where the question concerns them, say what they change."
    )


# The version of a question set's answers' prompts (app/prompts.py), which their usage rows
# carry: an answer's, with the task said after it. They are `answer` usage, so a set's answers
# and the chats' show under that kind as two versions at once.
QUESTION_SET_PROMPT_VERSION = prompt_version(*AGENT_PROMPTS, ASKED_ALONE, _arrivals_told)


def _answer(report_id: int, asked: Asked, services: Services, lease: Lease | None) -> None:
    """Have the agent answer the question over the project and keep the answer in its place,
    with what it used, whichever way its run ended."""
    usage = ModelUsage()
    events: Generator[dict[str, Any]] = run_agent(
        services,
        asked.project_id,
        [{"role": "user", "content": asked.question}],
        usage,
        task=asked.task,
    )
    reply = Reply()
    try:
        for event in events:
            reply.apply(event)
        with SessionLocal() as session:
            _keep(session, report_id, asked.index, reply, lease)
    finally:
        try:
            events.close()
        finally:
            _charge(usage, report_id, asked.payer)


def _keep(session: Session, report_id: int, index: int, reply: Reply, lease: Lease | None) -> None:
    """Keep the answer in the report as its run ended, written or failed, committed with the
    lease renewed; a report deleted meanwhile, or failed, takes nothing."""
    report = session.scalar(
        select(QuestionReport).where(QuestionReport.id == report_id).with_for_update()
    )
    if report is None or report.status != "running":
        session.rollback()
        return
    answers = [dict(entry) for entry in report.answers]
    if answers[index]["status"] == "running":
        answers[index].update(
            status="failed" if reply.error else "done",
            content=reply.content,
            sources=list(reply.sources.values()),
            notice=reply.notice,
            error=reply.error,
        )
        report.answers = answers
    _commit(session, lease)


def _settle(report_id: int, lease: Lease | None) -> None:
    """Mark the report kept once every question is answered or refused."""
    with SessionLocal() as session:
        session.execute(
            update(QuestionReport)
            .where(QuestionReport.id == report_id, QuestionReport.status.in_(("queued", "running")))
            .values(status="done", finished_at=func.now())
        )
        _commit(session, lease)


def _charge(usage: ModelUsage, report_id: int, payer: dict[str, int | None]) -> None:
    """Record an answer's model calls as the report's usage, in a transaction of its own,
    whichever way its run ended."""
    if not usage.calls:
        return
    try:
        with SessionLocal() as session:
            for model, calls in usage.models.items():
                record(
                    session,
                    **payer,
                    kind=ANSWER,
                    measures=calls.measures,
                    model=model,
                    prompt_version=QUESTION_SET_PROMPT_VERSION,
                    ref=(QUESTION_REPORT, report_id),
                )
            session.commit()
    except PoolTimeout:
        log.warning(
            "The usage of an answer of report %s was not recorded: every database connection "
            "is in use",
            report_id,
        )


def _commit(session: Session, lease: Lease | None) -> None:
    if lease is not None and not renew(session, lease):
        session.rollback()
        raise LeaseLost(lease)
    session.commit()


def fail_report(report_id: int, error: str | None = None) -> None:
    """End the report failed, and each question it had not answered yet with the sentence."""
    error = error or REPORT_FAILED
    with SessionLocal() as session:
        report = session.scalar(
            select(QuestionReport).where(QuestionReport.id == report_id).with_for_update()
        )
        if report is None or report.status not in ("queued", "running"):
            return
        answers = [dict(entry) for entry in report.answers]
        for entry in answers:
            if entry["status"] in ("queued", "running"):
                entry.update(status="failed", error=error)
        report.answers = answers
        report.status, report.error, report.finished_at = "failed", error, datetime.now(UTC)
        session.commit()


def abandon_report(report_id: int, attempts: int) -> None:
    """Every run of the job was lost to a dying process: the report ends failed, for a member to
    run the set again."""
    fail_report(
        report_id,
        f"It stopped unexpectedly {attempts} times and was not tried again. Run the set again in "
        "a moment.",
    )


def counted(answers: list[dict[str, Any]]) -> tuple[int, int]:
    """How many of a report's questions are answered, and how many failed or were refused."""
    statuses = [entry["status"] for entry in answers]
    return statuses.count("done"), statuses.count("failed")


def tell_report(report_id: int, lease: Lease | None = None) -> int:
    """Tell that the report was kept, once, as it settles: to the member who ran it, or to whom
    its set names, each as they chose for the kind of notice; returns to how many. A failed
    report, and one told already, tell none."""
    with SessionLocal() as session:
        report = session.scalar(
            select(QuestionReport).where(QuestionReport.id == report_id).with_for_update()
        )
        if report is None or report.status != "done" or report.told_at is not None:
            return 0
        report.told_at = datetime.now(UTC)
        project = session.get(Project, report.project_id)
        told = 0
        if project.deleted_at is None:
            told = _tell(session, report, project)
        _commit(session, lease)
        return told


def _tell(session: Session, report: QuestionReport, project: Project) -> int:
    organization = project.organization
    if report.trigger == "member":
        # The member who ran it, while they are one and see the project.
        users = audience_of(session, project, "maker", report.user_id)
    else:
        maker = session.scalar(
            select(QuestionSet.created_by).where(QuestionSet.id == report.set_id)
        )
        users = audience_of(session, project, report.audience, maker)
    page = REPORT_PAGE.format(project.id, report.id)
    url = app_address()
    answered, _ = counted(report.answers)
    arrived = len(report.arrived["documents"]) if report.arrived else 0
    told = 0
    for user in users:
        content = report_ready_email(
            user.name,
            organization.name,
            project.name,
            report.title,
            report.trigger,
            arrived,
            len(report.answers),
            answered,
            f"{url}{page}",
        )
        told += tell(session, user, organization.id, REPORT_READY, content, page, url)
    return told
