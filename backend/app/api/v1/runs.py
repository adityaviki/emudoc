"""The versioned API's workflows (app/api/v1/): a project's cross-checks, and the runs of them
(app/check_runs.py) that a program starts with an Idempotency-Key, follows until they are worked
out and reads what each rule found of, with the discrepancy report the agent writes when asked.

Behind the flag `extraction_templates` (app/flags.py), as the page's routes are, so an
organisation it is off for finds none of them. A run is made and its findings read through the
page's routes (app/api/check_runs.py), whose checks, refusals and audit rows hold here as there.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app.api import check_runs
from app.api.deps import (
    AccountDep,
    EditableProjectDep,
    ProjectDep,
    RunDep,
    SessionDep,
    flagged,
    limited,
)
from app.api.v1.chats import source_of
from app.api.v1.deps import MOST, PAGE, After, Limit, OnceDep, cursor, cursor_of, shown
from app.api.v1.schemas import (
    CheckRun,
    CheckRunList,
    CrossCheck,
    CrossCheckList,
    Fact,
    Finding,
    FindingList,
    NewCheckRun,
    Partner,
    Rule,
)
from app.cross_checks import rule_of, sides_of
from app.extraction import templates_for
from app.flags import EXTRACTION_TEMPLATES
from app.idempotency import fingerprint
from app.models import CrossCheck as CrossCheckRow
from app.models import WorkflowRun
from app.ratelimit import SEARCHES
from app.schemas import CheckRunIn, FindingOutcome

router = APIRouter(dependencies=[Depends(flagged(EXTRACTION_TEMPLATES))])


@router.get(
    "/projects/{project_id}/cross-checks", response_model=CrossCheckList, tags=["cross-checks"]
)
def list_cross_checks(project: ProjectDep, session: SessionDep):
    """The project's cross-checks by name, every one of them on one page, each with why it cannot
    be worked out now, if it cannot; members write them in the app."""
    templates = templates_for(session, project.id)
    rows = session.scalars(
        select(CrossCheckRow)
        .where(CrossCheckRow.project_id == project.id)
        .order_by(CrossCheckRow.title, CrossCheckRow.id)
    ).all()
    checks = []
    for row in rows:
        sides = sides_of(rule_of(row), templates)
        checks.append(
            CrossCheck(
                id=row.id, title=row.title, problem=sides if isinstance(sides, str) else None
            )
        )
    return CrossCheckList(data=checks, next=None)


@router.post(
    "/projects/{project_id}/check-runs",
    response_model=CheckRun,
    status_code=201,
    dependencies=[Depends(limited(SEARCHES))],
    tags=["check-runs"],
)
def run_checks(
    project: EditableProjectDep,
    body: NewCheckRun,
    request: Request,
    session: SessionDep,
    account: AccountDep,
    once: OnceDep,
):
    """Run the project's cross-checks, those named or all of them, over its documents as they
    read now; the worker works the run out, which is followed by its id. Refused with 409 while
    another run of the project's is under way, and with 400 when it has no cross-checks. A
    report counts as one of the workspace's questions. With an Idempotency-Key, the run asked for
    again is given the first one's answer, and made once."""

    def work() -> CheckRun:
        made = check_runs.run_checks(
            project, CheckRunIn(**body.model_dump()), session, account, request
        )
        return run_of(session.get(WorkflowRun, made.id))

    return once.run(lambda: fingerprint(request, body.model_dump(mode="json")), 201, work)


@router.get("/projects/{project_id}/check-runs", response_model=CheckRunList, tags=["check-runs"])
def list_runs(project: ProjectDep, session: SessionDep, limit: Limit = PAGE, after: After = None):
    """The project's runs, the newest first."""
    start = cursor(after, int)
    query = select(WorkflowRun).where(WorkflowRun.project_id == project.id)
    if start:
        query = query.where(WorkflowRun.id < start[0])
    rows, more = shown(
        session.scalars(query.order_by(WorkflowRun.id.desc()).limit(limit + 1)).all(), limit
    )
    return CheckRunList(
        data=[run_of(run) for run in rows], next=cursor_of(rows[-1].id) if more else None
    )


@router.get("/check-runs/{run_id}", response_model=CheckRun, tags=["check-runs"])
def get_run(run: RunDep):
    """The run: how far it and its report are, what each of its rules found, and the report."""
    return run_of(run)


@router.get(
    "/check-runs/{run_id}/findings",
    response_model=FindingList,
    dependencies=[Depends(limited(SEARCHES))],
    tags=["check-runs"],
)
def run_findings(
    run: RunDep,
    session: SessionDep,
    rule: Annotated[int, Query(ge=0, description="The rule's place in the run's `rules`.")],
    outcome: Annotated[FindingOutcome | None, Query(description="Only those of it.")] = None,
    limit: Annotated[
        int, Query(ge=1, le=MOST, description="How many items the page holds.")
    ] = PAGE,
    after: After = None,
):
    """What one rule of the run found, as it was found: differing first, then missing a value,
    then holding, each by its document's name."""
    start = cursor(after, int)
    page = check_runs.run_findings(run, session, rule, outcome, start[0] if start else None, limit)
    return FindingList(
        data=[
            Finding(
                document_id=finding.document_id,
                title=finding.title,
                filename=finding.filename,
                outcome=finding.outcome,
                reason=finding.reason,
                left=Fact.model_validate(finding.left.model_dump()) if finding.left else None,
                right=[Fact.model_validate(fact.model_dump()) for fact in finding.right],
                partners=[Partner.model_validate(each.model_dump()) for each in finding.partners],
                difference=finding.difference,
            )
            for finding in page.findings
        ],
        total=page.total,
        next=cursor_of(page.next_after) if page.next_after is not None else None,
    )


def run_of(run: WorkflowRun) -> CheckRun:
    counts = check_runs.run_counts(run)
    return CheckRun(
        id=run.id,
        project_id=run.project_id,
        status=run.status,
        report_status=run.report_status,
        rules=[
            Rule(
                rule=place,
                check_id=kept["check_id"],
                title=kept["title"],
                problem=kept.get("problem"),
                counts=kept.get("counts"),
            )
            for place, kept in enumerate(run.rules)
        ],
        counts=counts.model_dump() if counts is not None else None,
        error=run.error,
        report=run.report,
        report_sources=[source_of(source) for source in run.report_sources],
        report_error=run.report_error,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )
