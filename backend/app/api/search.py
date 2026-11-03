"""The search of every project a member sees, from a page of its own beside the projects
(`/search`): one ranking over all their passages (`search_projects` in app/search.py), each hit
naming its project, narrowed to some of those projects, to documents of some types and to a span
of their dates. Decided 2026-10-30 (3.10): the page's alone; the agent searches one project, as
every query the server makes for it, and the versioned API too, until a program needs more.

The projects are those `visible_to` the account, as the projects page lists them: a private one
is searched for its chosen members and the owners and admins alone, and one in the trash for
nobody. Tags and folders are each project's own, so nothing here narrows by them.
Like the project's search, it is held to RATE_SEARCHES a minute."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text

from app.access import visible_to
from app.api.deps import AccountDep, ServicesDep, SessionDep, limited
from app.api.query import hit_fields
from app.document_filter import BACKWARDS, anywhere
from app.models import Document, Project
from app.ratelimit import SEARCHES
from app.schemas import (
    DocumentFilter,
    DocumentTypeOut,
    SearchAllHitOut,
    SearchAllIn,
    SearchAllOut,
)
from app.search import SearchResult, search_projects

router = APIRouter(tags=["query"], dependencies=[Depends(limited(SEARCHES))])

# As a project the account does not see is answered everywhere (`get_project` in api/deps.py).
NOT_FOUND = "Project not found"


@router.get("/search", response_model=SearchAllOut)
def search_all(
    account: AccountDep,
    session: SessionDep,
    services: ServicesDep,
    asked: Annotated[SearchAllIn, Query()],
):
    """Search the passages of every project the member sees, or of those chosen among them,
    within the documents of the types and the dates asked for."""
    if asked.after and asked.before and asked.after > asked.before:
        raise HTTPException(400, BACKWARDS)
    seen = select(Project.id, Project.name).where(visible_to(account))
    if asked.projects:
        seen = seen.where(Project.id.in_(asked.projects))
    names = dict(session.execute(seen.order_by(Project.id)).all())
    if not names.keys() >= set(asked.projects):
        raise HTTPException(404, NOT_FOUND)
    chosen = DocumentFilter(types=asked.types, after=asked.after, before=asked.before)
    document_ids = None
    if chosen != DocumentFilter():
        # The documents' ids first, as the project's search does: how many the filter takes is
        # the projects', so the statement is planned for them (database.md).
        session.execute(text("SET LOCAL plan_cache_mode = force_custom_plan"))
        document_ids = list(
            session.scalars(
                select(Document.id).where(
                    Document.project_id.in_(names),
                    Document.deleted_at.is_(None),
                    *anywhere(chosen),
                )
            )
        )
    if document_ids == []:
        # Nothing to search: no filter at all would mean everything.
        result = SearchResult([], semantic=True)
    else:
        result = search_projects(
            session, services.embedder, list(names), asked.q, asked.limit, document_ids
        )
    return SearchAllOut(
        semantic=result.semantic,
        hits=[
            SearchAllHitOut(
                **hit_fields(hit), project_id=hit.project_id, project_name=names[hit.project_id]
            )
            for hit in result.hits
        ],
    )


@router.get("/document-types", response_model=list[DocumentTypeOut])
def list_document_types(account: AccountDep, session: SessionDep):
    """The types of the documents of every project the member sees, out of the trash, the most
    common first: what the search across them offers to narrow by."""
    rows = session.execute(
        select(Document.doc_type, func.count())
        .join(Project, Project.id == Document.project_id)
        .where(
            visible_to(account),
            Document.deleted_at.is_(None),
            Document.doc_type.is_not(None),
        )
        .group_by(Document.doc_type)
        .order_by(func.count().desc(), Document.doc_type)
    ).all()
    return [DocumentTypeOut(type=doc_type, count=count) for doc_type, count in rows]
