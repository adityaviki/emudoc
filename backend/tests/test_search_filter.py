"""The library's search narrowed by what a member chose (`DocumentFilter`, app/document_filter.py,
`searched` in app/api/query.py): by tags, types, dates, reviews and a folder, and within what
earlier searches found, by the words they were for."""

from sqlalchemy import text

from app.document_filter import BACKWARDS
from app.indexing.pipeline import process_document
from app.schemas import MAX_MENTIONS
from tests.test_api import create_project, upload
from tests.test_folders import make_folder, upload_into
from tests.test_tags import make_tag, tag


def set_columns(session, document, **columns):
    if "review" in columns:
        columns["review_changed_at"] = "2024-07-01T00:00:00Z"
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    session.execute(
        text(f"UPDATE documents SET {assignments} WHERE id = :id"),
        {**columns, "id": document["id"]},
    )
    session.commit()


# Four short documents, each a passage of its own, to search and to narrow searches by.
WRITTEN = {
    "a.txt": "Late delivery penalty of five percent per week.",
    "b.txt": "Late delivery is excused by force majeure.",
    "c.txt": "The penalty clause applies to every invoice.",
    "d.txt": "Payment terms are thirty days.",
}


def written(client, services, project_id, folder_id, directory, names):
    directory.mkdir(exist_ok=True)
    paths = []
    for name in names:
        (directory / name).write_text(WRITTEN[name])
        paths.append(directory / name)
    made = upload_into(client, project_id, folder_id, *paths) if folder_id else None
    documents = (made or upload(client, project_id, *paths))["documents"]
    for document in documents:
        process_document(document["id"], services)
    return documents


def found(client, project_id, q="delivery", **params):
    """The files the passages a search found are in, by their names without `.txt`. The project
    is small enough for the ranking by meaning to bring every passage the filter leaves, so these
    are the documents the filter matches."""
    response = client.get(
        f"/api/projects/{project_id}/search", params={"q": q, "limit": 50, **params}
    )
    assert response.status_code == 200, response.text
    ids = {hit["document_id"] for hit in response.json()["hits"]}
    return sorted(
        client.get(f"/api/documents/{id}").json()["filename"].removesuffix(".txt") for id in ids
    )


def test_a_search_is_narrowed_by_what_its_member_chose(client, services, session, tmp_path):
    project = create_project(client)
    other = create_project(client, "Other")
    invoices = make_folder(client, project["id"], "Invoices")
    a, b = written(client, services, project["id"], None, tmp_path, ["a.txt", "b.txt"])
    c, d = written(
        client, services, project["id"], invoices["id"], tmp_path / "i", ["c.txt", "d.txt"]
    )
    urgent = make_tag(client, project["id"], "Urgent")
    paid = make_tag(client, project["id"], "Paid")
    tag(client, a["id"], urgent["id"])
    tag(client, b["id"], urgent["id"], paid["id"])
    tag(client, c["id"], paid["id"])
    set_columns(session, a, doc_type="invoice", doc_date="2024-01-10")
    set_columns(session, b, doc_type="contract", doc_date="2024-06-01", review="disputed")
    set_columns(session, c, doc_type="invoice", doc_date="2023-12-31", review="approved")
    # Undated, whatever the model read.
    set_columns(session, d, doc_type="invoice", doc_date=None)
    every = ["a", "b", "c", "d"]
    pid = project["id"]

    assert found(client, pid) == every
    both = [urgent["id"], paid["id"]]
    assert found(client, pid, tags=both) == ["a", "b", "c"]
    assert found(client, pid, tags=both, every_tag=True) == ["b"]
    assert found(client, pid, types=["Invoice"]) == ["a", "c", "d"]
    assert found(client, pid, after="2024-01-01") == ["a", "b"]
    assert found(client, pid, before="2024-01-10", types=["invoice"]) == ["a", "c"]
    assert found(client, pid, reviews=["approved", "disputed"]) == ["b", "c"]
    assert found(client, pid, folder_id=invoices["id"], tags=[paid["id"]]) == ["c"]
    assert found(client, pid, document_id=a["id"], types=["contract"]) == []

    # Within what earlier searches found: the documents with a passage holding every word of
    # each, in any form the language gives it ("penalties" holds "penalty").
    assert found(client, pid, mentions=["late delivery"]) == ["a", "b"]
    assert found(client, pid, mentions=["penalties"]) == ["a", "c"]
    assert found(client, pid, mentions=["late delivery", "penalty"]) == ["a"]
    assert found(client, pid, mentions=["penalty delivery"], types=["invoice"]) == ["a"]
    # Words too common to be indexed hold nothing back; words no passage holds, everything.
    assert found(client, pid, mentions=["the"]) == every
    assert found(client, pid, mentions=["zebra"]) == []
    # The trash's documents are never found, whatever matches them.
    client.delete(f"/api/documents/{a['id']}")
    assert found(client, pid, mentions=["penalty"]) == ["c"]

    search = f"/api/projects/{pid}/search"
    backwards = client.get(search, params={"q": "x", "after": "2024-02-01", "before": "2024-01"})
    assert backwards.status_code == 422
    backwards = client.get(search, params={"q": "x", "after": "2024-02-01", "before": "2024-01-01"})
    assert backwards.status_code == 400 and backwards.json()["detail"] == BACKWARDS
    theirs = make_folder(client, other["id"], "Theirs")
    assert client.get(search, params={"q": "x", "folder_id": theirs["id"]}).status_code == 404
    many = {"q": "x", "mentions": [f"word{n}" for n in range(MAX_MENTIONS + 1)]}
    assert client.get(search, params=many).status_code == 422
    assert client.get(search, params={"q": "x", "reviews": ["lost"]}).status_code == 422
    # A folder in the trash is searched for nothing, rather than the whole project.
    client.delete(f"/api/folders/{invoices['id']}")
    assert found(client, pid, folder_id=invoices["id"]) == []
