"""A project's activity, which everyone who sees it reads (app/activity.py)."""

from fastapi import APIRouter, Depends, Query

from app.activity import read_activity
from app.api.deps import ProjectDep, SessionDep, flagged
from app.flags import COMMENTS
from app.schemas import ActivityPage

router = APIRouter(tags=["activity"], dependencies=[Depends(flagged(COMMENTS))])

# How many items a page holds unless the request asks for another number.
PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


@router.get("/projects/{project_id}/activity", response_model=ActivityPage)
def list_activity(
    project: ProjectDep,
    session: SessionDep,
    before: int | None = None,
    limit: int = Query(PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """A page of what was done in the project, newest first, older than the item `before`
    names when it is given."""
    return read_activity(session, project, before, limit)
