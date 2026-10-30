"""Hybrid search: keyword and vector rankings over passages, merged by reciprocal rank. The
passages of the documents in the project's trash (app/trash.py) are left out inside each
ranking, before it takes its candidates, so that they never crowd out the live ones.

A search is one project's (the library's, the agent's, the versioned API's), or several
projects' at once (the search of every project a member sees, app/api/search.py): then each
ranking runs once over all their passages and the two are fused once, so that a passage ranks by
how well it matches wherever it is, rather than each project's best few side by side."""

import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import engine
from app.embeddings import Embedder, EmbeddingError
from app.languages import known_languages
from app.postgres import server

log = logging.getLogger(__name__)

# Reciprocal rank fusion constant: the usual value, which damps the gap between top ranks.
RRF_K = 60
# How many of the nearest vectors the HNSW index keeps in view as it searches its graph
# (`hnsw.ef_search`): more finds more of the truly nearest passages, and takes longer.
EF_SEARCH = 200
# Private-use characters wrap the matched words in a headline; they cannot occur in documents.
HIT_START, HIT_STOP = "", ""

_NO_VECTOR_CTE = "SELECT NULL::bigint AS id, NULL::bigint AS rank WHERE false"
# The documents of the projects in the trash, from their partial index: few, and passed to each
# ranking as a list, so that its filter needs no join.
_TRASHED = "SELECT id FROM documents WHERE project_id {scope} AND deleted_at IS NOT NULL"
_VECTOR_CTE = """
    SELECT p.id, row_number() OVER (ORDER BY p.embedding <=> CAST(:qvec AS vector)) AS rank
    FROM passages p
    WHERE p.project_id {scope} AND p.embedding IS NOT NULL {doc_filter}
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
    WHERE p.project_id {scope} {doc_filter}
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
    WHERE p.project_id {scope} AND p.tsv @@ q.tsq {doc_filter}
    ORDER BY ts_rank_cd(p.tsv, q.tsq) DESC, p.id
    LIMIT :candidates
),
vec AS ({vector_cte})
SELECT p.id, p.project_id, p.document_id, p.section_id, p.context, p.text, p.page_start,
       p.page_end,
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
    project_id: int
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
    """The search of one project, within the documents given if any."""
    return search_projects(session, embedder, [project_id], query, limit, document_ids)


def search_projects(
    session: Session,
    embedder: Embedder,
    project_ids: list[int],
    query: str,
    limit: int = 10,
    document_ids: list[int] | None = None,
) -> SearchResult:
    """The search of the projects given, one ranking over all of them, within the documents
    given if any."""
    query = query.strip()
    if not query or not project_ids:
        return SearchResult([], semantic=True)
    # One project's search keeps the plain comparison every plan of it was measured with.
    one = len(project_ids) == 1
    scope = "= :project_id" if one else "= ANY(:project_ids)"
    where = f"project {project_ids[0]}" if one else f"{len(project_ids)} projects"

    try:
        vector = embedder.embed_query(query)
    except EmbeddingError as error:
        # The members see it on the status line and the agent in its tool's result; this line
        # is the operator's, since search goes on working, only worse.
        log.warning("A search in %s matched keywords only: %s", where, error)
        vector = None

    scoped = {"project_id": project_ids[0], "project_ids": project_ids}
    trashed = session.scalars(text(_TRASHED.format(scope=scope)), scoped).all()
    doc_filter = " ".join(
        [
            "AND p.document_id = ANY(:document_ids)" if document_ids else "",
            "AND p.document_id <> ALL(:trashed)" if trashed else "",
        ]
    )
    sql = _QUERY.format(
        scope=scope,
        doc_filter=doc_filter,
        vector_cte=(
            _VECTOR_CTE.format(scope=scope, doc_filter=doc_filter) if vector else _NO_VECTOR_CTE
        ),
        start=HIT_START,
        stop=HIT_STOP,
    )
    params = {
        **scoped,
        "query": query,
        "limit": limit,
        "candidates": max(limit * 4, 40),
        "k": RRF_K,
        "document_ids": document_ids or [],
        "trashed": list(trashed),
    }
    # Plan every search for its own projects. psycopg prepares a statement it has run five times
    # on a connection, and Postgres may then keep one plan for every project, made for a
    # project of the average size: in a table of many projects, that is a sort of the whole
    # project by distance, which made a search of a 75,000-passage project three times slower
    # (deploying.md, "Load"). The planner's own choice takes the vector index only for a
    # project of a few percent of the table, where its candidates never run short.
    session.execute(text("SET LOCAL plan_cache_mode = force_custom_plan"))
    if vector:
        params["qvec"] = "[" + ",".join(f"{value:.7g}" for value in vector) + "]"
        # At pgvector's default of 40, the vector index's candidates held 83 to 89 % of the 10
        # passages nearest the question in a project of 93,000, and missed the nearest in one
        # search of ten; at 200 they held 93 to 95 % and missed it in three of a hundred, for
        # 3 to 6 ms more in a search that took 200 (deploying.md, "Load").
        session.execute(text(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}"))
        if _iterative_scan_supported():
            # Keep scanning the vector index until enough rows pass the project filter.
            session.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))

    rows = session.execute(text(sql), params).mappings().all()
    hits = [
        SearchHit(
            passage_id=row["id"],
            project_id=row["project_id"],
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
        known = known_languages(connection)
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
