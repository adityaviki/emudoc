import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.config import get_settings
from app.indexing.pipeline import process_document
from app.main import create_app
from app.models import Passage, Project
from app.search import _segments, check_fts_language, hybrid_search
from tests.conftest import FakeEmbedder


def search(session, services, document, query, **kwargs):
    return hybrid_search(session, services.embedder, document.project_id, query, **kwargs)


def languages_of(session, document) -> set[str]:
    return set(session.scalars(select(Passage.language).where(Passage.document_id == document.id)))


def test_finds_the_passage_and_marks_the_matched_words(session, services, indexed):
    result = search(session, services, indexed, "termination notice")

    assert result.semantic is True
    top = result.hits[0]
    assert "ninety (90) days" in top.text
    assert top.document_title == "Master Services Agreement"
    assert top.context == "Master Services Agreement › 3. Termination"
    assert top.page_start == 2
    # Found by both rankings, so it carries a rank from each.
    assert top.keyword_rank == 1 and top.vector_rank is not None
    matched = {text.lower() for text, is_match in top.snippet if is_match}
    assert "notice" in matched or "terminate" in matched


def test_any_query_word_may_match(session, services, indexed):
    # "zebra" appears nowhere; the passage still ranks on the word that does.
    result = search(session, services, indexed, "zebra consulting")
    assert any("consulting" in hit.text.lower() for hit in result.hits)


def test_section_titles_are_searchable_through_the_path(session, services, indexed):
    # "Interpretation" appears in the heading; the passage below it is found by its path.
    result = search(session, services, indexed, "interpretation")
    assert result.hits[0].context.endswith("1.1 Interpretation")


def test_results_never_cross_projects(
    session, services, account, make_document, indexed, sample_docx
):
    other = Project(org_id=account.organization.id, name="Other")
    session.add(other)
    session.flush()
    policy = make_document(sample_docx, project=other)
    process_document(policy.id, services)

    here = search(session, services, indexed, "flights receipts termination")
    there = search(session, services, policy, "flights receipts termination")
    assert here.hits and {hit.document_id for hit in here.hits} == {indexed.id}
    assert there.hits and {hit.document_id for hit in there.hits} == {policy.id}


def test_each_search_is_planned_for_its_project(session, services, indexed):
    # psycopg prepares a statement it has run five times on a connection, and Postgres may
    # then keep one plan for every project, made for none of them: a search of a large
    # project would sort all its passages, one of a small project scan the vector index for
    # candidates that run short. Behind a pooler nothing is prepared, and nothing is held.
    for _ in range(15):
        assert search(session, services, indexed, "termination notice").hits
    held = session.scalar(
        text(
            "SELECT coalesce(sum(generic_plans), 0) FROM pg_prepared_statements"
            " WHERE statement LIKE '%WITH langs AS%'"
        )
    )
    assert held == 0


def test_document_filter(session, services, make_document, indexed, sample_docx):
    project = session.get(Project, indexed.project_id)
    policy = make_document(sample_docx, project=project)
    process_document(policy.id, services)

    everything = search(session, services, indexed, "days")
    assert {hit.document_id for hit in everything.hits} == {indexed.id, policy.id}
    only_policy = search(session, services, indexed, "days", document_ids=[policy.id])
    assert {hit.document_id for hit in only_policy.hits} == {policy.id}


def test_each_passage_is_matched_in_the_language_it_was_indexed_with(
    session, services, make_document, indexed, sample_docx, monkeypatch
):
    # The policy is indexed with the `simple` configuration, which keeps words as they are,
    # into the project that already holds the contract indexed in English.
    monkeypatch.setattr(get_settings(), "fts_language", "simple")
    policy = make_document(sample_docx, project=session.get(Project, indexed.project_id))
    process_document(policy.id, services)
    assert languages_of(session, policy) == {"simple"}
    assert languages_of(session, indexed) == {"english"}

    services.embedder.fail = True  # keywords only, so every hit below is a text match
    # English stemming still applies to the contract: "terminate" finds "termination".
    assert "ninety (90) days" in search(session, services, indexed, "terminate").hits[0].text
    # The policy is parsed as `simple`, so only the exact word finds "Submit receipts".
    assert search(session, services, indexed, "receipt").hits == []
    hits = search(session, services, indexed, "receipts").hits
    assert [hit.document_id for hit in hits] == [policy.id]
    assert [text for text, is_match in hits[0].snippet if is_match] == ["receipts"]
    # Restricting the search to one document only considers that document's language.
    assert search(session, services, indexed, "receipts", document_ids=[indexed.id]).hits == []


def test_app_refuses_a_text_search_configuration_the_database_lacks(services, monkeypatch):
    check_fts_language()

    monkeypatch.setattr(get_settings(), "fts_language", "klingon")
    with pytest.raises(RuntimeError, match="FTS_LANGUAGE is 'klingon'") as error:
        check_fts_language()
    assert "english" in str(error.value) and "simple" in str(error.value)
    with pytest.raises(RuntimeError, match="FTS_LANGUAGE"):
        with TestClient(create_app(services=services)):
            pass


def test_keyword_search_survives_an_embedding_outage(session, services, indexed, caplog):
    services.embedder.fail = True
    with caplog.at_level("WARNING", logger="app.search"):
        result = search(session, services, indexed, "termination notice")
    assert result.semantic is False
    assert "ninety (90) days" in result.hits[0].text
    assert result.hits[0].vector_rank is None
    # The operator hears of it, without the query.
    assert caplog.messages == [
        f"A search in project {indexed.project_id} matched keywords only: "
        f"{FakeEmbedder.UNREACHABLE}"
    ]


def test_vector_ranking_alone_can_return_hits(session, services, indexed):
    # Only stop words: the keyword query is empty, so every hit comes from the vectors.
    result = search(session, services, indexed, "the of and")
    assert result.hits
    assert all(hit.keyword_rank is None and hit.vector_rank for hit in result.hits)
    # With no matched words there is no headline; the snippet falls back to the passage start.
    assert all(not is_match for hit in result.hits for _, is_match in hit.snippet)


def test_limit_and_blank_query(session, services, indexed):
    assert len(search(session, services, indexed, "agreement services", limit=2).hits) == 2
    assert search(session, services, indexed, "   ").hits == []


def test_segments_split_on_match_markers():
    assert _segments("give notice in writing.") == [
        ("give ", False),
        ("notice", True),
        (" in ", False),
        ("writing", True),
        (".", False),
    ]
    assert _segments(None) == []


def test_snippets_show_table_cells_without_markdown_scaffolding():
    headline = "| Region | Daily \ue000allowance\ue001 |\n| --- | --- |\n| Europe | EUR 60 |"
    assert _segments(headline) == [
        ("Region · Daily ", False),
        ("allowance", True),
        (" · Europe · EUR 60", False),
    ]
