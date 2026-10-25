"""A project's tags (app/tags.py, app/api/tags.py): made, renamed, recoloured and deleted by a
member who may change the project, put on its documents, and the library's listing of a tag's
documents wherever they are."""

from app.tags import MAX_TAGS, NAME_TAKEN, NOT_THE_PROJECTS, TOO_MANY
from tests.test_api import create_project, upload
from tests.test_folders import make_folder, upload_into
from tests.test_library import files


def make_tag(client, project_id, name, color="grey"):
    response = client.post(f"/api/projects/{project_id}/tags", json={"name": name, "color": color})
    assert response.status_code == 201, response.text
    return response.json()


def tag(client, document_id, *tag_ids):
    response = client.put(f"/api/documents/{document_id}/tags", json={"tag_ids": list(tag_ids)})
    assert response.status_code == 200, response.text
    return response.json()


def tagged(client, project_id, tag_id, **params):
    """The names on the first page of the tag's documents."""
    response = client.get(f"/api/projects/{project_id}/documents", params={"tag": tag_id, **params})
    assert response.status_code == 200, response.text
    return [document["filename"] for document in response.json()["documents"]]


def test_a_project_s_tags_are_listed_by_name_with_how_many_documents_carry_each(client, tmp_path):
    project = create_project(client)
    a, b = upload(client, project["id"], *files(tmp_path, ["a.txt", "b.txt"]))["documents"]
    urgent = make_tag(client, project["id"], "urgent", "red")
    paid = make_tag(client, project["id"], "Paid", "green")
    make_tag(client, project["id"], "Archive")
    tag(client, a["id"], urgent["id"], paid["id"])
    tag(client, b["id"], urgent["id"])

    listed = client.get(f"/api/projects/{project['id']}/tags").json()
    assert listed["max_tags"] == MAX_TAGS
    assert [(t["name"], t["color"], t["document_count"]) for t in listed["tags"]] == [
        ("Archive", "grey", 0),
        ("Paid", "green", 1),
        ("urgent", "red", 2),
    ]
    # A document in the trash counts for nothing.
    client.delete(f"/api/documents/{b['id']}")
    counts = {
        t["name"]: t["document_count"]
        for t in client.get(f"/api/projects/{project['id']}/tags").json()["tags"]
    }
    assert counts["urgent"] == 1


def test_a_tag_s_name_is_the_project_s_own_whatever_its_case(client):
    project = create_project(client)
    other = create_project(client, "Other")
    urgent = make_tag(client, project["id"], "Urgent")
    # Another project has its own names.
    make_tag(client, other["id"], "Urgent")

    taken = client.post(f"/api/projects/{project['id']}/tags", json={"name": " urgent "})
    assert taken.status_code == 409 and taken.json()["detail"] == NAME_TAKEN
    late = make_tag(client, project["id"], "Late")
    renamed = client.patch(f"/api/tags/{late['id']}", json={"name": "URGENT"})
    assert renamed.status_code == 409
    # Its own name in another case is its own.
    recased = client.patch(f"/api/tags/{urgent['id']}", json={"name": "urgent"})
    assert recased.status_code == 200 and recased.json()["name"] == "urgent"
    recoloured = client.patch(f"/api/tags/{urgent['id']}", json={"color": "purple"})
    assert recoloured.json() == {**recased.json(), "color": "purple"}
    assert client.patch(f"/api/tags/{urgent['id']}", json={"color": "beige"}).status_code == 422
    assert (
        client.post(f"/api/projects/{project['id']}/tags", json={"name": "  "}).status_code == 422
    )


def test_a_project_has_at_most_so_many_tags(client, monkeypatch):
    monkeypatch.setattr("app.api.tags.MAX_TAGS", 2)
    project = create_project(client)
    make_tag(client, project["id"], "One")
    make_tag(client, project["id"], "Two")
    refused = client.post(f"/api/projects/{project['id']}/tags", json={"name": "Three"})
    assert refused.status_code == 409 and refused.json()["detail"] == TOO_MANY


def test_a_document_carries_the_tags_it_is_given_and_none_of_another_project_s(client, tmp_path):
    project = create_project(client)
    other = create_project(client, "Other")
    (document,) = upload(client, project["id"], *files(tmp_path, ["a.txt"]))["documents"]
    assert document["tag_ids"] == []
    urgent = make_tag(client, project["id"], "Urgent")
    paid = make_tag(client, project["id"], "Paid")
    theirs = make_tag(client, other["id"], "Theirs")

    # Given twice, a tag is carried once, in the order given; left out, it is taken off.
    assert tag(client, document["id"], paid["id"], urgent["id"], paid["id"])["tag_ids"] == [
        paid["id"],
        urgent["id"],
    ]
    assert tag(client, document["id"], urgent["id"])["tag_ids"] == [urgent["id"]]
    refused = client.put(
        f"/api/documents/{document['id']}/tags", json={"tag_ids": [urgent["id"], theirs["id"]]}
    )
    assert refused.status_code == 404 and refused.json()["detail"] == NOT_THE_PROJECTS
    assert client.get(f"/api/documents/{document['id']}").json()["tag_ids"] == [urgent["id"]]


def test_a_tag_s_documents_are_listed_wherever_they_are_and_out_of_the_trash(client, tmp_path):
    project = create_project(client)
    folder = make_folder(client, project["id"], "Invoices")
    deeper = client.post(
        f"/api/projects/{project['id']}/folders",
        json={"name": "2024", "parent_id": folder["id"]},
    ).json()
    root = upload(client, project["id"], *files(tmp_path, ["b.txt", "untagged.txt"]))
    inside = upload_into(client, project["id"], folder["id"], *files(tmp_path / "i", ["a.txt"]))
    below = upload_into(client, project["id"], deeper["id"], *files(tmp_path / "d", ["c.txt"]))
    urgent = make_tag(client, project["id"], "Urgent")
    for document in (root["documents"][0], inside["documents"][0], below["documents"][0]):
        tag(client, document["id"], urgent["id"])

    assert tagged(client, project["id"], urgent["id"]) == ["a.txt", "b.txt", "c.txt"]
    assert tagged(client, project["id"], urgent["id"], order="desc") == ["c.txt", "b.txt", "a.txt"]
    client.delete(f"/api/documents/{inside['documents'][0]['id']}")
    assert tagged(client, project["id"], urgent["id"]) == ["b.txt", "c.txt"]

    url = f"/api/projects/{project['id']}/documents"
    other = create_project(client, "Other")
    theirs = make_tag(client, other["id"], "Theirs")
    assert client.get(url, params={"tag": theirs["id"]}).status_code == 404
    # One place at a time.
    both = client.get(url, params={"tag": urgent["id"], "folder_id": folder["id"]})
    assert both.status_code == 422


def test_tagging_a_document_changes_the_page_that_shows_it(client, tmp_path):
    project = create_project(client)
    (document,) = upload(client, project["id"], *files(tmp_path, ["a.txt"]))["documents"]
    url = f"/api/projects/{project['id']}/documents"
    held = client.get(url).headers["etag"]
    urgent = make_tag(client, project["id"], "Urgent")
    # Making a tag shows nothing new on the page; putting it on a document does.
    assert client.get(url, headers={"If-None-Match": held}).status_code == 304
    tag(client, document["id"], urgent["id"])
    again = client.get(url, headers={"If-None-Match": held})
    assert again.status_code == 200 and again.json()["documents"][0]["tag_ids"] == [urgent["id"]]


def test_a_tag_deleted_is_taken_off_every_document_the_trash_s_too(client, tmp_path):
    project = create_project(client)
    a, b = upload(client, project["id"], *files(tmp_path, ["a.txt", "b.txt"]))["documents"]
    urgent = make_tag(client, project["id"], "Urgent")
    paid = make_tag(client, project["id"], "Paid")
    tag(client, a["id"], urgent["id"], paid["id"])
    tag(client, b["id"], urgent["id"])
    client.delete(f"/api/documents/{b['id']}")

    assert client.delete(f"/api/tags/{urgent['id']}").status_code == 204
    assert client.get(f"/api/documents/{a['id']}").json()["tag_ids"] == [paid["id"]]
    client.post(f"/api/trash/documents/{b['id']}/restore")
    assert client.get(f"/api/documents/{b['id']}").json()["tag_ids"] == []
    assert client.patch(f"/api/tags/{urgent['id']}", json={"color": "red"}).status_code == 404
    assert [
        t["name"] for t in client.get(f"/api/projects/{project['id']}/tags").json()["tags"]
    ] == ["Paid"]
