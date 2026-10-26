"""A document's note (app/notes.py): one text its project's members share, written by whoever may
change the project and read by everyone who reads the document, which goes with the document,
into the trash and out, travels in the project's export and outlives its writer's account."""

import json

from sqlalchemy import delete

from app.models import User
from tests.test_access import crew  # noqa: F401
from tests.test_api import create_project
from tests.test_exports import archive_of, ask, run_export
from tests.test_trash import restore
from tests.test_versions import text_document

NONE = {"text": "", "changed_by": None, "changed_at": None}


def note(client, document_id) -> dict:
    response = client.get(f"/api/documents/{document_id}/note")
    assert response.status_code == 200, response.text
    return response.json()


def write(client, document_id, text):
    return client.put(f"/api/documents/{document_id}/note", json={"text": text})


def test_a_note_is_written_by_the_project_s_editors_and_read_by_everyone(
    client,
    services,
    session,
    crew,  # noqa: F811
    act_as,
):
    project = create_project(client)
    document = text_document(client, services, project["id"])
    assert note(client, document["id"]) == NONE

    act_as(crew.bob)
    saved = write(client, document["id"], "  Paid by transfer on 3 March.\n\nAsk Cat.  \n")
    assert saved.status_code == 200, saved.text
    # Its own lines are the member's; what is around them is not.
    assert saved.json()["text"] == "Paid by transfer on 3 March.\n\nAsk Cat."
    assert saved.json()["changed_by"] == "Bob" and saved.json()["changed_at"]

    # A viewer reads it, and is offered no change.
    act_as(crew.vic)
    assert note(client, document["id"]) == saved.json()
    assert write(client, document["id"], "Mine.").status_code == 403

    # The last to save is named.
    act_as(crew.cat)
    assert write(client, document["id"], "Paid in full.").json()["changed_by"] == "Cat"

    # A note outlives its writer's account, without their name.
    session.execute(delete(User).where(User.id == crew.cat.user.id))
    session.commit()
    act_as(crew.owner)
    assert note(client, document["id"])["changed_by"] is None

    # A blank note is none.
    assert write(client, document["id"], " \n ").json() == NONE
    assert note(client, document["id"]) == NONE


def test_a_note_goes_into_the_trash_with_its_document_and_back(client, services):
    project = create_project(client)
    document = text_document(client, services, project["id"])
    write(client, document["id"], "Signed copy in the safe.")
    assert client.delete(f"/api/documents/{document['id']}").status_code == 204
    assert client.get(f"/api/documents/{document['id']}/note").status_code == 404
    assert restore(client, "documents", document["id"]).status_code == 200
    assert note(client, document["id"])["text"] == "Signed copy in the safe."


def test_the_project_s_export_holds_each_note_and_the_details_members_set(client, services):
    project = create_project(client)
    document = text_document(client, services, project["id"])
    plain = text_document(client, services, project["id"], b"Nothing to say.\n", "plain.txt")
    write(client, document["id"], "Signed copy in the safe.")
    client.patch(f"/api/documents/{document['id']}/details", json={"doc_date": "2024-04-02"})

    made = ask(client, project["id"]).json()
    run_export(services)
    listed = {
        entry["filename"]: entry
        for entry in json.loads(archive_of(client, project["id"], made["id"]).read("project.json"))[
            "documents"
        ]
    }
    exported = listed[document["filename"]]
    assert exported["note"]["text"] == "Signed copy in the safe."
    assert exported["note"]["changed_by"] == "Owner"
    assert exported["edited_details"] == ["doc_date"] and exported["doc_date"] == "2024-04-02"
    assert listed[plain["filename"]]["note"] is None
