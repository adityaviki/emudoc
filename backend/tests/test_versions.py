"""A document's versions: a new file read in the earlier one's place, which is kept to open."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import engine
from app.entitlements import month_of, pages_this_month
from app.indexing.pipeline import process_document
from app.maintenance import sweep_storage
from app.models import Document, DocumentVersion, Project
from app.storage import document_prefix
from app.versions import BEING_INDEXED, SAME_FILE
from tests.conftest import columns_pdf
from tests.test_api import create_project, upload
from tests.test_audit import actions, rows_of_action
from tests.test_entitlements import member, plan  # noqa: F401
from tests.test_exports import archive_of, ask, run_export
from tests.test_trash import hits

OLD = b"The service fee is 100 EUR a month, paid on the first day of each month.\n"
NEW = b"The service fee is 250 EUR a month. Deliveries arrive within five working days.\n"


def give(client, document_id, name, content: bytes):
    """Give the document a new version: the file `name` with `content`."""
    return client.post(f"/api/documents/{document_id}/versions", files={"file": (name, content)})


def given(client, services, document_id, name, content: bytes) -> dict:
    """Give the document a new version and read it, as the worker would."""
    response = give(client, document_id, name, content)
    assert response.status_code == 201, response.text
    process_document(document_id, services)
    return client.get(f"/api/documents/{document_id}").json()


def versions(client, document_id) -> list[dict]:
    response = client.get(f"/api/documents/{document_id}/versions")
    assert response.status_code == 200, response.text
    return response.json()


def text_document(client, services, project_id, content: bytes = OLD, name="terms.txt") -> dict:
    response = client.post(
        f"/api/projects/{project_id}/documents", files=[("files", (name, content))]
    )
    assert response.status_code == 201, response.text
    (document,) = response.json()["documents"]
    process_document(document["id"], services)
    return client.get(f"/api/documents/{document['id']}").json()


def test_a_new_version_is_read_in_place_of_the_earlier_one_which_stays_to_open(
    client, services, session, account
):
    project = create_project(client)
    document = text_document(client, services, project["id"])
    assert document["version"] == 1
    assert hits(client, project["id"], "first day") == {document["id"]}

    response = give(client, document["id"], "terms-2.txt", NEW)
    assert response.status_code == 201, response.text
    queued = response.json()
    # The same document, holding the new file, waiting to read it: the earlier reading is gone.
    assert (queued["id"], queued["version"], queued["status"]) == (document["id"], 2, "queued")
    assert queued["filename"] == "terms.txt"
    assert queued["size_bytes"] == len(NEW)
    assert queued["summary"] is None
    assert hits(client, project["id"], "first day") == set()

    process_document(document["id"], services)
    now = client.get(f"/api/documents/{document['id']}").json()
    assert now["status"] == "ready"
    assert hits(client, project["id"], "working days") == {document["id"]}
    content = client.get(f"/api/documents/{document['id']}/content").json()
    assert [block["text"] for block in content["blocks"]] == [NEW.decode().strip()]

    current, earlier = versions(client, document["id"])
    assert (current["number"], current["current"], current["size_bytes"]) == (2, True, len(NEW))
    assert (earlier["number"], earlier["current"], earlier["size_bytes"]) == (1, False, len(OLD))
    assert earlier["filename"] == "terms.txt"
    assert earlier["served"] and earlier["has_text"]
    assert earlier["uploaded_by"] == current["uploaded_by"] == account.user.name
    assert earlier["uploaded_at"] < current["uploaded_at"]

    # The earlier file as it was uploaded, and its text as its parse read it.
    old_file = client.get(f"/api/documents/{document['id']}/versions/1/file")
    assert old_file.status_code == 200
    assert old_file.content == OLD
    old_text = client.get(f"/api/documents/{document['id']}/versions/1/content").json()
    assert [block["text"] for block in old_text["blocks"]] == [OLD.decode().strip()]
    assert old_text["page_sizes"] == []
    assert client.get(f"/api/documents/{document['id']}/file").content == NEW
    assert client.get(f"/api/documents/{document['id']}/versions/2/file").status_code == 404
    assert client.get(f"/api/documents/{document['id']}/versions/7/content").status_code == 404

    assert rows_of_action(session, account.organization.id, "document_version_uploaded") == [
        {"filename": "terms.txt", "size_bytes": len(NEW), "version": 2}
    ]
    assert rows_of_action(session, account.organization.id, "document_downloaded") == [
        {"filename": "terms.txt", "version": 1},
        {"filename": "terms.txt"},
    ]


def test_each_version_is_an_upload_of_its_own_whose_pages_count_again(
    client, services, session, account
):
    project = create_project(client)
    document = text_document(client, services, project["id"])
    first_upload = session.get(Document, document["id"]).upload_id
    given(client, services, document["id"], "terms.txt", NEW)

    row = session.get(Document, document["id"])
    session.refresh(row)
    assert row.upload_id not in (None, first_upload)
    # The new file's usage starts again; the earlier one's stays with its version.
    assert row.usage["upload"]["bytes"] == len(NEW)
    assert row.usage["upload"]["pages"] == 1
    (earlier,) = session.scalars(select(DocumentVersion)).all()
    assert earlier.usage["upload"] == {"bytes": len(OLD), "pages": 1}
    assert earlier.upload_id == first_upload
    assert pages_this_month(session, account.organization.id, month_of()[0]) == 2
    assert row.versions_bytes == len(OLD)


def test_a_new_file_of_another_type_takes_its_extension_and_its_own_drawings(
    client, services, session, sample_pdf, sample_docx, tmp_path
):
    project = create_project(client)
    (contract,) = upload(client, project["id"], sample_docx)["documents"]
    process_document(contract["id"], services)
    first = session.get(Document, contract["id"])
    first_key = first.storage_key

    signed = columns_pdf(tmp_path / "signed.pdf")
    now = given(client, services, contract["id"], "signed.pdf", signed.read_bytes())
    assert now["filename"] == "policy.pdf"
    assert now["mime_type"] == "application/pdf"
    assert now["page_count"] == 1
    row = session.get(Document, contract["id"])
    session.refresh(row)
    prefix = document_prefix(row.project.org_id, project["id"], row.id)
    assert row.storage_key == f"{prefix}versions/2/original.pdf"
    assert row.storage_prefix == prefix
    assert row.thumbnail_key == f"{prefix}versions/2/thumbnail.png"
    assert services.storage.exists(first_key)

    # The thumbnail is the new file's, drawn under its own prefix; one drawn from the earlier
    # file goes once a third version replaces it.
    assert client.get(f"/api/documents/{row.id}/thumbnail.png").status_code == 200
    drawn = row.thumbnail_key
    assert services.storage.exists(drawn)
    # The first version's bytes again: a document holds no file twice as its current one, but
    # its own earlier file may come back as a new version.
    given(client, services, row.id, "policy.docx", sample_docx.read_bytes())
    session.refresh(row)
    assert row.version == 3
    assert row.filename == "policy.docx"
    assert not services.storage.exists(drawn)
    assert services.storage.exists(f"{prefix}versions/2/original.pdf")

    # Nothing of a live document's looks left behind to the storage's walk.
    with engine.begin() as connection:
        sweep = sweep_storage(services.storage, connection, keep_images=timedelta(days=30))
    assert sweep.orphans == []

    # Deleted for good, every version's files go.
    assert client.delete(f"/api/documents/{row.id}").status_code == 204
    assert client.delete(f"/api/trash/documents/{row.id}").status_code == 204
    assert [entry.key for entry in services.storage.list(prefix)] == []


def test_a_file_an_upload_would_refuse_is_no_new_version(client, services, session, sample_pdf):
    project = create_project(client)
    document = text_document(client, services, project["id"])
    other = text_document(client, services, project["id"], NEW, "other.txt")

    def refused(name, content, status):
        response = give(client, document["id"], name, content)
        assert response.status_code == status, response.text
        return response.json()["detail"]

    assert refused("terms.txt", OLD, 409) == SAME_FILE
    assert refused("copy.txt", NEW, 409) == "Already in this project as 'other.txt'."
    assert refused("terms.exe", b"MZ", 400).startswith("Unsupported type.")
    assert refused("terms.txt", b"", 400) == "Empty file."
    assert refused("terms.pdf", b"not a pdf at all", 400)
    assert refused("terms.eml", b"From: a@b.example\n\nHello", 400).startswith("Unsupported")

    row = session.get(Document, document["id"])
    row.status = "processing"
    session.commit()
    assert refused("terms.txt", b"A third reading.", 409) == BEING_INDEXED

    session.refresh(row)
    assert (row.version, row.sha256) == (1, document_sha(session, document["id"]))
    assert session.scalars(select(DocumentVersion)).all() == []
    assert other["version"] == 1


def document_sha(session, document_id) -> str:
    session.expire_all()
    return session.get(Document, document_id).sha256


def test_a_viewer_and_the_sample_project_take_no_new_version(
    client, services, session, account, make_account, act_as
):
    project = create_project(client)
    document = text_document(client, services, project["id"])
    sample = session.get(Project, project["id"])
    viewer = make_account("vic@example.com", account.organization, role="viewer")
    act_as(viewer)
    assert give(client, document["id"], "terms.txt", NEW).status_code == 403
    assert [version["number"] for version in versions(client, document["id"])] == [1]

    client.app.dependency_overrides.clear()
    sample.sample = True
    session.commit()
    response = give(client, document["id"], "terms.txt", NEW)
    assert response.status_code == 409
    assert "sample project" in response.json()["detail"]


def test_a_review_of_the_earlier_file_is_opened_again(client, services, session, account):
    project = create_project(client)
    document = text_document(client, services, project["id"])
    row = session.get(Document, document["id"])
    row.review = "approved"
    row.assignee_id = account.user.id
    row.review_changed_at = datetime.now(UTC) - timedelta(days=2)
    session.commit()

    now = given(client, services, document["id"], "terms.txt", NEW)
    assert now["review"] == "to_review"
    assert now["assignee_id"] == account.user.id
    assert now["review_changer"] == account.user.name
    assert [action for action, _ in actions(session)][-2:] == [
        "document_version_uploaded",
        "document_review_changed",
    ]
    assert rows_of_action(session, account.organization.id, "document_review_changed") == [
        {"filename": "terms.txt", "from": "approved", "to": "to_review"}
    ]


def test_the_plan_holds_the_earlier_versions_bytes(client, services, request):
    # The owner of a fresh organisation on the free plan (`member`), held to so many bytes.
    request.getfixturevalue("member")
    limits = request.getfixturevalue("plan")
    project = create_project(client)
    document = text_document(client, services, project["id"])
    limits(storage_bytes=len(OLD) + len(NEW) - 1)
    response = give(client, document["id"], "terms.txt", NEW)
    assert response.status_code == 402
    assert response.json()["limit"] == "storage_bytes"

    limits(storage_bytes=len(OLD) + len(NEW))
    given(client, services, document["id"], "terms.txt", NEW)
    usage = client.get("/api/organization/usage").json()
    assert usage["storage_bytes"]["used"] == len(OLD) + len(NEW)


def test_a_projects_export_holds_each_documents_earlier_files_and_their_text(
    client, services, session
):
    project = create_project(client)
    document = text_document(client, services, project["id"])
    given(client, services, document["id"], "terms.txt", NEW)

    made = ask(client, project["id"]).json()
    run_export(services)
    archive = archive_of(client, project["id"], made["id"])
    assert archive.read("originals/terms.txt") == NEW
    assert archive.read("versions/terms (version 1).txt") == OLD
    listed = json.loads(archive.read("project.json"))["documents"]
    ((version,),) = [entry["versions"] for entry in listed]
    assert (listed[0]["version"], version["number"]) == (2, 1)
    assert version["original"] == "versions/terms (version 1).txt"
    assert version["sha256"] == hashlib.sha256(OLD).hexdigest()
    data = json.loads(archive.read(f"documents/{document['id']}.json"))
    assert data["versions"] == [
        {
            "number": 1,
            "blocks": [
                {"kind": "paragraph", "level": None, "text": OLD.decode().strip(), "page": None}
            ],
        }
    ]
