"""Read-side endpoints over a project's indexes: search and facts. Each is cheap, and none is
bounded by the plan, so the router holds every caller to RATE_SEARCHES a minute."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select

from app.api.deps import ProjectDep, ServicesDep, SessionDep, limited
from app.api.folders import resolve_folder, subtree_ids
from app.fact_checks import fact_out
from app.models import Document, Fact
from app.ratelimit import SEARCHES
from app.schemas import FactList, FactName, SearchHitOut, SearchOut, SnippetSegment
from app.search import SearchResult, hybrid_search

router = APIRouter(
    prefix="/projects/{project_id}", tags=["query"], dependencies=[Depends(limited(SEARCHES))]
)


@router.get("/search", response_model=SearchOut)
def search(
    project: ProjectDep,
    session: SessionDep,
    services: ServicesDep,
    q: str,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    document_id: int | None = None,
    folder_id: int | None = None,
):
    """Search the project's passages, within one document or one folder's subtree if asked."""
    document_ids = [document_id] if document_id else None
    folder = resolve_folder(session, project, folder_id)
    if folder is not None:
        in_folder = session.scalars(
            select(Document.id).where(
                Document.folder_id.in_(subtree_ids(session, folder)), Document.deleted_at.is_(None)
            )
        ).all()
        document_ids = [d for d in in_folder if document_ids is None or d in document_ids]
    if document_ids == []:
        # An empty folder: there is nothing to search, and no filter would mean everything.
        result = SearchResult([], semantic=True)
    else:
        result = hybrid_search(session, services.embedder, project.id, q, limit, document_ids)
    return SearchOut(
        semantic=result.semantic,
        hits=[
            SearchHitOut(
                passage_id=hit.passage_id,
                document_id=hit.document_id,
                document_title=hit.document_title,
                doc_type=hit.doc_type,
                context=hit.context,
                snippet=[SnippetSegment(text=text, match=match) for text, match in hit.snippet],
                page_start=hit.page_start,
                page_end=hit.page_end,
                score=hit.score,
                keyword_rank=hit.keyword_rank,
                vector_rank=hit.vector_rank,
            )
            for hit in result.hits
        ],
    )


@router.get("/facts", response_model=FactList)
def list_facts(
    project: ProjectDep,
    session: SessionDep,
    name: str | None = None,
    q: str | None = None,
    document_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    # The facts of the documents out of the trash (app/trash.py).
    filters = [Fact.project_id == project.id, Document.deleted_at.is_(None)]
    if name:
        filters.append(Fact.name == name)
    if document_id:
        filters.append(Fact.document_id == document_id)
    if q:
        pattern = f"%{q}%"
        filters.append(or_(Fact.value.ilike(pattern), Fact.name.ilike(pattern)))

    total = session.scalar(
        select(func.count())
        .select_from(Fact)
        .join(Document, Document.id == Fact.document_id)
        .where(*filters)
    )
    rows = session.execute(
        select(Fact, Document)
        .join(Document, Document.id == Fact.document_id)
        .where(*filters)
        .order_by(Fact.document_id, Fact.name, Fact.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return FactList(total=total or 0, facts=[fact_out(fact, document) for fact, document in rows])


@router.get("/facts/names", response_model=list[FactName])
def list_fact_names(project: ProjectDep, session: SessionDep):
    rows = session.execute(
        select(Fact.name, func.count())
        .join(Document, Document.id == Fact.document_id)
        .where(Fact.project_id == project.id, Document.deleted_at.is_(None))
        .group_by(Fact.name)
        .order_by(func.count().desc(), Fact.name)
    ).all()
    return [FactName(name=name, count=count) for name, count in rows]
