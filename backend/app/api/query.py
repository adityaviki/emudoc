"""Read-side endpoints over a project's indexes: search, facts and the agent chat."""

import json
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select

from app.agent.loop import run_agent
from app.api.deps import ProjectDep, ServicesDep, SessionDep
from app.api.documents import fact_out
from app.db import SessionLocal
from app.models import Document, Fact, Project
from app.schemas import (
    ChatRequest,
    FactList,
    FactName,
    SearchHitOut,
    SearchOut,
    SnippetSegment,
)
from app.search import hybrid_search

router = APIRouter(prefix="/projects/{project_id}", tags=["query"])


@router.get("/search", response_model=SearchOut)
def search(
    project: ProjectDep,
    session: SessionDep,
    services: ServicesDep,
    q: str,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    document_id: int | None = None,
):
    result = hybrid_search(
        session,
        services.embedder,
        project.id,
        q,
        limit,
        [document_id] if document_id else None,
    )
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
    filters = [Fact.project_id == project.id]
    if name:
        filters.append(Fact.name == name)
    if document_id:
        filters.append(Fact.document_id == document_id)
    if q:
        pattern = f"%{q}%"
        filters.append(or_(Fact.value.ilike(pattern), Fact.name.ilike(pattern)))

    total = session.scalar(select(func.count()).select_from(Fact).where(*filters))
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
        .where(Fact.project_id == project.id)
        .group_by(Fact.name)
        .order_by(func.count().desc(), Fact.name)
    ).all()
    return [FactName(name=name, count=count) for name, count in rows]


@router.post("/chat")
def chat(project: ProjectDep, body: ChatRequest, services: ServicesDep):
    """Answer the last user message, streamed as server-sent events."""
    messages = [
        {"role": message.role, "content": message.content}
        for message in body.messages
        if message.content.strip()
    ]
    # The API requires the conversation to open with a user message.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    project_id = project.id

    def events() -> Iterator[str]:
        if not messages or messages[-1]["role"] != "user":
            yield _sse({"type": "error", "message": "Send a question to get an answer."})
            return
        # The request's session closes when this endpoint returns; the stream outlives it.
        with SessionLocal() as session:
            current = session.get(Project, project_id)
            for event in run_agent(session, services, current, messages):
                yield _sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"
