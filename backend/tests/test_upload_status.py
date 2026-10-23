"""What the library follows of an upload once its files are sent: how many of its documents are
ready, partly indexed among them, being indexed, waiting for a retry and failed, and why those
failed, over the uploads of one batch of the page's requests (`upload_status` in
app/api/documents.py)."""

from datetime import datetime

from sqlalchemy import select, text

from app.api.documents import MAX_FOLLOWED
from app.models import Document, Job
from app.scanning import quarantine_message
from tests.conftest import EICAR, EICAR_NAME, SCANNER_DOWN, pageless_pdf
from tests.test_api import create_project, upload
from tests.test_jobs import RATE_LIMITED
from tests.test_notices import run_next


def status(client, project_id: int, *upload_ids: int) -> dict:
    response = client.get(f"/api/projects/{project_id}/uploads", params={"ids": list(upload_ids)})
    assert response.status_code == 200, response.text
    return response.json()


def standing(shown: dict) -> dict:
    """The counts alone, by name."""
    return {
        name: shown[name]
        for name in ("documents", "ready", "partly_indexed", "indexing", "retrying", "failed")
    }


def counted(**counts: int) -> dict:
    return {
        "documents": 0,
        "ready": 0,
        "partly_indexed": 0,
        "indexing": 0,
        "retrying": 0,
        "failed": 0,
    } | counts


def text_file(name: str, words: str = "A note about the delivery of 12 pallets.") -> tuple:
    return ("files", (name, words.encode()))


def sent(client, project_id: int, *files: tuple) -> dict:
    response = client.post(f"/api/projects/{project_id}/documents", files=list(files))
    assert response.status_code == 201, response.text
    return response.json()


def test_an_upload_is_followed_until_each_document_is_ready_or_failed_with_why(
    client, session, services, sample_pdf, tmp_path
):
    project = create_project(client)
    # One batch of two requests, each an upload of its own, as the page sends a large one.
    first = upload(client, project["id"], sample_pdf, pageless_pdf(tmp_path / "empty.pdf"))
    second = sent(client, project["id"], text_file("note.txt"))
    ids = (first["upload_id"], second["upload_id"])
    assert all(isinstance(upload_id, int) for upload_id in ids) and ids[0] != ids[1]
    assert {document["id"] for document in first["documents"]} == set(
        session.scalars(select(Document.id).where(Document.upload_id == ids[0]))
    )

    shown = status(client, project["id"], *ids)
    assert standing(shown) == counted(documents=3, indexing=3)
    assert shown["retry_at"] is None and shown["failures"] == []

    run_next(services)
    assert standing(status(client, project["id"], *ids)) == counted(
        documents=3, ready=1, indexing=2
    )
    run_next(services)
    run_next(services)
    shown = status(client, project["id"], *ids)
    assert standing(shown) == counted(documents=3, ready=2, failed=1)
    # The one that failed, named as the library names it, with the sentence its tab shows.
    broken = session.get(Document, first["documents"][1]["id"])
    assert shown["failures"] == [
        {"id": broken.id, "filename": "empty.pdf", "reason": broken.error, "quarantined": False}
    ]
    assert broken.error

    # One upload of the two alone is what was asked about.
    assert standing(status(client, project["id"], ids[1])) == counted(documents=1, ready=1)

    # "Index again" puts it back among those being indexed.
    assert client.post(f"/api/documents/{broken.id}/reprocess").status_code == 202
    assert standing(status(client, project["id"], *ids)) == counted(
        documents=3, ready=2, indexing=1
    )


def test_a_request_that_keeps_no_file_is_no_upload(client):
    project = create_project(client)
    shown = sent(client, project["id"], ("files", ("tool.exe", b"MZ")))
    assert shown["upload_id"] is None and shown["documents"] == []
    assert shown["rejected"][0]["filename"] == "tool.exe"


def test_a_retry_waiting_is_counted_apart_with_when_the_soonest_is_due(
    client, session, services, sample_pdf
):
    project = create_project(client)
    # The model rate limits the summaries: the contract is ready, but its enrich waits.
    services.llm.failure = RATE_LIMITED
    contract = upload(client, project["id"], sample_pdf)
    run_next(services)
    shown = status(client, project["id"], contract["upload_id"])
    assert standing(shown) == counted(documents=1, retrying=1)
    enrich_retry = session.scalars(
        select(Job).where(Job.status == "queued").order_by(Job.id.desc())
    ).first()
    assert datetime.fromisoformat(shown["retry_at"]) == enrich_retry.run_at

    # The scanner is down for a note sent later: a required stage waits, and the note stays
    # queued. The contract's retry is due first, and stays the soonest of the two.
    services.scanner.outage = SCANNER_DOWN
    note = sent(client, project["id"], text_file("note.txt"))
    run_next(services)
    assert (
        session.scalar(select(Document.status).where(Document.upload_id == note["upload_id"]))
        == "queued"
    )
    note_retry = session.scalars(
        select(Job).where(Job.status == "queued").order_by(Job.id.desc())
    ).first()
    assert note_retry.id != enrich_retry.id and note_retry.run_at > enrich_retry.run_at
    both = (contract["upload_id"], note["upload_id"])
    shown = status(client, project["id"], *both)
    assert standing(shown) == counted(documents=2, retrying=2)
    assert datetime.fromisoformat(shown["retry_at"]) == enrich_retry.run_at
    shown = status(client, project["id"], note["upload_id"])
    assert datetime.fromisoformat(shown["retry_at"]) == note_retry.run_at

    # Once both ran again with the services back, nothing waits.
    services.llm.failure = None
    services.scanner.outage = None
    session.execute(text("UPDATE jobs SET run_at = now() WHERE status = 'queued'"))
    session.commit()
    run_next(services)
    run_next(services)
    shown = status(client, project["id"], *both)
    assert standing(shown) == counted(documents=2, ready=2)
    assert shown["retry_at"] is None


def test_a_partly_indexed_document_is_ready_and_counted_as_partly_indexed(
    client, services, sample_pdf
):
    project = create_project(client)
    services.llm.available = False
    contract = upload(client, project["id"], sample_pdf)
    run_next(services)
    assert standing(status(client, project["id"], contract["upload_id"])) == counted(
        documents=1, ready=1, partly_indexed=1
    )


def test_a_quarantined_document_is_listed_as_set_apart(client, services):
    project = create_project(client)
    flagged = sent(client, project["id"], ("files", ("note.txt", b"Before. " + EICAR)))
    run_next(services)
    shown = status(client, project["id"], flagged["upload_id"])
    assert standing(shown) == counted(documents=1, failed=1)
    assert shown["failures"] == [
        {
            "id": flagged["documents"][0]["id"],
            "filename": "note.txt",
            "reason": quarantine_message(EICAR_NAME),
            "quarantined": True,
        }
    ]


def test_only_the_project_s_uploads_and_its_documents_out_of_the_trash_count(client, services):
    project = create_project(client)
    other = create_project(client, "Archive")
    here = sent(client, project["id"], text_file("a.txt"), text_file("b.txt", "Another note."))
    there = sent(client, other["id"], text_file("c.txt"))

    # Another project's upload counts nothing in this one.
    assert standing(status(client, project["id"], there["upload_id"])) == counted()
    assert standing(status(client, project["id"], here["upload_id"], there["upload_id"])) == (
        counted(documents=2, indexing=2)
    )
    # A document put in the trash is left out, as its upload's notice leaves it out.
    trashed = here["documents"][0]["id"]
    assert client.delete(f"/api/documents/{trashed}").status_code == 204
    assert standing(status(client, project["id"], here["upload_id"])) == counted(
        documents=1, indexing=1
    )


def test_the_failures_listed_are_bounded_and_the_ids_asked_about_too(
    client, services, monkeypatch, tmp_path
):
    project = create_project(client)
    made = upload(client, project["id"], pageless_pdf(tmp_path / "b.pdf"))
    flagged = sent(client, project["id"], text_file("a.txt", "Before. " + EICAR.decode()))
    for _ in range(2):
        run_next(services)
    monkeypatch.setattr("app.api.documents.LISTED", 1)
    shown = status(client, project["id"], made["upload_id"], flagged["upload_id"])
    assert standing(shown) == counted(documents=2, failed=2)
    # The first by name alone.
    assert [failure["filename"] for failure in shown["failures"]] == ["a.txt"]

    path = f"/api/projects/{project['id']}/uploads"
    assert client.get(path).status_code == 422
    too_many = {"ids": list(range(1, MAX_FOLLOWED + 2))}
    assert client.get(path, params=too_many).status_code == 422
    assert client.get(path, params={"ids": list(range(1, MAX_FOLLOWED + 1))}).status_code == 200
