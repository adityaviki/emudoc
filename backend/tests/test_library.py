"""The library's listing: one folder's documents a page at a time, in the order the page shows
them, a page that has not changed answered with a 304, and the files whose names hold some
letters wherever they are (`list_documents` in app/api/documents.py)."""

from sqlalchemy import text

from app.api.documents import BAD_CURSOR, MAX_NAME_FILTER, NAME_IN_FOLDER
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


def ordered(client, project_id, sort, order, limit, **params):
    """Every page of a place in an order, followed from the first by `next_after`."""
    params = {"sort": sort, "order": order, "limit": limit, **params}
    found = []
    while True:
        response = client.get(f"/api/projects/{project_id}/documents", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        found.append([document["filename"] for document in page["documents"]])
        if page["next_after"] is None:
            return found
        params["after"] = page["next_after"]


def dated(session, documents, dates):
    for document, day in zip(documents, dates, strict=True):
        session.execute(
            text("UPDATE documents SET doc_date = :day WHERE id = :id"),
            {"day": day, "id": document["id"]},
        )
    session.commit()


def test_a_folder_is_listed_in_each_order_the_library_offers(client, session, tmp_path):
    project = create_project(client)
    names = ["c.txt", "a.txt", "d.txt", "b.txt", "e.txt"]
    directory = tmp_path / "f"
    directory.mkdir()
    paths = []
    for index, name in enumerate(names):
        path = directory / name
        # Each a size of its own: a's the largest, then c, e, d, b.
        path.write_text("x" * {"a": 50, "c": 40, "e": 30, "d": 20, "b": 10}[name[0]] + str(index))
        paths.append(path)
    documents = upload(client, project["id"], *paths)["documents"]
    dated(session, documents, ["2024-03-01", None, "2023-01-15", None, "2024-03-01"])
    added = [document["filename"] for document in documents]

    assert ordered(client, project["id"], "name", "desc", 2) == [
        ["e.txt", "d.txt"],
        ["c.txt", "b.txt"],
        ["a.txt"],
    ]
    assert sum(ordered(client, project["id"], "added", "asc", 2), []) == added
    assert sum(ordered(client, project["id"], "added", "desc", 3), []) == added[::-1]
    assert sum(ordered(client, project["id"], "size", "desc", 2), []) == [
        "a.txt",
        "c.txt",
        "e.txt",
        "d.txt",
        "b.txt",
    ]
    # By date, the dated first either way, a tie by the order they were added in, and then the
    # undated, whichever page the turn from one to the other falls on.
    for limit in (1, 2, 3, 10):
        assert sum(ordered(client, project["id"], "date", "asc", limit), []) == [
            "d.txt",
            "c.txt",
            "e.txt",
            "a.txt",
            "b.txt",
        ], limit
        assert sum(ordered(client, project["id"], "date", "desc", limit), []) == [
            "e.txt",
            "c.txt",
            "d.txt",
            "b.txt",
            "a.txt",
        ], limit


def test_a_cursor_holds_only_for_the_order_it_was_given_in(client, tmp_path):
    project = create_project(client)
    upload(client, project["id"], *files(tmp_path, ["a.txt", "b.txt", "c.txt"]))
    url = f"/api/projects/{project['id']}/documents"
    by_size = client.get(url, params={"sort": "size", "limit": 1}).json()["next_after"]
    for params in ({}, {"sort": "size", "order": "desc"}, {"sort": "date"}):
        refused = client.get(url, params={**params, "after": by_size})
        assert refused.status_code == 422 and refused.json()["detail"] == BAD_CURSOR, params
    # A cursor by name from before the other orders is still read as one.
    by_name = client.get(url, params={"limit": 1}).json()["next_after"]
    assert [
        d["filename"] for d in client.get(url, params={"after": by_name}).json()["documents"]
    ] == [
        "b.txt",
        "c.txt",
    ]
    assert client.get(url, params={"sort": "colour"}).status_code == 422


def test_a_name_filter_finds_the_files_whose_names_hold_its_letters_in_every_folder(
    client, tmp_path
):
    project = create_project(client)
    root = ["Invoice 2.txt", "annex.txt", "50%_off.txt", "500 off.txt"]
    loose = upload(client, project["id"], *files(tmp_path, root))["documents"]
    contracts = make_folder(client, project["id"], "Contracts")
    inside = files(tmp_path / "c", ["invoice 10.txt", "msa.txt"])
    (invoice, _) = upload_into(client, project["id"], contracts["id"], *inside)["documents"]
    other = create_project(client, "Other")
    upload(client, other["id"], *files(tmp_path / "o", ["invoice 3.txt"]))
    url = f"/api/projects/{project['id']}/documents"

    def named(name, sort="name", order="asc", **params):
        return sum(ordered(client, project["id"], sort, order, 100, name=name, **params), [])

    # Any case, from every folder of this project alone, a page at a time in the order asked for.
    assert ordered(client, project["id"], "name", "asc", 1, name="INVOICE") == [
        ["Invoice 2.txt"],
        ["invoice 10.txt"],
    ]
    assert named(" invoice ", sort="size", order="desc") == ["invoice 10.txt", "Invoice 2.txt"]
    # Its letters as they are: a % or an _ is no wildcard.
    assert named("0%_") == ["50%_off.txt"]
    # Spaces alone are no filter: the root, as without one.
    assert named("   ") == ["50%_off.txt", "500 off.txt", "annex.txt", "Invoice 2.txt"]
    # It narrows a tag's documents too.
    made = client.post(f"/api/projects/{project['id']}/tags", json={"name": "Due", "color": "red"})
    tag_id = made.json()["id"]
    for document in (invoice, loose[1]):
        put = client.put(f"/api/documents/{document['id']}/tags", json={"tag_ids": [tag_id]})
        assert put.status_code == 200, put.text
    assert named("invoice", tag=tag_id) == ["invoice 10.txt"]
    # In the trash, a file is no longer found.
    client.delete(f"/api/documents/{invoice['id']}")
    assert named("invoice") == ["Invoice 2.txt"]
    # A folder's listing is that folder's alone, which a name filter is not.
    refused = client.get(url, params={"name": "invoice", "folder_id": contracts["id"]})
    assert refused.status_code == 422 and refused.json()["detail"] == NAME_IN_FOLDER
    assert client.get(url, params={"name": "x" * (MAX_NAME_FILTER + 1)}).status_code == 422
