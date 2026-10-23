"""A partly indexed document: ready without summaries and facts or without its vectors, found by
its words meanwhile, and indexed in full by "Index the rest", alone or with the others of its
project (`unfinished_stages` in app/indexing/pipeline.py, `reprocess_project` in
app/api/projects.py)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, text

from app.agent.tools import ListDocumentsInput, ToolContext, list_documents
from app.indexing.pipeline import (
    StageFailure,
    await_retry,
    process_document,
    unfinished_stages,
)
from app.jobs import BULK, claim
from app.models import Job, Passage, Project
from app.worker import MAX_RETRIES
from tests.conftest import FakeEmbedder
from tests.test_jobs import INDEX, all_jobs, run_index
from tests.test_pipeline import stage_statuses


def project_of(client, document) -> dict:
    return client.get(f"/api/projects/{document.project_id}").json()


def counts(client, document) -> tuple[int, int, int]:
    shown = project_of(client, document)
    return shown["document_count"], shown["partly_indexed_count"], shown["unenriched_count"]


def three(session, services, make_document, sample_pdf, sample_docx, sample_csv):
    """A project of a contract indexed in full, a letter indexed while the model was out and a
    table while the embeddings endpoint was, each run played by hand to its end."""
    contract = make_document(sample_pdf)
    project = session.get(Project, contract.project_id)
    process_document(contract.id, services)
    services.llm.available = False
    letter = make_document(sample_docx, project=project)
    process_document(letter.id, services)
    services.llm.available = True
    services.embedder.fail = True
    table = make_document(sample_csv, project=project)
    process_document(table.id, services)
    services.embedder.fail = False
    # Their runs were played without the worker, so their jobs are done with.
    session.execute(delete(Job))
    session.commit()
    for document in (contract, letter, table):
        session.refresh(document)
    return contract, letter, table


def test_a_document_ready_without_an_optional_stage_is_partly_indexed(
    client, session, services, make_document, sample_pdf, sample_docx, sample_csv
):
    contract, letter, table = three(
        session, services, make_document, sample_pdf, sample_docx, sample_csv
    )
    assert (contract.status, letter.status, table.status) == ("ready", "ready", "ready")
    assert unfinished_stages(contract) == []
    assert unfinished_stages(letter) == ["enrich"]
    assert unfinished_stages(table) == ["embed"]
    # The project counts both, and the one that waits for the model as well.
    assert counts(client, contract) == (3, 2, 1)
    (listed,) = client.get("/api/projects").json()
    assert (listed["partly_indexed_count"], listed["unenriched_count"]) == (2, 1)

    # A table without vectors is found by its words all the same.
    assert all(
        passage.embedding is None
        for passage in session.query(Passage).filter(Passage.document_id == table.id)
    )
    found = client.get(f"/api/projects/{table.project_id}/search", params={"q": "London"}).json()
    assert table.id in {hit["document_id"] for hit in found["hits"]}

    # The agent reads what each lacks, to look for it by searching and reading instead.
    shown = list_documents(
        ToolContext(session, services.embedder, contract.project_id), ListDocumentsInput()
    )
    lines = {line.split(" · ")[0]: line for line in shown.splitlines() if line.startswith("doc ")}
    assert "partly indexed" not in lines[f"doc {contract.id}"]
    assert lines[f"doc {letter.id}"].endswith(" · partly indexed, no summaries or facts")
    assert lines[f"doc {table.id}"].endswith(" · partly indexed, found by keyword search alone")

    # While a retry waits for the stage, the retry runs it: nothing is for the member to do.
    await_retry(
        table,
        StageFailure("embed", FakeEmbedder.UNREACHABLE, transient=True),
        f"Retrying in 1 minute (1 of {MAX_RETRIES}).",
        datetime.now(UTC) + timedelta(minutes=1),
    )
    session.commit()
    assert unfinished_stages(table) == []
    assert counts(client, contract) == (3, 1, 1)
    # Nor is a document being indexed, or one that failed.
    letter.status = "queued"
    session.commit()
    assert unfinished_stages(letter) == []
    assert counts(client, contract) == (3, 0, 0)


def test_index_the_rest_runs_only_what_each_partly_indexed_document_went_without(
    client, session, services, make_document, sample_pdf, sample_docx, sample_csv
):
    contract, letter, table = three(
        session, services, make_document, sample_pdf, sample_docx, sample_csv
    )
    calls = len(services.llm.calls)
    embedded = contract.usage["embed"]["inputs"]

    assert client.post(f"/api/projects/{contract.project_id}/reprocess").json() == {"queued": 2}
    for document in (contract, letter, table):
        session.refresh(document)
    # The contract is left as it was; the letter runs from enrich, whose new title moves the
    # paths that embed reads, and the table from embed alone.
    assert contract.status == "ready"
    assert stage_statuses(letter) == {
        "scan": "done",
        "parse": "done",
        "structure": "done",
        "enrich": "pending",
        "embed": "pending",
    }
    assert stage_statuses(table) == {
        "scan": "done",
        "parse": "done",
        "structure": "done",
        "enrich": "done",
        "embed": "pending",
    }
    jobs = all_jobs(session)
    assert sorted(job.payload["document_id"] for job in jobs) == sorted([letter.id, table.id])
    assert {job.priority for job in jobs} == {BULK}

    for _ in jobs:
        run_index(claim("w:1", INDEX), services)
    for document in (contract, letter, table):
        session.refresh(document)
        assert set(stage_statuses(document).values()) == {"done"}, document.filename
    assert counts(client, contract) == (3, 0, 0)
    # The model was asked about the letter alone, and the contract embedded once.
    assert len(services.llm.calls) > calls
    assert contract.usage["embed"]["inputs"] == embedded
    assert letter.summary is not None

    # Asked for a stage, every document runs from it, as "Index again" does.
    reprocessed = client.post(
        f"/api/projects/{contract.project_id}/reprocess", params={"from_stage": "embed"}
    )
    assert reprocessed.json() == {"queued": 3}


def test_a_document_partly_indexed_by_an_outage_is_offered_the_rest_once_retrying_ends(
    client, session, services, make_document, sample_pdf
):
    document = make_document(sample_pdf)
    services.embedder.fail = True
    run_index(claim("w:1", INDEX), services)
    session.refresh(document)
    # Waiting for its retry, it is not partly indexed yet.
    assert document.status == "ready" and unfinished_stages(document) == []

    session.execute(
        text("UPDATE jobs SET run_at = now(), payload = payload || :retry WHERE status = 'queued'"),
        {"retry": f'{{"retry": {MAX_RETRIES}}}'},
    )
    session.commit()
    run_index(claim("w:1", INDEX), services)
    session.refresh(document)
    assert document.status == "ready" and unfinished_stages(document) == ["embed"]
    # Its stage says what fixes it: the rest, not the whole document again.
    assert document.stages["embed"]["detail"] == (
        f"{FakeEmbedder.UNREACHABLE} Tried {MAX_RETRIES + 1} times over 15 minutes; use "
        '"Index the rest" once the service is back.'
    )
    assert counts(client, document) == (1, 1, 0)

    services.embedder.fail = False
    shown = client.post(f"/api/documents/{document.id}/reprocess", params={"from_stage": "embed"})
    assert shown.status_code == 202
    assert shown.json()["stages"]["enrich"]["status"] == "done"
    run_index(claim("w:1", INDEX), services)
    session.refresh(document)
    assert set(stage_statuses(document).values()) == {"done"}
    assert counts(client, document) == (1, 0, 0)
