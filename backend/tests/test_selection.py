"""The documents chosen together in the library (app/selection.py): each indexed again or
indexed the rest of (app/api/documents.py), a choice refused whole when one of it is not the
project's. Their export is in test_exports.py."""

from sqlalchemy import delete, update

from app.jobs import BULK
from app.models import Document, Job
from app.selection import MAX_CHOSEN, NOT_CHOSEN
from tests.test_api import create_project, upload
from tests.test_jobs import all_jobs
from tests.test_library import files
from tests.test_partly_indexed import three
from tests.test_pipeline import stage_statuses


def reprocess_many(client, project_id, ids, from_stage=None):
    return client.post(
        f"/api/projects/{project_id}/documents/reprocess",
        json={"document_ids": list(ids), "from_stage": from_stage},
    )


def test_a_choice_is_of_the_project_s_documents_out_of_the_trash_or_refused_whole(client, tmp_path):
    project = create_project(client)
    other = create_project(client, "Other")
    a, gone = upload(client, project["id"], *files(tmp_path, ["a.txt", "gone.txt"]))["documents"]
    (elsewhere,) = upload(client, other["id"], *files(tmp_path, ["b.txt"]))["documents"]
    client.delete(f"/api/documents/{gone['id']}")

    for ids in ([a["id"], elsewhere["id"]], [a["id"], gone["id"]], [a["id"], 999_999]):
        indexed = reprocess_many(client, project["id"], ids, "parse")
        assert indexed.status_code == 404 and indexed.json()["detail"] == NOT_CHOSEN, ids
    # A choice holds one document at least, and a page of them at most.
    assert reprocess_many(client, project["id"], [], "parse").status_code == 422
    too_many = list(range(1, MAX_CHOSEN + 2))
    assert reprocess_many(client, project["id"], too_many, "parse").status_code == 422


def test_the_chosen_are_indexed_again_behind_fresh_uploads_and_one_being_indexed_is_left(
    client, session, tmp_path
):
    project = create_project(client)
    a, b, busy = upload(client, project["id"], *files(tmp_path, ["a.txt", "b.txt", "busy.txt"]))[
        "documents"
    ]
    session.execute(delete(Job))
    session.execute(update(Document).values(status="ready"))
    session.execute(update(Document).where(Document.id == busy["id"]).values(status="processing"))
    session.commit()

    response = reprocess_many(client, project["id"], [a["id"], b["id"], busy["id"]], "parse")
    assert response.status_code == 202, response.text
    shown = response.json()
    assert [(d["id"], d["status"]) for d in shown["documents"]] == [
        (a["id"], "queued"),
        (b["id"], "queued"),
    ]
    assert shown["skipped"] == 1
    jobs = all_jobs(session)
    assert sorted(job.payload["document_id"] for job in jobs) == [a["id"], b["id"]]
    assert {job.priority for job in jobs} == {BULK}
    assert client.get(f"/api/documents/{busy['id']}").json()["status"] == "processing"


def test_the_rest_of_the_chosen_runs_only_what_each_went_without(
    client, session, services, make_document, sample_pdf, sample_docx, sample_csv
):
    contract, letter, table = three(
        session, services, make_document, sample_pdf, sample_docx, sample_csv
    )
    response = reprocess_many(client, contract.project_id, [contract.id, letter.id, table.id])
    assert response.status_code == 202, response.text
    assert [d["id"] for d in response.json()["documents"]] == [letter.id, table.id]
    assert response.json()["skipped"] == 0
    for document in (contract, letter, table):
        session.refresh(document)
    assert contract.status == "ready"
    assert stage_statuses(letter)["enrich"] == "pending"
    assert stage_statuses(table) == {
        "scan": "done",
        "parse": "done",
        "structure": "done",
        "enrich": "done",
        "embed": "pending",
    }
    assert sorted(job.payload["document_id"] for job in all_jobs(session)) == sorted(
        [letter.id, table.id]
    )
