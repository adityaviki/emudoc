"""The versioned API's reads over a project's indexes (app/api/v1/): search and the facts read from
its documents, held to RATE_SEARCHES a minute as the page's are (app/api/query.py), and the
passages that answers, reports and searches cite, which a program looks up one by one."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select

from app.api import documents, query
from app.api.deps import PassageDep, ProjectDep, ServicesDep, SessionDep, limited
from app.api.folders import resolve_folder
from app.api.v1.deps import PAGE, After, Limit, cursor, cursor_of, shown
from app.api.v1.schemas import Fact, FactList, Passage, SearchHit, SearchResults
from app.fact_checks import fact_out
from app.models import Document
from app.models import Fact as FactRow
from app.ratelimit import SEARCHES
from app.schemas import DocumentFilter

router = APIRouter()
SEARCHED = [Depends(limited(SEARCHES))]


@router.get(
    "/projects/{project_id}/search",
    response_model=SearchResults,
    dependencies=SEARCHED,
    tags=["search"],
)
def search(
    project: ProjectDep,
    session: SessionDep,
    services: ServicesDep,
    q: Annotated[
        str,
        Query(min_length=1, description="What to look for, in words.", examples=["late delivery"]),
    ],
    limit: Annotated[int, Query(ge=1, le=50, description="How many passages at most.")] = 10,
    document_id: Annotated[int | None, Query(description="Only in this document.")] = None,
    folder_id: Annotated[
        int | None, Query(description="Only in this folder and the folders in it.")
    ] = None,
):
    """The project's passages that match best, by keywords and by meaning together, the best
    first; by keywords alone while the embeddings are out (`semantic` false)."""
    # A folder in the trash is 404 here, as it always was, where the page's search finds nothing.
    resolve_folder(session, project, folder_id)
    found = query.searched(
        project, session, services, q, limit, document_id, DocumentFilter(folder_id=folder_id)
    )
    return SearchResults(
        data=[
            SearchHit(
                passage_id=hit.passage_id,
                document_id=hit.document_id,
                document_title=hit.document_title,
                doc_type=hit.doc_type,
                context=hit.context,
                snippet="".join(segment.text for segment in hit.snippet),
                page_start=hit.page_start,
                page_end=hit.page_end,
                score=hit.score,
            )
            for hit in found.hits
        ],
        semantic=found.semantic,
    )


@router.get(
    "/projects/{project_id}/facts", response_model=FactList, dependencies=SEARCHED, tags=["facts"]
)
def list_facts(
    project: ProjectDep,
    session: SessionDep,
    name: Annotated[str | None, Query(description="Only the facts of this field.")] = None,
    q: Annotated[
        str | None, Query(description="Only the facts whose field or value holds this.")
    ] = None,
    document_id: Annotated[int | None, Query(description="Only this document's.")] = None,
    limit: Limit = PAGE,
    after: After = None,
):
    """The facts read from the project's documents, the oldest first. A document read again has
    its facts written anew, under new ids."""
    start = cursor(after, int)
    filters = [FactRow.project_id == project.id, Document.deleted_at.is_(None)]
    if name:
        filters.append(FactRow.name == name)
    if document_id:
        filters.append(FactRow.document_id == document_id)
    if q:
        pattern = f"%{q}%"
        filters.append(or_(FactRow.value.ilike(pattern), FactRow.name.ilike(pattern)))
    if start:
        filters.append(FactRow.id > start[0])
    rows, more = shown(
        session.execute(
            select(FactRow, Document)
            .join(Document, Document.id == FactRow.document_id)
            .where(*filters)
            .order_by(FactRow.id)
            .limit(limit + 1)
        ).all(),
        limit,
    )
    return FactList(
        data=[fact_of(fact, document) for fact, document in rows],
        next=cursor_of(rows[-1][0].id) if more else None,
    )


@router.get("/passages/{passage_id}", response_model=Passage, tags=["search"])
def get_passage(passage: PassageDep, session: SessionDep):
    """A passage an answer, a report or a search names: where it is and what it says."""
    return Passage.model_validate(documents.get_passage(passage, session).model_dump())


def fact_of(fact: FactRow, document: Document) -> Fact:
    return Fact.model_validate(fact_out(fact, document).model_dump())
