"""The versioned API's reports (app/question_reports.py): the answers to a project's question
sets, which members write in the app and which run on a schedule or as documents arrive. A
program hears of a report kept by the `report.completed` webhook event, which names it, and
reads its answers here.
"""

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import ProjectDep, ReportDep, SessionDep
from app.api.v1.chats import source_of
from app.api.v1.deps import PAGE, After, Limit, cursor, cursor_of, shown
from app.api.v1.schemas import Report, ReportAnswer, ReportList, ReportSummary
from app.models import QuestionReport
from app.question_reports import counted

router = APIRouter(tags=["reports"])


@router.get("/projects/{project_id}/reports", response_model=ReportList)
def list_reports(
    project: ProjectDep, session: SessionDep, limit: Limit = PAGE, after: After = None
):
    """The project's reports, the newest first, without their answers."""
    start = cursor(after, int)
    query = select(QuestionReport).where(QuestionReport.project_id == project.id)
    if start:
        query = query.where(QuestionReport.id < start[0])
    rows, more = shown(
        session.scalars(query.order_by(QuestionReport.id.desc()).limit(limit + 1)).all(), limit
    )
    return ReportList(
        data=[summary_of(report) for report in rows],
        next=cursor_of(rows[-1].id) if more else None,
    )


@router.get("/reports/{report_id}", response_model=Report)
def get_report(report: ReportDep):
    """The report: how far it is, and each of its questions with its answer and the passages the
    answer cites."""
    return Report(
        **summary_of(report).model_dump(),
        answers=[
            ReportAnswer(
                question=entry["question"],
                status=entry["status"],
                answer=entry["content"],
                sources=[source_of(source) for source in entry["sources"]],
                error=entry["error"],
            )
            for entry in report.answers
        ],
    )


def summary_of(report: QuestionReport) -> ReportSummary:
    """What a report is, without what its answers say, as the list and the webhook event give
    it."""
    answered, _ = counted(report.answers)
    return ReportSummary(
        id=report.id,
        project_id=report.project_id,
        question_set_id=report.set_id,
        title=report.title,
        trigger=report.trigger,
        status=report.status,
        questions=len(report.answers),
        answered=answered,
        arrived=len(report.arrived["documents"]) if report.arrived else 0,
        error=report.error,
        created_at=report.created_at,
        finished_at=report.finished_at,
    )
