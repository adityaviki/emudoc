from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.api.documents import ONE_PATH_A_FILE
from app.db import engine
from app.folders import FOLDERS_LOCK, MAX_DEPTH
from app.indexing.pipeline import process_document
from app.models import Document, Folder, Upload
from app.uploads import UNMAKEABLE_FOLDER
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


def upload_folder(client, project_id, files, folder_id=None):
    """Upload {path: text} as an upload of a folder does: each file with the folder it is in."""
    response = client.post(
        f"/api/projects/{project_id}/documents",
        params={} if folder_id is None else {"folder_id": folder_id},
        files=[("files", (Path(path).name, body.encode())) for path, body in files.items()],
        data={"paths": [path.rpartition("/")[0] for path in files]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def tree(client, project_id):
    """Each folder's path, with the names of the files directly in it."""
    folders = client.get(f"/api/projects/{project_id}/folders").json()
    by_id = {folder["id"]: folder for folder in folders}

    def path(folder):
        parent = by_id.get(folder["parent_id"])
        return f"{path(parent)}/{folder['name']}" if parent else folder["name"]

    return {
        path(folder): [d["filename"] for d in listed(client, project_id, folder["id"])]
        for folder in folders
    }


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
    remaining = listed(client, project["id"])
    assert [d["id"] for d in remaining] == [docx["id"]]
    # The files wait in the trash with the folder, and go once it is deleted from there.
    assert services.storage.exists(key)
    assert client.delete(f"/api/trash/folders/{contracts['id']}").status_code == 204
    assert not services.storage.exists(key)


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


def test_an_uploaded_folder_is_filed_as_it_was_into_the_folders_of_its_names(client, session):
    project = create_project(client)
    contracts = make_folder(client, project["id"], "Contracts")
    make_folder(client, project["id"], "signed", contracts["id"])

    result = upload_folder(
        client,
        project["id"],
        {
            "Signed/acme.txt": "Acme's contract",
            "Signed/2026/globex.txt": "Globex's contract",
            "notes.txt": "Notes",
            "  Drafts /  /initech.txt": "Initech's draft",
        },
        contracts["id"],
    )
    assert result["rejected"] == []
    # A folder of a name already there, whatever its case, takes the files; the others are made,
    # their names' spaces tidied as a new folder's are.
    assert tree(client, project["id"]) == {
        "Contracts": ["notes.txt"],
        "Contracts/signed": ["acme.txt"],
        "Contracts/signed/2026": ["globex.txt"],
        "Contracts/Drafts": ["initech.txt"],
    }
    # One upload, whose notice is about all of them (app/notices.py).
    assert session.scalar(select(func.count()).select_from(Upload)) == 1
    assert session.scalar(select(func.count(func.distinct(Document.upload_id)))) == 1

    # The same folder again goes into the folders it made, and a file it holds already is named
    # by its path in the folder.
    again = upload_folder(
        client,
        project["id"],
        {"Signed/acme.txt": "Acme's contract", "Drafts/hooli.txt": "Hooli's draft"},
        contracts["id"],
    )
    assert again["rejected"] == [
        {
            "filename": "Signed/acme.txt",
            "reason": "Already in this project as 'acme.txt' in Contracts/signed.",
            "limit": None,
        }
    ]
    assert tree(client, project["id"])["Contracts/Drafts"] == ["hooli.txt", "initech.txt"]

    # A folder in the trash holds no name: the upload makes the folder afresh.
    drafts = next(
        f
        for f in client.get(f"/api/projects/{project['id']}/folders").json()
        if f["name"] == "Drafts"
    )
    assert client.delete(f"/api/folders/{drafts['id']}").status_code == 204
    upload_folder(client, project["id"], {"Drafts/umbrella.txt": "Umbrella's"}, contracts["id"])
    assert tree(client, project["id"])["Contracts/Drafts"] == ["umbrella.txt"]
    # Nothing but the files uploaded made a folder: none was made for a file that did not go in.
    assert session.scalar(select(func.count()).select_from(Folder)) == 5


def test_a_path_that_cannot_be_made_rejects_its_file_alone(client):
    project = create_project(client)
    deep = "/".join(f"level {n}" for n in range(MAX_DEPTH + 1))
    result = upload_folder(
        client,
        project["id"],
        {
            "../escape.txt": "Up",
            f"{'x' * 201}/long.txt": "Long",
            f"{deep}/deep.txt": "Deep",
            "Fine/kept.txt": "Kept",
        },
    )
    assert [d["filename"] for d in result["documents"]] == ["kept.txt"]
    assert result["rejected"] == [
        {"filename": name, "reason": UNMAKEABLE_FOLDER, "limit": None}
        for name in ("escape.txt", "long.txt", "deep.txt")
    ]
    assert tree(client, project["id"]) == {"Fine": ["kept.txt"]}
    # Exactly as deep as an upload may go is made.
    within = "/".join(f"level {n}" for n in range(MAX_DEPTH))
    assert upload_folder(client, project["id"], {f"{within}/deep.txt": "Deep"})["rejected"] == []

    # A path for each file, or none: a list of another length cannot be matched up.
    response = client.post(
        f"/api/projects/{project['id']}/documents",
        files=[("files", ("a.txt", b"A")), ("files", ("b.txt", b"B"))],
        data={"paths": ["Somewhere"]},
    )
    assert (response.status_code, response.json()["detail"]) == (422, ONE_PATH_A_FILE)


def test_two_uploads_of_one_folder_at_once_make_it_once(client):
    project = create_project(client)
    with engine.connect() as holder, ThreadPoolExecutor(1) as pool:
        # Another upload holds the project's folders and is making "Scans"...
        holder.execute(
            text("SELECT pg_advisory_xact_lock(:key, :project)"),
            {"key": FOLDERS_LOCK, "project": project["id"]},
        )
        holder.execute(
            text("INSERT INTO folders (project_id, name) VALUES (:project, 'Scans')"),
            {"project": project["id"]},
        )
        # ...while this one, which found no "Scans", waits for it.
        waiting = pool.submit(upload_folder, client, project["id"], {"Scans/a.txt": "A scan"})
        with pytest.raises(TimeoutError):
            waiting.result(timeout=0.5)
        holder.commit()
        assert waiting.result(timeout=10)["rejected"] == []
    assert tree(client, project["id"]) == {"Scans": ["a.txt"]}
