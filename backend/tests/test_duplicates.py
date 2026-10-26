"""Copies of a file across an organisation's projects (app/duplicates.py): a document's copies in
the other projects the member sees, and the report of every file held in more than one of them,
the trash left out."""

from tests.test_access import access, crew  # noqa: F401
from tests.test_api import create_project
from tests.test_folders import make_folder


def put(client, project_id, name, content: bytes, folder_id=None) -> dict:
    """Upload a file, which needs no reading to have copies."""
    response = client.post(
        f"/api/projects/{project_id}/documents",
        params={} if folder_id is None else {"folder_id": folder_id},
        files=[("files", (name, content))],
    )
    assert response.status_code == 201, response.text
    (document,) = response.json()["documents"]
    return document


def copies(client, document_id) -> list[dict]:
    response = client.get(f"/api/documents/{document_id}/copies")
    assert response.status_code == 200, response.text
    return response.json()["copies"]


def report(client, **params) -> dict:
    response = client.get("/api/duplicates", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def where(found: dict) -> list[list[tuple[str, str, str]]]:
    """Each file of the report as its copies' project, folder and name."""
    return [
        [(copy["project_name"], copy["folder"], copy["filename"]) for copy in file["copies"]]
        for file in found["duplicates"]
    ]


def test_a_file_held_in_two_projects_lists_its_copies_where_the_member_sees_them(
    client,
    crew,  # noqa: F811
    act_as,
):
    contracts = create_project(client, "Contracts")
    archive = create_project(client, "Archive")
    year = make_folder(client, archive["id"], "2024")
    terms = put(client, contracts["id"], "terms.txt", b"The fee is 100 EUR a month.\n")
    copy = put(
        client, archive["id"], "terms (copy).txt", b"The fee is 100 EUR a month.\n", year["id"]
    )
    alone = put(client, contracts["id"], "alone.txt", b"Held once.\n")

    assert copies(client, terms["id"]) == [
        {
            "id": copy["id"],
            "project_id": archive["id"],
            "project_name": "Archive",
            "folder": "2024",
            "folder_id": year["id"],
            "filename": "terms (copy).txt",
        }
    ]
    assert [c["id"] for c in copies(client, copy["id"])] == [terms["id"]]
    assert copies(client, alone["id"]) == []
    found = report(client)
    assert (found["files"], found["copy_bytes"], found["next"]) == (1, 28, None)
    assert where(found) == [
        [("Archive", "2024", "terms (copy).txt"), ("Contracts", "", "terms.txt")]
    ]
    assert found["duplicates"][0]["mime_type"] == "text/plain"

    # Made private without Cat, the archive's copy is not hers to know of.
    assert access(client, archive["id"], "private", crew.bob).status_code == 200
    act_as(crew.cat)
    assert copies(client, terms["id"]) == []
    assert report(client)["files"] == 0
    act_as(crew.bob)
    assert len(copies(client, terms["id"])) == 1

    # A copy in the trash is none.
    act_as(crew.owner)
    assert client.delete(f"/api/documents/{copy['id']}").status_code == 204
    assert copies(client, terms["id"]) == []
    assert report(client)["duplicates"] == []


def test_the_report_comes_a_page_at_a_time_the_latest_copy_first(client):
    first, second, third = (create_project(client, name) for name in ("One", "Two", "Three"))
    for content in (b"a\n", b"b\n", b"c\n"):
        put(client, first["id"], "file.txt", content)
        put(client, second["id"], "file.txt", content)
    put(client, third["id"], "again.txt", b"a\n")

    page = report(client, limit=2)
    assert (page["files"], page["copy_bytes"]) == (3, 8)
    assert [len(names) for names in where(page)] == [3, 2]
    rest = report(client, limit=2, after=page["next"])
    assert rest["next"] is None
    # The file "a" came last, into the third project.
    assert where(page)[0] == [
        ("One", "", "file.txt"),
        ("Three", "", "again.txt"),
        ("Two", "", "file.txt"),
    ]
    assert len(where(rest)) == 1 and rest["files"] == 3
