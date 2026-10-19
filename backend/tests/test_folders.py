from pathlib import Path

from app.indexing.pipeline import process_document
from tests.test_api import create_project, listed, storage_key, upload


def make_folder(client, project_id, name, parent_id=None):
    response = client.post(
        f"/api/projects/{project_id}/folders", json={"name": name, "parent_id": parent_id}
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload_into(client, project_id, folder_id, *paths):
    files = [("files", (Path(path).name, Path(path).read_bytes())) for path in paths]
    response = client.post(
        f"/api/projects/{project_id}/documents", params={"folder_id": folder_id}, files=files
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_folder_tree_crud(client):
    project = create_project(client)
    contracts = make_folder(client, project["id"], "Contracts")
    signed = make_folder(client, project["id"], "  Signed  ", contracts["id"])
    assert signed["name"] == "Signed" and signed["parent_id"] == contracts["id"]

    listed = client.get(f"/api/projects/{project['id']}/folders").json()
    assert [(f["name"], f["parent_id"]) for f in listed] == [
        ("Contracts", None),
        ("Signed", contracts["id"]),
    ]

    # Siblings need distinct names, whatever their case; the same name elsewhere is fine.
    folders = f"/api/projects/{project['id']}/folders"
    assert client.post(folders, json={"name": "contracts"}).status_code == 409
    nested = make_folder(client, project["id"], "Contracts", contracts["id"])
    assert nested["parent_id"] == contracts["id"]
    assert client.post(folders, json={"name": "a/b"}).status_code == 422
    assert client.post(folders, json={"name": "x", "parent_id": 999}).status_code == 404

    renamed = client.patch(f"/api/folders/{signed['id']}", json={"name": "Executed"}).json()
    assert renamed["name"] == "Executed" and renamed["parent_id"] == contracts["id"]
    moved = client.patch(f"/api/folders/{signed['id']}", json={"parent_id": None}).json()
    assert moved["parent_id"] is None and moved["name"] == "Executed"

    # A folder cannot be moved into itself or into its own subtree.
    inside_itself = client.patch(
        f"/api/folders/{contracts['id']}", json={"parent_id": contracts["id"]}
    )
    assert inside_itself.status_code == 409
    into_child = client.patch(f"/api/folders/{contracts['id']}", json={"parent_id": nested["id"]})
    assert into_child.status_code == 409
    assert client.patch("/api/folders/999", json={"name": "x"}).status_code == 404


def test_deleting_a_folder_removes_its_subtree_and_files(client, services, sample_pdf, sample_docx):
    project = create_project(client)
    contracts = make_folder(client, project["id"], "Contracts")
    signed = make_folder(client, project["id"], "Signed", contracts["id"])
    (pdf,) = upload_into(client, project["id"], signed["id"], sample_pdf)["documents"]
    (docx,) = upload(client, project["id"], sample_docx)["documents"]
    assert pdf["folder_id"] == signed["id"] and docx["folder_id"] is None
    process_document(pdf["id"], services)
    key = storage_key(pdf["id"])
    assert services.storage.exists(key)

    assert client.delete(f"/api/folders/{contracts['id']}").status_code == 204
    assert client.get(f"/api/projects/{project['id']}/folders").json() == []
    assert client.get(f"/api/documents/{pdf['id']}").status_code == 404
    assert not services.storage.exists(key)
    remaining = listed(client, project["id"])
    assert [d["id"] for d in remaining] == [docx["id"]]


def test_document_rename_and_move(client, sample_pdf, tmp_path):
    project = create_project(client)
    folder = make_folder(client, project["id"], "Contracts")
    (pdf,) = upload(client, project["id"], sample_pdf)["documents"]
    patch = f"/api/documents/{pdf['id']}"

    # The extension stays, because the parser picks the format from it.
    assert client.patch(patch, json={"filename": "agreement"}).json()["filename"] == "agreement.pdf"
    renamed = client.patch(patch, json={"filename": " agreement.v2.txt "}).json()
    assert renamed["filename"] == "agreement.v2.txt.pdf"
    assert client.patch(patch, json={"filename": "   "}).status_code == 422
    assert client.patch(patch, json={"folder_id": 999}).status_code == 404
    moved = client.patch(patch, json={"folder_id": folder["id"]}).json()
    assert moved["folder_id"] == folder["id"]

    # Sibling names must differ: a rename into a clash is refused, an upload is renamed.
    (tmp_path / "a.txt").write_text("first")
    (tmp_path / "b.txt").write_text("second")
    (tmp_path / "c.txt").write_text("third")
    a, b = upload(client, project["id"], tmp_path / "a.txt", tmp_path / "b.txt")["documents"]
    assert client.patch(f"/api/documents/{b['id']}", json={"filename": "A"}).status_code == 409
    assert client.patch(f"/api/documents/{b['id']}", json={"filename": "b.txt"}).status_code == 200
    (tmp_path / "copy").mkdir()
    (tmp_path / "copy" / "a.txt").write_text("third")
    (c,) = upload(client, project["id"], tmp_path / "copy" / "a.txt")["documents"]
    assert c["filename"] == "a (2).txt"
    # In another folder the name is free again.
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "a.txt").write_text("fourth")
    (d,) = upload_into(client, project["id"], folder["id"], tmp_path / "other" / "a.txt")[
        "documents"
    ]
    assert d["filename"] == "a.txt"

    # A re-upload of the same bytes says where the original is.
    again = upload(client, project["id"], sample_pdf)
    assert again["rejected"][0]["reason"] == (
        "Already in this project as 'agreement.v2.txt.pdf' in Contracts."
    )


def test_search_within_a_folder(client, services, sample_pdf, sample_docx):
    project = create_project(client)
    policies = make_folder(client, project["id"], "Policies")
    empty = make_folder(client, project["id"], "Empty")
    (pdf,) = upload(client, project["id"], sample_pdf)["documents"]
    (docx,) = upload_into(client, project["id"], policies["id"], sample_docx)["documents"]
    for document in (pdf, docx):
        process_document(document["id"], services)
    search = f"/api/projects/{project['id']}/search"

    everything = client.get(search, params={"q": "days"}).json()
    assert {hit["document_id"] for hit in everything["hits"]} == {pdf["id"], docx["id"]}
    scoped = client.get(search, params={"q": "days", "folder_id": policies["id"]}).json()
    assert {hit["document_id"] for hit in scoped["hits"]} == {docx["id"]}
    assert client.get(search, params={"q": "days", "folder_id": empty["id"]}).json()["hits"] == []
    assert client.get(search, params={"q": "days", "folder_id": 999}).status_code == 404
