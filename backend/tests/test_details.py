"""A document's title, kind and date as its members set them (app/details.py): kept in place of
what its reading found through every later reading until a member takes the reading's again, and
read by everything that reads the three, the outline's paths and the search among them."""

from sqlalchemy import select

from app.api.documents import BOTH_WAYS
from app.indexing.pipeline import process_document
from app.models import Section
from tests.test_api import create_project
from tests.test_versions import given, text_document

# What the fake model reads every document to be (tests/conftest.py).
READ = {"title": "Master Services Agreement", "doc_type": "contract", "doc_date": "2024-03-01"}


def details(client, document_id, **body):
    response = client.patch(f"/api/documents/{document_id}/details", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def shown(document) -> dict:
    return {name: document[name] for name in READ}


def read(document) -> dict:
    return {name: document[f"read_{name}"] for name in READ}


def paths(client, session, document_id) -> list[str]:
    """The paths of the document's outline, and of its passages as the search finds them."""
    outline = session.scalars(
        select(Section.path).where(Section.document_id == document_id).order_by(Section.ordinal)
    ).all()
    project_id = client.get(f"/api/documents/{document_id}").json()["project_id"]
    found = client.get(f"/api/projects/{project_id}/search", params={"q": "service fee"}).json()
    return [*outline, *(hit["context"] for hit in found["hits"])]


def test_a_members_details_hold_through_every_reading_until_the_readings_are_taken_again(
    client, session, services
):
    project = create_project(client)
    document = text_document(client, services, project["id"])
    assert shown(document) == READ and read(document) == READ
    assert document["edited_details"] == []
    assert paths(client, session, document["id"]) == ["Master Services Agreement › terms"] * 2

    changed = details(client, document["id"], title="  Acme   services ", doc_date="2024-04-02")
    assert shown(changed) == READ | {"title": "Acme services", "doc_date": "2024-04-02"}
    assert read(changed) == READ
    assert sorted(changed["edited_details"]) == ["doc_date", "title"]
    # The outline and the passages start with the title the member gave.
    assert paths(client, session, document["id"]) == ["Acme services › terms"] * 2

    # "Index again" reads the document afresh and keeps what the member set.
    again = client.post(f"/api/documents/{document['id']}/reprocess")
    assert again.status_code == 202, again.text
    process_document(document["id"], services)
    reread = client.get(f"/api/documents/{document['id']}").json()
    assert shown(reread) == shown(changed) and read(reread) == READ
    assert paths(client, session, document["id"]) == ["Acme services › terms"] * 2

    # So does a new version, whose reading is the new file's.
    versioned = given(client, services, document["id"], "terms.txt", b"The service fee rises.\n")
    assert versioned["version"] == 2
    assert shown(versioned) == shown(changed)

    # Taking the reading's title again takes the latest reading's, the paths with it.
    back = details(client, document["id"], as_read=["title"])
    assert back["title"] == "Master Services Agreement" and back["doc_date"] == "2024-04-02"
    assert back["edited_details"] == ["doc_date"]
    assert paths(client, session, document["id"])[0] == "Master Services Agreement › terms"


def test_a_detail_is_cleaned_set_to_none_and_read_by_the_library_and_smart_folders(
    client, services
):
    project = create_project(client)
    document = text_document(client, services, project["id"])
    other = text_document(client, services, project["id"], b"A second file.\n", "second.txt")

    changed = details(client, document["id"], doc_type=" Purchase\n Order ", title=None)
    # A kind compares in lower case, as the reading writes it; no title shows the file's name.
    assert changed["doc_type"] == "purchase order" and changed["title"] is None
    types = client.get(f"/api/projects/{project['id']}/document-types").json()
    assert "purchase order" in str(types)
    folder = client.post(
        f"/api/projects/{project['id']}/smart-folders",
        json={"name": "Orders", "filter": {"types": ["Purchase order"]}},
    )
    assert folder.status_code == 201, folder.text
    listed = client.get(
        f"/api/projects/{project['id']}/documents", params={"smart": folder.json()["id"]}
    ).json()
    assert [row["id"] for row in listed["documents"]] == [document["id"]]

    # The library's date order reads the member's date.
    details(client, other["id"], doc_date="2020-01-01")
    by_date = client.get(
        f"/api/projects/{project['id']}/documents", params={"sort": "date", "order": "asc"}
    ).json()
    assert [row["id"] for row in by_date["documents"]] == [other["id"], document["id"]]


def test_a_detail_is_set_or_taken_from_the_reading_not_both(client, services):
    project = create_project(client)
    document = text_document(client, services, project["id"])
    response = client.patch(
        f"/api/documents/{document['id']}/details", json={"title": "Mine", "as_read": ["title"]}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == BOTH_WAYS
    # A field left out stays, and an empty change changes nothing.
    assert shown(details(client, document["id"])) == READ


def test_a_change_made_while_the_document_is_read_holds(client, session, services):
    project = create_project(client)
    document = text_document(client, services, project["id"])
    client.post(f"/api/documents/{document['id']}/reprocess")
    answer = services.llm.structured

    def meanwhile(content, schema, usage=None):
        # The member sets the title while the model reads the document's summaries.
        if schema.__name__ == "DocumentAnalysis":
            details(client, document["id"], title="Set while read")
        return answer(content, schema, usage)

    services.llm.structured = meanwhile
    process_document(document["id"], services)
    reread = client.get(f"/api/documents/{document['id']}").json()
    assert reread["title"] == "Set while read" and reread["read_title"] == READ["title"]
    assert paths(client, session, document["id"]) == ["Set while read › terms"] * 2
