"""The documents chosen together in the library (app/selection.py): tags put on and taken off
every one at once (app/api/tags.py), and each indexed again or indexed the rest of
(app/api/documents.py), a choice refused whole when one of it is not the project's. Their export
is in test_exports.py."""

from sqlalchemy import delete, update

from app.jobs import BULK
from app.models import Document, Job
from app.selection import MAX_CHOSEN, NOT_CHOSEN
from app.tags import BOTH_WAYS, NOT_THE_PROJECTS
from tests.test_api import create_project, upload
from tests.test_jobs import all_jobs
from tests.test_library import files
from tests.test_partly_indexed import three
from tests.test_pipeline import stage_statuses
from tests.test_tags import make_tag, tag


def tag_many(client, project_id, ids, add=(), remove=()):
    return client.post(
        f"/api/projects/{project_id}/documents/tags",
        json={"document_ids": list(ids), "add": list(add), "remove": list(remove)},
    )


def tags_of(client, *documents) -> list[list[int]]:
    return [client.get(f"/api/documents/{d['id']}").json()["tag_ids"] for d in documents]


def reprocess_many(client, project_id, ids, from_stage=None):
    return client.post(
        f"/api/projects/{project_id}/documents/reprocess",
        json={"document_ids": list(ids), "from_stage": from_stage},
    )


def test_tags_go_on_and_come_off_every_chosen_document_and_the_rest_stay(client, tmp_path):
    project = create_project(client)
    a, b, c = upload(client, project["id"], *files(tmp_path, ["a.txt", "b.txt", "c.txt"]))[
        "documents"
    ]
    urgent = make_tag(client, project["id"], "Urgent")
    paid = make_tag(client, project["id"], "Paid")
    archive = make_tag(client, project["id"], "Archive")
    tag(client, a["id"], urgent["id"])
    tag(client, b["id"], paid["id"], urgent["id"])

    changed = tag_many(
        client, project["id"], [a["id"], b["id"], c["id"]], [archive["id"]], [urgent["id"]]
    )
    assert changed.status_code == 200, changed.text
    # Paid, which only one carried and the change named neither way, stays where it was; what is
    # put on goes after what each keeps.
    assert [d["tag_ids"] for d in changed.json()] == [
        [archive["id"]],
        [paid["id"], archive["id"]],
        [archive["id"]],
    ]
    assert tags_of(client, a, b, c) == [d["tag_ids"] for d in changed.json()]
    # A tag put on again is not put on twice, nor moved.
    again = tag_many(client, project["id"], [b["id"], b["id"]], [paid["id"], paid["id"]])
    assert [d["tag_ids"] for d in again.json()] == [[paid["id"], archive["id"]]]


def test_a_document_without_room_refuses_the_whole_change_by_its_name(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr("app.tags.MAX_DOCUMENT_TAGS", 2)
    project = create_project(client)
    full, empty = upload(client, project["id"], *files(tmp_path, ["full.txt", "empty.txt"]))[
        "documents"
    ]
    one, two, more = (make_tag(client, project["id"], name)["id"] for name in "ABC")
    tag(client, full["id"], one, two)

    refused = tag_many(client, project["id"], [full["id"], empty["id"]], [more])
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("“full.txt” would carry more than 2 tags")
    assert tags_of(client, full, empty) == [[one, two], []]
    # Taking one off in the same change makes the room.
    swapped = tag_many(client, project["id"], [full["id"], empty["id"]], [more], [one])
    assert swapped.status_code == 200, swapped.text
    assert tags_of(client, full, empty) == [[two, more], [more]]


def test_a_choice_is_of_the_project_s_documents_out_of_the_trash_or_refused_whole(client, tmp_path):
    project = create_project(client)
    other = create_project(client, "Other")
    a, gone = upload(client, project["id"], *files(tmp_path, ["a.txt", "gone.txt"]))["documents"]
    (elsewhere,) = upload(client, other["id"], *files(tmp_path, ["b.txt"]))["documents"]
    client.delete(f"/api/documents/{gone['id']}")
    paid = make_tag(client, project["id"], "Paid")["id"]
    theirs = make_tag(client, other["id"], "Theirs")["id"]

    for ids in ([a["id"], elsewhere["id"]], [a["id"], gone["id"]], [a["id"], 999_999]):
        tagged = tag_many(client, project["id"], ids, [paid])
        assert tagged.status_code == 404 and tagged.json()["detail"] == NOT_CHOSEN, ids
        indexed = reprocess_many(client, project["id"], ids, "parse")
        assert indexed.status_code == 404 and indexed.json()["detail"] == NOT_CHOSEN, ids
    foreign = tag_many(client, project["id"], [a["id"]], [theirs])
    assert foreign.status_code == 404 and foreign.json()["detail"] == NOT_THE_PROJECTS
    both = tag_many(client, project["id"], [a["id"]], [paid], [paid])
    assert both.status_code == 422 and both.json()["detail"] == BOTH_WAYS
    assert tags_of(client, a) == [[]]
    # A choice holds one document at least, and a page of them at most.
    assert tag_many(client, project["id"], [], [paid]).status_code == 422
    too_many = list(range(1, MAX_CHOSEN + 2))
    assert tag_many(client, project["id"], too_many, [paid]).status_code == 422
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
