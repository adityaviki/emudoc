"""Read-side endpoints over a project's indexes: search and facts. Each is cheap, and none is
bounded by the plan, so the router holds every caller to RATE_SEARCHES a minute."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.api.deps import ProjectDep, ServicesDep, SessionDep, limited
from app.document_filter import BACKWARDS, matching
from app.fact_checks import fact_out
from app.indexing.pipeline import Services
from app.models import Document, Fact, Folder, Project
from app.ratelimit import SEARCHES
from app.schemas import (
    DocumentFilter,
    DocumentTypeOut,
    FactList,
    FactName,
    SearchHitOut,
    SearchIn,
    SearchOut,
    SnippetSegment,
)
from app.search import SearchHit, SearchResult, hybrid_search

router = APIRouter(
    prefix="/projects/{project_id}", tags=["query"], dependencies=[Depends(limited(SEARCHES))]
)


@router.get("/search", response_model=SearchOut)
def search(
    project: ProjectDep,
    session: SessionDep,
    services: ServicesDep,
    asked: Annotated[SearchIn, Query()],
):
    """Search the project's passages, within one document and the documents a filter matches if
    asked: the library's search in a folder, with what a member chose to narrow it by, and
    within what earlier searches found."""
    chosen = DocumentFilter.model_validate(
        asked.model_dump(include=set(DocumentFilter.model_fields))
    )
    return searched(project, session, services, asked.q, asked.limit, asked.document_id, chosen)


def searched(
    project: Project,
    session: Session,
    services: Services,
    q: str,
    limit: int,
    document_id: int | None,
    chosen: DocumentFilter,
) -> SearchOut:
    """The search of the documents the filter matches, or of the whole project for none (v1's
    search too, by its folder alone). A folder of another project is 404, as the filter's; one
    in the trash matches nothing."""
    if chosen.folder_id is not None:
        held_by = session.scalar(select(Folder.project_id).where(Folder.id == chosen.folder_id))
        if held_by != project.id:
            raise HTTPException(404, "Folder not found")
    if chosen.after and chosen.before and chosen.after > chosen.before:
        raise HTTPException(400, BACKWARDS)
    document_ids = None
    if document_id is not None or chosen != DocumentFilter():
        conditions = matching(session, project.id, chosen)
        if document_id is not None:
            conditions.append(Document.id == document_id)
        # The documents' ids first, as a folder's always were: how many a filter takes is the
        # project's, so the statement is planned for it (database.md).
        session.execute(text("SET LOCAL plan_cache_mode = force_custom_plan"))
        document_ids = list(
            session.scalars(
                select(Document.id).where(
                    Document.project_id == project.id, Document.deleted_at.is_(None), *conditions
                )
            )
        )
    if document_ids == []:
        # Nothing to search: no filter at all would mean everything.
        result = SearchResult([], semantic=True)
    else:
        result = hybrid_search(session, services.embedder, project.id, q, limit, document_ids)
    return SearchOut(
        semantic=result.semantic, hits=[SearchHitOut(**hit_fields(hit)) for hit in result.hits]
    )


def hit_fields(hit: SearchHit) -> dict:
    """A passage found as the page shows it, for a search of one project or of several
    (app/api/search.py)."""
    return {
        "passage_id": hit.passage_id,
        "document_id": hit.document_id,
        "document_title": hit.document_title,
        "doc_type": hit.doc_type,
        "context": hit.context,
        "snippet": [SnippetSegment(text=text, match=match) for text, match in hit.snippet],
        "page_start": hit.page_start,
        "page_end": hit.page_end,
        "score": hit.score,
        "keyword_rank": hit.keyword_rank,
        "vector_rank": hit.vector_rank,
    }


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


@router.get("/document-types", response_model=list[DocumentTypeOut])
def list_document_types(project: ProjectDep, session: SessionDep):
    """The types of the project's documents out of the trash, as the model read them or a member
    set them (app/details.py), the most common first: what the search's filter or a filing rule
    offers to choose from."""
    rows = session.execute(
        select(Document.doc_type, func.count())
        .where(
            Document.project_id == project.id,
            Document.deleted_at.is_(None),
            Document.doc_type.is_not(None),
        )
        .group_by(Document.doc_type)
        .order_by(func.count().desc(), Document.doc_type)
    ).all()
    return [DocumentTypeOut(type=doc_type, count=count) for doc_type, count in rows]
