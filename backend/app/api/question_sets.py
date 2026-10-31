"""A project's question sets (app/question_sets.py) and the reports of their answers
(app/question_reports.py), which the library's Reports view lists.

Every route is behind the flag `question_sets` (app/flags.py), and making, changing and running a
set behind the plan's `question_sets` (402). Whoever may change the project makes, changes, turns
off and deletes its sets, and runs one there and then, each a row of the organisation's audit
log naming the set and counting its questions, never one of them; a set is written whole and
deleted for good, with no trash, the reports it made staying with the project's, naming it as it
was. Anyone who sees the project reads the sets and the reports, and a member deletes a report.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.api.check_runs import refuse_agent_run
from app.api.deps import (
    AccountDep,
    DeletableQuestionSetDep,
    DeletableReportDep,
    EditableProjectDep,
    EditableQuestionSetDep,
    ProjectDep,
    ReportDep,
    SessionDep,
    flagged,
    limited,
)
from app.audit import audit
from app.entitlements import PlanRefused, check_question_sets
from app.flags import QUESTION_SETS
from app.models import Project, QuestionReport, QuestionSet, User
from app.question_reports import counted, run_detail, start_report
from app.question_sets import (
    MAX_QUESTION_SETS,
    MAX_SET_QUESTIONS,
    plan_next,
    problem_of,
    schedule_words,
    zone_name,
)
from app.ratelimit import QUESTIONS
from app.schemas import (
    QuestionReportOut,
    QuestionReportsOut,
    QuestionReportSummaryOut,
    QuestionSetIn,
    QuestionSetOut,
    QuestionSetsOut,
    ReportAnswerOut,
)

router = APIRouter(tags=["question-sets"], dependencies=[Depends(flagged(QUESTION_SETS))])

TOO_MANY = (
    f"A project has at most {MAX_QUESTION_SETS} question sets. Delete one it no longer needs "
    "before adding another."
)
NOTHING_RUNS_IT = (
    "A question set runs on a schedule, as documents arrive, or both. Choose a schedule, or have "
    "arriving documents run it."
)
NO_WEEKDAY = "A weekly schedule runs on one day of the week. Choose the day."
STILL_RUNNING = (
    "This set's questions are being answered. Wait for its report before running it again."
)
STILL_WRITTEN = "This report is still being answered. Wait for it to finish before deleting it."
# The reports a page of the list holds.
REPORTS_PAGE = 20

# What of a set a change may change, as the audit log names it, by its columns.
PARTS = {
    "title": ("title",),
    "questions": ("questions",),
    "schedule": ("schedule", "weekday", "hour"),
    "arrivals": ("on_arrivals",),
    "audience": ("audience",),
    "enabled": ("enabled",),
}


@router.get("/projects/{project_id}/question-sets", response_model=QuestionSetsOut)
def list_question_sets(project: ProjectDep, session: SessionDep):
    """The project's question sets by name, each with when its schedule runs it next, why it
    would run nothing as its organisation stands now, if it would not, and when it last ran."""
    rows = session.scalars(
        select(QuestionSet)
        .where(QuestionSet.project_id == project.id)
        .order_by(QuestionSet.title, QuestionSet.id)
    ).all()
    return QuestionSetsOut(
        question_sets=_outs(session, project, list(rows)),
        max_question_sets=MAX_QUESTION_SETS,
        max_questions=MAX_SET_QUESTIONS,
    )


@router.post("/projects/{project_id}/question-sets", response_model=QuestionSetOut, status_code=201)
def create_question_set(
    project: EditableProjectDep,
    body: QuestionSetIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Make a question set of the project, its schedule's first run queued."""
    if refusal := check_question_sets(account.organization):
        raise PlanRefused(refusal)
    columns = _written(body)
    # The project's row, so that two made at once cannot pass its ceiling.
    session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    held = session.scalar(select(func.count()).where(QuestionSet.project_id == project.id))
    if held >= MAX_QUESTION_SETS:
        raise HTTPException(409, TOO_MANY)
    row = QuestionSet(
        org_id=project.org_id, project_id=project.id, created_by=account.user.id, **columns
    )
    session.add(row)
    session.flush()
    plan_next(session, row)
    audit(
        session,
        account,
        request,
        "question_set_created",
        project_id=project.id,
        target=("question_set", row.id),
        detail=_named(row),
    )
    session.commit()
    return _outs(session, project, [row])[0]


@router.put("/question-sets/{set_id}", response_model=QuestionSetOut)
def update_question_set(
    row: EditableQuestionSetDep,
    body: QuestionSetIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Write the set anew, whole: its schedule runs it next as it now says, and what arrives
    from now on runs it if it says so."""
    if refusal := check_question_sets(account.organization):
        raise PlanRefused(refusal)
    columns = _written(body)
    # Held while it changes, so that its job, waking meanwhile, reads it before or after.
    session.execute(select(QuestionSet.id).where(QuestionSet.id == row.id).with_for_update())
    session.refresh(row)
    changed = [
        part
        for part, names in PARTS.items()
        if any(getattr(row, name) != columns[name] for name in names)
    ]
    if changed:
        for name, value in columns.items():
            setattr(row, name, value)
        plan_next(session, row)
        audit(
            session,
            account,
            request,
            "question_set_changed",
            project_id=row.project_id,
            target=("question_set", row.id),
            detail={**_named(row), "changed": changed},
        )
    session.commit()
    return _outs(session, session.get(Project, row.project_id), [row])[0]


@router.delete("/question-sets/{set_id}", status_code=204)
def delete_question_set(
    row: DeletableQuestionSetDep, session: SessionDep, account: AccountDep, request: Request
):
    """Delete the set for good: nothing runs it any more, its job waking to find it gone, and the
    reports it made stay, naming it as it was."""
    audit(
        session,
        account,
        request,
        "question_set_deleted",
        project_id=row.project_id,
        target=("question_set", row.id),
        detail=_named(row),
    )
    session.delete(row)
    session.commit()


@router.post(
    "/question-sets/{set_id}/run",
    response_model=QuestionReportOut,
    status_code=201,
    dependencies=[Depends(limited(QUESTIONS))],
)
def run_question_set(
    row: EditableQuestionSetDep, session: SessionDep, account: AccountDep, request: Request
):
    """Ask the set's questions now, for a report told to the member who ran it once it is kept.
    Refused while a report of the set is waiting or being answered (409), and as a question is,
    past the agent's runs in flight, the month's questions and the day's spending; each question
    counts as one of the month's as it is asked."""
    if refusal := check_question_sets(account.organization):
        raise PlanRefused(refusal)
    session.execute(select(QuestionSet.id).where(QuestionSet.id == row.id).with_for_update())
    if session.scalar(select(exists().where(_running(row.id)))):
        raise HTTPException(409, STILL_RUNNING)
    refuse_agent_run(session, account, f"A run of question set {row.id}")
    project = session.get(Project, row.project_id)
    report = start_report(session, project, row, "member", account.user.id)
    audit(
        session,
        account,
        request,
        "question_set_run",
        project_id=project.id,
        target=("question_report", report.id),
        detail=run_detail(row, "member", 0),
    )
    session.commit()
    return report_out(session, report)


@router.get("/projects/{project_id}/reports", response_model=QuestionReportsOut)
def list_reports(project: ProjectDep, session: SessionDep, before: int | None = None):
    """The project's reports, the newest first, a page at a time: `before` the id the last page
    ended at."""
    query = select(QuestionReport).where(QuestionReport.project_id == project.id)
    if before is not None:
        query = query.where(QuestionReport.id < before)
    rows = session.scalars(query.order_by(QuestionReport.id.desc()).limit(REPORTS_PAGE + 1)).all()
    shown = rows[:REPORTS_PAGE]
    names = _names(session, {row.user_id for row in shown})
    return QuestionReportsOut(
        reports=[_summary(row, names) for row in shown],
        next_before=shown[-1].id if len(rows) > REPORTS_PAGE else None,
    )


@router.get("/reports/{report_id}", response_model=QuestionReportOut)
def get_report(report: ReportDep, session: SessionDep):
    """The report: how far it is, and each question with its answer."""
    return report_out(session, report)


@router.delete("/reports/{report_id}", status_code=204)
def delete_report(
    report: DeletableReportDep, session: SessionDep, account: AccountDep, request: Request
):
    """Delete the report for good, once its questions are answered."""
    if report.status in ("queued", "running"):
        raise HTTPException(409, STILL_WRITTEN)
    audit(
        session,
        account,
        request,
        "question_report_deleted",
        project_id=report.project_id,
        target=("question_report", report.id),
        detail={"name": report.title, "questions": len(report.answers)},
    )
    session.delete(report)
    session.commit()


def _written(body: QuestionSetIn) -> dict:
    """The set's columns as they are kept; refused for one nothing runs, and for a weekly
    schedule without its day. A day given to another schedule is dropped."""
    if body.schedule is None and not body.on_arrivals:
        raise HTTPException(400, NOTHING_RUNS_IT)
    if body.schedule == "weekly" and body.weekday is None:
        raise HTTPException(400, NO_WEEKDAY)
    return {
        "title": body.title,
        "questions": list(body.questions),
        "schedule": body.schedule,
        "weekday": body.weekday if body.schedule == "weekly" else None,
        "hour": body.hour,
        "on_arrivals": body.on_arrivals,
        "audience": body.audience,
        "enabled": body.enabled,
    }


def _named(row: QuestionSet) -> dict:
    """What the audit log keeps of a set: its name, how many questions it asks (never one of
    them, a member's words asked of documents), when it runs and whom it tells."""
    return {
        "name": row.title,
        "questions": len(row.questions),
        "schedule": schedule_words(row.schedule, row.weekday, row.hour),
        "arrivals": row.on_arrivals,
        "audience": row.audience,
        "enabled": row.enabled,
    }


def _running(set_id: int):
    return (QuestionReport.set_id == set_id) & QuestionReport.status.in_(("queued", "running"))


def _outs(session: Session, project: Project, rows: list[QuestionSet]) -> list[QuestionSetOut]:
    """The sets as the page shows them, their makers and reports read once for them all."""
    if not rows:
        return []
    ids = [row.id for row in rows]
    names = _names(session, {row.created_by for row in rows})
    last = dict(
        session.execute(
            select(QuestionReport.set_id, func.max(QuestionReport.created_at))
            .where(QuestionReport.set_id.in_(ids))
            .group_by(QuestionReport.set_id)
        ).all()
    )
    running = set(
        session.scalars(
            select(QuestionReport.set_id).where(
                QuestionReport.set_id.in_(ids), QuestionReport.status.in_(("queued", "running"))
            )
        )
    )
    organization = project.organization
    return [
        QuestionSetOut(
            id=row.id,
            title=row.title,
            questions=row.questions,
            schedule=row.schedule,
            weekday=row.weekday,
            hour=row.hour,
            on_arrivals=row.on_arrivals,
            audience=row.audience,
            enabled=row.enabled,
            time_zone=zone_name(session, row),
            next_run_at=row.next_run_at,
            created_by=row.created_by,
            made_by=names.get(row.created_by),
            problem=problem_of(session, row, organization),
            last_report_at=last.get(row.id),
            running=row.id in running,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


def _names(session: Session, ids: set[int | None]) -> dict[int, str]:
    return dict(session.execute(select(User.id, User.name).where(User.id.in_(ids - {None}))).all())


def _summary(report: QuestionReport, names: dict[int, str]) -> QuestionReportSummaryOut:
    answered, failed = counted(report.answers)
    arrived = report.arrived or {}
    return QuestionReportSummaryOut(
        id=report.id,
        question_set_id=report.set_id,
        title=report.title,
        trigger=report.trigger,
        run_by=names.get(report.user_id) if report.user_id is not None else None,
        status=report.status,
        questions=len(report.answers),
        answered=answered,
        failed=failed,
        arrived=len(arrived.get("documents", [])),
        names=arrived.get("names", []),
        created_at=report.created_at,
        finished_at=report.finished_at,
    )


def report_out(session: Session, report: QuestionReport) -> QuestionReportOut:
    """The report whole, as the page and a member's run answer it."""
    session.flush()
    session.refresh(report)
    summary = _summary(report, _names(session, {report.user_id}))
    return QuestionReportOut(
        **summary.model_dump(),
        project_id=report.project_id,
        user_id=report.user_id,
        error=report.error,
        answers=[ReportAnswerOut.model_validate(entry) for entry in report.answers],
        usage=report.usage,
    )
