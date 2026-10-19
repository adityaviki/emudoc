"""The library's listing: one folder's documents a page at a time, in the order the page shows
them, and a page that has not changed answered with a 304 (`list_documents` in
app/api/documents.py)."""

from sqlalchemy import text

from app.api.documents import BAD_CURSOR
from app.indexing.pipeline import process_document
from tests.test_api import create_project, upload
from tests.test_folders import make_folder, upload_into

NAMES = ["invoice 10.txt", "Invoice 2.txt", "invoice 1.txt", "Annex.txt", "annex b.txt", "9.txt"]
# A space sorts before a dot, as in the browser's `localeCompare`, which sorts the folders.
IN_ORDER = ["9.txt", "annex b.txt", "Annex.txt", "invoice 1.txt", "Invoice 2.txt", "invoice 10.txt"]


def files(directory, names):
    directory.mkdir(exist_ok=True)
    paths = []
    for name in names:
        path = directory / name
        path.write_text(f"The text of {name}.")
        paths.append(path)
    return paths


def pages(client, project_id, limit, folder_id=None):
    """Every page of a folder, followed from the first by `next_after`."""
    params = {"limit": limit} | ({} if folder_id is None else {"folder_id": folder_id})
    found = []
    while True:
        response = client.get(f"/api/projects/{project_id}/documents", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page["documents"]) <= limit
        found.append([document["filename"] for document in page["documents"]])
        if page["next_after"] is None:
            return found
        params["after"] = page["next_after"]


def test_a_folder_is_listed_a_page_at_a_time_by_name_with_numbers_as_numbers(client, tmp_path):
    project = create_project(client)
    upload(client, project["id"], *files(tmp_path, NAMES))
    contracts = make_folder(client, project["id"], "Contracts")
    upload_into(client, project["id"], contracts["id"], *files(tmp_path / "c", ["msa.txt"]))

    # Case and accents set aside, numbers compared as numbers: as the page sorts its folders.
    assert pages(client, project["id"], limit=4) == [IN_ORDER[:4], IN_ORDER[4:]]
    assert pages(client, project["id"], limit=2) == [IN_ORDER[:2], IN_ORDER[2:4], IN_ORDER[4:]]
    # A folder lists what it holds itself, and the root none of it.
    assert pages(client, project["id"], limit=10, folder_id=contracts["id"]) == [["msa.txt"]]
    # The folders say how many documents each holds, since the library no longer has them all.
    (folder,) = client.get(f"/api/projects/{project['id']}/folders").json()
    assert folder["document_count"] == 1
    renamed = client.patch(f"/api/folders/{contracts['id']}", json={"name": "Agreements"})
    assert renamed.json()["document_count"] == 1


def test_the_next_page_holds_after_the_document_the_last_one_ended_on_is_gone(client, tmp_path):
    project = create_project(client)
    documents = upload(client, project["id"], *files(tmp_path, NAMES))["documents"]
    by_name = {document["filename"]: document for document in documents}
    url = f"/api/projects/{project['id']}/documents"
    first = client.get(url, params={"limit": 3}).json()
    assert [d["filename"] for d in first["documents"]] == IN_ORDER[:3]

    # The page ended on "Annex.txt"; the next one starts after its name, wherever it went.
    assert client.delete(f"/api/documents/{by_name['Annex.txt']['id']}").status_code == 204
    after = {"limit": 3, "after": first["next_after"]}
    assert [d["filename"] for d in client.get(url, params=after).json()["documents"]] == (
        IN_ORDER[3:]
    )


def test_a_page_that_has_not_changed_is_answered_with_a_304(
    client, services, session, tmp_path, sample_pdf
):
    project = create_project(client)
    (pdf,) = upload(client, project["id"], sample_pdf)["documents"]
    folder = make_folder(client, project["id"], "Elsewhere")
    url = f"/api/projects/{project['id']}/documents"
    response = client.get(url)
    tag = response.headers["etag"]
    assert response.status_code == 200 and tag.startswith('"')

    def asked_again(held):
        return client.get(url, headers={"If-None-Match": held})

    unchanged = asked_again(tag)
    assert unchanged.status_code == 304 and unchanged.content == b""
    assert unchanged.headers["etag"] == tag
    # A proxy that compresses the page may hand the tag on weakened; it is the same page.
    assert asked_again(f'"other", W/{tag}').status_code == 304
    # Another folder's documents are not on the page.
    upload_into(client, project["id"], folder["id"], *files(tmp_path, ["elsewhere.txt"]))
    assert asked_again(tag).status_code == 304

    def changed(held):
        response = asked_again(held)
        assert response.status_code == 200 and response.headers["etag"] != held
        return response.headers["etag"]

    # Every change to a row on the page moves the tag: the pipeline's,
    process_document(pdf["id"], services)
    tag = changed(tag)
    # a rename,
    client.patch(f"/api/documents/{pdf['id']}", json={"filename": "contract.pdf"})
    tag = changed(tag)
    # an update the ORM did not make, which leaves `updated_at` alone (as `_fail_document`
    # in app/worker.py does),
    session.execute(text("UPDATE documents SET error = 'x' WHERE id = :id"), {"id": pdf["id"]})
    session.commit()
    tag = changed(tag)
    # a new document, and one gone.
    (note,) = upload(client, project["id"], *files(tmp_path / "n", ["note.txt"]))["documents"]
    tag = changed(tag)
    client.delete(f"/api/documents/{note['id']}")
    tag = changed(tag)
    assert asked_again(tag).status_code == 304


def test_a_page_that_does_not_exist_is_refused(client):
    project = create_project(client)
    other = create_project(client, "Other")
    elsewhere = make_folder(client, other["id"], "Theirs")
    url = f"/api/projects/{project['id']}/documents"
    for after in ("not a cursor", "W10", "WyJhIiwgMF0", "eyJhIjogMX0"):
        response = client.get(url, params={"after": after})
        assert response.status_code == 422 and response.json()["detail"] == BAD_CURSOR, after
    assert client.get(url, params={"folder_id": elsewhere["id"]}).status_code == 404
    assert client.get(url, params={"limit": 0}).status_code == 422


def test_a_project_counts_its_documents_that_wait_for_a_model(
    client, services, session, sample_pdf, sample_docx
):
    project = create_project(client)
    pdf, docx = upload(client, project["id"], sample_pdf, sample_docx)["documents"]
    process_document(pdf["id"], services)
    services.llm.available = False
    process_document(docx["id"], services)

    shown = client.get(f"/api/projects/{project['id']}").json()
    assert (shown["document_count"], shown["unenriched_count"]) == (2, 1)
    (listed,) = client.get("/api/projects").json()
    assert (listed["document_count"], listed["unenriched_count"]) == (2, 1)
