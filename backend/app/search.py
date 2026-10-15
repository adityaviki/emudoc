"""Hybrid search: keyword and vector rankings over passages, merged by reciprocal rank."""

import re
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import engine
from app.embeddings import Embedder, EmbeddingError
from app.postgres import server

# Reciprocal rank fusion constant: the usual value, which damps the gap between top ranks.
RRF_K = 60
# Private-use characters wrap the matched words in a headline; they cannot occur in documents.
HIT_START, HIT_STOP = "", ""

_NO_VECTOR_CTE = "SELECT NULL::bigint AS id, NULL::bigint AS rank WHERE false"
_VECTOR_CTE = """
    SELECT p.id, row_number() OVER (ORDER BY p.embedding <=> CAST(:qvec AS vector)) AS rank
    FROM passages p
    WHERE p.project_id = :project_id AND p.embedding IS NOT NULL {doc_filter}
    ORDER BY p.embedding <=> CAST(:qvec AS vector)
    LIMIT :candidates
"""
_QUERY = """
WITH langs AS (
    -- Each passage was indexed with a text search configuration; the query is parsed once per
    -- configuration in use and matched against the passages indexed with it. A project holds
    -- one or two, and the text index is probed once for each.
    SELECT DISTINCT p.language
    FROM passages p
    WHERE p.project_id = :project_id {doc_filter}
),
q AS (
    -- Any query word may match; ranking then favours passages that match more of them.
    SELECT l.language, to_tsquery(l.language,
        NULLIF(replace(plainto_tsquery(l.language, :query)::text, ' & ', ' | '), '')) AS tsq
    FROM langs l
),
kw AS (
    SELECT p.id, row_number() OVER (ORDER BY ts_rank_cd(p.tsv, q.tsq) DESC, p.id) AS rank
    FROM passages p
    JOIN q ON q.language = p.language
    WHERE p.project_id = :project_id AND p.tsv @@ q.tsq {doc_filter}
    ORDER BY ts_rank_cd(p.tsv, q.tsq) DESC, p.id
    LIMIT :candidates
),
vec AS ({vector_cte})
SELECT p.id, p.document_id, p.section_id, p.context, p.text, p.page_start, p.page_end,
       d.title, d.filename, d.doc_type,
       kw.rank AS keyword_rank, vec.rank AS vector_rank,
       COALESCE(1.0 / (:k + kw.rank), 0) + COALESCE(1.0 / (:k + vec.rank), 0) AS score,
       ts_headline(p.language, p.text, q.tsq,
           'MaxFragments=2, MaxWords=32, MinWords=12, StartSel={start}, StopSel={stop}') AS headline
FROM (SELECT id FROM kw UNION SELECT id FROM vec) hits
JOIN passages p ON p.id = hits.id
JOIN documents d ON d.id = p.document_id
JOIN q ON q.language = p.language
LEFT JOIN kw ON kw.id = p.id
LEFT JOIN vec ON vec.id = p.id
ORDER BY score DESC, p.id
LIMIT :limit
"""


@dataclass
class SearchHit:
    passage_id: int
    document_id: int
    document_title: str
    doc_type: str | None
    section_id: int
    context: str
    text: str
    # The best-matching excerpt as (text, is_match) segments.
    snippet: list[tuple[str, bool]]
    page_start: int | None
    page_end: int | None
    score: float
    keyword_rank: int | None
    vector_rank: int | None


@dataclass
class SearchResult:
    hits: list[SearchHit]
    # False when the embedding endpoint was unavailable and only keywords were used.
    semantic: bool


def hybrid_search(
    session: Session,
    embedder: Embedder,
    project_id: int,
    query: str,
    limit: int = 10,
    document_ids: list[int] | None = None,
) -> SearchResult:
    query = query.strip()
    if not query:
        return SearchResult([], semantic=True)

    try:
        vector = embedder.embed_query(query)
    except EmbeddingError:
        vector = None

    doc_filter = "AND p.document_id = ANY(:document_ids)" if document_ids else ""
    sql = _QUERY.format(
        doc_filter=doc_filter,
        vector_cte=_VECTOR_CTE.format(doc_filter=doc_filter) if vector else _NO_VECTOR_CTE,
        start=HIT_START,
        stop=HIT_STOP,
    )
    params = {
        "project_id": project_id,
        "query": query,
        "limit": limit,
        "candidates": max(limit * 4, 40),
        "k": RRF_K,
        "document_ids": document_ids or [],
    }
    if vector:
        params["qvec"] = "[" + ",".join(f"{value:.7g}" for value in vector) + "]"
        if _iterative_scan_supported():
            # Keep scanning the vector index until enough rows pass the project filter.
            session.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))

    rows = session.execute(text(sql), params).mappings().all()
    hits = [
        SearchHit(
            passage_id=row["id"],
            document_id=row["document_id"],
            document_title=row["title"] or row["filename"],
            doc_type=row["doc_type"],
            section_id=row["section_id"],
            context=row["context"],
            text=row["text"],
            snippet=_segments(row["headline"]) or _segments(row["text"][:300]),
            page_start=row["page_start"],
            page_end=row["page_end"],
            score=float(row["score"]),
            keyword_rank=row["keyword_rank"],
            vector_rank=row["vector_rank"],
        )
        for row in rows
    ]
    return SearchResult(hits, semantic=vector is not None)


_TABLE_RULE = re.compile(r"\|(?:\s*:?-{3,}:?\s*\|)+")
_TABLE_PIPES = re.compile(r"(?:\s*\|\s*)+")


def _segments(headline: str | None) -> list[tuple[str, bool]]:
    """Split a headline on its match markers into (text, is_match) segments."""
    if not headline:
        return []
    # A snippet is one line of prose: show a table's cells without its Markdown scaffolding.
    headline = _TABLE_PIPES.sub(" · ", _TABLE_RULE.sub("|", headline)).strip(" ·")
    segments: list[tuple[str, bool]] = []
    for index, part in enumerate(headline.split(HIT_START)):
        if index == 0:
            if part:
                segments.append((part, False))
            continue
        matched, _, rest = part.partition(HIT_STOP)
        if matched:
            segments.append((matched, True))
        if rest:
            segments.append((rest, False))
    return segments


def check_fts_language() -> None:
    """Refuse to serve when FTS_LANGUAGE names no text search configuration in the database.

    The name is written into every passage as it is indexed, so a wrong one would fail every
    upload in a background job rather than here, where the message can say what to set.
    """
    name = get_settings().fts_language
    with engine.connect() as connection:
        known = connection.scalars(text("SELECT cfgname FROM pg_ts_config ORDER BY 1")).all()
    if name in known:
        return
    raise RuntimeError(
        f"FTS_LANGUAGE is '{name}', but the database has no text search configuration of that "
        f"name. Set it to one of: {', '.join(known)}; then start the app again."
    )


@lru_cache
def _iterative_scan_supported() -> bool:
    """Iterative index scans arrived in pgvector 0.8. Production does not start without them
    (`check_database` in app/postgres.py); development searches without them."""
    with engine.connect() as connection:
        return server(connection).iterative_scans
