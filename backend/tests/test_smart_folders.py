"""A project's smart folders (app/smart_folders.py, app/api/smart_folders.py): saved filters of
its documents, by tags, types, dates, reviews and a folder, listed as a folder is, and what one
lists once a tag or the folder it names is gone."""

from sqlalchemy import text

from app.api.smart_folders import BACKWARDS
from app.smart_folders import FOLDER_GONE, MAX_SMART_FOLDERS, TAGS_GONE, TOO_MANY
from app.tags import NOT_THE_PROJECTS
from tests.test_api import create_project, upload
from tests.test_folders import make_folder, upload_into
from tests.test_library import files
from tests.test_tags import make_tag, tag


def make_smart_folder(client, project_id, name, **chosen):
    response = client.post(
        f"/api/projects/{project_id}/smart-folders", json={"name": name, "filter": chosen}
    )
    assert response.status_code == 201, response.text
    return response.json()


def shown(client, project_id, smart_folder_id, **params):
    response = client.get(
        f"/api/projects/{project_id}/documents", params={"smart": smart_folder_id, **params}
    )
    assert response.status_code == 200, response.text
    return [document["filename"] for document in response.json()["documents"]]


def set_columns(session, document, **columns):
    if "review" in columns:
        columns["review_changed_at"] = "2024-07-01T00:00:00Z"
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    session.execute(
        text(f"UPDATE documents SET {assignments} WHERE id = :id"),
        {**columns, "id": document["id"]},
    )
    session.commit()


def test_a_smart_folder_lists_what_its_filter_matches_wherever_it_is(client, session, tmp_path):
    project = create_project(client)
    invoices = make_folder(client, project["id"], "Invoices")
    names = ["a.txt", "b.txt", "c.txt", "d.txt"]
    a, b = upload(client, project["id"], *files(tmp_path, names[:2]))["documents"]
    c, d = upload_into(client, project["id"], invoices["id"], *files(tmp_path / "i", names[2:]))[
        "documents"
    ]
    urgent = make_tag(client, project["id"], "Urgent")
    paid = make_tag(client, project["id"], "Paid")
    tag(client, a["id"], urgent["id"])
    tag(client, b["id"], urgent["id"], paid["id"])
    tag(client, c["id"], paid["id"])
    set_columns(session, a, doc_type="invoice", doc_date="2024-01-10")
    set_columns(session, b, doc_type="contract", doc_date="2024-06-01", review="disputed")
    set_columns(session, c, doc_type="invoice", doc_date="2023-12-31", review="approved")
    set_columns(session, d, doc_type="invoice")

    def listing(**chosen):
        smart = make_smart_folder(client, project["id"], f"Smart {len(chosen)}", **chosen)
        return shown(client, project["id"], smart["id"])

    both = [urgent["id"], paid["id"]]
    assert listing(tags=both) == ["a.txt", "b.txt", "c.txt"]
    assert listing(tags=both, every_tag=True) == ["b.txt"]
    # Types as the model writes them, whatever the case asked for.
    assert listing(types=["Invoice"]) == ["a.txt", "c.txt", "d.txt"]
    # A span of dates leaves out the undated.
    assert listing(after="2024-01-01") == ["a.txt", "b.txt"]
    assert listing(before="2024-01-10", types=["invoice"]) == ["a.txt", "c.txt"]
    assert listing(reviews=["approved", "disputed"]) == ["b.txt", "c.txt"]
    assert listing(folder_id=invoices["id"], tags=[paid["id"]]) == ["c.txt"]
    # Nothing chosen is every document of the project, as a flat folder.
    assert listing() == ["a.txt", "b.txt", "c.txt", "d.txt"]
    # In the trash, a document is in no smart folder.
    client.delete(f"/api/documents/{a['id']}")
    assert listing(tags=[urgent["id"]]) == ["b.txt"]


def test_a_smart_folder_is_listed_in_the_order_asked_for(client, session, tmp_path):
    project = create_project(client)
    a, b, c = upload(client, project["id"], *files(tmp_path, ["a.txt", "b.txt", "c.txt"]))[
        "documents"
    ]
    set_columns(session, a, doc_date="2024-05-01")
    set_columns(session, c, doc_date="2024-01-01")
    smart = make_smart_folder(client, project["id"], "Everything")
    assert shown(client, project["id"], smart["id"], sort="date", order="desc") == [
        "a.txt",
        "c.txt",
        "b.txt",
    ]
    page = client.get(
        f"/api/projects/{project['id']}/documents",
        params={"smart": smart["id"], "sort": "date", "limit": 2},
    ).json()
    rest = shown(client, project["id"], smart["id"], sort="date", after=page["next_after"])
    assert [d["filename"] for d in page["documents"]] + rest == ["c.txt", "a.txt", "b.txt"]


def test_smart_folders_are_listed_by_name_and_written_whole(client):
    project = create_project(client)
    urgent = make_tag(client, project["id"], "Urgent")
    later = make_smart_folder(client, project["id"], "urgent ones", tags=[urgent["id"]])
    first = make_smart_folder(client, project["id"], "Contracts", types=["contract"])
    listed = client.get(f"/api/projects/{project['id']}/smart-folders").json()
    assert listed["max_smart_folders"] == MAX_SMART_FOLDERS
    assert [s["name"] for s in listed["smart_folders"]] == ["Contracts", "urgent ones"]
    assert first["filter"] == {
        "tags": [],
        "every_tag": False,
        "types": ["contract"],
        "after": None,
        "before": None,
        "reviews": [],
        "folder_id": None,
    }
    assert (first["folder"], first["problem"], first["made_by"]) == (None, None, "Owner")

    rewritten = client.put(
        f"/api/smart-folders/{later['id']}",
        json={"name": "Due", "filter": {"after": "2024-01-01"}},
    )
    assert rewritten.status_code == 200
    assert rewritten.json()["filter"]["tags"] == [] and rewritten.json()["name"] == "Due"
    assert client.delete(f"/api/smart-folders/{later['id']}").status_code == 204
    url = f"/api/projects/{project['id']}/documents"
    assert client.get(url, params={"smart": later["id"]}).status_code == 404


def test_a_smart_folder_names_only_what_the_project_holds(client, monkeypatch):
    project = create_project(client)
    other = create_project(client, "Other")
    theirs = make_tag(client, other["id"], "Theirs")
    their_folder = make_folder(client, other["id"], "Theirs")
    url = f"/api/projects/{project['id']}/smart-folders"

    def made(**chosen):
        return client.post(url, json={"name": "Smart", "filter": chosen})

    refused = made(tags=[theirs["id"]])
    assert refused.status_code == 404 and refused.json()["detail"] == NOT_THE_PROJECTS
    assert made(folder_id=their_folder["id"]).status_code == 404
    backwards = made(after="2024-02-01", before="2024-01-01")
    assert backwards.status_code == 400 and backwards.json()["detail"] == BACKWARDS
    assert made(reviews=["lost"]).status_code == 422

    monkeypatch.setattr("app.api.smart_folders.MAX_SMART_FOLDERS", 1)
    assert made().status_code == 201
    assert made().status_code == 409 and made().json()["detail"] == TOO_MANY


def test_a_smart_folder_whose_tag_or_folder_is_gone_lists_nothing_by_it(client, tmp_path):
    project = create_project(client)
    folder = make_folder(client, project["id"], "Invoices")
    (inside,) = upload_into(client, project["id"], folder["id"], *files(tmp_path, ["a.txt"]))[
        "documents"
    ]
    urgent = make_tag(client, project["id"], "Urgent")
    paid = make_tag(client, project["id"], "Paid")
    tag(client, inside["id"], urgent["id"], paid["id"])
    by_tags = make_smart_folder(client, project["id"], "Both", tags=[urgent["id"], paid["id"]])
    every = make_smart_folder(
        client, project["id"], "Every", tags=[urgent["id"], paid["id"]], every_tag=True
    )
    in_folder = make_smart_folder(client, project["id"], "In it", folder_id=folder["id"])
    assert in_folder["folder"] == "Invoices"

    def problems():
        listed = client.get(f"/api/projects/{project['id']}/smart-folders").json()
        return {s["name"]: s["problem"] for s in listed["smart_folders"]}

    # One tag of two gone: "any" still lists by the other; "every" lists nothing.
    client.delete(f"/api/tags/{urgent['id']}")
    assert shown(client, project["id"], by_tags["id"]) == ["a.txt"]
    assert shown(client, project["id"], every["id"]) == []
    assert problems() == {"Both": None, "Every": TAGS_GONE, "In it": None}
    client.delete(f"/api/tags/{paid['id']}")
    assert shown(client, project["id"], by_tags["id"]) == []
    # Its folder in the trash, it lists nothing, rather than every document of the project.
    client.delete(f"/api/folders/{folder['id']}")
    assert shown(client, project["id"], in_folder["id"]) == []
    assert problems() == {"Both": TAGS_GONE, "Every": TAGS_GONE, "In it": FOLDER_GONE}
