"""Runs of a project's cross-checks (app/check_runs.py): a member runs the project's rules, or
some of them, which the worker works out over the documents as they read then and keeps with
what they found, asks the agent for a discrepancy report from that, and the project's members
read the runs, newest first, what each rule of one found, and its report.

Every route is behind the flag `extraction_templates` (app/flags.py), as the rules are. Whoever
may change the project runs its checks, asks for a report and deletes a run, each a row of the
organisation's audit log naming the rules, never a value; anyone who sees the project reads them.
A report is a run of the agent, so asking for one is held to what holds a question: the agent's
runs in flight, the month's questions, of which it counts one, and the day's spending.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import Account
from app.api.auth import app_url
from app.api.chats import refuse_past_answers_in_flight, refuse_past_spending
from app.api.deps import (
    AccountDep,
    DeletableRunDep,
    EditableProjectDep,
    EditableRunDep,
    ProjectDep,
    RunDep,
    SessionDep,
    flagged,
    limited,
)
from app.audit import audit
from app.check_runs import gone_documents, queue_run, start_run
from app.cross_checks import OUTCOMES
from app.entitlements import PlanRefused, check_question
from app.flags import EXTRACTION_TEMPLATES
from app.models import CrossCheck, Project, User, WorkflowFinding, WorkflowRun
from app.notices import MONTH_QUESTIONS, note_quota
from app.ratelimit import QUESTIONS, SEARCHES
from app.schemas import (
    CheckRunIn,
    CheckRunOut,
    CheckRunRuleOut,
    CheckRunsOut,
    CheckRunSummaryOut,
    FindingCountsOut,
    FindingOut,
    FindingOutcome,
    RunFindingsOut,
    RunTriggerOut,
)
from app.usage import ANSWER, record

router = APIRouter(tags=["check-runs"], dependencies=[Depends(flagged(EXTRACTION_TEMPLATES))])

NOTHING_TO_RUN = "This project has no cross-checks to run. Add one first."
STILL_RUNNING = (
    "The project's cross-checks are being run already. Wait for that run to finish, then run "
    "them again."
)
NOT_YET = "The checks of this run are still being worked out. Ask for its report once they are."
RUN_FAILED = "This run failed, so it has nothing to report. Run the checks again."
WRITING = "The report of this run is being written already. Wait for it."
NO_SUCH_RULE = "This run has no such rule."

# Runs a page of the project's list, and findings a page of a rule's.
RUNS_SHOWN = 20
MAX_RUNS_SHOWN = 100
FINDINGS_SHOWN = 50
MAX_FINDINGS_SHOWN = 200


@router.post(
    "/projects/{project_id}/check-runs",
    response_model=CheckRunOut,
    status_code=201,
    dependencies=[Depends(limited(SEARCHES))],
)
def run_checks(
    project: EditableProjectDep,
    body: CheckRunIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Run the project's cross-checks, those named or all of them, with the report written once
    they are worked out when asked; the worker works the run out, which the page follows."""
    query = select(CrossCheck).where(CrossCheck.project_id == project.id)
    if body.check_ids is not None:
        query = query.where(CrossCheck.id.in_(body.check_ids))
    rows = session.scalars(query.order_by(CrossCheck.title, CrossCheck.id)).all()
    if body.check_ids is not None and len(rows) < len(set(body.check_ids)):
        raise HTTPException(404, "Cross-check not found")
    if not rows:
        raise HTTPException(400, NOTHING_TO_RUN)
    # The project's row orders two runs asked at once: one is running, the other refused.
    session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    if session.scalar(
        select(WorkflowRun.id).where(
            WorkflowRun.project_id == project.id, WorkflowRun.status.in_(("queued", "running"))
        )
    ):
        raise HTTPException(409, STILL_RUNNING)
    if body.report:
        refuse_agent_run(session, account, f"A report of project {project.id}'s checks")
    run = start_run(session, project, account.user.id, list(rows), report=body.report)
    if body.report:
        count_question(session, account, project.id, request)
    audit(
        session,
        account,
        request,
        "cross_checks_run",
        project_id=project.id,
        target=("workflow_run", run.id),
        detail={"names": [row.title for row in rows], "report": body.report},
    )
    session.commit()
    return _out(session, run)


@router.get("/projects/{project_id}/check-runs", response_model=CheckRunsOut)
def list_runs(
    project: ProjectDep,
    session: SessionDep,
    before: int | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_RUNS_SHOWN)] = RUNS_SHOWN,
):
    """The project's runs, newest first, a page at a time before the run `before` names."""
    query = (
        select(WorkflowRun, User.name)
        .outerjoin(User, User.id == WorkflowRun.user_id)
        .where(WorkflowRun.project_id == project.id)
    )
    if before is not None:
        query = query.where(WorkflowRun.id < before)
    rows = session.execute(query.order_by(WorkflowRun.id.desc()).limit(limit + 1)).all()
    shown = rows[:limit]
    return CheckRunsOut(
        runs=[_summary(run, name) for run, name in shown],
        next_before=shown[-1][0].id if len(rows) > limit else None,
    )


@router.get("/check-runs/{run_id}", response_model=CheckRunOut)
def get_run(run: RunDep, session: SessionDep):
    """The run whole: its rules with what each found, and its report."""
    return _out(session, run)


@router.get(
    "/check-runs/{run_id}/findings",
    response_model=RunFindingsOut,
    dependencies=[Depends(limited(SEARCHES))],
)
def run_findings(
    run: RunDep,
    session: SessionDep,
    rule: Annotated[int, Query(ge=0)],
    outcome: FindingOutcome | None = None,
    after: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_FINDINGS_SHOWN)] = FINDINGS_SHOWN,
):
    """A page of what one of the run's rules found, as it was found: those of one outcome or
    all, differing first, then missing a value, then holding, each by its document's name."""
    if rule >= len(run.rules):
        raise HTTPException(404, NO_SUCH_RULE)
    query = select(WorkflowFinding).where(
        WorkflowFinding.run_id == run.id, WorkflowFinding.rule == rule
    )
    if outcome is not None:
        query = query.where(WorkflowFinding.outcome == outcome)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    if after is not None:
        query = query.where(WorkflowFinding.seq > after)
    rows = session.scalars(query.order_by(WorkflowFinding.seq).limit(limit + 1)).all()
    shown = rows[:limit]
    named = {
        fact["document_id"]
        for finding in shown
        for fact in ([finding.left] if finding.left else []) + finding.right
    } | {finding.document_id for finding in shown}
    return RunFindingsOut(
        rule=_rule_out(run.rules[rule]),
        outcome=outcome,
        findings=[
            FindingOut(
                document_id=finding.document_id,
                title=finding.title,
                filename=finding.filename,
                outcome=finding.outcome,
                reason=finding.reason,
                left=finding.left,
                right=finding.right,
                partners=finding.partners,
                difference=finding.difference,
            )
            for finding in shown
        ],
        total=total,
        next_after=shown[-1].seq if len(rows) > limit else None,
        gone=gone_documents(session, run.project_id, named),
    )


@router.post(
    "/check-runs/{run_id}/report",
    response_model=CheckRunOut,
    dependencies=[Depends(limited(QUESTIONS))],
)
def ask_report(run: EditableRunDep, session: SessionDep, account: AccountDep, request: Request):
    """Have the agent write the run's discrepancy report, or write it again, from what the run
    found; the worker writes it, which the page follows."""
    # The lock orders this with the worker settling the run, and with a second ask at once.
    session.execute(select(WorkflowRun.id).where(WorkflowRun.id == run.id).with_for_update())
    session.refresh(run)
    if run.status in ("queued", "running"):
        raise HTTPException(409, NOT_YET)
    if run.status == "failed":
        raise HTTPException(409, RUN_FAILED)
    if run.report_status in ("queued", "writing"):
        raise HTTPException(409, WRITING)
    refuse_agent_run(session, account, f"A report of run {run.id}")
    run.report_status = "queued"
    run.report, run.report_sources = "", []
    run.report_notice = run.report_error = None
    queue_run(session, run)
    count_question(session, account, run.project_id, request)
    audit(
        session,
        account,
        request,
        "check_report_requested",
        project_id=run.project_id,
        target=("workflow_run", run.id),
        detail={"names": [kept["title"] for kept in run.rules]},
    )
    session.commit()
    return _out(session, run)


@router.delete("/check-runs/{run_id}", status_code=204)
def delete_run(run: DeletableRunDep, session: SessionDep, account: AccountDep, request: Request):
    """Delete the run for good, with what it found and its report; its rules stay."""
    audit(
        session,
        account,
        request,
        "check_run_deleted",
        project_id=run.project_id,
        target=("workflow_run", run.id),
        detail={"names": [kept["title"] for kept in run.rules]},
    )
    session.delete(run)
    session.commit()


def refuse_agent_run(session: Session, account: Account, what: str) -> None:
    """Refuse a run of the agent asked for without a question, a report or a draft
    (app/api/drafts.py), past what holds a question: the agent's runs in flight, the month's
    questions and the day's spending; `what` names it for the log."""
    refuse_past_answers_in_flight(session, account, what)
    if refusal := check_question(session, account.organization):
        raise PlanRefused(refusal)
    refuse_past_spending(session, account, what)


def count_question(session: Session, account: Account, project_id: int, request: Request) -> None:
    """Count a report or a draft as one of the month's questions, as asking does
    (app/api/chats.py)."""
    record(
        session,
        org_id=account.organization.id,
        project_id=project_id,
        user_id=account.user.id,
        kind=ANSWER,
        measures={"questions": 1},
    )
    note_quota(session, account.organization, MONTH_QUESTIONS, app_url(request))


def _rule_out(kept: dict) -> CheckRunRuleOut:
    return CheckRunRuleOut.model_validate(kept)


def run_counts(run: WorkflowRun) -> FindingCountsOut | None:
    """What the run's rules found together, once it is worked out."""
    if run.status != "done":
        return None
    counted = [kept["counts"] for kept in run.rules if kept.get("counts")]
    return FindingCountsOut(
        **{outcome: sum(counts[outcome] for counts in counted) for outcome in OUTCOMES}
    )


def _summary(run: WorkflowRun, run_by: str | None) -> CheckRunSummaryOut:
    return CheckRunSummaryOut(
        id=run.id,
        status=run.status,
        report_status=run.report_status,
        run_by=run_by,
        trigger=_trigger_out(run),
        titles=[kept["title"] for kept in run.rules],
        counts=run_counts(run),
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def _trigger_out(run: WorkflowRun) -> RunTriggerOut | None:
    """What an automation's run was made for, counted; None for a member's run."""
    if run.trigger is None:
        return None
    return RunTriggerOut(
        automation_id=run.automation_id,
        automation=run.trigger["automation"],
        documents=len(run.trigger["documents"]),
        partners=len(run.trigger["partners"]),
        names=run.trigger["names"],
    )


def _out(session: Session, run: WorkflowRun) -> CheckRunOut:
    session.refresh(run)
    run_by = session.scalar(select(User.name).where(User.id == run.user_id))
    return CheckRunOut(
        **_summary(run, run_by).model_dump(),
        project_id=run.project_id,
        user_id=run.user_id,
        error=run.error,
        rules=[_rule_out(kept) for kept in run.rules],
        report=run.report,
        report_sources=run.report_sources,
        report_notice=run.report_notice,
        report_error=run.report_error,
        usage=run.usage,
    )
